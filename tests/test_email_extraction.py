"""Mapping a parsed message onto the schema.

pypff and extract_msg are third-party readers, tested by their authors, and
their inputs (.pst, .msg) are proprietary binaries that cannot be honestly
synthesised. What is ours -- and what was entirely uncovered -- is the
translation from the objects they hand back into catalogue rows. These
stand-ins have exactly the attribute surface the extractors touch, so a
change to what we read from a message shows up here.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from datetime import datetime, timezone

import pytest

from shelfmark import emails


class FakeAttachment:
    def __init__(self, name):
        self._name = name

    def getFilename(self):        # extract_msg
        return self._name

    def get_name(self):           # pypff
        return self._name


class FakeMsg:
    """Shaped like extract_msg.openMsg()'s return value."""

    def __init__(self, **kw):
        self.subject = kw.get("subject", "Quarterly numbers")
        self.sender = kw.get("sender", "A Sender <a@example.com>")
        self.to = kw.get("to", "b@example.org")
        self.cc = kw.get("cc", "")
        self.date = kw.get("date", datetime(2024, 3, 1, 9, 0,
                                            tzinfo=timezone.utc))
        self.body = kw.get("body", "the body")
        self.conversationTopic = kw.get("topic", "Numbers")
        self.attachments = [FakeAttachment(n) for n in kw.get("attachments", [])]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakePstMessage:
    def __init__(self, **kw):
        self.subject = kw.get("subject", "Quarterly numbers")
        self.sender_name = kw.get("sender_name", "A Sender")
        self.conversation_topic = kw.get("topic", "Numbers")
        self.delivery_time = kw.get("date", datetime(2024, 3, 1, 9, 0,
                                                     tzinfo=timezone.utc))
        self.transport_headers = kw.get("headers", (
            "From: A Sender <a@example.com>\n"
            "To: b@example.org, c@example.net\n"
            "Subject: Quarterly numbers\n"))
        self.plain_text_body = kw.get("body", "the body")
        self._atts = [FakeAttachment(n) for n in kw.get("attachments", [])]

    @property
    def number_of_attachments(self):
        return len(self._atts)

    def get_attachment(self, i):
        return self._atts[i]


class FakePstFolder:
    def __init__(self, name, messages=(), subfolders=()):
        self.name = name
        self._messages = list(messages)
        self._subfolders = list(subfolders)

    @property
    def number_of_sub_messages(self):
        return len(self._messages)

    def get_sub_message(self, i):
        return self._messages[i]

    @property
    def number_of_sub_folders(self):
        return len(self._subfolders)

    def get_sub_folder(self, i):
        return self._subfolders[i]


@pytest.fixture
def edb(tmp_path):
    con = sqlite3.connect(tmp_path / "e.db")
    con.executescript(emails.SCHEMA)
    yield con
    con.close()


@pytest.fixture
def stats():
    return {"msg": 0, "pst": 0, "evicted": 0, "errors": []}


# --------------------------------------------------------------------- .msg

@pytest.fixture
def fake_extract_msg(monkeypatch):
    """Stand in for the reader so the mapping can be driven without a binary."""
    mod = types.ModuleType("extract_msg")
    mod.opened = []

    def openMsg(path):
        return mod.next_msg

    mod.openMsg = openMsg
    mod.next_msg = FakeMsg()
    monkeypatch.setitem(sys.modules, "extract_msg", mod)
    return mod


def test_msg_fields_map_onto_the_schema(cfg, edb, stats, fake_extract_msg):
    fake_extract_msg.next_msg = FakeMsg(attachments=["deck.pptx", "sheet.xlsx"])
    p = cfg.primary_root.path / "Mail" / "one.msg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"stub")

    emails._ingest_msg(edb, cfg, [p], "archive", True, stats)

    assert stats["msg"] == 1
    r = edb.execute("SELECT * FROM emails").fetchone()
    cols = [c[0] for c in edb.execute("SELECT * FROM emails").description]
    row = dict(zip(cols, r))
    assert row["source_kind"] == "msg"
    assert row["source_path"] == "Mail/one.msg"
    assert row["pst_folder"] is None
    assert row["subject"] == "Quarterly numbers"
    assert row["sender_email"] == "a@example.com"
    assert row["sender_domain"] == "example.com"
    assert row["recipient_domains"] == "example.org"
    assert row["sent_year"] == 2024
    assert row["has_attachments"] == 1
    assert row["attachment_names"] == "deck.pptx; sheet.xlsx"
    assert row["rights"] == emails.DEFAULT_RIGHTS


