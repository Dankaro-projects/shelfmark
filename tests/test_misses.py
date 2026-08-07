"""Recording what could not be found.

The README refuses embeddings and content extraction and says real misses
should decide whether to revisit that. These tests are about the split that
makes the log evidence rather than a tally: could metadata search EVER have
found the thing, or was it a phrasing problem?
"""

from __future__ import annotations

import json

import pytest

from shelfmark import misses
from conftest import one


@pytest.fixture
def srv(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    return server


# ------------------------------------------------------------- what is kept

def test_a_genuine_miss_is_recorded(srv, built):
    srv.search_docs("zzzznotacorpusword", limit=5)
    rows = misses.load(built)
    assert [r["query"] for r in rows] == ["zzzznotacorpusword"]


def test_a_hit_is_not_recorded(srv, built):
    srv.search_docs("status report", limit=5)
    assert misses.load(built) == []


def test_a_bad_filter_is_not_a_miss(srv, built):
    """It is already reported as a bad filter. Logging it would bury the
    real misses under the caller's typos."""
    out = srv.search_docs("report", doc_type="powerpoint", limit=5)
    assert "bad filter" in out.lower()
    assert misses.load(built) == []


def test_an_impossible_year_range_is_not_a_miss(srv, built):
    srv.search_docs("report", year_from=2026, year_to=2020)
    assert misses.load(built) == []


def test_an_empty_query_is_not_a_miss(srv, built):
    srv.search_docs("   ", limit=5)
    assert misses.load(built) == []


@pytest.mark.parametrize("q", ["", "   ", "\t\n", None])
def test_record_itself_declines_an_empty_query(built, q):
    """search_docs returns early on an empty query, so the guard inside
    record() is only reachable directly -- and a caller that logs blank
    lines poisons the report with rows carrying no term at all."""
    misses.record(built, q)
    assert misses.load(built) == []


def test_the_filters_in_play_are_recorded(srv, built):
    srv.search_docs("zzzznotacorpusword", root="Clients", shareable_only=True)
    r = misses.load(built)[0]
    assert r["filters"]["root"] == "Clients"
    assert r["filters"]["shareable_only"] is True
    assert "doc_type" not in r["filters"]        # unset filters are not noise


def test_a_miss_against_a_stale_index_is_flagged(srv, built):
    """Not evidence about coverage: you caused it by not refreshing."""
    st = json.loads(built.status_path.read_text())
    st["state"] = "failed"
    built.status_path.write_text(json.dumps(st))

    srv.search_docs("zzzznotacorpusword", limit=5)
    assert misses.load(built)[0]["stale"] is True


def test_logging_can_be_turned_off(srv, built, monkeypatch):
    """Recording query text is a privacy-affecting behaviour, and whether
    that is acceptable genuinely varies by operator and corpus."""
    object.__setattr__(built, "misses_enabled", False)
    srv.search_docs("zzzznotacorpusword", limit=5)
    assert misses.load(built) == []


def test_a_broken_log_never_fails_a_search(srv, built, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(misses, "record", boom)
    out = srv.search_docs("zzzznotacorpusword", limit=5)
    assert "No matches" in out


def test_the_log_is_capped(built):
    for i in range(60):
        misses.record(built, f"term{i}")
    object.__setattr__(built, "misses_keep", 10)
    for i in range(60):
        misses.record(built, f"later{i}")
    assert len(misses.load(built)) <= 15          # keep * 1.5, the trim point


# --------------------------------------------------------------- the report

def test_the_report_separates_reachable_from_unreachable(built, monkeypatch):
    """The whole point. A term that is in no filename, path, author or title
    was unreachable however it was phrased; a term that IS there was a
    phrasing problem. Counting misses without that split proves nothing."""
    for _ in range(12):
        misses.record(built, "quarkiness")            # in nothing
    for _ in range(12):
        misses.record(built, "status")                # in status_report_*.md

    out = misses.report(built)
    assert "quarkiness" in out and "nowhere in your metadata" in out
    lines = [ln for ln in out.splitlines() if ln.strip().endswith("status")]
    assert lines, "the reachable term should be listed"
    assert "nowhere" not in lines[0]


def test_the_verdict_says_reopen_when_misses_are_unreachable(built):
    for i in range(25):
        misses.record(built, f"quarkiness{i} floccinaucity{i}")
    out = misses.report(built)
    assert "NEVER have found" in out
    assert "content extraction" in out


def test_the_verdict_says_phrasing_when_the_material_is_there(built):
    for i in range(25):
        misses.record(built, "status report market note")
    out = misses.report(built)
    assert "phrasing" in out.lower()
    assert "would not have helped" in out


def test_it_refuses_to_conclude_from_too_few_misses(built):
    for i in range(5):
        misses.record(built, f"quarkiness{i}")
    assert "Too few" in misses.report(built)


def test_stale_misses_are_excluded_from_the_verdict(built):
    for i in range(30):
        misses.record(built, f"quarkiness{i}", stale=True)
    out = misses.report(built)
    assert "excluded" in out
    assert "Too few" in out or "NEVER" not in out


def test_nothing_recorded_says_so_plainly(built):
    assert "No misses recorded" in misses.report(built)


def test_report_explains_itself_when_logging_is_off(built):
    object.__setattr__(built, "misses_enabled", False)
    assert "logging is off" in misses.report(built)


def test_restricted_material_cannot_make_a_term_look_reachable(built):
    """`_in_corpus` asks files_fts, which never holds RESTRICTED rows — so a
    term that appears only in a sealed file still reads as unreachable
    rather than quietly confirming the file exists."""
    import sqlite3
    con = sqlite3.connect(f"file:{built.db}?mode=ro", uri=True)
    try:
        assert one(built, "SELECT COUNT(*) FROM files WHERE filename='id_rsa'") == 1
        assert misses._in_corpus(con, "id_rsa") is False
    finally:
        con.close()


def test_common_words_do_not_dominate_the_ranking(built):
    for _ in range(10):
        misses.record(built, "what is the report about")
    out = misses.report(built)
    assert "  the" not in out and " what" not in out
