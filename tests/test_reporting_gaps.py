"""Three ways an honest index used to report the opposite of the truth.

1. A filename crashed the command that reports it, on any machine whose
   ANSI codepage is not UTF-8.
2. A year filter over an undated corpus returned "No matches", which reads
   as "you do not have this" when it means "this filter excludes
   everything".
3. `stats` told the operator no [authors] rule could reach their files
   without saying why, so the one fix that cannot work looked like the fix.
"""

from __future__ import annotations

import io
import sqlite3
import sys

import pytest

from shelfmark import catalog, cli


# ------------------------------------------------- 1. legacy console codepage

# Built from an explicit escape because the spelling IS the test: "e" +
# U+0301 COMBINING ACUTE ACCENT, the decomposed (NFD) form of "cafe" with an
# accent. cp1252 encodes the precomposed U+00E9 quite happily and cannot
# encode the combining mark. Written as a plain literal, an editor or a tool
# that normalises to NFC would silently turn this into a name cp1252 CAN
# encode, and the test below would pass against the unfixed code.
DECOMPOSED = "café_note.docx"


def test_the_fixture_is_actually_hostile_to_cp1252():
    """Guards the guard: if this name ever becomes encodable, the test below
    can no longer fail and stops meaning anything."""
    with pytest.raises(UnicodeEncodeError):
        DECOMPOSED.encode("cp1252")


def test_output_survives_a_codepage_that_cannot_encode_the_name(monkeypatch):
    """Windows' default ANSI codepage is still 1252 outside the console, so
    a redirected `shelfmark stats > out.txt` encodes with cp1252 and used to
    raise UnicodeEncodeError over one filename."""
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "stderr", buf)

    cli._survive_legacy_console()
    print(f"  1  {DECOMPOSED}")            # must not raise
    buf.flush()
    assert buf.errors == "backslashreplace"


def test_the_encoding_itself_is_left_alone(monkeypatch):
    """Only the error handler changes. Re-encoding the stream as UTF-8 would
    hand mojibake to a consumer that asked for cp1252."""
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "stderr", buf)
    cli._survive_legacy_console()
    assert buf.encoding.lower().replace("-", "") == "cp1252"


# ------------------------------------------------------ 2. inert year filter

def _undate_everything(cfg):
    con = sqlite3.connect(cfg.db)
    try:
        con.execute("UPDATE files SET authored_date = NULL")
        con.commit()
    finally:
        con.close()


@pytest.fixture
def srv(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    return server


def test_a_year_filter_over_an_undated_corpus_says_so(srv, built):
    """The corpus has the files; the filter excludes all of them. Reporting
    that as 'No matches' is a statement about the corpus, and it is false."""
    _undate_everything(built)

    plain = srv.search_docs("status report")
    assert "match(es)" in plain, "precondition: the documents are findable"

    filtered = srv.search_docs("status report", year_from=2020)
    assert "No matches" not in filtered
    assert "authored date" in filtered.lower()
    assert "year_from" in filtered


def test_the_explanation_does_not_fire_when_dates_exist(srv):
    """A real empty result must stay a real empty result -- otherwise the
    message becomes noise attached to every failed year search."""
    out = srv.search_docs("status report", year_from=1990, year_to=1991)
    assert "No matches" in out


def test_an_undated_search_is_not_recorded_as_a_miss(srv, built, monkeypatch):
    """A filter that excluded everything is no evidence about what the
    corpus lacks -- recording it would bury the real misses, which are the
    only argument for ever building content extraction."""
    from shelfmark import misses
    _undate_everything(built)

    recorded = []
    monkeypatch.setattr(misses, "record", lambda *a, **k: recorded.append(a))
    srv.search_docs("status report", year_from=2020)
    assert not recorded


# ------------------------------------------- 3. why the authorship is absent

def test_stats_names_eviction_as_the_reason_authors_are_missing(built):
    """Authorship and authored dates are read from inside the file, and an
    evicted file is never opened. Without that sentence, "no [authors] rule
    can reach them" reads as an instruction to go and write [authors] rules
    -- the one thing that cannot work on a mostly-evicted tree."""
    con = sqlite3.connect(built.db)
    try:
        con.execute("UPDATE files SET evicted = 1, author = NULL,"
                    " rights = 'UNKNOWN'")
        con.commit()
        out = catalog.stats(con)
    finally:
        con.close()

    assert "next step" in out
    assert "evicted" in out
    assert "never opened" in out
    assert "Path rules do not care" in out


def test_stats_stays_quiet_about_eviction_on_a_local_corpus(built):
    """A local tree that is simply unclassified gets the original advice,
    with no cloud explanation attached to a cause that is not present."""
    con = sqlite3.connect(built.db)
    try:
        con.execute("UPDATE files SET evicted = 0, author = NULL,"
                    " rights = 'UNKNOWN'")
        con.commit()
        out = catalog.stats(con)
    finally:
        con.close()

    assert "next step" in out
    assert "cloud-evicted" not in out
