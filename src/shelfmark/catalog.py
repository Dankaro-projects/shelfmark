"""Build the metadata catalogue.

Metadata only: path facets, filename-derived doc type, OOXML authorship,
content hash, eviction/corruption status. No body text is ever extracted.

Incremental by (size, mtime, residency). Residency is in the skip key on
purpose: a cloud sync engine (iCloud "Optimize Storage") materialising or
evicting a file changes st_blocks but neither size nor mtime, so a
(size, mtime)-only skip freezes the evicted flag forever. Anything derived
at ingest time inherits the skip — a field computed inside ingest() is only
recomputed when the key changes. Before adding a derived column, decide
what invalidates it; if that is not in the skip key, it needs its own
backfill pass (see hashes.py).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePath

from .config import Config, extended, has_prefix, unextended
# is_evicted is re-exported: emails.py and the tests reach it as
# catalog.is_evicted, and ingest() resolves it through this module's
# namespace so a monkeypatch here still lands. The definition lives in
# probe.py -- see the note there.
from .probe import is_evicted, parse_app, parse_core
from .rules import (DATE_IN_NAME_RE, FORMAT_WINS_EXT, FORMAT_WINS_NAME,
                    OOXML_EXT, VERSION_RE)
from .rules import DEFAULT_SKIP_PREFIXES as SKIP_PREFIXES
from .schema import OWNED_TABLES, OWNED_VIEWS, SCHEMA


def connect(cfg: Config, rebuild: bool = False) -> sqlite3.Connection:
    cfg.db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.db)
    if rebuild:
        for v in OWNED_VIEWS:
            con.execute(f"DROP VIEW IF EXISTS {v}")
        for t in OWNED_TABLES:
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.commit()
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


# ------------------------------------------------------------------ derivation

def dir_seg(parts: list[str], i: int) -> str | None:
    """parts[i], but only when it is a DIRECTORY — i.e. something follows it.

    The last segment is the filename. Taking it as a facet makes a file
    sitting directly under a root label itself: 'Misc/notes.json' would
    produce client='notes.json', poisoning the client vocabulary and every
    client-filtered search."""
    return parts[i] if len(parts) > i + 1 else None


def path_facets(rel: str, cfg: Config) -> tuple[str, str | None, str | None, int, str]:
    """Derive (root, client, project, depth, domain) from a catalogue path.

    Which roots count as work/personal, and which subtrees re-anchor client
    extraction (work material misfiled under a personal root), come from
    config — the taxonomy is the operator's, not the tool's."""
    parts = rel.split("/")
    root = parts[0]
    domain = ("work" if root in cfg.facet_work_roots else
              "personal" if root in cfg.facet_personal_roots else "other")

    for prefix in cfg.facet_reanchor:
        if rel.startswith(prefix):
            # e.g. a work archive filed under a personal root: re-anchor so
            # its first level reads as a client, not as an admin subfolder.
            anchor_parts = prefix.rstrip("/").split("/")
            sub = parts[len(anchor_parts):]
            client = dir_seg(sub, 0) or anchor_parts[-1]
            project = dir_seg(sub, 1)
            return root, client, project, len(parts) - 1, "work"

    if root in cfg.facet_work_roots:
        client = dir_seg(parts, 1)
        project = None
        if len(parts) > 2 and not parts[2].startswith("."):
            project = dir_seg(parts, 2)
        return root, client, project, len(parts) - 1, domain

    return root, dir_seg(parts, 1), None, len(parts) - 1, domain


def classify_doc_type(filename: str, ext: str, cfg: Config) -> tuple[str | None, str]:
    """What the FILE is. Filename first, extension as fallback.

    Deliberately does NOT look at the path: matching the whole path lets one
    folder brand all its children (a 'Meeting …' folder turning every
    spreadsheet inside it into minutes). Folder meaning is context_type."""
    if FORMAT_WINS_NAME.match(filename or ""):
        return "code", "name-locked"
    if ext in FORMAT_WINS_EXT:
        return cfg.ext_fallback.get(ext, "code"), "ext-locked"
    stem = filename.rsplit(".", 1)[0]
    for name, pat in cfg.doc_type_rules:
        if pat.search(stem):
            return name, "filename"
    if ext in cfg.ext_fallback:
        return cfg.ext_fallback[ext], "ext"
    return None, "none"


def classify_context(rel: str, cfg: Config) -> tuple[str | None, str | None]:
    """What the FOLDER is for. Walks parents inward-out; closest label wins."""
    parts = rel.split("/")[:-1]
    for folder in reversed(parts):
        for name, pat in cfg.context_rules:
            if pat.search(folder):
                return name, folder
    return None, parts[-1] if parts else None


def infer_rights(rel: str, author: str | None, last_author: str | None,
                 sensitive: bool, cfg: Config) -> str:
    """Coarse authorship-based rights, written at ingest time.

    The full path-rule pass (rights.py) runs after every build and refines
    this — the builder's upsert would overwrite the refined value for any
    file it touches, which is why the two always run as a pair."""
    if sensitive:
        return "RESTRICTED"
    # Client authorship wins over the OWN patterns, so a client who happens
    # to share a name fragment with the operator is never misfiled as OWN.
    if cfg.client_author_re:
        for a in (author, last_author):
            if a and cfg.client_author_re.search(a):
                return "REFERENCE"
        if cfg.client_author_re.search(rel):
            return "REFERENCE"
    if cfg.own_author_re:
        for a in (author, last_author):
            if a and cfg.own_author_re.search(a):
                return "OWN"
    if author and cfg.tool_author_re and cfg.tool_author_re.search(author):
        return "OWN"
    if author:
        return "REFERENCE"
    return "UNKNOWN"


# ----------------------------------------------------------------- file inspection

def rel_key(p: PurePath, base: PurePath, label: str = "") -> str:
    """The catalogue key for `p` under `base` — ALWAYS forward-slashed.

    Catalogue keys are the grammar everything downstream parses: the
    /-anchored regexes in rules.py, prefix rules, FTS paths and extra-root
    label prefixes all assume '/'. PurePath.__str__ uses the platform
    separator, so on Windows the same file would key as 'a\\b.docx' and
    silently match no rule at all — as_posix() makes the key identical on
    every platform. EVERY writer of a catalogue path key must come through
    here; a second grammar in the same DB is unfindable by the first."""
    rel = p.relative_to(base).as_posix()
    return f"{label}/{rel}" if label else rel


def _is_junction(path: str) -> bool:
    """Windows directory junctions cross the trust boundary exactly like
    symlinks but are not symlinks: os.path.islink answers False for them on
    3.12+, so os.walk descends them. Nothing on POSIX ever is one, which is
    what makes the early return safe — and free."""
    if os.name != "nt":
        return False
    isjunction = getattr(os.path, "isjunction", None)     # 3.12+
    if isjunction is not None:
        return isjunction(path)
    try:
        st = os.lstat(path)                               # 3.11 fallback
    except OSError:
        return False
    # IO_REPARSE_TAG_MOUNT_POINT — the stat module only names it on Windows
    return getattr(st, "st_reparse_tag", 0) == 0xA0000003


def sha256_of(path: Path, st: os.stat_result) -> str | None:
    """Hash only materialised files.

    A dataless cloud file can read as empty and yield the SHA-256 of the
    empty string, which silently makes distinct files look identical."""
    if st.st_size == 0 or is_evicted(st):
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    digest = h.hexdigest()
    # empty-string hash on a non-empty file means the read never materialised
    if digest.startswith("e3b0c44298fc1c14") and st.st_size > 0:
        return None
    return digest


def sniff_non_ooxml(path: Path) -> dict:
    """Classify a file that carries an OOXML extension but is not a zip.

    'Not a zip' is NOT the same as corrupt: plain text or Markdown saved with
    a .docx/.pptx extension, and legacy OLE files carrying a modern extension,
    are perfectly readable. Reserve 'corrupt' for bytes we truly cannot
    account for."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return {"_error": "unreadable"}
    if not head:
        return {"_status": "corrupt", "_note": "zero bytes"}
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return {"_status": "ok", "_note": "legacy OLE format, extension misleading"}
    if head[:5] == b"{\\rtf":
        return {"_status": "ok", "_note": "RTF, extension misleading"}
    if head[:4] == b"%PDF":
        return {"_status": "ok", "_note": "PDF, extension misleading"}
    try:
        txt = head.decode("utf-8")
    except UnicodeDecodeError:
        return {"_status": "corrupt", "_note": "not zip, not OLE, not text"}
    kind = "markdown" if txt.lstrip().startswith("#") else "plain text"
    return {"_status": "ok", "_note": f"{kind}, extension misleading",
            "_real_type": "note"}


