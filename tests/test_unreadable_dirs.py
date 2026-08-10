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
    assert "prune SKIPPED" in capsys.readouterr().err


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
