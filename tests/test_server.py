"""The MCP tools — including how they behave when the answer is 'no'.

An empty result that reads like a genuine absence, when the real cause was a
typo'd filter or a stale index, is the failure mode that quietly wastes a
caller's turn. Most of these tests are about that.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def srv(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    return server


def test_search_finds_by_filename(srv):
    out = srv.search_docs("status report", limit=5)
    assert "status_report_" in out


def test_search_finds_by_slide_title(srv):
    out = srv.search_docs("Challenge", limit=5)
    assert "real_titles.pptx" in out


def test_a_title_excerpt_stays_on_one_line(srv):
    """The slide-title column is newline-joined; an un-collapsed snippet
    breaks one indented bullet into several unindented lines.

    Checked by shape, not by looking for a newline inside an already-split
    line -- that assertion can never fail, which a mutation run proved."""
    out = srv.search_docs("Challenge", limit=5)
    lines = out.splitlines()
    assert any(ln.startswith("    titles: ") for ln in lines)
    # Everything after the header is either a result bullet or an indented
    # detail line. A leaked newline shows up as neither.
    for ln in lines[1:]:
        if not ln.strip():
            continue
        assert ln.startswith(("- ", "    ")), f"stray continuation line: {ln!r}"


def test_hyphenated_identifiers_do_not_crash_the_parser(srv):
    for q in ("ACME-2026-014", "status_report_1", "a:b", "2024-03-01"):
        out = srv.search_docs(q, limit=5)
        assert "Query error" not in out, q


def test_a_stray_quote_returns_results_not_an_error(srv):
    out = srv.search_docs('report" note', limit=5)
    assert "Query error" not in out


def test_an_unknown_filter_is_reported_as_a_bad_filter(srv):
    out = srv.search_docs("report", doc_type="powerpoint", limit=5)
    assert "bad filter" in out.lower()
    assert "known values" in out.lower()
    assert "no matches" not in out.lower()


def test_a_misspelled_filter_suggests_the_real_value(srv):
    out = srv.search_docs("report", root="client", limit=5)
    assert "Clients" in out


def test_case_is_corrected_silently(srv):
    out = srv.search_docs("report", root="clients", limit=5)
    assert "Unknown" not in out


def test_an_impossible_year_range_says_so(srv):
    out = srv.search_docs("report", year_from=2026, year_to=2020)
    assert "year" in out.lower()
    assert "no matches" not in out.lower()


def test_a_cut_list_admits_it_was_cut(srv):
    out = srv.search_docs("status report", limit=5)
    assert "showing 5 of" in out


def test_the_limit_is_capped(srv):
    out = srv.search_docs("status report", limit=10_000)
    assert out.count("\n- ") <= srv.cfg().max_limit


def test_a_zero_limit_does_not_collapse_to_one_row(srv):
    """0 means 'unspecified'. Collapsing it to 1 returns a single row under a
    header that reads like a deliberate cut."""
    assert srv.search_docs("status report", limit=0).count("\n- ") > 1


def test_a_genuine_miss_says_body_text_is_not_indexed(srv):
    out = srv.search_docs("zzzzznotacorpusword", limit=5)
    assert "no matches" in out.lower()
    assert "body text" in out.lower()


def test_browse_folder_answers_what_is_here(srv):
    out = srv.browse_folder("Clients/Alpha")
    assert "engagement" in out
    assert "40" in out


def test_browse_folder_is_not_a_query_builder(srv):
    """It answers the question rather than handing back the machinery."""
    out = srv.browse_folder("Clients")
    assert "Alpha" in out and "Beta" in out


def test_get_file_reports_a_full_record(srv):
    out = srv.get_file("Decks/real_titles.pptx")
    assert "The Challenge" in out
    assert "deck" in out


def test_get_file_on_an_unknown_path_is_explicit(srv):
    assert "Not in catalogue" in srv.get_file("Clients/Nope/missing.pptx")


def test_corpus_stats_volunteers_freshness(srv):
    out = srv.corpus_stats()
    assert "index fresh" in out.lower() or "min ago" in out.lower()


def test_corpus_stats_admits_when_it_cannot_verify(srv, built):
    """An index that answers confidently from a frozen snapshot is the
    failure this whole design exists to prevent."""
    for p in (built.primary_root.path / "Clients" / "Alpha"
              / "engagement").glob("*.md"):
        p.unlink()
    out = srv.corpus_stats()
    assert "cannot verify" in out.lower() or "⚠" in out


def test_selftest_runs_end_to_end(srv, capsys):
    srv.selftest()
    out = capsys.readouterr().out
    assert "shelfmark corpus" in out
