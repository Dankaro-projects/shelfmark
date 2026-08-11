"""Why is this not working — answered before it costs anyone a corpus.

Every check here exists because the failure it catches is silent: the
catalogue keeps answering, confidently, from a position that is already
wrong. The config comments already state most of these rules, but a comment
is not a check, and the operator who most needs the rule is the one who did
not read the file.

Three properties, deliberately:

Each finding names the fix, not the fault. "db is inside a cloud-synced
folder" is a diagnosis; "move it to <path>, then refresh" is what the
operator needed.

Evidence is shown, never implied. Sync-folder detection is a guess made
from a matched marker, so the marker is printed and the operator overrules
it if it is wrong. Naming a cause with no evidence attached is how a
diagnostic tool teaches people to ignore it.

No flag changes what is checked. Everything it reports it can determine,
so there is nothing to switch on. `--report` is not that kind of flag: the
checks and their verdicts are identical, and only the audience changes —
one form for the operator standing at the terminal, one for an issue
tracker.

That second form exists because nothing here phones home, which is the
product working as intended and also why a stranger's broken catalogue is
invisible to its author. The failure that motivated the streak fields ran
for four days in a log nobody opened. A report an operator can paste is
the only channel a local-only tool gets, so it must carry the diagnosis
and none of the corpus: counts, states and verdict codes, never a path, a
filename, a root label or a config string. That constraint is enforced by
a test rather than left to the care of whoever adds the next check —
`report()` copies no free text, so a new Finding cannot leak through it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .config import Config, _inside

# Status ranks: the exit code is decided by the worst one present.
OK, WARN, FAIL = "ok", "warn", "fail"
_RANK = {OK: 0, WARN: 1, FAIL: 2}


class Finding:
    """One verdict.

    `title`, `detail` and `fix` are written for a person and quote the
    machine freely — paths, root labels, the name of the sync folder. `code`
    is the same verdict with none of that: a fixed slug from the set below,
    safe to send somewhere the corpus must not go. `count` carries the one
    number a code cannot express (how many files, what percentage), and is
    None where the verdict has no number attached.
    """

    __slots__ = ("status", "title", "detail", "fix", "code", "count")

    def __init__(self, status: str, title: str, detail: str = "",
                 fix: str = "", code: str = "", count: int | None = None
                 ) -> None:
        self.status, self.title, self.detail, self.fix = (
            status, title, detail, fix)
        self.code, self.count = code, count


# ------------------------------------------------------------------ cloud

def sync_roots() -> list[tuple[Path, str]]:
    """Folders on this machine that a sync client owns.

    Built at runtime rather than hardcoded, because the same product uses a
    different path on each platform and a localised name in each tenancy —
    "OneDrive - Contoso" is not something a literal list can hold. Windows
    and macOS both publish theirs; Linux clients do not, so those are
    recognised by their conventional home-relative names and will miss a
    relocated one. A missed folder produces no finding at all, which is the
    right direction to fail in: this must never invent a problem.
    """
    home = Path.home()
    found: list[tuple[Path, str]] = []

    def add(p: Path, name: str) -> None:
        if p.is_dir() and not any(q == p for q, _ in found):
            found.append((p, name))

    # Windows publishes OneDrive's real location, tenancy name included.
    for env, name in (("OneDrive", "OneDrive"),
                      ("OneDriveConsumer", "OneDrive"),
                      ("OneDriveCommercial", "OneDrive for Business")):
        value = os.environ.get(env)
        if value:
            add(Path(value), name)

    # macOS: the modern location for every third-party client, plus iCloud.
    add(home / "Library" / "Mobile Documents", "iCloud Drive")
    cloud_storage = home / "Library" / "CloudStorage"
    if cloud_storage.is_dir():
        try:
            for entry in os.scandir(cloud_storage):
                if entry.is_dir():
                    # e.g. OneDrive-Contoso, GoogleDrive-someone, Dropbox
                    add(Path(entry.path), entry.name.split("-")[0])
        except OSError:
            pass

    # Conventional home-relative names, all platforms. OneDrive is listed
    # here too: the environment variable is absent on macOS and Linux.
    for name, label in (("OneDrive", "OneDrive"), ("Dropbox", "Dropbox"),
                        ("Google Drive", "Google Drive"),
                        ("GoogleDrive", "Google Drive"),
                        ("iCloudDrive", "iCloud Drive"),
                        ("Nextcloud", "Nextcloud"), ("ownCloud", "ownCloud"),
                        ("Insync", "Insync"), ("pCloudDrive", "pCloud"),
                        ("Proton Drive", "Proton Drive")):
        add(home / name, label)

    # "OneDrive - Contoso" style, which the plain name above cannot match.
    try:
        for entry in os.scandir(home):
            if entry.is_dir() and entry.name.startswith("OneDrive -"):
                add(Path(entry.path), entry.name)
    except OSError:
        pass
    return found


def in_sync_folder(path: Path) -> tuple[Path, str] | None:
    """The sync folder containing `path`, if any. Resolved on both sides:
    an unresolved comparison is defeated by a symlink, which is exactly how
    a database ends up somewhere its owner did not put it."""
    for root, name in sync_roots():
        if _inside(path, root):
            return root, name
    return None


# ----------------------------------------------------------------- checks

def _check_db(cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    db = cfg.db

    hit = in_sync_folder(db)
    if hit:
        root, name = hit
        out.append(Finding(
            FAIL, f"the catalogue is inside {name}",
            f"{db}\n     {name} owns {root}\n"
            f"     A sync client uploads this file on every write. The "
            f"catalogue is a mutating SQLite database with -wal and -shm "
            f"sidecars, so it thrashes the upload queue, and a client that "
            f"copies the three files at different moments restores a torn "
            f"database that opens and answers wrongly.",
            f"Move it out of the synced tree, then refresh:\n"
            f"       [index] db = \"~/.local/share/shelfmark/catalog.db\"",
            code="db_in_sync_folder"))
    else:
        out.append(Finding(OK, "the catalogue is outside any sync folder",
                           code="db_outside_sync_folder"))

    # No check here for "the catalogue is inside an indexed root": config.load
    # raises ConfigError on it, so this command cannot run at all in that
    # state. A guard placed after the one that already fired is not
    # defence in depth, it is a guard that can never be reached — and a
    # test written against it passes for the wrong reason.

    parent = db.parent
    if not parent.is_dir():
        out.append(Finding(
            WARN, "the catalogue folder does not exist yet",
            str(parent), "It is created by the first refresh.",
            code="db_folder_absent"))
    elif not os.access(parent, os.W_OK):
        out.append(Finding(
            FAIL, "the catalogue folder is not writable", str(parent),
            "Grant write access, or point [index] db somewhere writable.",
            code="db_folder_not_writable"))
    return out


def _check_roots(cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    for root in cfg.roots:
        label = root.label or "(primary)"
        if not root.path.exists():
            out.append(Finding(
                FAIL, f"root {label} does not exist", str(root.path),
                "Fix the path in [[roots]], or remove it.", code="root_absent"))
            continue
        if not root.path.is_dir():
            out.append(Finding(
                FAIL, f"root {label} is not a folder", str(root.path),
                code="root_not_a_folder"))
            continue
        try:
            with os.scandir(root.path) as entries:
                next(iter(entries), None)
            out.append(Finding(OK, f"root {label} is readable",
                               code="root_readable"))
        except PermissionError:
            # macOS denies Documents, Desktop and Downloads to processes
            # without Full Disk Access, and denies them by returning an
            # error the walk would otherwise swallow into an empty tree.
            out.append(Finding(
                FAIL, f"root {label} cannot be read", str(root.path),
                "On macOS grant Full Disk Access to the program that runs "
                "shelfmark (System Settings > Privacy & Security);\n"
                "       elsewhere check the folder's permissions.",
                code="root_unreadable"))
        except OSError as exc:
            out.append(Finding(
                FAIL, f"root {label} cannot be read",
                f"{root.path}\n     {exc}", code="root_unreadable"))
    return out


def _check_readers(cfg: Config) -> list[Finding]:
    """Only report a missing reader when there is something it would read.
    An install instruction for a format the operator does not have is noise
    that trains them to skim the rest.

    Counted from the catalogue, not from a walk: the extensions are already
    a column, and a diagnostic that re-walks the whole corpus to learn what
    the index could have told it is slow for no reason. Before the first
    refresh there is nothing to say here — the missing catalogue is already
    its own finding.
    """
    out: list[Finding] = []
    if not cfg.db.exists():
        return out
    try:
        con = sqlite3.connect(cfg.ro_uri, uri=True)
    except sqlite3.Error:
        return out
    try:
        present = {ext: con.execute(
            "SELECT COUNT(*) FROM files WHERE ext = ?", (ext,)).fetchone()[0]
            for ext in (".msg", ".pst")}
    except sqlite3.Error:
        return out
    finally:
        con.close()

    for ext, module, extra in ((".msg", "extract_msg", "shelfmark[msg]"),
                               (".pst", "pypff", "shelfmark[pst]")):
        if not present[ext]:
            continue
        try:
            __import__(module)
            out.append(Finding(OK, f"{ext} reader is installed",
                                   code=f"reader_present_{ext[1:]}"))
        except ImportError:
            note = ""
            if module == "pypff":
                note = ("\n       (.pst support compiles from C source: it "
                        "needs Visual C++ Build Tools on Windows, "
                        "build-essential on Linux, or the Xcode command "
                        "line tools on macOS)")
            out.append(Finding(
                WARN, f"{present[ext]} {ext} files are catalogued, "
                f"with no reader",
                "They are catalogued as files, but their messages are not "
                "searchable.",
                f"pip install '{extra}'{note}",
                code=f"reader_missing_{ext[1:]}", count=present[ext]))
    return out


def _check_catalogue(cfg: Config) -> list[Finding]:
    """What the catalogue itself says about whether it can answer."""
    out: list[Finding] = []
    if not cfg.db.exists():
        out.append(Finding(
            WARN, "no catalogue yet", str(cfg.db),
            "Run:  shelfmark refresh", code="catalogue_absent"))
        return out
    try:
        con = sqlite3.connect(cfg.ro_uri, uri=True)
    except sqlite3.Error as exc:
        out.append(Finding(FAIL, "the catalogue will not open", str(exc),
                           code="catalogue_will_not_open"))
        return out
    try:
        total, evicted = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(evicted), 0) FROM files"
        ).fetchone()
        if not total:
            out.append(Finding(
                WARN, "the catalogue is empty", str(cfg.db),
                "Run:  shelfmark refresh", code="catalogue_empty"))
            return out

        if evicted * 100 // total >= 50:
            out.append(Finding(
                WARN, f"{evicted * 100 // total}% of the corpus is "
                f"cloud-evicted",
                "Authorship, document titles and authored dates are read "
                "from inside the file, and an evicted file is never "
                "opened,\n     so those stay empty and year filters match "
                "nothing. Path rules are unaffected.",
                "Nothing to fix if that is deliberate — downloading a "
                "corpus to index it is usually the wrong trade.",
                code="corpus_mostly_evicted",
                count=evicted * 100 // total))
        else:
            out.append(Finding(OK, "the corpus is materialised on disk",
                               code="corpus_materialised"))

        unknown, = con.execute(
            "SELECT COUNT(*) FROM files WHERE rights='UNKNOWN'").fetchone()
        if unknown * 100 // total >= 50:
            out.append(Finding(
                WARN, f"{unknown * 100 // total}% of the catalogue has "
                f"UNKNOWN rights",
                "UNKNOWN is never returned under shareable_only, so that "
                "filter looks empty until rules exist.",
                "Run:  shelfmark review", code="rights_mostly_unknown",
                count=unknown * 100 // total))
    finally:
        con.close()
    return out


# ----------------------------------------------------------------- report

# The refresh writes `detail` for a person, and it quotes the machine: the
# unreadable-folder detail names the folder. So the report classifies it
# instead of copying it. An unrecognised detail becomes "other" rather than
# being passed through — the failure mode of a wrong bucket is a slightly
# vague report, and the failure mode of a passthrough is somebody's folder
# structure in a public issue.
_DETAIL_KINDS = (
    ("clean", "clean"),
    ("prune REFUSED", "prune_refused"),
    ("prune skipped", "prune_skipped"),
    ("walk saw", "coverage_floor"),
    ("unreadable", "unreadable_folders"),
)


def _detail_kind(detail: str) -> str:
    for marker, kind in _DETAIL_KINDS:
        if detail.startswith(marker) or marker in detail:
            return kind
    return "other"


def _status_summary(cfg: Config) -> dict:
    """State and streak from REFRESH_STATUS.json, with the detail classified.

    The streak is the point. "It is broken" and "it has been broken since
    Tuesday, 113 runs" are different reports, and only the second one tells
    the reader whether they are looking at a transient or at something that
    has been wrong since an upgrade.
    """
    import json
    try:
        st = json.loads(cfg.status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": False}
    return {
        "present": True,
        "state": str(st.get("state", "?")),
        "detail_kind": _detail_kind(str(st.get("detail", ""))),
        "files": st.get("files"),
        "failing_since": st.get("failing_since"),
        "consecutive_failures": st.get("consecutive_failures", 0),
    }


def report(cfg: Config, findings: list[Finding] | None = None) -> dict:
    """The same verdicts as `run`, with the corpus taken out.

    Every value here is a count, a boolean, a version, or a slug this module
    defines. Nothing is copied from a path, a filename, a root label or the
    config — see the module docstring for why that is a hard rule and not a
    preference.
    """
    import platform
    import sys
    from importlib.metadata import PackageNotFoundError, version

    if findings is None:
        findings = _all_findings(cfg)

    try:
        ver = version("shelfmark")
    except PackageNotFoundError:              # running from a source tree
        ver = "unknown"

    try:
        db_bytes = cfg.db.stat().st_size
    except OSError:
        db_bytes = None

    # Root paths never appear; only how many there are and how many opened.
    readable = sum(1 for f in findings if f.code == "root_readable")

    return {
        "shelfmark": ver,
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "release": platform.release(),
        "config_present": cfg.source is not None,
        "roots": {"configured": len(cfg.roots), "readable": readable},
        "scan": {"skip_dirs": len(cfg.skip_dirs),
                 "skip_names": len(cfg.skip_names)},
        "rights_rules": {
            "own_prefixes": len(cfg.own_prefixes),
            "own_confidential_prefixes": len(cfg.own_confidential_prefixes),
            "reference_prefixes": len(cfg.reference_prefixes),
            "neutral_prefixes": len(cfg.neutral_prefixes),
            "client_roots": len(cfg.client_roots),
            "personal_roots": len(cfg.personal_roots),
            "private_rule": cfg.private_re is not None,
        },
        "guards": {"prune_ceiling_pct": cfg.prune_ceiling_pct,
                   "prune_min_rows": cfg.prune_min_rows,
                   "coverage_floor_pct": cfg.coverage_floor_pct,
                   "stale_after_hours": cfg.stale_after_hours},
        "catalogue": _catalogue_summary(cfg, db_bytes),
        "status": _status_summary(cfg),
        "findings": [{"code": f.code or "uncoded", "status": f.status,
                      **({"count": f.count} if f.count is not None else {})}
                     for f in findings],
        "worst": max((f.status for f in findings),
                     key=lambda s: _RANK[s], default=OK),
    }


def _catalogue_summary(cfg: Config, db_bytes: int | None) -> dict:
    """Shape of the catalogue: how many rows, and how they are classified.

    Rights counts are the useful part — a corpus that is 99% UNKNOWN is a
    different bug report from one that is 99% RESTRICTED, and neither
    requires knowing what any of the files are.
    """
    out: dict = {"present": cfg.db.exists(), "bytes": db_bytes}
    if not out["present"]:
        return out
    try:
        con = sqlite3.connect(cfg.ro_uri, uri=True)
    except sqlite3.Error:
        out["readable"] = False
        return out
    try:
        out["readable"] = True
        out["files"], evicted = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(evicted), 0) FROM files"
        ).fetchone()
        out["evicted"] = evicted
        out["rights"] = dict(con.execute(
            "SELECT rights, COUNT(*) FROM files GROUP BY rights").fetchall())
        out["ext_families"] = len(con.execute(
            "SELECT DISTINCT ext FROM files").fetchall())
    except sqlite3.Error as exc:
        # The class name is shelfmark's own vocabulary; str(exc) can quote a
        # path, so it does not travel.
        out["readable"] = False
        out["error"] = type(exc).__name__
    finally:
        con.close()
    return out


def _check_status(cfg: Config) -> list[Finding]:
    """Whether the last refresh actually worked.

    Every other check here asks whether the setup *could* work. This one
    asks whether it *did*, which is not the same question and is the one
    with a history: the failure that motivated the streak fields announced
    itself on stderr 113 times into a log nobody opened, while every
    surface that gets read said nothing. A diagnostic that inspects the
    configuration and skips the status file would reproduce exactly that —
    a clean bill of health next to a catalogue that has been failing since
    Tuesday.

    The streak is included because "it failed" and "it has failed on every
    run for four days" send the operator to different places.
    """
    import json
    out: list[Finding] = []
    try:
        st = json.loads(cfg.status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out                 # covered by catalogue_absent
    except (OSError, ValueError) as exc:
        return [Finding(WARN, "the refresh status file is unreadable",
                        f"{cfg.status_path}\n     {exc}",
                        "The next refresh rewrites it.",
                        code="status_unreadable")]

    state = str(st.get("state", "?"))
    if state == "ok":
        return [Finding(OK, "the last refresh completed cleanly",
                        code="refresh_ok")]

    streak = st.get("consecutive_failures") or 0
    since = st.get("failing_since")
    run_word = "run" if streak == 1 else "runs"
    age = (f"{streak} consecutive {run_word} since {since}"
           if streak > 1 and since else f"since {since}" if since else "")

    detail = str(st.get("detail", "")).strip()
    return [Finding(
        FAIL if state == "failed" else WARN,
        f"the last refresh {'FAILED' if state == 'failed' else 'degraded'}"
        + (f" — {age}" if age else ""),
        detail,
        "The refresh already said this on stderr; if you are only seeing "
        "it now,\n       whatever runs it is discarding its output.",
        code=f"refresh_{state}", count=streak or None)]


def _all_findings(cfg: Config) -> list[Finding]:
    findings: list[Finding] = []
    findings += _check_roots(cfg)
    findings += _check_db(cfg)
    findings += _check_catalogue(cfg)
    findings += _check_status(cfg)
    findings += _check_readers(cfg)
    return findings


def run(cfg: Config, out=None, as_report: bool = False) -> int:
    """Print the report. Returns the process exit code."""
    import sys
    out = out or sys.stdout
    findings = _all_findings(cfg)

    if as_report:
        import json
        print(json.dumps(report(cfg, findings), indent=2, sort_keys=True),
              file=out)
        worst = max((_RANK[f.status] for f in findings), default=0)
        return 1 if worst == _RANK[FAIL] else 0

    print(f"\nconfig:  {cfg.source}", file=out)
    print(f"db:      {cfg.db}\n", file=out)

    mark = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}
    for f in findings:
        print(f"  [{mark[f.status]}]  {f.title}", file=out)
        if f.detail:
            print(f"     {f.detail}", file=out)
        if f.fix:
            print(f"     -> {f.fix}", file=out)

    worst = max((_RANK[f.status] for f in findings), default=0)
    bad = [f for f in findings if f.status != OK]
    print("", file=out)
    if not bad:
        print(f"{len(findings)} checks, nothing to fix.", file=out)
    else:
        print(f"{len(bad)} of {len(findings)} checks need attention.",
              file=out)
    return 1 if worst == _RANK[FAIL] else 0
