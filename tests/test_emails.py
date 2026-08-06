"""Email ingestion — the parts that do not need a real archive.

A .pst is a proprietary binary and a .msg is an OLE compound file; neither
can be honestly synthesised here, and a fake one would test the fake. So the
extraction functions are driven with stand-in message objects shaped like
what pypff/extract_msg return, and everything downstream of extraction --
storage, identity, dedupe, governance, search -- is tested against a real
database.
"""

from __future__ import annotations

import sqlite3

import pytest

from shelfmark import emails


@pytest.fixture
def edb(tmp_path):
    con = sqlite3.connect(tmp_path / "e.db")
    con.executescript(emails.SCHEMA)
    yield con
    con.close()


def rec(**over):
    base = dict(source_path="archive/mail.pst", source_kind="pst",
                pst_folder="Inbox", era="archive", subject="Quarterly numbers",
                thread_topic=None, sender_name="A Sender",
                sender_email="a@example.com", sender_domain="example.com",
                recipients="b@example.org", recipient_domains="example.org",
                participant_count=2, sent_utc="2024-03-01T09:00:00+00:00",
                sent_year=2024, has_attachments=0, attachment_names=None,
                rights=emails.DEFAULT_RIGHTS)
    base.update(over)
    return base


# ------------------------------------------------------------- field parsing

@pytest.mark.parametrize("addr,expect", [
    ("a@example.com", "example.com"),
    ("A@Example.COM", "example.com"),
    ("noise <a@example.com>,", "example.com"),
    ("not an address", None),
    (None, None),
    ("", None),
])
def test_domain_of(addr, expect):
    assert emails.domain_of(emails.first_email(addr) or addr) == expect


def test_domains_in_is_sorted_and_deduplicated():
    hdr = "To: a@x.com, b@y.org, c@x.com"
    assert emails.domains_in(hdr) == "x.com,y.org"


def test_clean_strips_nulls_and_decodes_bytes():
    assert emails.clean(b"caf\xc3\xa9\x00") == "café"
    assert emails.clean("   ") is None
    assert emails.clean(None) is None


def test_iso_dt_assumes_utc_for_naive_timestamps():
    from datetime import datetime, timezone
    naive = datetime(2024, 3, 1, 9, 0, 0)
    aware = datetime(2024, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert emails.iso_dt(naive) == emails.iso_dt(aware)
    assert emails.iso_dt(None) == (None, None)
    assert emails.iso_dt("not a date") == (None, None)


# ----------------------------------------------------------------- sensitivity

def test_substantive_markers_flag_a_message(edb):
    emails.store(edb, rec(subject="your password reset"), None, False)
    assert edb.execute("SELECT sensitive FROM emails").fetchone()[0] == 1


def test_legal_footers_do_not_flag_every_message(edb):
    """'confidential' and 'privileged' sit in the footer of virtually every
    corporate email; flagging them makes the flag useless."""
    body = ("Numbers attached.\n\nThis email and any attachments are "
            "confidential and privileged. Do not forward.")
    emails.store(edb, rec(), body, True)
    assert edb.execute("SELECT sensitive FROM emails").fetchone()[0] == 0


def test_a_sensitive_body_flags_even_with_an_innocuous_subject(edb):
    emails.store(edb, rec(subject="notes"), "the api_key is ...", True)
    assert edb.execute("SELECT sensitive FROM emails").fetchone()[0] == 1


# --------------------------------------------------------------------- store

def test_bodies_are_only_stored_when_asked(edb):
    emails.store(edb, rec(), "the body text", want_body=False)
    assert edb.execute("SELECT COUNT(*) FROM email_bodies").fetchone()[0] == 0
    # ...but its length is still recorded, because identity depends on it.
    assert edb.execute("SELECT body_chars FROM emails").fetchone()[0] == len("the body text")


def test_reingesting_the_same_archive_is_a_no_op(edb):
    """The table-level UNIQUE is dead whenever a column is NULL, so this is
    really a test of the expression index."""
    assert emails.store(edb, rec(), None, False) is True
    assert emails.store(edb, rec(), None, False) is False
    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1


def test_idempotency_holds_when_columns_are_null(edb):
    """A .msg has no pst_folder, and NULL never compares equal -- the exact
    case where the table constraint silently lets duplicates through."""
    r = rec(source_kind="msg", pst_folder=None, subject=None, sent_utc=None)
    assert emails.store(edb, dict(r), None, False) is True
    assert emails.store(edb, dict(r), None, False) is False
    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1


def test_every_stored_message_is_searchable(edb):
    emails.store(edb, rec(), None, False)
    n = edb.execute("SELECT COUNT(*) FROM emails_fts").fetchone()[0]
    assert n == edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0]


