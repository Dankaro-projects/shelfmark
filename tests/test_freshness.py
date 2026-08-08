"""Every tool says when it cannot vouch for its answer.

Until this existed only corpus_stats() reported freshness, so an agent that
never called it got answers from a frozen snapshot with nothing to indicate
that -- the failure the README names as worse than having no index at all.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def srv(built, monkeypatch):
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", built)
    return server


def set_status(cfg, **over):
    st = {"state": "ok", "detail": "clean",
          "finished_utc": "2026-08-06T00:00:00Z", "files": 1, "added": 0}
    st.update(over)
    cfg.status_path.write_text(json.dumps(st))


# ------------------------------------------------------------- when to speak

def test_a_healthy_index_says_nothing(srv):
    """A banner on every call is noise. Silence has to mean something."""
    assert srv.index_warning() is None
    out = srv.search_docs("status report", limit=3)
    assert "⚠" not in out


def test_a_never_refreshed_catalogue_says_so(srv, built):
    built.status_path.unlink()
    assert "never been refreshed" in srv.index_warning()


def test_a_failed_refresh_says_so(srv, built):
    set_status(built, state="failed", detail="prune REFUSED — 40 rows stale")
    warn = srv.index_warning()
    assert "FAILED" in warn
    assert "prune REFUSED" in warn


def test_a_stale_index_says_how_stale(srv, built):
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    set_status(built, finished_utc=old.strftime("%Y-%m-%dT%H:%M:%SZ"))
    warn = srv.index_warning()
    assert "last refreshed" in warn
    assert "h ago" in warn


def test_a_future_timestamp_is_reported_as_an_unusable_clock(srv, built):
    """Not 'very fresh'. A negative age passes the staleness check forever,
    which permanently disables the one signal that the refresher has stopped
    firing -- so the honest answer is that age cannot be judged."""
    from datetime import datetime, timedelta, timezone
    ahead = datetime.now(timezone.utc) + timedelta(hours=40)
    set_status(built, finished_utc=ahead.strftime("%Y-%m-%dT%H:%M:%SZ"))

    warn = srv.index_warning()
    assert "future" in warn
    assert "cannot be judged" in warn


def test_small_clock_differences_are_tolerated(srv, built):
    """A refresh writing on one machine and a server reading on another
    differ by seconds constantly. Flagging that would be noise."""
    from datetime import datetime, timedelta, timezone
    ahead = datetime.now(timezone.utc) + timedelta(seconds=90)
    set_status(built, finished_utc=ahead.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert srv.index_warning() is None


def test_an_unreadable_status_file_says_freshness_is_unknown(srv, built):
    built.status_path.write_text("{ this is not json")
    assert "unreadable" in srv.index_warning()


# -------------------------------------------------------- where it turns up

@pytest.mark.parametrize("call", [
    lambda s: s.search_docs("status report", limit=3),
    lambda s: s.search_docs("zzzznotacorpusword", limit=3),   # the empty result
    lambda s: s.browse_folder("Clients"),
    lambda s: s.get_file("Decks/real_titles.pptx"),
    lambda s: s.get_file("Clients/Nope/missing.pptx"),
])
def test_the_warning_reaches_every_tool(srv, built, call):
    set_status(built, state="failed", detail="something went wrong")
    assert call(srv).startswith("⚠")


def test_an_empty_result_is_where_it_matters_most(srv, built):
    """'No matches' reads as 'you do not have this' rather than 'I have not
    looked recently'."""
    set_status(built, state="failed", detail="walk saw 3/92 files")
    out = srv.search_docs("zzzznotacorpusword", limit=3)
    assert out.startswith("⚠")
    assert "No matches" in out


def test_the_warning_does_not_disturb_the_answer(srv, built):
    set_status(built, state="failed", detail="x")
    out = srv.search_docs("status report", limit=3)
    body = out.split("\n\n", 1)[1]
    assert body.startswith(("showing", "1 match", "2 match", "3 match"))


# ------------------------------------------- a file the index has not caught

def test_a_file_on_disk_but_not_indexed_is_not_reported_as_absent(srv, built):
    """'Not in catalogue' and 'not on your disk' are different answers, and
    only one of them is the caller's problem."""
    new = built.primary_root.path / "Clients" / "Alpha" / "brand_new.md"
    new.write_text("just written\n")

    out = srv.get_file("Clients/Alpha/brand_new.md")
    assert "IS on disk" in out
    assert "has not caught up" in out


def test_a_genuinely_absent_file_is_still_reported_as_absent(srv):
    out = srv.get_file("Clients/Alpha/never_existed.md")
    assert "Not in catalogue" in out
    assert "IS on disk" not in out


