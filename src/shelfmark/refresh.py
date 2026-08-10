"""The keep-it-current path: build + rights + assertions, in one guarded run.

Fast and unattended — no cloud pulling, no content hashing (that would mean
reading the whole corpus; see hashes.py). Cheap enough to run at session
start because the builder is incremental by (size, mtime, residency).

Never leave the DB with the coarse rights the builder writes: its upsert
does rights=excluded.rights, so a build overwrites the rights backfill for
every file it touches. Build and rights ALWAYS run as a pair, in this order.

Scheduling note (macOS): do NOT run this from launchd. TCC denies
~/Documents to a user LaunchAgent, os.walk swallows the error, and the
builder walks a handful of files, reports `new 0`, and exits 0 — every layer
reports success while the index never updates. The walk-coverage assertion
below catches exactly that, but the correct trigger is something that
inherits a terminal's TCC grant (e.g. a Claude Code SessionStart hook).

A silent index is worse than a missing one — it answers confidently from a
frozen snapshot. This writes REFRESH_STATUS.json next to the DB, and the MCP
server's corpus_stats() reports it on every call.

Every guard that declines to touch the index also says so on stderr, not
only in the log and the status file — the operator who typed the command
reads neither, and a bare non-zero exit teaches nothing. The size guards
(walk coverage, prune ceiling) cannot tell an unreadable root from a real
mass deletion, so they name both possibilities and offer --force rather
than asserting a cause.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import catalog, rights
from .config import Config


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _win_pid_running(pid: int) -> tuple[bool, bool]:
    """(running, another_user) — asked via OpenProcess, which only asks.

    The POSIX probe is os.kill(pid, 0), and on Windows that call is NOT a
    probe: any signal number outside the two console events goes straight to
    TerminateProcess, so 'checking' the lock holder would kill a live
    refresh mid-prune and then report it running. ACCESS_DENIED maps to the
    POSIX PermissionError case — the process exists, it just is not ours."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        k32.CloseHandle(handle)
        return True, False
    return ctypes.get_last_error() == ERROR_ACCESS_DENIED, True


