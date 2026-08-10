"""Ingest .pst / .msg email archives into the catalogue. Optional feature.

Requires a reader for the format you have:
  pip install 'shelfmark[msg]'    # .msg — extract-msg, pure wheels
  pip install 'shelfmark[pst]'    # .pst — libpff-python, compiles from C
  pip install 'shelfmark[email]'  # both

Headers always. Bodies only with bodies=True, and they land in a separate
table so they can be dropped without rebuilding anything else. Ingested
mail defaults to rights=REFERENCE — an archive of correspondence is context
to draw on, not material to ship verbatim.

Idempotency lives in `ux_emails_ident`, an expression index over COALESCEd
columns. The table-level UNIQUE looks like it does the job but is DEAD
whenever a column is NULL — NULL never compares equal, so INSERT OR IGNORE
silently re-inserts every .msg on every run (pst_folder is NULL for .msg).
Do not rely on the table-level constraint.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import traceback
from datetime import timezone
from pathlib import Path

from .catalog import is_evicted, rel_key
from .config import Config

DEFAULT_RIGHTS = "REFERENCE"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Flagged for review, not excluded — these turn up in real business mail.
# Deliberately EXCLUDES "confidential"/"privileged"/"do not forward": those
# sit in the legal footer of virtually every corporate email; including them
# produces ~90% false positives and makes the flag useless. Substantive
# markers only.
SENSITIVE_RE = re.compile(
    r"\bpassword\b|\bpasswd\b|\bcontrase\w+|api[_ -]?key|secret[_ -]?key"
    r"|\bIBAN\b[^\n]{0,40}[A-Z]{2}\d{2}|credit ?card|\bcvv\b"
    r"|\bsalary\b|\bsalario\b|\bnómina\b|\bnomina\b|payroll"
    r"|\bNIF\b|\bDNI\b|passport number|\bssn\b",
    re.I,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id                INTEGER PRIMARY KEY,
    source_path       TEXT NOT NULL,
    source_kind       TEXT NOT NULL,        -- 'msg' | 'pst'
    pst_folder        TEXT,
    era               TEXT,                 -- provenance label
    subject           TEXT,
    thread_topic      TEXT,
    sender_name       TEXT,
    sender_email      TEXT,
    sender_domain     TEXT,
    recipients        TEXT,
    recipient_domains TEXT,
    participant_count INTEGER,
    sent_utc          TEXT,
    sent_year         INTEGER,
    has_attachments   INTEGER DEFAULT 0,
    attachment_names  TEXT,
    body_chars        INTEGER,
    rights            TEXT,
    sensitive         INTEGER DEFAULT 0,
    source_ordinal    INTEGER,              -- position within its .pst folder
    UNIQUE(source_path, pst_folder, subject, sent_utc)
);
-- The UNIQUE above is dead whenever a column is NULL. This expression index
-- is the constraint that actually holds.
--
-- source_ordinal is in the key because headers are not. A .pst routinely
-- holds entries with no subject and no timestamp -- calendar items, drafts,
-- unreadable rows -- and every one of them keys identically, so without a
-- position the SECOND such message in a folder is silently dropped as a
-- re-ingest of the first. The ordinal is stable for a given archive, so
-- re-running the same file is still a no-op. .msg files leave it NULL:
-- their source_path is already one message.
CREATE UNIQUE INDEX IF NOT EXISTS ux_emails_ident ON emails(
    source_path,
    COALESCE(pst_folder, ''),
    COALESCE(subject, ''),
    COALESCE(sent_utc, ''),
    COALESCE(source_ordinal, -1)
);
CREATE INDEX IF NOT EXISTS ix_em_domain ON emails(sender_domain);
CREATE INDEX IF NOT EXISTS ix_em_year   ON emails(sent_year);
CREATE INDEX IF NOT EXISTS ix_em_sens   ON emails(sensitive);

-- Separate table so bodies can be dropped independently of the header index.
CREATE TABLE IF NOT EXISTS email_bodies (
    email_id INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    body     TEXT
);

-- content='' — columns read back EMPTY on MATCH. Always join back to emails
-- / email_bodies by rowid for readable text; never repeat this shape for a
-- table that must return its own columns.
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, sender_email, recipients, attachment_names, body, content=''
);

CREATE VIEW IF NOT EXISTS v_email_domains AS
SELECT sender_domain, COUNT(*) AS messages,
       MIN(sent_year) AS from_year, MAX(sent_year) AS to_year,
       SUM(has_attachments) AS with_attach
FROM emails WHERE sender_domain IS NOT NULL
GROUP BY 1 ORDER BY messages DESC;

CREATE VIEW IF NOT EXISTS v_email_years AS
SELECT sent_year, COUNT(*) AS messages, COUNT(DISTINCT sender_domain) AS domains
FROM emails WHERE sent_year IS NOT NULL GROUP BY 1 ORDER BY 1;
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender_email, recipients, attachment_names, body, content=''
)
"""