def probe_ooxml(path: Path) -> dict:
    """Pull docProps only. Never reads slides, paragraphs or cells."""
    try:
        with zipfile.ZipFile(path) as z:
            parts, app = parse_app(z)
            out = parse_core(z)
            out.update(app)
            out.update(parts)
            return out
    except zipfile.BadZipFile:
        return sniff_non_ooxml(path)
    except Exception:  # noqa: BLE001
        return {}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- walk

def _sidecars(cfg: Config) -> set[Path]:
    """Every file the catalogue itself owns, resolved."""
    db = cfg.db
    out = {db, Path(str(db) + "-wal"), Path(str(db) + "-shm"),
           cfg.status_path, cfg.dirty_marker, cfg.log_path}
    return {Path(os.path.realpath(p)) for p in out}


def walk(cfg: Config, stats: dict | None = None):
    """Yield (absolute_path, catalogue_key) over the primary root plus extras.

    The catalogue key is the path recorded in the DB. For extra roots it is
    prefixed with the root label so entries stay distinguishable from, and
    never collide with, primary-relative paths. A missing EXTRA root is
    skipped here and reported by ingest() as roots_missing — an unmounted
    root is indistinguishable from a mass deletion, so the prune must know.

    SYMLINKS ARE NOT FOLLOWED. The roots are the trust boundary, and a
    symlink is an invitation to leave it: a link to /etc/passwd inside a
    root reads as an ordinary file, gets catalogued, and `hash` opens it.
    os.walk already declines to descend symlinked directories; this declines
    the files too. A tree you do mean to index is an extra root — saying so
    in config is the honest way to widen the boundary.

    A DIRECTORY THAT WILL NOT OPEN IS COUNTED, NEVER SWALLOWED. os.walk's
    default onerror is None, which discards the error and yields nothing for
    that subtree — the files simply cease to exist as far as every caller is
    concerned. That is the failure this module exists to prevent: the rows
    then look stale to the prune and get deleted, and the catalogue reports
    itself fresh over a hole. Windows reaches this state routinely without
    any permission being wrong: a path over MAX_PATH raises
    ERROR_PATH_NOT_FOUND unless long paths are enabled machine-wide.

    `stats`, if given, receives a `symlinks` count and an `unreadable_dirs`
    count so both skips are reported rather than silent. It also receives
    `unreadable_keys` — the catalogue-relative prefixes that went unseen —
    because "which rows must the prune not touch" is a different question
    from "what do I tell the operator", and only the walk knows the answer
    to either."""
    seen_links = 0
    unreadable: list[str] = []
    unreadable_keys: list[str] = []
    guard = _sidecars(cfg)

    def files_under(base: Path, to_key):
        nonlocal seen_links

        def on_error(exc: OSError) -> None:
            # os.walk calls this instead of discarding the error. Record and
            # continue: one unreadable folder must not abort a whole refresh,
            # but it must not pass for an empty one either.
            failed = str(getattr(exc, "filename", "") or base)
            unreadable.append(failed)
            # The catalogue key for the same folder, so the prune can spare
            # exactly the rows underneath it and nothing else. A path that
            # will not resolve under this root tells us nothing about which
            # rows are at risk, so it is reported but not used for scoping.
            try:
                key = to_key(Path(failed))
            except (ValueError, OSError):
                return
            # A root that will not open keys as "." against itself ("label/."
            # for a labelled root) — a relative path, not a prefix, and
            # truthy, so it would silently scope to nothing. macOS TCC denies
            # exactly this way, which is the case that must not be the one
            # that slips. Normalise to the root's own prefix instead.
            if key == ".":
                key = ""
            elif key.endswith("/."):
                key = key[:-2]
            unreadable_keys.append(key)

        # followlinks defaults to False, which is what declines to descend a
        # symlinked directory. test_a_symlinked_directory_is_not_descended
        # guards the behaviour, so it survives anyone changing the mechanism.
        # Junctions are the Windows way to plant the same escape, and walk's
        # symlink refusal does not cover them — prune them here, counted with
        # the symlinks so the skip is reported, not silent.
        for dirpath, dirnames, filenames in os.walk(base, onerror=on_error):
            keep = []
            for d in dirnames:
                if d in cfg.skip_dirs:
                    continue
                if _is_junction(os.path.join(dirpath, d)):
                    seen_links += 1
                    continue
                keep.append(d)
            dirnames[:] = keep
            for fn in filenames:
                if fn in cfg.skip_names or fn.startswith(SKIP_PREFIXES):
                    continue
                full = os.path.join(dirpath, fn)
                if os.path.islink(full):
                    seen_links += 1
                    continue
                p = Path(full)
                # Defence in depth: config refuses a catalogue inside a root,
                # but never index our own database or its sidecars.
                #
                # unextended(): on Windows the walk runs over \\?\ paths, and
                # a prefixed path never equals its plain twin. Comparing the
                # two forms would leave this guard permanently unmatched —
                # a check that silently stops checking.
                if unextended(os.path.realpath(p)) in guard:
                    continue
                yield p, to_key(p)

    # Walk the extended form so a tree deeper than MAX_PATH is catalogued
    # instead of reported missing. Keys are still taken relative to the same
    # extended base, so the catalogue grammar is unchanged: the prefix
    # cancels out of `relative_to` and never reaches the database.
    primary = extended(cfg.primary_root.path)
    if cfg.primary_root.path.is_dir():
        yield from files_under(primary, lambda p: rel_key(p, primary))

    for label, base in cfg.extra_roots.items():
        if not base.is_dir():
            continue
        ext_base = extended(base)
        yield from files_under(ext_base, lambda p, b=ext_base, l=label:
                               rel_key(p, b, l))

    if stats is not None:
        stats["symlinks"] = seen_links
        stats["unreadable_dirs"] = len(unreadable)
        # Plain form: this is printed. \\?\ in an error message is noise the
        # operator has to mentally strip before recognising their own folder.
        stats["unreadable_paths"] = [str(unextended(u)) for u in unreadable[:10]]
        stats["unreadable_keys"] = unreadable_keys