def test_msg_cc_recipients_are_counted(cfg, edb, stats, fake_extract_msg):
    fake_extract_msg.next_msg = FakeMsg(to="b@example.org", cc="c@example.net")
    p = cfg.primary_root.path / "Mail" / "two.msg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"stub")

    emails._ingest_msg(edb, cfg, [p], "archive", False, stats)

    row = edb.execute("SELECT recipient_domains, participant_count"
                      " FROM emails").fetchone()
    assert row[0] == "example.net,example.org"
    assert row[1] == 3          # two recipients plus the sender


def test_an_unreadable_msg_is_recorded_not_fatal(cfg, edb, stats, monkeypatch):
    mod = types.ModuleType("extract_msg")

    def boom(path):
        raise ValueError("not an OLE file")

    mod.openMsg = boom
    monkeypatch.setitem(sys.modules, "extract_msg", mod)

    good = cfg.primary_root.path / "Mail" / "bad.msg"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"stub")

    emails._ingest_msg(edb, cfg, [good], "archive", False, stats)

    assert stats["msg"] == 0
    assert len(stats["errors"]) == 1
    assert "bad.msg" in stats["errors"][0]


# --------------------------------------------------------------------- .pst

def test_pst_fields_map_onto_the_schema(edb, stats):
    folder = FakePstFolder("Inbox", messages=[
        FakePstMessage(attachments=["report.pdf"])])

    emails._walk_pst_folder(folder, edb, "archive/mail.pst", "archive",
                            True, stats)

    assert stats["pst"] == 1
    cols = [c[0] for c in edb.execute("SELECT * FROM emails").description]
    row = dict(zip(cols, edb.execute("SELECT * FROM emails").fetchone()))
    assert row["source_kind"] == "pst"
    assert row["pst_folder"] == "Inbox"
    assert row["sender_email"] == "a@example.com"
    assert row["recipients"] == "b@example.org, c@example.net"
    assert row["recipient_domains"] == "example.net,example.org"
    assert row["attachment_names"] == "report.pdf"
    assert row["body_chars"] == len("the body")


def test_pst_subfolders_are_walked_and_named_by_their_path(edb, stats):
    tree = FakePstFolder("Root", subfolders=[
        FakePstFolder("Inbox", messages=[FakePstMessage(subject="a")]),
        FakePstFolder("Projects", subfolders=[
            FakePstFolder("Alpha", messages=[FakePstMessage(subject="b")]),
        ]),
    ])

    emails._walk_pst_folder(tree, edb, "archive/mail.pst", "archive",
                            False, stats)

    folders = sorted(f for (f,) in edb.execute(
        "SELECT pst_folder FROM emails"))
    assert folders == ["Root/Inbox", "Root/Projects/Alpha"]
    assert stats["pst"] == 2


def test_one_broken_message_does_not_abandon_the_folder(edb, stats):
    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("corrupt entry")

    folder = FakePstFolder("Inbox", messages=[
        FakePstMessage(subject="before"), Exploding(),
        FakePstMessage(subject="after")])

    emails._walk_pst_folder(folder, edb, "archive/mail.pst", "archive",
                            False, stats)

    subjects = sorted(s for (s,) in edb.execute("SELECT subject FROM emails"))
    assert subjects == ["after", "before"]
    assert len(stats["errors"]) == 1