IDENT = ("COALESCE(sender_email,''), COALESCE(subject,''), "
         "COALESCE(sent_utc,''), COALESCE(body_chars,0)")

# A row has to say something about itself before it can be called a copy of
# something else. See dupe_losers().
#
# body_chars is deliberately NOT enough on its own: it is a length, and two
# unrelated one-line messages share one constantly. It discriminates between
# rows that already agree on sender, subject and time; it does not identify
# a row by itself.
IDENTIFIABLE = ("(COALESCE(sender_email,'') <> '' OR COALESCE(subject,'') <> ''"
                " OR COALESCE(sent_utc,'') <> '')")


def ensure_schema(con) -> None:
    """Create the email tables, and bring an older catalogue up to date.

    CREATE TABLE IF NOT EXISTS is a no-op on a table that already exists, so
    a new column has to be added explicitly or the schema silently stays at
    the old shape -- and the identity index would keep collapsing distinct
    header-less messages. The unique index is rebuilt because its definition
    changed, not just its columns."""
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(emails)")}
    if "source_ordinal" not in have:
        con.execute("ALTER TABLE emails ADD COLUMN source_ordinal INTEGER")
        con.execute("DROP INDEX IF EXISTS ux_emails_ident")
        con.executescript(SCHEMA)
    con.commit()


def domain_of(addr: str | None) -> str | None:
    if not addr or "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].strip().lower().rstrip(".,;:)")


def domains_in(text: str | None) -> str:
    if not text:
        return ""
    return ",".join(sorted({domain_of(a) or "" for a in EMAIL_RE.findall(text)} - {""}))


def first_email(text: str | None) -> str | None:
    if not text:
        return None
    m = EMAIL_RE.search(text)
    return m.group(0).lower() if m else None


def clean(s) -> str | None:
    if s is None:
        return None
    if isinstance(s, bytes):
        s = s.decode("utf-8", "replace")
    s = str(s).replace("\x00", " ").strip()
    return s or None


def iso_dt(dt) -> tuple[str | None, int | None]:
    if not dt:
        return None, None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds"), dt.year
    except Exception:  # noqa: BLE001
        return None, None


def store(con, rec: dict, body: str | None, want_body: bool) -> bool:
    txt = " ".join(filter(None, (rec.get("subject"), rec.get("attachment_names"),
                                 body or "")))
    rec["sensitive"] = int(bool(SENSITIVE_RE.search(txt))) if txt else 0
    rec["body_chars"] = len(body) if body else 0
    cols = ("source_path", "source_kind", "pst_folder", "era", "subject",
            "thread_topic", "sender_name", "sender_email", "sender_domain",
            "recipients", "recipient_domains", "participant_count", "sent_utc",
            "sent_year", "has_attachments", "attachment_names", "body_chars",
            "rights", "sensitive", "source_ordinal")
    cur = con.execute(
        f"INSERT OR IGNORE INTO emails ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})",
        tuple(rec.get(c) for c in cols),
    )
    if not cur.rowcount:
        return False
    eid = cur.lastrowid
    stored_body = body if (want_body and body) else None
    if stored_body:
        con.execute("INSERT OR REPLACE INTO email_bodies(email_id, body) VALUES (?,?)",
                    (eid, stored_body))
    con.execute(
        "INSERT INTO emails_fts(rowid, subject, sender_email, recipients,"
        " attachment_names, body) VALUES (?,?,?,?,?,?)",
        (eid, rec.get("subject") or "", rec.get("sender_email") or "",
         rec.get("recipients") or "", rec.get("attachment_names") or "",
         stored_body or ""),
    )
    return True