# ------------------------------------------------------------------- ingest

def ingest(con: sqlite3.Connection, cfg: Config, do_hash: bool = True) -> dict:
    cur = con.cursor()
    known = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.execute(
        "SELECT path, bytes, mtime, evicted, status FROM files")}
    counts = {"new": 0, "updated": 0, "skipped": 0, "evicted": 0,
              "rematerialised": 0, "corrupt": 0, "restricted": 0, "total": 0,
              "symlinks": 0, "corrupt_paths": [],
              "unreadable_dirs": 0, "unreadable_paths": []}
    # Microseconds, not seconds: the prune compares seen_at < run_ts, and two
    # refreshes inside the same second (hooks firing back-to-back) would make
    # a deletion invisible to the second one at coarser resolution.
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    # seen_at must mean "this path was on disk during this walk", not "this
    # path was written during this walk" — the refresh prunes on it, and a
    # row left with an older stamp gets deleted. So every path the walk
    # yields is stamped, including unchanged ones the skip never writes.
    unchanged: list[str] = []

    # Paths whose cloud materialisation flipped but whose content did not:
    # (path, evicted, status). Corrected in bulk, cheaply — see below.
    rematerialised: list[tuple[str, int, str]] = []

    def flush_unchanged(force: bool = False) -> None:
        if unchanged and (force or len(unchanged) >= 5000):
            cur.executemany("UPDATE files SET seen_at=? WHERE path=?",
                            [(now, r) for r in unchanged])
            unchanged.clear()

    def flush_rematerialised(force: bool = False) -> None:
        if rematerialised and (force or len(rematerialised) >= 5000):
            cur.executemany(
                "UPDATE files SET evicted=?, status=?, seen_at=? WHERE path=?",
                [(ev, stt, now, r) for r, ev, stt in rematerialised])
            rematerialised.clear()

    for p, rel in walk(cfg, stats=counts):
        try:
            st = p.stat()
        except OSError:
            # The file is there (os.walk listed it) but unreadable this run —
            # a sync race or a permission bump. Stamp it so a transient stat
            # failure never reads as a deletion and drops its metadata.
            unchanged.append(rel)
            flush_unchanged()
            continue
        counts["total"] += 1
        mtime = iso(st.st_mtime)
        evicted = is_evicted(st)

        prev = known.get(rel)
        if prev and prev[0] == st.st_size and prev[1] == mtime:
            if bool(prev[2]) == evicted:
                counts["skipped"] += 1
                unchanged.append(rel)
                flush_unchanged()
                continue
            # Materialisation flipped, content did not. Cloud sync changes
            # st_blocks but neither size nor mtime, so a size+mtime skip
            # would freeze the old flag forever. Correct the flag here
            # rather than fall through to a full re-ingest: re-hashing tens
            # of GB inside a session-start refresh destroys its time budget;
            # hashes.py picks the hashes up on demand. 'corrupt' is a
            # content verdict, not a residency one — keep it.
            status = prev[3] if prev[3] == "corrupt" else (
                "evicted" if evicted else "ok")
            rematerialised.append((rel, int(evicted), status))
            counts["rematerialised"] += 1
            if evicted:
                counts["evicted"] += 1
            flush_rematerialised()
            continue

        ext = p.suffix.lower()
        sensitive = bool(cfg.secret_re.search(rel) or
                         (cfg.private_re and cfg.private_re.search(rel)))
        status = "evicted" if evicted else "ok"

        root, client, project, depth, domain = path_facets(rel, cfg)
        doc_type, doc_type_src = classify_doc_type(p.name, ext, cfg)
        context_type, folder_label = classify_context(rel, cfg)
        vm = VERSION_RE.search(p.name)
        dm = DATE_IN_NAME_RE.search(p.name)

        sha = None
        if do_hash and not evicted and not sensitive:
            sha = sha256_of(p, st)

        meta: dict = {}
        note = None
        probed = ext in OOXML_EXT and not evicted and not sensitive
        if probed:
            meta = probe_ooxml(p)
            if meta.get("_status") == "corrupt":
                status = "corrupt"
                counts["corrupt"] += 1
                # The count alone is not actionable: "corrupt 2" leaves the
                # operator to go find them. The _note travels along because
                # "zero bytes" (a sync stub, benign) and "not zip, not OLE,
                # not text" (real damage) call for different responses.
                counts["corrupt_paths"].append(
                    f"{rel} ({meta.get('_note') or '?'})")
            note = meta.get("_note")
            if meta.get("_real_type"):
                doc_type, doc_type_src = meta["_real_type"], "sniffed"

        author = meta.get("creator")
        last_author = meta.get("lastmod_by")
        rights = infer_rights(rel, author, last_author, sensitive, cfg)

        if evicted:
            counts["evicted"] += 1
        if rights == "RESTRICTED":
            counts["restricted"] += 1

        btime = getattr(st, "st_birthtime", None)  # absent on Linux
        cur.execute(
            """INSERT INTO files
               (path, filename, root, client, project, depth, ext, bytes,
                evicted, status, mtime, btime, name_year, version_tag, doc_type,
                doc_type_src, context_type, folder_label, domain,
                sha256, author, last_author, company, ooxml_title,
                authored_date, slide_count, rights, sensitive, note, seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                bytes=excluded.bytes, mtime=excluded.mtime, status=excluded.status,
                evicted=excluded.evicted, sha256=excluded.sha256,
                author=excluded.author, last_author=excluded.last_author,
                company=excluded.company, ooxml_title=excluded.ooxml_title,
                authored_date=excluded.authored_date,
                slide_count=excluded.slide_count, rights=excluded.rights,
                sensitive=excluded.sensitive, doc_type=excluded.doc_type,
                note=excluded.note,
                doc_type_src=excluded.doc_type_src,
                context_type=excluded.context_type,
                folder_label=excluded.folder_label, domain=excluded.domain,
                seen_at=excluded.seen_at
            """,
            (rel, p.name, root, client, project, depth, ext, st.st_size,
             int(evicted), status, mtime,
             iso(btime) if btime is not None else None,
             int(dm.group(1)) if dm else None,
             vm.group(1).lower() if vm else None, doc_type,
             doc_type_src, context_type, folder_label, domain,
             sha, author, last_author, meta.get("company"),
             (meta.get("title") or None), meta.get("created"),
             int(meta["slides"]) if str(meta.get("slides", "")).isdigit() else None,
             rights, int(sensitive), note, now),
        )
        fid = cur.execute("SELECT id FROM files WHERE path=?", (rel,)).fetchone()[0]

        # Blanks are holes, not absences: the probe blanks placeholder titles
        # ("Slide 7") and keeps the slot, so idx must come from the original
        # position or every later title reports the wrong slide number.
        #
        # Only a run that actually opened the file may clear its titles.
        # Rewriting from an empty meta would silently strip the titles of
        # every deck that went cloud-evicted since the last pass.
        titles: list[str] = []
        if probed:
            raw_titles = meta.get("slide_titles") or []
            keep = [(fid, i, t) for i, t in enumerate(raw_titles) if t.strip()]
            titles = [t for _, _, t in keep]
            cur.execute("DELETE FROM slide_titles WHERE file_id=?", (fid,))
            if keep:
                cur.executemany(
                    "INSERT INTO slide_titles(file_id, idx, title) "
                    "VALUES (?,?,?)", keep,
                )

        # FTS: metadata + slide titles only, and never for restricted material
        cur.execute("DELETE FROM files_fts WHERE rowid=?", (fid,))
        if rights != "RESTRICTED":
            cur.execute(
                "INSERT INTO files_fts(rowid, path, filename, doc_type, author,"
                " company, ooxml_title, titles) VALUES (?,?,?,?,?,?,?,?)",
                (fid, rel, p.name, doc_type or "", author or "",
                 meta.get("company") or "", meta.get("title") or "",
                 " \n".join(titles)),
            )

        counts["updated" if prev else "new"] += 1
        if counts["total"] % 2000 == 0:
            flush_unchanged(force=True)
            flush_rematerialised(force=True)
            con.commit()
            print(f"  ... {counts['total']} files seen", file=sys.stderr)

    flush_unchanged(force=True)
    flush_rematerialised(force=True)

    # Rows under a folder the walk could not open carry an old seen_at for
    # exactly the same reason a deleted file does, so the prune would delete
    # files that are still on disk. Stamp them as seen: the walk knows which
    # subtrees went unseen, and that is the only place the distinction
    # exists.
    #
    # Scoped to those subtrees, deliberately. Skipping the whole prune
    # instead would mean one permanently unreadable folder — a root-owned
    # directory, lost+found, a macOS .Trashes — disables pruning for the
    # entire corpus on every future run, and phantom rows for genuinely
    # deleted files would accumulate forever with no way to clear them.
    # Everything outside the unseen subtrees prunes normally.
    for key in counts.get("unreadable_keys", []):
        if key:
            # substr(), not LIKE: a folder named "report_2024" is a valid
            # LIKE pattern where "_" matches any character, so the prefix
            # would spare rows it has no business sparing. Escaping LIKE
            # correctly needs an ESCAPE clause and three substitutions;
            # comparing a prefix needs neither.
            prefix = key + "/"
            cur.execute(
                "UPDATE files SET seen_at=? WHERE path=? OR substr(path,1,?)=?",
                (now, key, len(prefix), prefix))
        else:
            # The root itself would not open: every row is at risk, and no
            # prefix can express that.
            cur.execute("UPDATE files SET seen_at=?", (now,))
    con.commit()
    counts["run_ts"] = now
    counts["roots_missing"] = [
        lab for lab, base in cfg.extra_roots.items() if not base.is_dir()]
    if not cfg.primary_root.path.is_dir():
        counts["roots_missing"].insert(0, str(cfg.primary_root.path))
    return counts