class _Log:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def say(self, msg: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {msg}\n")


def _write_status(cfg: Config, state: str, detail: str,
                  files: int = 0, added: int = 0) -> None:
    cfg.status_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.status_path.write_text(json.dumps({
        "state": state,
        "detail": detail,
        "finished_utc": _now_utc(),
        "files": files,
        "added": added,
    }, indent=2) + "\n", encoding="utf-8")


def _tell(msg: str) -> None:
    """Say it where the operator is standing -- the status file and log are
    for the next process, not the person who typed the command."""
    print(msg, file=sys.stderr)


def _fail(cfg: Config, log: _Log, terse: str, detail: str,
          files: int = 0, added: int = 0) -> int:
    log.say(f"FAIL: {terse}")
    _write_status(cfg, "failed", detail, files, added)
    _tell(f"\nFAILED — {detail}")
    _tell(f"  log: {cfg.log_path}")
    return 1


class _Lock:
    """Single-instance lock. mkdir is atomic on every filesystem we target;
    flock is absent on stock macOS."""

    def __init__(self, lock_dir: Path, log: _Log):
        self.dir = lock_dir
        self.log = log
        self.held = False

    def acquire(self) -> bool:
        try:
            os.mkdir(self.dir)
        except FileExistsError:
            pid_file = self.dir / "pid"
            if self._holder_is_alive(pid_file):
                return False
            self.log.say("stale lock, reclaiming")
            try:
                import shutil
                shutil.rmtree(self.dir)
                os.mkdir(self.dir)
            except OSError:
                self.log.say("cannot lock")
                return False
        (self.dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
        self.held = True
        return True

    def _holder_is_alive(self, pid_file: Path) -> bool:
        """Is the process named in the lock still running?

        PermissionError must NOT be folded in with ProcessLookupError:
        signalling a process owned by another user fails that way, which
        proves it exists. Treating it as stale lets a second run reclaim a
        live lock, and two refreshes pruning against different run
        timestamps is the wrong-prune this module guards against."""
        try:
            pid = int(pid_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if sys.platform == "win32":
            # os.kill(pid, 0) TERMINATES on Windows -- see _win_pid_running.
            running, other = _win_pid_running(pid)
            if running:
                self.log.say(
                    f"lock held by pid {pid} (another user) -- exiting"
                    if other else f"already running (pid {pid}) -- exiting")
            return running
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            self.log.say(f"lock held by pid {pid} (another user) -- exiting")
            return True
        except OSError:
            return False
        self.log.say(f"already running (pid {pid}) -- exiting")
        return True

    def release(self) -> None:
        if self.held:
            import shutil
            shutil.rmtree(self.dir, ignore_errors=True)
            self.held = False


def _misclassified_secrets(con: sqlite3.Connection, cfg: Config) -> int:
    """Count files that match the secret patterns but are not sealed.

    Checked in Python because the patterns are regexes, not LIKE clauses —
    the check must use the SAME patterns classification uses, or the two
    drift and the assertion goes blind."""
    bad = 0
    for path, r, c in con.execute(
            "SELECT path, rights, confidential FROM files"):
        if cfg.secret_re.search(path) and not (r == "RESTRICTED" and c == 1):
            bad += 1
    return bad


def run(cfg: Config, force: bool = False) -> int:
    """Returns process exit code. 0 = clean (including 'another run holds
    the lock'); 1 = failed, status written.

    force=True stands down the two size guards (walk coverage, prune
    ceiling) for one run. Needed because the guards cannot tell a real mass
    deletion from an unreadable root: without an override, a corpus that
    genuinely shrank can never be refreshed again."""
    log = _Log(cfg.log_path)
    lock = _Lock(cfg.lock_dir, log)
    if not lock.acquire():
        return 0

    try:
        # Clear the dirty marker up front: a write that lands DURING this
        # run sets it again and the next trigger picks it up.
        try:
            cfg.dirty_marker.unlink(missing_ok=True)
        except OSError:
            pass

        con = sqlite3.connect(cfg.db) if cfg.db.exists() else None
        before = 0
        if con is not None:
            try:
                before = con.execute(
                    "SELECT COUNT(*) FROM files").fetchone()[0]
            except sqlite3.OperationalError:
                before = 0
            finally:
                con.close()

        # ---- build ------------------------------------------------------
        try:
            counts = catalog.build(cfg, do_hash=False)
        except Exception as exc:  # noqa: BLE001
            return _fail(cfg, log, f"catalogue build: {exc}",
                         f"catalogue build raised: {exc}", before, 0)

        # ---- walk-coverage assertion ------------------------------------
        # An unreadable root produces a short walk that exits 0 (macOS TCC
        # does exactly this to background processes). Compare what the walk
        # actually saw against what is already catalogued — an index that
        # stops updating still answers every query confidently.
        #
        # A short walk has two causes and this check CANNOT distinguish them:
        # the files were unreadable, or they were really deleted. Report both
        # and let the operator decide — naming only one is a confident wrong
        # answer, which is the thing this module exists to prevent.
        seen = counts["total"]
        floor = before * cfg.coverage_floor_pct // 100
        if before and seen < floor and not force:
            return _fail(
                cfg, log,
                f"walk saw only {seen} of {before} catalogued files",
                f"walk saw {seen}/{before} catalogued files, below the "
                f"{cfg.coverage_floor_pct}% floor — the root was unreadable "
                f"to this process, that many files really went away, or an "
                f"upgrade widened the default skip list (newly skipped rows "
                f"stay catalogued but are no longer walked). Index NOT "
                f"updated; re-run with --force if the deletion or the new "
                f"skip list is right.", before, 0)
        if before and seen < floor and force:
            log.say(f"--force: coverage {seen}/{before} accepted")
            _tell(f"--force: accepting coverage {seen}/{before} "
                  f"(below the {cfg.coverage_floor_pct}% floor)")

        # ---- prune ------------------------------------------------------
        # The builder only ever inserts and updates, so a deleted or renamed
        # file stays searchable forever and get_file() reports it as fine —
        # a confident wrong answer, worse than a miss. The builder stamps
        # seen_at on every path the walk yields, changed or not, so anything
        # left carrying an older stamp was not on disk during the walk.
        # Guards, because a wrong prune is only recoverable from the backup:
        #   1. the coverage assertion above already ran
        #   2. refuse if any root is missing — an unmounted root is
        #      indistinguishable from a mass deletion
        #   3. refuse if the prune would take more than the configured share
        #      of the catalogue
        prune_note = ""
        prune_news = ""
        run_ts = counts["run_ts"]
        con = sqlite3.connect(cfg.db)
        try:
            stale_sql = "seen_at IS NULL OR seen_at < ?"
            if counts["roots_missing"]:
                miss = ",".join(counts["roots_missing"])
                prune_note = " prune=skipped(roots-missing)"
                prune_news = f"prune skipped — roots not on disk: {miss}"
                log.say(f"prune skipped: roots not on disk: {miss}")
                _tell(f"\nprune SKIPPED — these roots are not on disk: {miss}")
                _tell("  Their rows were kept. Remount them, or drop them "
                      "from [[roots]], then refresh again.")
            else:
                stale = con.execute(
                    f"SELECT COUNT(*) FROM files WHERE {stale_sql}",
                    (run_ts,)).fetchone()[0]
                ceiling = before * cfg.prune_ceiling_pct // 100
                if stale == 0:
                    prune_note = " pruned=0"
                elif stale > ceiling and stale > cfg.prune_min_rows and not force:
                    prune_note = f" prune=REFUSED({stale})"
                    prune_news = (f"prune REFUSED — {stale} of {before} rows "
                                  f"are stale (>{cfg.prune_ceiling_pct}%); "
                                  f"nothing deleted, see refresh.log")
                    log.say(f"prune REFUSED: {stale} of {before} rows stale "
                            f"(ceiling {ceiling}). Nothing deleted. Inspect:")
                    log.say(f"  SELECT path FROM files WHERE seen_at IS NULL "
                            f"OR seen_at < '{run_ts}' LIMIT 40;")
                    _tell(f"\nprune REFUSED — {stale} of {before} rows are no "
                          f"longer on disk, over the "
                          f"{cfg.prune_ceiling_pct}% ceiling.")
                    _tell("  Nothing was deleted; the index still lists them. "
                          "A real deletion, an unreadable subtree, or an "
                          "upgrade widening the default skip list all look "
                          "like this. Check what went missing, then re-run "
                          "with --force to accept it.")
                    _tell(f"  log: {cfg.log_path}")
                else:
                    # Consistent copy of a WAL database — plain cp would tear
                    # it. sqlite's backup API is the safe form.
                    bak = cfg.db.with_name(cfg.db.name + ".bak-preprune")
                    try:
                        dst = sqlite3.connect(bak)
                        with dst:
                            con.backup(dst)
                        dst.close()
                    except sqlite3.Error as exc:
                        log.say(f"backup before prune failed: {exc}")
                    with con:
                        con.execute(
                            f"DELETE FROM files_fts WHERE rowid IN "
                            f"(SELECT id FROM files WHERE {stale_sql})",
                            (run_ts,))
                        con.execute(
                            f"DELETE FROM slide_titles WHERE file_id IN "
                            f"(SELECT id FROM files WHERE {stale_sql})",
                            (run_ts,))
                        con.execute(
                            f"DELETE FROM files WHERE {stale_sql}", (run_ts,))
                    prune_note = f" pruned={stale}"
                    log.say(f"pruned {stale} rows no longer on disk")
                    if stale > ceiling and stale > cfg.prune_min_rows:
                        # only reachable under --force
                        log.say(f"--force: prune ceiling overridden ({stale} "
                                f"rows, ceiling {ceiling})")
                        _tell(f"--force: pruned {stale} rows, over the "
                              f"{cfg.prune_ceiling_pct}% ceiling "
                              f"(backup: {bak.name})")
        finally:
            con.close()

        # ---- rights (ALWAYS after build) --------------------------------
        try:
            rights.apply(cfg)
        except Exception as exc:  # noqa: BLE001
            return _fail(
                cfg, log,
                f"rights backfill: {exc} -- rights left COARSE, "
                f"governance not enforced",
                f"rights backfill failed after build: {exc}", before, 0)

        # ---- governance assertions --------------------------------------
        # A refresh that leaves any of these non-zero is a failure even
        # though every step above exited cleanly.
        con = sqlite3.connect(cfg.db)
        try:
            leak = con.execute(
                "SELECT COUNT(*) FROM files_fts JOIN files f "
                "ON f.id=files_fts.rowid WHERE f.rights='RESTRICTED'"
            ).fetchone()[0]
            secr = _misclassified_secrets(con, cfg)
            # Every non-RESTRICTED row must be searchable. A row missing from
            # files_fts is browsable but invisible to search — a silent hole.
            gap = con.execute(
                "SELECT (SELECT COUNT(*) FROM files WHERE rights!='RESTRICTED')"
                " - (SELECT COUNT(*) FROM files_fts)").fetchone()[0]
            after = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            con.close()

        added = after - before
        if leak != 0 or secr != 0 or gap != 0:
            return _fail(
                cfg, log,
                f"assertions -- restricted_in_fts={leak} "
                f"misclassified_secrets={secr} fts_gap={gap}",
                f"RESTRICTED leak={leak} secrets={secr} fts_gap={gap}",
                after, added)

        # `added` is the net row delta, so with a prune in the same run it is
        # (new files - pruned rows). prune_note carries the prune side.
        # A routine "pruned nothing" stays in the log line and OUT of the
        # status detail, or every session reads as an exception.
        #
        # A REFUSED prune is not "ok": the run completed, but the catalogue
        # knowingly lists rows whose files were not on disk, and every query
        # answers from them. "degraded" keeps that fact in the status file
        # where the server and the hook re-read it EVERY run — a catalogue
        # once carried 19,157 phantom rows for four days because the one
        # refusal message was easy to miss and nothing repeated it. A
        # missing EXTRA root stays "ok": a laptop away from its NAS is
        # normal life, not a degradation — the detail still carries it.
        state = "degraded" if "REFUSED" in prune_note else "ok"
        log.say(f"{state}: files={after} (net {added}){prune_note} "
                f"leak=0 secrets=0 fts_gap=0")
        _write_status(cfg, state, prune_news or "clean", after, added)
        return 0
    finally:
        lock.release()


def run_if_needed(cfg: Config, force: bool = False) -> int:
    """Refresh only when a write landed in the tree (dirty marker) or the
    index is older than max_age_seconds. For end-of-turn / on-idle hooks —
    costs nothing on turns that touched neither."""
    import time
    last = 0.0
    if cfg.status_path.exists():
        last = cfg.status_path.stat().st_mtime
    age = time.time() - last
    if cfg.dirty_marker.exists() or age >= cfg.max_age_seconds:
        return run(cfg, force=force)
    return 0