def test_the_on_disk_check_understands_extra_roots(srv, built):
    """Extra-root rows carry a label prefix; joining that onto the primary
    root points at a path that does not exist."""
    new = built.extra_roots["extra"] / "late_arrival.md"
    new.write_text("elsewhere\n")

    out = srv.get_file("extra/late_arrival.md")
    assert "IS on disk" in out


@pytest.mark.parametrize("platform,expect,reject", [
    ("darwin", "Full Disk Access", None),
    ("linux", "permissions", "Full Disk Access"),
    ("win32", "permissions", "Full Disk Access"),
])
def test_unreadable_root_guidance_matches_the_platform(srv, built,
                                                       monkeypatch, platform,
                                                       expect, reject):
    """'macOS: Full Disk Access' on a Linux box is advice written for
    somebody else — the remedy line follows sys.platform."""
    monkeypatch.setattr(srv, "disk_drift",
                        lambda con: {"state": "unreadable",
                                     "seen": 3, "rows": 92})
    monkeypatch.setattr(srv.sys, "platform", platform)
    out = srv.corpus_stats()
    assert "CANNOT VERIFY" in out
    assert expect in out
    if reject:
        assert reject not in out


# ----------------------------------------------------- the empty catalogue

def _solo_config(tmp_path, root):
    """A config whose only root is `root`, loaded for real."""
    from shelfmark import config as config_mod
    f = tmp_path / "solo.toml"
    f.write_text(f'[index]\ndb = "{tmp_path / "solo.db"}"\n\n'
                 f'[[roots]]\npath = "{root}"\n')
    return config_mod.load(f)


def test_an_empty_catalogue_is_never_a_green_tick(tmp_path, monkeypatch):
    """The shipped config can point at an empty ~/Documents; refresh
    succeeds over nothing and the agent's first call then gets a clean
    bill of health over zero files — the confident wrong answer the
    freshness machinery exists to prevent, arriving on day one."""
    from shelfmark import refresh, server
    hollow = tmp_path / "hollow"
    hollow.mkdir()
    c = _solo_config(tmp_path, hollow)
    refresh.run(c)
    monkeypatch.setattr(server, "_CFG", c)
    out = server.corpus_stats()
    assert "CATALOGUE EMPTY" in out
    assert "✓ index fresh" not in out
    assert str(hollow) in out                     # names the root


def test_a_missing_root_is_named_not_ticked(tmp_path, monkeypatch):
    from shelfmark import refresh, server
    gone = tmp_path / "never_created"
    c = _solo_config(tmp_path, gone)
    try:
        refresh.run(c)
    except Exception:                             # noqa: BLE001
        pass          # a refused refresh is fine; the tick is the failure
    monkeypatch.setattr(server, "_CFG", c)
    out = server.corpus_stats()
    assert "CATALOGUE EMPTY" in out
    assert "does not exist" in out
    assert str(gone) in out
    assert "✓ index fresh" not in out


def test_every_tool_warns_on_an_empty_catalogue(tmp_path, monkeypatch):
    """corpus_stats walking is not enough — search_docs answering 'No
    matches' with no banner still reads as 'you do not have this'."""
    from shelfmark import refresh, server
    hollow = tmp_path / "hollow"
    hollow.mkdir()
    c = _solo_config(tmp_path, hollow)
    refresh.run(c)
    monkeypatch.setattr(server, "_CFG", c)
    assert "EMPTY" in server.search_docs("anything", limit=3)
    assert "EMPTY" in server.get_file("nope.docx")


def test_files_on_disk_with_an_empty_db_say_behind_not_empty(srv, built):
    """An emptied catalogue with a full disk is a stale index, not an empty
    corpus — the two must not share a message."""
    import sqlite3 as s3
    con = s3.connect(built.db)
    con.execute("DELETE FROM files_fts")
    con.execute("DELETE FROM files")
    con.commit()
    con.close()
    out = srv.corpus_stats()
    assert "INDEX BEHIND DISK" in out
    assert "CATALOGUE EMPTY" not in out


def test_an_email_only_catalogue_is_not_flagged_empty(srv, built):
    """Zero files with ingested emails is a legitimate corpus shape."""
    import sqlite3 as s3
    con = s3.connect(built.db)
    con.execute("DELETE FROM files_fts")
    con.execute("DELETE FROM files")
    con.execute("CREATE TABLE IF NOT EXISTS emails (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO emails (id) VALUES (1)")
    con.commit()
    con.close()
    assert srv.index_warning() is None


def test_a_restricted_path_does_not_leak_through_the_on_disk_check(srv):
    """The on-disk branch must not become a way to confirm sealed files:
    a sealed path answers byte-identically to one that was never there."""
    absent = srv.get_file("Admin/zz_never_existed")
    out = srv.get_file("Admin/id_rsa")
    assert out == absent.replace("zz_never_existed", "id_rsa")
    assert "IS on disk" not in out
