"""A folder the walk cannot open must be counted, reported, and must not
cost the operator the rows underneath it.

os.walk's default onerror is None: it discards the error and yields nothing
for that subtree. Every file under it then looks deleted to the prune, so a
refresh that hit one unreadable folder deletes real files from the catalogue
and reports itself clean. Windows reaches this without any permission being
wrong -- a path over MAX_PATH raises ERROR_PATH_NOT_FOUND unless long paths
are enabled machine-wide.

The failure is injected through os.scandir rather than through real ACLs or
a real 300-character path: chmod is a no-op for an administrator on Windows,
long-path behaviour depends on a machine-wide registry key, and neither
reproduces on all three platforms. What os.walk sees -- scandir raising
OSError for one directory -- is identical either way, and that is the whole
contract this guard rests on.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from shelfmark import catalog, refresh

DEEP = "unreadable_subtree"


@pytest.fixture
def corpus_with_subtree(corpus):
    """Two files in their own folder -- small enough that losing them stays
    inside the coverage floor and the prune ceiling. If it tripped either,
    this test would pass on the wrong guard and prove nothing about this one.
    """
    d = corpus / DEEP
    d.mkdir(parents=True, exist_ok=True)
    (d / "kept_note_a.md").write_text("a\n")
    (d / "kept_note_b.md").write_text("b\n")
    return corpus


@pytest.fixture
def blind_to_subtree(monkeypatch):
    """Make os.scandir fail for the one directory, as Win32 does past
    MAX_PATH. The error carries `filename` because a real OSError from
    scandir does, and that is what names the folder in the report."""
    real = os.scandir

    def failing(path=".", *a, **kw):
        if DEEP in str(path):
            raise OSError(3, "The system cannot find the path specified",
                          str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", failing)


def test_an_unreadable_directory_is_counted_not_swallowed(
        corpus_with_subtree, cfg, blind_to_subtree):
    stats: dict = {}
    seen = [rel for _, rel in catalog.walk(cfg, stats=stats)]

    assert not [r for r in seen if DEEP in r], "subtree should be invisible"
    assert stats["unreadable_dirs"] >= 1, (
        "the walk swallowed a directory error: the files under it are gone "
        "from this run and nothing said so")
    assert any(DEEP in p for p in stats["unreadable_paths"])


def test_a_root_that_will_not_open_scopes_to_the_whole_root(cfg, monkeypatch):
    """macOS TCC denies a documents root to a background process exactly this
    way. The root keys as "." against itself -- a relative path, not a
    prefix, and truthy -- so an unnormalised key would scope the protection
    to nothing and the rows would be pruned after all."""
    from shelfmark.config import unextended
    real = os.scandir
    root = cfg.primary_root.path

    def failing(path=".", *a, **kw):
        # Compared through unextended(): on Windows the walk hands scandir
        # the \\?\ form, which is not string-equal to the configured root.
        if unextended(path) == root:
            raise OSError(13, "Operation not permitted", str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", failing)
    stats: dict = {}
    list(catalog.walk(cfg, stats=stats))
    assert "" in stats["unreadable_keys"], (
        f"expected the whole-root marker, got {stats['unreadable_keys']!r}")


def test_the_build_reports_the_unreadable_folder(
        corpus_with_subtree, cfg, blind_to_subtree, capsys):
    catalog.build(cfg, do_hash=False)
    err = capsys.readouterr().err
    assert "could not be read" in err
    assert DEEP in err


def test_a_refresh_that_cannot_read_a_folder_keeps_its_rows(
        corpus_with_subtree, built, blind_to_subtree, capsys):
    """The regression that matters: the files are still on disk, so deleting
    their rows is a wrong answer the operator cannot see."""
    con = sqlite3.connect(built.db)
    try:
        before = con.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            (f"%{DEEP}%",)).fetchone()[0]
    finally:
        con.close()
    assert before == 2, "fixture should be catalogued before we blind the walk"

    assert refresh.run(built) == 0

    con = sqlite3.connect(built.db)
    try:
        after = con.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            (f"%{DEEP}%",)).fetchone()[0]
    finally:
        con.close()
    assert after == 2, (
        "rows were pruned for files that are still on disk -- the walk could "
        "not see them, which is not the same as their being deleted")
    err = capsys.readouterr().err
    assert "could not be read" in err
    assert "KEPT" in err


def test_a_deletion_elsewhere_still_prunes(
        corpus_with_subtree, built, blind_to_subtree, capsys):
    """The rest of the corpus must keep pruning while one folder is
    unreadable.

    Skipping the whole prune would be the easy fix and a worse bug: a folder
    that never opens -- root-owned, lost+found, a macOS .Trashes -- would
    disable pruning for the entire corpus on every future run, and phantom
    rows for genuinely deleted files would pile up with no way to clear
    them. That failure is invisible on the platform the original bug was
    found on and routine on the other two.
    """
    victim = corpus_with_subtree / "Decks" / "draft_0.md"
    assert victim.exists()
    victim.unlink()

    assert refresh.run(built) == 0

    con = sqlite3.connect(built.db)
    try:
        gone = con.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            ("%draft_0.md",)).fetchone()[0]
        kept = con.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            (f"%{DEEP}%",)).fetchone()[0]
    finally:
        con.close()
    assert gone == 0, ("a real deletion outside the unreadable folder was "
                       "not pruned -- one bad folder must not freeze the "
                       "whole prune")
    assert kept == 2, "rows under the unreadable folder must still be spared"


def test_the_subtree_returns_when_it_becomes_readable_again(
        corpus_with_subtree, built):
    """The guard must not be sticky: nothing was wrong with the tree, so a
    later clean run prunes normally again."""
    assert refresh.run(built) == 0
    con = sqlite3.connect(built.db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            (f"%{DEEP}%",)).fetchone()[0] == 2
    finally:
        con.close()


# ----------------------------------------- the status and the drift, honest

def test_an_unreadable_folder_degrades_the_status(
        corpus_with_subtree, built, blind_to_subtree):
    """Keeping the rows is half the job; saying so where the repeating
    surfaces read it is the other half. With state "ok" the stop hook said
    nothing, no streak accumulated, and only the terminal line — which a
    hook-run refresh discards — carried the news."""
    import json
    for expected_n in (1, 2):
        assert refresh.run(built) == 0
        st = json.loads(built.status_path.read_text())
        assert st["state"] == "degraded"
        assert "unreadable" in st["detail"]
        if expected_n > 1:
            assert st["consecutive_failures"] == expected_n


def test_the_drift_names_unreachable_not_deleted(
        corpus_with_subtree, built, blind_to_subtree):
    """The walk KNOWS which folders it could not open, and the drift used to
    discard that knowledge: 10 unreachable files reported as "10 deleted"
    with "Run `shelfmark refresh`" as the remedy — the one thing that cannot
    reach them."""
    import sqlite3
    from shelfmark import freshness
    con = sqlite3.connect(built.db)
    con.row_factory = sqlite3.Row
    try:
        line = freshness.freshness_line(con, built)
    finally:
        con.close()
    assert "2 unreachable" in line
    assert "deleted" not in line
    assert "cannot reach them" in line
    # the misleading half of the old remedy must not be the only advice
    assert "made readable" in line or "skipped in config" in line


def test_a_readable_again_folder_returns_to_ok_and_clears_the_streak(
        corpus_with_subtree, built, monkeypatch):
    """The degradation must not be sticky: reopen the folder and the next
    run is a plain ok with no streak residue."""
    import json
    import os
    real = os.scandir

    def failing(path=".", *a, **kw):
        if DEEP in str(path):
            raise OSError(3, "The system cannot find the path specified",
                          str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", failing)
    assert refresh.run(built) == 0
    assert json.loads(built.status_path.read_text())["state"] == "degraded"

    monkeypatch.undo()
    assert refresh.run(built) == 0
    st = json.loads(built.status_path.read_text())
    assert st["state"] == "ok"
    assert "consecutive_failures" not in st
