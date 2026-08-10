"""Catalogue keys are forward-slashed on every platform.

The keys are the grammar everything downstream parses — the /-anchored
regexes in rules.py, prefix rules, FTS paths, extra-root label prefixes.
str(PurePath) uses the platform separator, so a str()-based key writer
produces a second, backslashed grammar on Windows that matches no rule and
no prefix. These tests drive rel_key with PureWindowsPath from POSIX CI:
that is the only form of the regression that can go red without a Windows
runner. Reverting rel_key to str(p.relative_to(base)) must fail here.
"""

from pathlib import PurePosixPath, PureWindowsPath

from shelfmark.catalog import rel_key


def test_windows_paths_key_with_forward_slashes():
    base = PureWindowsPath(r"C:\Users\o\Documents")
    p = PureWindowsPath(r"C:\Users\o\Documents\Clients\Acme\deck.pptx")
    assert rel_key(p, base) == "Clients/Acme/deck.pptx"


def test_extra_root_label_prefix_is_forward_slashed_on_windows():
    base = PureWindowsPath(r"D:\Archive")
    p = PureWindowsPath(r"D:\Archive\2019\report.docx")
    assert rel_key(p, base, "Archive") == "Archive/2019/report.docx"


def test_posix_paths_are_unchanged():
    base = PurePosixPath("/home/o/Documents")
    p = PurePosixPath("/home/o/Documents/Clients/Acme/deck.pptx")
    assert rel_key(p, base) == "Clients/Acme/deck.pptx"


def test_ro_uri_survives_uri_metacharacters_in_the_db_path(tmp_path):
    # A URI metacharacter in the db path makes the hand-built
    # f"file:{db}?mode=ro" form truncate, silently opening a DIFFERENT
    # (empty) db. Path.as_uri() percent-encodes it. Reverting ro_uri to the
    # f-string must fail here. '#' rather than '?': sqlite discards both a
    # '?' query and a '#' fragment identically, but '?' cannot exist in an
    # NTFS filename, and this must fail on every platform CI runs.
    import sqlite3

    from shelfmark.config import Config

    odd = tmp_path / "cat#logue.db"
    con = sqlite3.connect(odd)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()

    uri = Config.ro_uri.fget(type("C", (), {"db": odd})())
    ro = sqlite3.connect(uri, uri=True)
    try:
        assert ro.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        try:
            ro.execute("INSERT INTO t VALUES (2)")
            raise AssertionError("mode=ro was lost — the URI is writable")
        except sqlite3.OperationalError:
            pass
    finally:
        ro.close()
