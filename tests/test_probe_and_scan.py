"""What gets into the catalogue, and what the probe says about it."""

from __future__ import annotations

from conftest import one, rows, write_pptx


# ------------------------------------------------------------------ scanning

def test_junk_filenames_are_skipped(built):
    for pattern in ("~$%", ".~lock.%", ".fuse_hidden%"):
        assert one(built, "SELECT COUNT(*) FROM files WHERE filename LIKE ?",
                   pattern) == 0, pattern


def test_skip_dirs_are_not_walked(built):
    assert one(built, "SELECT COUNT(*) FROM files"
                      " WHERE path LIKE '%node_modules%'") == 0


def test_extra_roots_are_labelled_not_merged(built):
    assert one(built, "SELECT COUNT(*) FROM files WHERE path='extra/loose_note.md'") == 1


# -------------------------------------------------------------- slide titles

def test_library_placeholders_are_dropped(built):
    fid = one(built, "SELECT id FROM files WHERE filename='library_placeholders.pptx'")
    assert rows(built, "SELECT title FROM slide_titles WHERE file_id=?", fid) == []


def test_powerpoint_filler_is_dropped(built):
    fid = one(built, "SELECT id FROM files WHERE filename='powerpoint_filler.pptx'")
    assert rows(built, "SELECT title FROM slide_titles WHERE file_id=?", fid) == []


def test_uniform_titles_are_dropped_in_any_language(built):
    """The backstop: whatever the string says, a deck where every slide
    reports the same title is describing its template."""
    fid = one(built, "SELECT id FROM files WHERE filename='uniform_titles.pptx'")
    assert rows(built, "SELECT title FROM slide_titles WHERE file_id=?", fid) == []


def test_real_titles_survive(built):
    fid = one(built, "SELECT id FROM files WHERE filename='real_titles.pptx'")
    got = [t for (t,) in rows(built, "SELECT title FROM slide_titles"
                                     " WHERE file_id=? ORDER BY idx", fid)]
    assert got == ["The Challenge", "Our Approach", "Results", "Next Steps"]


def test_a_dropped_title_does_not_renumber_the_rest(built):
    """Positions are slide numbers. Dropping slide 1 must not promote slide 2
    into its place."""
    fid = one(built, "SELECT id FROM files WHERE filename='mixed_titles.pptx'")
    got = rows(built, "SELECT idx, title FROM slide_titles"
                      " WHERE file_id=? ORDER BY idx", fid)
    assert got == [(1, "The Real Heading")]


def test_placeholder_patterns():
    from shelfmark.probe import PLACEHOLDER_TITLE_RE as R
    for placeholder in ("Slide 1", "slide 12", "Diapositiva 3", "Folie 7",
                        "Slide_4", "PowerPoint Presentation",
                        "Presentación de PowerPoint", "PowerPoint-Präsentation"):
        assert R.match(placeholder), placeholder
    for real in ("The Challenge", "Slide 1 of 12", "Q3 Slide 2", "Phase 2",
                 "PowerPoint Tips for Consultants"):
        assert not R.match(real), real


def test_uniformity_rule_needs_three_slides():
    """A two-slide deck can legitimately repeat one heading, and there is
    nothing to generalise from."""
    from shelfmark.probe import drop_placeholder_titles as drop
    assert drop(["Agenda", "Agenda"]) == ["Agenda", "Agenda"]
    assert drop(["Agenda"] * 3) == ["", "", ""]
    assert drop(["Intro", "Agenda", "Intro"]) == ["Intro", "Agenda", "Intro"]


def test_evicted_deck_keeps_the_titles_it_already_had(built, monkeypatch):
    """Only a run that actually opened the file may clear its titles --
    otherwise every deck that goes cloud-evicted silently loses them."""
    from shelfmark import catalog, refresh
    fid = one(built, "SELECT id FROM files WHERE filename='real_titles.pptx'")
    before = rows(built, "SELECT idx, title FROM slide_titles WHERE file_id=?", fid)
    assert before

    deck = built.primary_root.path / "Decks" / "real_titles.pptx"
    monkeypatch.setattr(catalog, "is_evicted",
                        lambda st: st.st_size == deck.stat().st_size)
    deck.write_bytes(deck.read_bytes() + b"\0")   # force a re-ingest
    monkeypatch.setattr(catalog, "is_evicted",
                        lambda st: str(st.st_size) == str(deck.stat().st_size))
    refresh.run(built, force=True)

    after = rows(built, "SELECT idx, title FROM slide_titles WHERE file_id=?", fid)
    assert after == before


# ---------------------------------------------------------------- robustness

def test_an_extension_that_lies_is_not_called_corrupt(built):
    """'Not a zip' is not the same as corrupt: text saved as .docx is common."""
    status = one(built, "SELECT status FROM files"
                        " WHERE filename='not_really_a_deck.docx'")
    assert status != "corrupt"


def test_a_truncated_ooxml_file_is_recorded_not_fatal(cfg, tmp_path):
    from shelfmark import refresh
    good = cfg.primary_root.path / "Decks" / "real_titles.pptx"
    broken = cfg.primary_root.path / "Decks" / "truncated.pptx"
    broken.write_bytes(good.read_bytes()[:40])

    assert refresh.run(cfg) == 0
    assert one(cfg, "SELECT COUNT(*) FROM files WHERE filename='truncated.pptx'") == 1


def test_hashing_finds_the_duplicate_pair(built):
    from shelfmark import hashes
    hashes.backfill(built)
    groups = rows(built, "SELECT sha256, COUNT(*) c FROM files"
                         " WHERE sha256 IS NOT NULL GROUP BY 1 HAVING c > 1")
    assert any(c == 2 for _, c in groups)