# -------------------------------------------------------------------- dedupe

def test_the_same_message_from_two_archives_is_one_result(edb):
    """Two archive files can hold the same mailbox; without this a two-row
    search spends both slots on the same message."""
    emails.store(edb, rec(source_path="a.pst"), "body", True)
    emails.store(edb, rec(source_path="b.pst"), "body", True)
    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 2
    assert len(emails.dupe_losers(edb)) == 1


def test_the_first_archive_ingested_wins(edb):
    emails.store(edb, rec(source_path="a.pst"), None, False)
    emails.store(edb, rec(source_path="b.pst"), None, False)
    survivor = min(r[0] for r in edb.execute("SELECT id FROM emails"))
    assert survivor not in emails.dupe_losers(edb)


def test_different_messages_are_not_collapsed(edb):
    emails.store(edb, rec(subject="one"), None, False)
    emails.store(edb, rec(subject="two"), None, False)
    assert emails.dupe_losers(edb) == []


def test_headerless_messages_are_never_called_duplicates(edb):
    """Real archives hold entries with no sender, subject or timestamp --
    calendar items, drafts, unreadable rows. They all COALESCE to the same
    identity key, so treating that as content-identity deletes distinct
    messages and reports success. A row we cannot identify is not a
    duplicate: absence of evidence is not identity."""
    for i, src in enumerate(["a.pst", "b.pst", "c.pst"]):
        emails.store(edb, rec(source_path=src, pst_folder=f"Inbox/{i}",
                              subject=None, sender_email=None,
                              sender_domain=None, sent_utc=None,
                              sent_year=None), None, False)
    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 3
    assert emails.dupe_losers(edb) == []


def test_identifiable_duplicates_are_still_caught_alongside_headerless_rows(edb):
    """The guard above must not become a way for real duplicates to hide."""
    emails.store(edb, rec(source_path="a.pst", subject=None, sender_email=None,
                          sent_utc=None), None, False)
    emails.store(edb, rec(source_path="a.pst"), None, False)
    emails.store(edb, rec(source_path="b.pst"), None, False)
    assert len(emails.dupe_losers(edb)) == 1


def test_dedupe_handles_more_rows_than_sqlite_allows_parameters(cfg):
    """SQLite caps host parameters at 32,766. A mailbox ingested from several
    archives passes that easily -- so an IN (?,?,?…) list fails on exactly
    the corpora that need deduping most.

    Drives dedupe() itself, not just dupe_losers(): the parameter list lived
    in the delete path, so a test that stops short of it proves nothing."""
    from shelfmark import refresh
    refresh.run(cfg)

    con = sqlite3.connect(cfg.db)
    con.executescript(emails.SCHEMA)
    limit = con.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    n = limit + 50
    con.executemany(
        "INSERT INTO emails (source_path, source_kind, subject, sender_email,"
        " sent_utc, body_chars) VALUES (?, 'pst', 'shared subject',"
        " 'a@example.com', '2024-03-01T09:00:00+00:00', 10)",
        [(f"archive_{i}.pst",) for i in range(n)])
    con.commit()
    con.close()

    out = emails.dedupe(cfg, apply=True)
    assert "must be 0" in out

    con = sqlite3.connect(cfg.db)
    try:
        assert con.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1
        assert emails.dupe_losers(con) == []
    finally:
        con.close()


def test_dedupe_applies_and_leaves_the_indexes_consistent(edb, tmp_path, cfg):
    emails.store(edb, rec(source_path="a.pst"), "body", True)
    emails.store(edb, rec(source_path="b.pst"), "body", True)
    edb.commit()
    edb.close()

    cfg.db.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(tmp_path / "e.db", cfg.db)

    out = emails.dedupe(cfg, apply=True)
    assert "must be 0" in out

    con = sqlite3.connect(cfg.db)
    try:
        assert con.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM emails_fts").fetchone()[0] == 1
        orphans = con.execute(
            "SELECT COUNT(*) FROM email_bodies WHERE email_id NOT IN"
            " (SELECT id FROM emails)").fetchone()[0]
        assert orphans == 0
        assert emails.dupe_losers(con) == []
    finally:
        con.close()