# ------------------------------------------------------------------------ .msg

def _ingest_msg(con, cfg, paths, era, want_body, stats):
    import extract_msg
    primary = cfg.primary_root.path
    for p in paths:
        try:
            st = p.stat()
            if is_evicted(st):
                stats["evicted"] += 1
                continue
            with extract_msg.openMsg(str(p)) as m:
                to = clean(m.to) or ""
                cc = clean(m.cc) or ""
                recips = ", ".join(filter(None, (to, cc)))
                sent, year = iso_dt(m.date)
                atts = [clean(a.getFilename()) for a in (m.attachments or [])]
                atts = [a for a in atts if a]
                rec = {
                    "source_path": rel_key(p, primary),
                    "source_kind": "msg",
                    "pst_folder": None,
                    "era": era,
                    "subject": clean(m.subject),
                    "thread_topic": clean(getattr(m, "conversationTopic", None)),
                    "sender_name": clean(m.sender),
                    "sender_email": first_email(clean(m.sender)),
                    "sender_domain": domain_of(first_email(clean(m.sender))),
                    "recipients": recips or None,
                    "recipient_domains": domains_in(recips) or None,
                    "participant_count": len(EMAIL_RE.findall(recips)) + 1,
                    "sent_utc": sent,
                    "sent_year": year,
                    "has_attachments": int(bool(atts)),
                    "attachment_names": "; ".join(atts) or None,
                    "rights": DEFAULT_RIGHTS,
                }
                if store(con, rec, clean(m.body), want_body):
                    stats["msg"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{p.name}: {type(e).__name__}: {e}")
        if (stats["msg"] + stats["evicted"]) % 250 == 0:
            con.commit()
    con.commit()


# ------------------------------------------------------------------------ .pst

def _walk_pst_folder(folder, con, src, era, want_body, stats, trail=""):
    name = clean(folder.name) or "(unnamed)"
    here = f"{trail}/{name}".lstrip("/")
    try:
        n_msg = folder.number_of_sub_messages
    except Exception:  # noqa: BLE001
        n_msg = 0
    for i in range(n_msg):
        try:
            m = folder.get_sub_message(i)
            hdr = clean(m.transport_headers) or ""
            sender = clean(m.sender_name)
            s_mail = first_email(hdr) or first_email(sender)
            # To: AND Cc: -- a Cc'd party is a recipient, and leaving them out
            # made recipient_domains mean something different here than it
            # does for .msg, where cc is included.
            parts = []
            for field in ("To", "Cc"):
                mh = re.search(rf"^{field}:\s*(.+?)(?=^\S|\Z)", hdr, re.M | re.S)
                if mh:
                    parts.append(" ".join(mh.group(1).split()))
            recips = ", ".join(p for p in parts if p)
            sent, year = iso_dt(m.delivery_time)
            try:
                atts = [clean(m.get_attachment(j).get_name())
                        for j in range(m.number_of_attachments)]
                atts = [a for a in atts if a]
            except Exception:  # noqa: BLE001
                atts = []
            rec = {
                "source_path": src,
                "source_kind": "pst",
                "pst_folder": here,
                "era": era,
                "subject": clean(m.subject),
                "thread_topic": clean(m.conversation_topic),
                "sender_name": sender,
                "sender_email": s_mail,
                "sender_domain": domain_of(s_mail),
                "recipients": recips or None,
                # From the recipients, NOT the whole header: the header also
                # carries From:, so using it filed every sender's domain as a
                # recipient domain and the column meant two different things
                # depending on which reader produced the row.
                "recipient_domains": domains_in(recips) or None,
                "participant_count": len(set(EMAIL_RE.findall(hdr))),
                "source_ordinal": i,
                "sent_utc": sent,
                "sent_year": year,
                "has_attachments": int(bool(atts)),
                "attachment_names": "; ".join(atts) or None,
                "rights": DEFAULT_RIGHTS,
            }
            body = None
            try:
                body = clean(m.plain_text_body)
            except Exception:  # noqa: BLE001
                pass
            if store(con, rec, body, want_body):
                stats["pst"] += 1
            if stats["pst"] % 500 == 0:
                con.commit()
                print(f"    ... {stats['pst']} pst messages", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{src}:{here}[{i}]: {type(e).__name__}: {e}")

    try:
        for j in range(folder.number_of_sub_folders):
            _walk_pst_folder(folder.get_sub_folder(j), con, src, era,
                             want_body, stats, here)
    except Exception as e:  # noqa: BLE001
        stats["errors"].append(f"{src}:{here} subfolders: {e}")


def _ingest_pst(con, cfg, paths, era, want_body, stats):
    import pypff
    primary = cfg.primary_root.path
    for p in paths:
        print(f"  opening {p.name} ({p.stat().st_size / 1048576:.0f} MB)",
              file=sys.stderr)
        pff = pypff.file()
        try:
            pff.open(str(p))
            root = pff.get_root_folder()
            _walk_pst_folder(root, con, rel_key(p, primary), era,
                             want_body, stats)
        except Exception:  # noqa: BLE001
            stats["errors"].append(f"{p.name}: {traceback.format_exc(limit=2)}")
        finally:
            try:
                pff.close()
            except Exception:  # noqa: BLE001
                pass
        con.commit()


# ----------------------------------------------------------------- dedupe

def dupe_losers(con) -> list[int]:
    """ids to drop: every row in a content-identical group except the lowest.

    Identity is CONTENT, not provenance — (sender_email, subject, sent_utc,
    body_chars). `ux_emails_ident` keys on source_path, which is exactly what
    it cannot use here: two archive files can hold the same mailbox, and
    every message common to both gets stored twice, so a two-row search
    spends both slots on the same message. Lowest id survives, so the first
    archive ingested wins and re-running is a no-op.

    IDENTIFIABLE excludes rows that carry no identity at all. Real archives
    hold header-less entries — calendar items, drafts, unreadable rows — and
    every one of them COALESCEs to the same ('', '', '', 0) key, so without
    this they collapse into a single survivor and the rest are deleted as
    "duplicates" they were never shown to be. Absence of evidence is not
    identity: a row we cannot identify is never a duplicate."""
    return [r[0] for r in con.execute(f"""
        SELECT id FROM emails
        WHERE {IDENTIFIABLE}
          AND id NOT IN (SELECT MIN(id) FROM emails
                         WHERE {IDENTIFIABLE} GROUP BY {IDENT})
        ORDER BY id
    """)]


def dedupe(cfg: Config, apply: bool = False) -> str:
    con = sqlite3.connect(cfg.db)
    con.row_factory = sqlite3.Row
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                           " AND name='emails'").fetchone():
            return ("No email corpus in this catalogue — run "
                    "`shelfmark ingest-email <folder>` first.")
        total = con.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        drop = dupe_losers(con)
        if not drop:
            return f"{total:,} emails — no content duplicates."

        out = []
        groups = con.execute(f"""
            SELECT COUNT(*) FROM (SELECT 1 FROM emails GROUP BY {IDENT}
                                  HAVING COUNT(*) > 1)
        """).fetchone()[0]
        out.append(f"{total:,} emails · {len(drop):,} duplicate rows in "
                   f"{groups:,} groups · {total - len(drop):,} would remain\n")

        # A temp table, not an IN (?,?,?…) list: SQLite caps host parameters
        # at SQLITE_MAX_VARIABLE_NUMBER (32,766), and a mailbox ingested from
        # several archives blows past that easily — so the naive form fails
        # on exactly the corpora that need deduping most.
        con.execute("CREATE TEMP TABLE IF NOT EXISTS _dupe_drop (id INTEGER PRIMARY KEY)")
        con.execute("DELETE FROM _dupe_drop")
        con.executemany("INSERT INTO _dupe_drop(id) VALUES (?)",
                        [(i,) for i in drop])

        out.append("archives they came from (dropped rows):")
        for r in con.execute(
            "SELECT source_path, COUNT(*) n FROM emails"
            " WHERE id IN (SELECT id FROM _dupe_drop)"
            " GROUP BY 1 ORDER BY n DESC"
        ):
            out.append(f"  {r['n']:>5,}  {r['source_path']}")

        if not apply:
            out.append("\nDry run. Re-run with --apply to write.")
            return "\n".join(out)

        # sqlite's backup API, not shutil.copy2: ingest() puts the database in
        # WAL mode, and a plain file copy of a WAL database can tear — the
        # backup would be the one thing you cannot rely on. Same reasoning as
        # the pre-prune backup in refresh.py.
        backup = cfg.db.with_name(cfg.db.name + ".bak-predupe")
        dst = sqlite3.connect(backup)
        try:
            with dst:
                con.backup(dst)
        finally:
            dst.close()
        out.append(f"\nbackup: {backup.name}")

        with con:
            con.execute("DELETE FROM email_bodies WHERE email_id IN"
                        " (SELECT id FROM _dupe_drop)")
            con.execute("DELETE FROM emails WHERE id IN"
                        " (SELECT id FROM _dupe_drop)")
            # content='' — rebuild rather than patch: a targeted FTS delete
            # needs byte-exact original column values, and a full rebuild
            # from emails LEFT JOIN email_bodies is deterministic.
            con.execute("DROP TABLE IF EXISTS emails_fts")
            con.execute(FTS_SCHEMA)
            con.execute("""
                INSERT INTO emails_fts(rowid, subject, sender_email, recipients,
                                       attachment_names, body)
                SELECT e.id, COALESCE(e.subject,''), COALESCE(e.sender_email,''),
                       COALESCE(e.recipients,''), COALESCE(e.attachment_names,''),
                       COALESCE(b.body,'')
                FROM emails e LEFT JOIN email_bodies b ON b.email_id = e.id
            """)

        left = len(dupe_losers(con))
        after = con.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        fts = con.execute("SELECT COUNT(*) FROM emails_fts").fetchone()[0]
        orphans = con.execute(
            "SELECT COUNT(*) FROM email_bodies WHERE email_id NOT IN"
            " (SELECT id FROM emails)").fetchone()[0]
        con.execute("INSERT INTO emails_fts(emails_fts) VALUES('integrity-check')")
        out.append(f"\ndropped {len(drop):,} · emails now {after:,}")
        out.append(f"invariant — content duplicates remaining: {left} (must be 0)")
        out.append(f"invariant — emails_fts rows == emails rows: {fts:,} vs {after:,}")
        out.append(f"invariant — orphaned bodies: {orphans} (must be 0)")
        out.append("emails_fts integrity-check: ok")
        return "\n".join(out)
    finally:
        con.close()


# ------------------------------------------------------------------ ingest

def ingest(cfg: Config, prefix: str, era: str = "archive",
           bodies: bool = False) -> int:
    """Ingest every .msg/.pst under `prefix` (relative to the primary root)."""
    base = cfg.primary_root.path / prefix.strip("/")
    if not base.is_dir():
        print(f"shelfmark: no such folder under the primary root: {prefix}",
              file=sys.stderr)
        return 2
    # Privacy rules apply here too. The file catalogue never opens a
    # RESTRICTED row, but ingestion globs the disk directly -- so without
    # this an archive sitting in a subtree the operator marked private is
    # read, and its bodies indexed, by a feature that never consulted the
    # classification.
    def sealed(p: Path) -> bool:
        try:
            rel = rel_key(p, cfg.primary_root.path)
        except ValueError:
            rel = p.as_posix()
        return bool(cfg.secret_re.search(rel)
                    or (cfg.private_re and cfg.private_re.search(rel)))

    all_msgs = sorted(base.rglob("*.msg"))
    all_psts = sorted(base.rglob("*.pst"))
    msgs = [p for p in all_msgs if not sealed(p)]
    psts = [p for p in all_psts if not sealed(p)]
    skipped = (len(all_msgs) - len(msgs)) + (len(all_psts) - len(psts))
    if skipped:
        print(f"  skipped {skipped} archive(s) under private/secret paths",
              file=sys.stderr)
    print(f"scope: {prefix}\n  .msg files: {len(msgs)}\n  .pst files: {len(psts)}",
          file=sys.stderr)
    print(f"  bodies: {'YES' if bodies else 'headers only'}\n", file=sys.stderr)

    if not msgs and not psts:
        print("shelfmark: nothing to ingest — no .msg or .pst files there.",
              file=sys.stderr)
        return 0

    # A reader is required only for the formats actually present. Demanding
    # both refuses a folder of .msg files because the .pst reader is missing
    # — and libpff-python is a C extension that routinely fails to build, so
    # that is the common case, not the rare one.
    def reader(module: str, fmt: str, extra: str) -> bool:
        try:
            __import__(module)
            return True
        except ImportError:
            print(f"shelfmark: cannot read {fmt} files — {module} is not "
                  f"installed. Install with:  pip install '{extra}'",
                  file=sys.stderr)
            if module == "pypff":
                # libpff-python publishes no wheels at all, so this install
                # compiles. Saying so beats letting pip fail with a linker
                # error the reader has no reason to connect to email.
                print("  (.pst support builds from C source — it needs a "
                      "compiler: Visual C++ Build Tools on Windows, "
                      "build-essential on Linux, Xcode CLT on macOS.)",
                      file=sys.stderr)
            return False

    # Name the extra that installs THIS reader, not the one that installs
    # both: pointing a .msg user at shelfmark[email] hands them the C build
    # they do not need and cannot complete without a compiler.
    can_pst = reader("pypff", ".pst", "shelfmark[pst]") if psts else False
    can_msg = reader("extract_msg", ".msg", "shelfmark[msg]") if msgs else False
    if not (can_pst or can_msg):
        return 2

    con = sqlite3.connect(cfg.db)
    con.execute("PRAGMA journal_mode=WAL")
    ensure_schema(con)

    stats = {"msg": 0, "pst": 0, "evicted": 0, "errors": []}
    if can_pst:
        _ingest_pst(con, cfg, psts, era, bodies, stats)
    if can_msg:
        _ingest_msg(con, cfg, msgs, era, bodies, stats)

    print(f"\ningested  pst={stats['pst']}  msg={stats['msg']}  "
          f"skipped-evicted={stats['evicted']}  errors={len(stats['errors'])}",
          file=sys.stderr)
    for e in stats["errors"][:5]:
        print(f"  ! {e[:160]}", file=sys.stderr)

    # Report cross-archive content duplicates here instead of leaving them to
    # be discovered in search results. Not applied automatically: dedupe
    # deletes rows and takes a backup first.
    dupes = dupe_losers(con)
    if dupes:
        print(f"\n! {len(dupes)} content-duplicate rows (same sender+subject+"
              f"timestamp+body length, different archive file).\n"
              f"  Collapse them:  shelfmark dedupe-emails --apply",
              file=sys.stderr)
    con.close()
    return 0
