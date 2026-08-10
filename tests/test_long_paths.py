r"""A tree deeper than MAX_PATH must be catalogued, not reported missing.

Win32 rejects any path over 260 characters with ERROR_PATH_NOT_FOUND unless
the machine-wide LongPathsEnabled key is set. That key needs an
administrator, so "enable it and re-run" is advice a large share of
operators cannot take -- and the files are perfectly readable through the
\\?\ prefix, which needs no privilege at all.

These tests must not depend on the registry state of the machine running
them: the prefix is what is under test, and it works whether or not long
paths are enabled. The deep fixture is therefore created through the same
prefix, which is also the only way to create it on a machine where the key
is off.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from shelfmark import catalog, refresh
from shelfmark.config import extended, unextended

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Win32 path limit")

# 12 x 42 characters, comfortably past 260 once joined to a tmp_path.
DEEP_PART = "deep_folder_level_%02d_padding_padding_pad"
LEAF = "buried_report.md"


# ------------------------------------------------------------ the helper

@pytest.mark.skipif(os.name == "nt", reason="POSIX behaviour")
def test_extended_is_identity_off_windows():
    r"""Nothing about POSIX paths changes -- there is no limit to lift, and a
    \\?\ prefix there would be a literal directory name."""
    p = Path("/tmp/a/b")
    assert extended(p) == p
    assert extended(str(p)) == p
    assert unextended(extended(p)) == p


@WINDOWS_ONLY
def test_extended_spells_drive_and_unc_paths():
    assert str(extended("C:\\a\\b")) == "\\\\?\\C:\\a\\b"
    assert str(extended("\\\\server\\share\\a")) == "\\\\?\\UNC\\server\\share\\a"


@WINDOWS_ONLY
def test_extended_is_safe_to_apply_twice():
    once = extended("C:\\a")
    assert extended(once) == once


@WINDOWS_ONLY
def test_unextended_round_trips_both_spellings():
    for plain in ("C:\\a\\b", "\\\\server\\share\\a"):
        assert str(unextended(extended(plain))) == plain


def test_unextended_leaves_a_plain_path_alone():
    p = os.path.join("tmp", "a")
    assert str(unextended(p)) == p


# ------------------------------------------------------- the real thing

@pytest.fixture
def deep_file(corpus):
    """A file whose absolute path is over 260 characters.

    Created and removed through the extended prefix, because on a machine
    with long paths disabled no other API can reach it -- including the
    cleanup, which would otherwise fail and leave the tree behind.
    """
    deep = corpus
    for i in range(12):
        deep = deep / (DEEP_PART % i)
    os.makedirs(extended(deep), exist_ok=True)
    leaf = deep / LEAF
    with open(extended(leaf), "w", encoding="utf-8") as fh:
        fh.write("buried\n")
    assert len(str(leaf)) > 260, f"fixture is only {len(str(leaf))} chars"
    yield leaf
    # Unwind from the leaf up; plain rmtree cannot reach any of this.
    try:
        os.remove(extended(leaf))
        for i in range(11, -1, -1):
            os.rmdir(extended(deep))
            deep = deep.parent
    except OSError:
        pass


@WINDOWS_ONLY
def test_the_walk_hands_the_filesystem_the_extended_form(cfg, monkeypatch):
    r"""The decisive test, and the only one that cannot be masked by the
    machine it runs on.

    Where LongPathsEnabled is already 1, a deep tree is reachable through
    plain paths too, so "the deep file was catalogued" passes with or
    without the prefix and proves nothing. What must hold on every machine
    is that the walk asks the filesystem in the form that works everywhere.
    """
    seen_paths = []
    real = os.scandir

    def spy(path=".", *a, **kw):
        seen_paths.append(str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", spy)
    list(catalog.walk(cfg))

    assert seen_paths, "the walk scanned nothing"
    unprefixed = [p for p in seen_paths if not p.startswith("\\\\?\\")]
    assert not unprefixed, (
        f"{len(unprefixed)} directory scans used a plain path and would fail "
        f"past 260 characters, e.g. {unprefixed[0]!r}")


@WINDOWS_ONLY
def test_a_file_past_max_path_is_catalogued(cfg, deep_file):
    stats: dict = {}
    seen = [rel for _, rel in catalog.walk(cfg, stats=stats)]

    assert not stats["unreadable_dirs"], (
        f"the deep tree was unreadable rather than indexed: "
        f"{stats['unreadable_paths']}")
    assert any(r.endswith(LEAF) for r in seen), (
        "a file past MAX_PATH was not walked")


@WINDOWS_ONLY
def test_the_extended_prefix_never_reaches_the_catalogue_key(cfg, deep_file):
    """The prefix is a syscall detail. A key carrying it would not match the
    /-anchored rules, would sort apart from its siblings, and would be a
    second path grammar in the same database."""
    seen = [rel for _, rel in catalog.walk(cfg)]
    assert not [r for r in seen if "?" in r or r.startswith("\\")]
    buried = [r for r in seen if r.endswith(LEAF)][0]
    assert buried.startswith(DEEP_PART % 0)
    assert "/" in buried and "\\" not in buried


@WINDOWS_ONLY
def test_a_deep_file_survives_a_refresh_and_is_retrievable(cfg, deep_file):
    assert refresh.run(cfg) == 0
    con = sqlite3.connect(cfg.db)
    try:
        row = con.execute(
            "SELECT path FROM files WHERE path LIKE ?", (f"%{LEAF}",)
        ).fetchone()
    finally:
        con.close()
    assert row, "the deep file was not catalogued"
    # And the catalogue key resolves back to a file that really opens.
    assert extended(cfg.abs_path(row[0])).exists()


@WINDOWS_ONLY
def test_a_second_refresh_does_not_prune_the_deep_file(cfg, deep_file):
    """The regression this replaces: the file used to be invisible to the
    walk, so the prune deleted its row while it sat on disk."""
    assert refresh.run(cfg) == 0
    assert refresh.run(cfg) == 0
    con = sqlite3.connect(cfg.db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            (f"%{LEAF}",)).fetchone()[0] == 1
    finally:
        con.close()