# -------------------------------------------------------------------- stats

def stats(con: sqlite3.Connection) -> str:
    q = con.execute
    total, = q("SELECT COUNT(*) FROM files").fetchone()
    out = [f"\ncatalogued files: {total}\n"]

    def table(title, sql, limit=12):
        rows = q(sql).fetchall()[:limit]
        if not rows:
            return
        out.append(f"=== {title} ===")
        for r in rows:
            label = r[0] if r[0] is not None else "(none)"
            out.append(f"  {r[1]:6d}  {str(label)[:58]}")
        out.append("")

    table("status", "SELECT status, COUNT(*) FROM files GROUP BY 1 ORDER BY 2 DESC")
    table("rights", "SELECT rights, COUNT(*) FROM files GROUP BY 1 ORDER BY 2 DESC")
    table("root", "SELECT root, COUNT(*) FROM files GROUP BY 1 ORDER BY 2 DESC")
    table("doc_type", "SELECT doc_type, COUNT(*) FROM files GROUP BY 1 ORDER BY 2 DESC", 20)
    table("top clients", "SELECT client, COUNT(*) FROM files WHERE domain='work' GROUP BY 1 ORDER BY 2 DESC", 20)
    table("authors", "SELECT author, COUNT(*) FROM files WHERE author IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", 15)

    dups = q("""SELECT COUNT(*), SUM(bytes) FROM (
                  SELECT sha256, COUNT(*) c, SUM(bytes)-MAX(bytes) bytes
                  FROM files WHERE sha256 IS NOT NULL
                  GROUP BY sha256 HAVING c > 1)""").fetchone()
    if dups and dups[0]:
        out.append(f"=== duplicates ===\n  {dups[0]} duplicate groups, "
                   f"{(dups[1] or 0)/1048576:.0f} MB reclaimable\n")

    # An unconfigured corpus classifies almost everything UNKNOWN, and
    # UNKNOWN is held back from shareable_only — so search looks broken
    # while every number above looks healthy.
    #
    # Name the mechanism that actually applies. Authorship only ever reaches
    # OOXML files, and most corpora are mostly not that: pointing at
    # [authors] here reads as half the answer when it is a few per cent of
    # it. Path rules are what classify the rest, and `review` is how to get
    # them without learning the schema.
    unknown, = q("SELECT COUNT(*) FROM files WHERE rights='UNKNOWN'").fetchone()
    if total and unknown * 100 // total >= 50:
        pct = unknown * 100 // total
        no_author, = q("SELECT COUNT(*) FROM files WHERE rights='UNKNOWN'"
                       " AND author IS NULL").fetchone()
        # Say WHY the authorship is missing when the engine actually knows.
        # Authorship and authored_date are read from OOXML, and probing is
        # skipped for evicted files — so on a mostly-evicted cloud tree the
        # absence is not a gap in the operator's config, it is the residency.
        # Without that sentence "no [authors] rule can reach them" reads as
        # an instruction to go and write [authors] rules, which is the one
        # thing that cannot work here.
        evicted, = q("SELECT COUNT(*) FROM files WHERE evicted=1").fetchone()
        why = ""
        if evicted * 100 // total >= 50:
            why = (f"  {evicted * 100 // total}% of the corpus is cloud-"
                   f"evicted, which is why: authorship and authored dates "
                   f"are\n"
                   f"  read from inside the file, and an evicted file is "
                   f"never opened. Year filters are inert for the same "
                   f"reason.\n"
                   f"  Path rules do not care — they read the path, which is "
                   f"always there.\n")
        out.append(
            f"=== next step ===\n"
            f"  {pct}% of the catalogue is UNKNOWN rights ({unknown} of "
            f"{total}), and {no_author} of those carry no author at all —\n"
            f"  so no [authors] rule can reach them. Only path rules can.\n"
            + why +
            f"  UNKNOWN is never returned under shareable_only, so that "
            f"filter will look empty until they exist.\n"
            f"  Run:  shelfmark review\n")
    return "\n".join(out)


