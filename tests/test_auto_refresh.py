"""The server keeps its own index current.

Manual or agent-triggered indexing is a failure point: most corpus changes
come from a word processor, a sync client or another machine, none of which
run a hook. The MCP server is already resident for the whole session, so it
is the thing that should notice.
"""

from __future__ import annotations

import json
import threading

import pytest

from conftest import one


@pytest.fixture
def srv(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    return server


@pytest.fixture
def keeper(cfg, monkeypatch):
    """Drive auto_refresh with a hard cap on how many passes can happen.

    The cap belongs to the harness, not to any one stub. A test whose only
    way of ending the loop is the branch it is testing does not fail when
    that branch is broken -- it hangs, which reads as a pass under a timeout
    and as nothing at all under a mutation run. Found the hard way.
    """
    from shelfmark import refresh, server
    monkeypatch.setattr(server, "_CFG", cfg)

    def run(passes=(), max_passes=6):
        """passes: what each successive pass should do (return code, or an
        exception instance to raise). Shorter than max_passes repeats last."""
        log, stop = [], threading.Event()

        def outcome(kind):
            def fn(c, force=False):
                log.append(kind)
                if len(log) >= max_passes:
                    stop.set()
                step = passes[min(len(log) - 1, len(passes) - 1)] if passes else 0
                if isinstance(step, BaseException):
                    raise step
                return step
            return fn

        monkeypatch.setattr(refresh, "run", outcome("run"))
        monkeypatch.setattr(refresh, "run_if_needed", outcome("if_needed"))
        server.auto_refresh(stop, tick=0.001)
        return log

    return run


# ------------------------------------------------------------------- keeper

def test_the_first_pass_goes_through_the_gate_too(keeper):
    """Startup is not special. run_if_needed gates on the status file's
    age, which counts wall-clock — including the time nothing was
    running — so a recent clean status at startup means at most the same
    staleness window the steady state already accepts, and an
    unconditional walk on every MCP-client launch bought nothing tighter
    while charging every startup a full-corpus walk. Reintroducing the
    unconditional first run must fail HERE, not only in the end-to-end
    pair below — this is the decision, that is the mechanism."""
    log = keeper(max_passes=3)
    assert log[0] == "if_needed"


def test_every_pass_only_refreshes_when_needed(keeper):
    log = keeper(max_passes=3)
    assert log == ["if_needed", "if_needed", "if_needed"]


def test_a_fresh_index_is_not_rebuilt_at_startup(built, monkeypatch):
    """End-to-end half 1: status younger than max_age_seconds, no dirty
    marker — the first tick must not walk the corpus."""
    from shelfmark import refresh
    walked = []
    monkeypatch.setattr(refresh, "run",
                        lambda c, force=False: walked.append(1) or 0)
    refresh.run_if_needed(built)
    assert walked == []


@pytest.mark.parametrize("make_stale", [
    lambda cfg: cfg.status_path.unlink(),                 # never refreshed
    lambda cfg: cfg.dirty_marker.touch(),                 # a write landed
])
def test_a_stale_or_dirty_index_refreshes_on_the_first_tick(built,
                                                            monkeypatch,
                                                            make_stale):
    """End-to-end half 2: what the old unconditional run actually bought is
    still bought — through the gate."""
    from shelfmark import refresh
    walked = []
    monkeypatch.setattr(refresh, "run",
                        lambda c, force=False: walked.append(1) or 0)
    make_stale(built)
    refresh.run_if_needed(built)
    assert walked == [1]


def test_a_guard_refusing_does_not_stop_the_keeper(keeper):
    """A refused prune is a normal outcome that explains itself in the status
    file. Treating it as fatal would let one unmounted root end indexing for
    the rest of the session."""
    log = keeper(passes=[1], max_passes=4)        # 1 = the exit code of a refusal
    assert len(log) == 4


def test_the_keeper_gives_up_after_repeated_exceptions(keeper, capsys):
    """A read-only database raises forever. A thread retrying that every
    minute is noise, not resilience -- and every tool still reports the
    staleness, so stopping is not the same as hiding."""
    import sqlite3
    from shelfmark import server
    err = sqlite3.OperationalError("attempt to write a readonly database")

    log = keeper(passes=[err], max_passes=50)

    assert len(log) == server.AUTO_REFRESH_MAX_ERRORS
    assert "giving up" in capsys.readouterr().err


def test_a_recovered_error_resets_the_budget(keeper):
    """Two transient failures either side of a success must not add up to a
    permanent one."""
    e = RuntimeError("transient")
    log = keeper(passes=[e, e, 0, e, e, 0, 0], max_passes=7)
    assert len(log) == 7            # never hit the consecutive-error ceiling


def test_the_keeper_never_writes_to_stdout(cfg, monkeypatch, capsys):
    """stdio MCP puts the protocol on stdout. A single stray print there
    corrupts the session rather than merely logging noise."""
    from shelfmark import refresh, server
    monkeypatch.setattr(server, "_CFG", cfg)
    stop = threading.Event()
    real_run = refresh.run

    def once(c, force=False):
        stop.set()
        return real_run(c, force=force)

    # BOTH entry points stop the loop: if only the one under test does, a
    # regression hangs the suite instead of failing it.
    monkeypatch.setattr(refresh, "run", once)
    monkeypatch.setattr(refresh, "run_if_needed", once)
    capsys.readouterr()

    server.auto_refresh(stop, tick=0.001)

    assert capsys.readouterr().out == ""


def test_the_keeper_actually_indexes_a_new_file(cfg, monkeypatch):
    """End to end, with the real refresh: nobody ran a command."""
    from shelfmark import refresh, server
    monkeypatch.setattr(server, "_CFG", cfg)
    refresh.run(cfg)
    before = one(cfg, "SELECT COUNT(*) FROM files")

    new = cfg.primary_root.path / "Clients" / "Alpha" / "arrived_later.md"
    new.write_text("written by something that is not an agent\n")

    stop = threading.Event()
    real_run = refresh.run

    def once(c, force=False):
        stop.set()
        return real_run(c, force=force)

    monkeypatch.setattr(refresh, "run", once)
    monkeypatch.setattr(refresh, "run_if_needed", once)
    server.auto_refresh(stop, tick=0.001)

    assert one(cfg, "SELECT COUNT(*) FROM files") == before + 1
    assert one(cfg, "SELECT COUNT(*) FROM files"
                    " WHERE filename='arrived_later.md'") == 1


def test_a_second_keeper_declines_while_the_first_holds_the_lock(cfg):
    """Two clients means two keepers. The lock is an atomic mkdir, so the
    second returns cleanly instead of racing the first."""
    import os
    from shelfmark import refresh
    refresh.run(cfg)
    before = one(cfg, "SELECT COUNT(*) FROM files")

    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    (cfg.lock_dir / "pid").write_text(str(os.getpid()))   # alive, and ours
    (cfg.primary_root.path / "Clients" / "Alpha" / "ignored.md").write_text("x\n")
    try:
        assert refresh.run(cfg) == 0
        assert one(cfg, "SELECT COUNT(*) FROM files") == before   # did nothing
    finally:
        (cfg.lock_dir / "pid").unlink()
        cfg.lock_dir.rmdir()


def test_a_lock_held_by_another_user_is_not_stolen(cfg, monkeypatch):
    """Signalling a process owned by someone else raises PermissionError,
    which PROVES it is alive. Folding that in with ProcessLookupError let a
    second run reclaim a live lock -- and two refreshes pruning the same
    catalogue against different run timestamps is the wrong-prune this
    module exists to prevent."""
    import os
    import sys
    from shelfmark import refresh
    refresh.run(cfg)
    before = one(cfg, "SELECT COUNT(*) FROM files")

    # Fake the probe each platform actually uses. On POSIX that is
    # os.kill(pid, 0) raising PermissionError; on Windows the probe is
    # OpenProcess (os.kill there would TERMINATE the target, which is why
    # refresh never calls it), and ACCESS_DENIED surfaces as (True, True).
    if sys.platform == "win32":
        monkeypatch.setattr(refresh, "_win_pid_running",
                            lambda pid: (True, True))
    else:
        def not_ours(pid, sig):
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr(os, "kill", not_ours)
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    (cfg.lock_dir / "pid").write_text("4242")
    (cfg.primary_root.path / "Clients" / "Alpha" / "ignored.md").write_text("x\n")
    try:
        assert refresh.run(cfg) == 0
        assert one(cfg, "SELECT COUNT(*) FROM files") == before
        assert cfg.lock_dir.exists()          # still held by the other process
    finally:
        (cfg.lock_dir / "pid").unlink()
        cfg.lock_dir.rmdir()


def test_a_lock_from_a_dead_process_is_reclaimed(cfg, monkeypatch):
    """The mirror: a crashed run must not block indexing forever."""
    import os
    from shelfmark import refresh
    refresh.run(cfg)
    before = one(cfg, "SELECT COUNT(*) FROM files")

    def gone(pid, sig):
        raise ProcessLookupError("No such process")

    monkeypatch.setattr(os, "kill", gone)
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    (cfg.lock_dir / "pid").write_text("4242")
    (cfg.primary_root.path / "Clients" / "Alpha" / "arrived.md").write_text("x\n")

    assert refresh.run(cfg) == 0
    assert one(cfg, "SELECT COUNT(*) FROM files") == before + 1
