"""Governance invariants — the promises the README makes to the operator.

These are the tests that matter most for trust: RESTRICTED material must be
unreachable through every path, not merely absent from the happy one.
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import hits, one, rows


def test_secrets_are_sealed(built):
    for name in (".env", ".env.production", "id_rsa"):
        r = rows(built, "SELECT rights, sensitive, confidential FROM files"
                        " WHERE filename = ?", name)
        assert r, f"{name} was not catalogued at all"
        rights, sensitive, confidential = r[0]
        assert rights == "RESTRICTED", f"{name} is {rights}"
        assert sensitive == 1
        assert confidential == 1


def test_restricted_never_reaches_the_search_index(built):
    leaked = one(built, "SELECT COUNT(*) FROM files_fts JOIN files f"
                        " ON f.id = files_fts.rowid WHERE f.rights='RESTRICTED'")
    assert leaked == 0


def test_every_visible_row_is_searchable(built):
    """The mirror of the leak test: a row missing from FTS is browsable but
    invisible to search, which is a silent hole rather than a loud one."""
    gap = one(built,
              "SELECT (SELECT COUNT(*) FROM files WHERE rights!='RESTRICTED')"
              " - (SELECT COUNT(*) FROM files_fts)")
    assert gap == 0


def test_secrets_are_never_hashed(built):
    """A sealed file must not be opened by any later feature."""
    from shelfmark import hashes
    hashes.backfill(built)
    unsealed = one(built, "SELECT COUNT(*) FROM files"
                          " WHERE rights='RESTRICTED' AND sha256 IS NOT NULL")
    assert unsealed == 0


def test_restricted_is_absent_from_every_mcp_tool(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)

    assert hits(server.search_docs("id_rsa", limit=100)) == []
    assert hits(server.search_docs("rsa", limit=100)) == []
    assert hits(server.search_docs("env", limit=100)) == []
    assert ".env" not in server.browse_folder("Admin", limit=100)

    # get_file must not become the back door either. A sealed path answers
    # BYTE-IDENTICALLY to an absent one: the earlier "withheld by policy"
    # refusal confirmed that a guessed path exists, one probe at a time,
    # after corpus_stats went to real trouble to hide the subtree at all.
    absent = server.get_file("Admin/zz_never_existed")
    assert "Not in catalogue" in absent
    for path in ("Admin/id_rsa", "Admin/.env", "Admin/.env.production"):
        out = server.get_file(path)
        assert out == absent.replace("Admin/zz_never_existed", path), path


def test_get_file_wildcards_are_literal(built, monkeypatch):
    """LIKE metacharacters in the caller's input must not match anything:
    an unescaped '%' matches every row and hands back the first record."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    for probe in ("%", "_", "%.md"):
        out = server.get_file(probe)
        assert "Not in catalogue" in out, probe
        assert "rights" not in out, probe


def test_get_file_disk_probe_stays_inside_the_roots(built, monkeypatch,
                                                    tmp_path):
    """`..` must not turn the on-disk hint into an existence oracle for
    arbitrary machine paths — the roots are the trust boundary."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    outside = tmp_path / "outside_the_roots.txt"
    outside.write_text("exists, but none of this tool's business\n")
    out = server.get_file(f"../{outside.name}")
    assert "IS on disk" not in out
    assert "Not in catalogue" in out


def test_get_file_hint_is_silent_for_uncatalogued_sealed_files(built,
                                                               monkeypatch):
    """A file the rules would seal, written after the last refresh, must
    not be confirmed by the on-disk hint before the walk ever sees it."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    new = built.primary_root.path / "Clients" / "Alpha" / "id_rsa.bak"
    new.write_text("-----BEGIN PRIVATE KEY-----\n")
    out = server.get_file("Clients/Alpha/id_rsa.bak")
    assert "IS on disk" not in out
    assert "Not in catalogue" in out


def test_sealed_rows_stay_out_of_corpus_stats_aggregates(built, monkeypatch):
    """The corpus-wide sealed COUNT is the one deliberate disclosure; the
    headline totals and type breakdowns must not fold sealed rows back in
    (sum(bytes) over sealed material is still a fact about it)."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    n_open = one(built,
                 "SELECT count(*) FROM files WHERE rights != 'RESTRICTED'")
    n_all = one(built, "SELECT count(*) FROM files")
    assert n_open < n_all          # the fixture must actually seal something
    head = server.corpus_stats().splitlines()[1]
    assert f"{n_open:,} files" in head


def test_no_argument_can_unhide_restricted(built, monkeypatch):
    """There is deliberately no flag for this. If one is ever added, the
    filters that do exist must still not become a way round it."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    for kwargs in ({}, {"shareable_only": False}, {"own_only": False},
                   {"root": "Admin"}, {"path_contains": "Admin"}):
        assert hits(server.search_docs("rsa", limit=100, **kwargs)) == [], kwargs


def test_the_server_cannot_write(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    con = server.connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM files")
    finally:
        con.close()


def test_shareable_means_positively_classified(built, monkeypatch):
    """UNKNOWN is held back: never-reviewed is not the same as cleared."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    out = server.search_docs("report OR note OR brief", shareable_only=True,
                             limit=100)
    for line in out.splitlines():
        if line.startswith("- "):
            assert "UNKNOWN" not in line
            assert "CONFIDENTIAL" not in line


def test_rights_and_confidential_are_independent_axes(built):
    """A deck you authored for a client is OWN *and* confidential. Collapsing
    the two is how a corpus ends up mostly-RESTRICTED and unsearchable."""
    client_rows = rows(built, "SELECT rights, confidential FROM files"
                              " WHERE path LIKE 'Clients/%'")
    assert client_rows
    assert all(c == 1 for _, c in client_rows), "client tree should be confidential"
    assert any(r != "RESTRICTED" for r, _ in client_rows), \
        "confidential must not imply RESTRICTED"