def test_header_less_messages_are_all_kept(edb, stats):
    """Header-less entries are normal in a real .pst -- calendar items,
    drafts, unreadable rows. Keyed on headers alone they are indistinguishable
    from one another, so the second and every one after it were silently
    dropped as re-ingests of the first: mail loss, reported as success."""
    folder = FakePstFolder("Inbox", messages=[
        FakePstMessage(subject=None, headers="", sender_name=None, date=None)
        for _ in range(3)
    ])

    emails._walk_pst_folder(folder, edb, "archive/mail.pst", "archive",
                            False, stats)

    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 3
    assert stats["pst"] == 3
    assert emails.dupe_losers(edb) == []


def test_reingesting_the_same_pst_is_still_a_no_op(edb, stats):
    """The position that makes those messages distinct has to be stable, or
    idempotency is traded away to fix mail loss."""
    def folder():
        return FakePstFolder("Inbox", messages=[
            FakePstMessage(subject=None, headers="", sender_name=None,
                           date=None) for _ in range(3)
        ] + [FakePstMessage(subject="real one")])

    emails._walk_pst_folder(folder(), edb, "archive/mail.pst", "archive",
                            False, stats)
    first = edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0]

    emails._walk_pst_folder(folder(), edb, "archive/mail.pst", "archive",
                            False, stats)
    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == first


def test_the_same_folder_name_in_two_archives_stays_separate(edb, stats):
    for src in ("archive/one.pst", "archive/two.pst"):
        emails._walk_pst_folder(
            FakePstFolder("Inbox", messages=[
                FakePstMessage(subject=None, headers="", sender_name=None,
                               date=None)]),
            edb, src, "archive", False, stats)
    assert edb.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 2


def test_recipient_domains_exclude_the_sender(edb, stats):
    """recipient_domains is derived from the recipients, not the whole header
    -- otherwise it also carried From:, and the column meant one thing for
    .pst rows and another for .msg rows."""
    emails._walk_pst_folder(
        FakePstFolder("Inbox", messages=[FakePstMessage()]),
        edb, "archive/mail.pst", "archive", False, stats)

    doms = edb.execute("SELECT recipient_domains FROM emails").fetchone()[0]
    assert doms == "example.net,example.org"
    assert "example.com" not in doms          # the sender


def test_cc_recipients_are_captured_for_pst(edb, stats):
    hdr = ("From: A Sender <a@example.com>\n"
           "To: b@example.org\n"
           "Cc: c@example.net\n"
           "Subject: x\n")
    emails._walk_pst_folder(
        FakePstFolder("Inbox", messages=[FakePstMessage(headers=hdr)]),
        edb, "archive/mail.pst", "archive", False, stats)

    row = edb.execute("SELECT recipients, recipient_domains FROM emails").fetchone()
    assert "c@example.net" in row[0]
    assert row[1] == "example.net,example.org"


def test_an_older_catalogue_is_migrated_not_left_behind(tmp_path):
    """CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so without
    an explicit migration the schema silently stays at the old shape."""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    old_schema = emails.SCHEMA.replace(
        "    source_ordinal    INTEGER,              -- position within its .pst folder\n", ""
    ).replace(",\n    COALESCE(source_ordinal, -1)\n", "\n")
    con.executescript(old_schema)
    con.execute("INSERT INTO emails (source_path, source_kind, subject)"
                " VALUES ('a.pst', 'pst', 'kept')")
    con.commit()

    emails.ensure_schema(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info(emails)")}
    assert "source_ordinal" in cols
    assert con.execute("SELECT subject FROM emails").fetchone()[0] == "kept"
    # and the rebuilt index actually distinguishes by position
    for i in (0, 1):
        con.execute("INSERT OR IGNORE INTO emails (source_path, source_kind,"
                    " source_ordinal) VALUES ('b.pst', 'pst', ?)", (i,))
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM emails"
                       " WHERE source_path='b.pst'").fetchone()[0] == 2
    con.close()


def test_pst_folders_without_a_name_do_not_break_the_path(edb, stats):
    folder = FakePstFolder(None, messages=[FakePstMessage()])
    emails._walk_pst_folder(folder, edb, "archive/mail.pst", "archive",
                            False, stats)
    assert edb.execute("SELECT pst_folder FROM emails").fetchone()[0] == "(unnamed)"