def build(cfg: Config, rebuild: bool = False, do_hash: bool = True) -> dict:
    """Walk the tree and update the catalogue. Returns the ingest counts."""
    con = connect(cfg, rebuild=rebuild)
    try:
        print(f"cataloguing {cfg.primary_root.path} -> {cfg.db}", file=sys.stderr)
        c = ingest(con, cfg, do_hash=do_hash)
        missing = c["roots_missing"]
        print(f"run_ts {c['run_ts']}", file=sys.stderr)
        print(f"roots_missing {','.join(missing) if missing else '-'}",
              file=sys.stderr)
        print(f"\nseen {c['total']}  new {c['new']}  updated {c['updated']}  "
              f"unchanged {c['skipped']}  "
              f"rematerialised {c['rematerialised']}", file=sys.stderr)
        print(f"evicted {c['evicted']}  corrupt {c['corrupt']}  "
              f"restricted {c['restricted']}", file=sys.stderr)
        # A folder the walk could not open is not a detail. Everything under
        # it is missing from this run, so say it here rather than let the
        # counts above read as a complete pass.
        if c["unreadable_dirs"]:
            print(f"\nWARNING: {c['unreadable_dirs']} folder(s) could not be "
                  f"read — everything under them is MISSING from this run.",
                  file=sys.stderr)
            for up in c["unreadable_paths"]:
                print(f"  unreadable: {up}", file=sys.stderr)
            print("  On Windows this is usually a path over 260 characters: "
                  "enable LongPathsEnabled, or shorten the folder names.",
                  file=sys.stderr)
        # Only THIS run's corrupt files, never the cumulative DB state — the
        # summary describes what just happened, and a list that outgrew the
        # count it sits under would say the opposite of what it means.
        for cp in c["corrupt_paths"][:10]:
            print(f"  corrupt: {cp}", file=sys.stderr)
        if len(c["corrupt_paths"]) > 10:
            print(f"  ... and {len(c['corrupt_paths']) - 10} more "
                  f"(status='corrupt' in the catalogue has all of them)",
                  file=sys.stderr)
        if c.get("symlinks"):
            print(f"skipped {c['symlinks']} symlink(s) — the roots are the "
                  f"trust boundary; add a tree as an extra root to index it",
                  file=sys.stderr)
        return c
    finally:
        con.close()
