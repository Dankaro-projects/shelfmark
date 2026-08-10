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

No flags. Everything it reports it can determine, so there is nothing to
switch on.
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
    __slots__ = ("status", "title", "detail", "fix")

    def __init__(self, status: str, title: str, detail: str = "",
                 fix: str = "") -> None:
        self.status, self.title, self.detail, self.fix = (
            status, title, detail, fix)


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
            f"       [index] db = \"~/.local/share/shelfmark/catalog.db\""))
    else:
        out.append(Finding(OK, "the catalogue is outside any sync folder"))

    # No check here for "the catalogue is inside an indexed root": config.load
    # raises ConfigError on it, so this command cannot run at all in that
    # state. A guard placed after the one that already fired is not
    # defence in depth, it is a guard that can never be reached — and a
    # test written against it passes for the wrong reason.

    parent = db.parent
    if not parent.is_dir():
        out.append(Finding(
            WARN, "the catalogue folder does not exist yet",
            str(parent), "It is created by the first refresh."))
    elif not os.access(parent, os.W_OK):
        out.append(Finding(
            FAIL, "the catalogue folder is not writable", str(parent),
            "Grant write access, or point [index] db somewhere writable."))
    return out


def _check_roots(cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    for root in cfg.roots:
        label = root.label or "(primary)"
        if not root.path.exists():
            out.append(Finding(
                FAIL, f"root {label} does not exist", str(root.path),
                "Fix the path in [[roots]], or remove it."))
            continue
        if not root.path.is_dir():
            out.append(Finding(
                FAIL, f"root {label} is not a folder", str(root.path)))
            continue
        try:
            with os.scandir(root.path) as entries:
                next(iter(entries), None)
            out.append(Finding(OK, f"root {label} is readable"))
        except PermissionError:
            # macOS denies Documents, Desktop and Downloads to processes
            # without Full Disk Access, and denies them by returning an
            # error the walk would otherwise swallow into an empty tree.
            out.append(Finding(
                FAIL, f"root {label} cannot be read", str(root.path),
                "On macOS grant Full Disk Access to the program that runs "
                "shelfmark (System Settings > Privacy & Security);\n"
                "       elsewhere check the folder's permissions."))
        except OSError as exc:
            out.append(Finding(
                FAIL, f"root {label} cannot be read",
                f"{root.path}\n     {exc}"))
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
            out.append(Finding(OK, f"{ext} reader is installed"))
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
                f"pip install '{extra}'{note}"))
    return out


def _check_catalogue(cfg: Config) -> list[Finding]:
    """What the catalogue itself says about whether it can answer."""
    out: list[Finding] = []
    if not cfg.db.exists():
        out.append(Finding(
            WARN, "no catalogue yet", str(cfg.db),
            "Run:  shelfmark refresh"))
        return out
    try:
        con = sqlite3.connect(cfg.ro_uri, uri=True)
    except sqlite3.Error as exc:
        out.append(Finding(FAIL, "the catalogue will not open", str(exc)))
        return out
    try:
        total, evicted = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(evicted), 0) FROM files"
        ).fetchone()
        if not total:
            out.append(Finding(
                WARN, "the catalogue is empty", str(cfg.db),
                "Run:  shelfmark refresh"))
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
                "corpus to index it is usually the wrong trade."))
        else:
            out.append(Finding(OK, "the corpus is materialised on disk"))

        unknown, = con.execute(
            "SELECT COUNT(*) FROM files WHERE rights='UNKNOWN'").fetchone()
        if unknown * 100 // total >= 50:
            out.append(Finding(
                WARN, f"{unknown * 100 // total}% of the catalogue has "
                f"UNKNOWN rights",
                "UNKNOWN is never returned under shareable_only, so that "
                "filter looks empty until rules exist.",
                "Run:  shelfmark review"))
    finally:
        con.close()
    return out


def run(cfg: Config, out=None) -> int:
    """Print the report. Returns the process exit code."""
    import sys
    out = out or sys.stdout
    findings: list[Finding] = []
    findings += _check_roots(cfg)
    findings += _check_db(cfg)
    findings += _check_catalogue(cfg)
    findings += _check_readers(cfg)

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