def test_dedupe_is_a_dry_run_by_default(edb, tmp_path, cfg):
    emails.store(edb, rec(source_path="a.pst"), None, False)
    emails.store(edb, rec(source_path="b.pst"), None, False)
    edb.commit()
    edb.close()
    import shutil
    cfg.db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp_path / "e.db", cfg.db)

    out = emails.dedupe(cfg)
    assert "Dry run" in out
    con = sqlite3.connect(cfg.db)
    try:
        assert con.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 2
    finally:
        con.close()


def test_dedupe_without_an_email_corpus_explains_itself(cfg):
    from shelfmark import refresh
    refresh.run(cfg)
    assert "No email corpus" in emails.dedupe(cfg)


# ---------------------------------------------------------------- governance

def test_sensitive_messages_are_never_returned(cfg, edb, tmp_path, monkeypatch):
    emails.store(edb, rec(subject="password reset link"), None, False)
    emails.store(edb, rec(subject="Quarterly numbers"), None, False)
    edb.commit()
    edb.close()

    from shelfmark import refresh
    refresh.run(cfg)
    con = sqlite3.connect(cfg.db)
    con.executescript(emails.SCHEMA)
    src = sqlite3.connect(tmp_path / "e.db")
    for row in src.execute("SELECT * FROM emails"):
        con.execute(f"INSERT INTO emails VALUES ({','.join('?' * len(row))})", row)
    for r in con.execute("SELECT id, subject FROM emails").fetchall():
        con.execute("INSERT INTO emails_fts(rowid, subject, sender_email,"
                    " recipients, attachment_names, body)"
                    " VALUES (?,?,'','','','')", r)
    con.commit()
    con.close()

    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", cfg)
    out = server.search_emails("password", limit=10)
    assert "password reset link" not in out

    assert "Quarterly" in server.search_emails("Quarterly", limit=10)


# ------------------------------------------------------------------- ingest

def test_ingest_reports_a_missing_folder(cfg, capsys):
    pytest.importorskip("extract_msg")
    pytest.importorskip("pypff")
    assert emails.ingest(cfg, prefix="no/such/folder") == 2
    assert "no such folder" in capsys.readouterr().err.lower()


@pytest.fixture
def no_email_deps(monkeypatch):
    """Neither reader importable — the normal state of a fresh install."""
    import builtins
    real_import = builtins.__import__

    def missing(name, *a, **kw):
        if name in ("extract_msg", "pypff"):
            raise ImportError(f"No module named {name!r}", name=name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", missing)


def test_ingest_without_the_optional_deps_says_how_to_install(cfg, capsys,
                                                              no_email_deps):
    mail = cfg.primary_root.path / "Mail"
    mail.mkdir(parents=True, exist_ok=True)
    (mail / "a.msg").write_bytes(b"not really a msg")

    assert emails.ingest(cfg, prefix="Mail") == 2
    assert "shelfmark[email]" in capsys.readouterr().err


def test_a_msg_only_folder_does_not_need_the_pst_reader(cfg, capsys, monkeypatch):
    """libpff-python is a C extension that routinely fails to build. Demanding
    it before reading a folder of .msg files refuses the common case."""
    import builtins
    real_import = builtins.__import__

    def only_pst_missing(name, *a, **kw):
        if name == "pypff":
            raise ImportError("No module named 'pypff'", name="pypff")
        return real_import(name, *a, **kw)

    pytest.importorskip("extract_msg")
    monkeypatch.setattr(builtins, "__import__", only_pst_missing)

    mail = cfg.primary_root.path / "Mail"
    mail.mkdir(parents=True, exist_ok=True)
    (mail / "a.msg").write_bytes(b"not really a msg")

    # Reaches ingestion (the file itself is unreadable, which is an error the
    # run records rather than a refusal to start).
    assert emails.ingest(cfg, prefix="Mail") == 0


def test_an_empty_folder_is_not_an_error(cfg, capsys, no_email_deps):
    empty = cfg.primary_root.path / "NoMail"
    empty.mkdir(parents=True, exist_ok=True)
    assert emails.ingest(cfg, prefix="NoMail") == 0
    assert "nothing to ingest" in capsys.readouterr().err.lower()
