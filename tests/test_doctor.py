"""doctor must find the problems that otherwise fail silently -- and must
not invent any.

The headline check is the catalogue sitting inside a sync folder. The
config comments already call that mandatory, and nothing enforced it: the
database is a mutating SQLite file with -wal and -shm sidecars, so a sync
client both thrashes on it and can restore the three files from different
moments, producing a torn database that opens and answers wrongly.

Sync folders are synthesised under the sandbox HOME that conftest already
points at tmp_path, so these run identically on all three platforms rather
than depending on whatever the machine happens to have installed.
"""

from __future__ import annotations

import io
import os
import sqlite3
import textwrap

import pytest

from shelfmark import config as config_mod
from shelfmark import doctor
from conftest import toml_str


@pytest.fixture(autouse=True)
def _no_real_sync_clients(monkeypatch):
    """conftest sandboxes HOME, but OneDrive publishes its location through
    the environment, which is not part of HOME. Without this, these tests
    pass or fail according to whether the machine running them happens to
    have OneDrive installed -- and the developer machine and CI disagree."""
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(env, raising=False)


def report(cfg) -> tuple[int, str]:
    buf = io.StringIO()
    rc = doctor.run(cfg, out=buf)
    return rc, buf.getvalue()


def write_config(tmp_path, corpus, db) -> object:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(textwrap.dedent(f"""
        [index]
        db = {toml_str(db)}

        [[roots]]
        path = {toml_str(corpus)}
    """).strip() + "\n")
    return config_mod.load(cfg_file)


# --------------------------------------------------------- the sync check

@pytest.mark.parametrize("folder", ["Dropbox", "OneDrive", "Nextcloud"])
def test_a_catalogue_inside_a_sync_folder_is_a_failure(tmp_path, corpus,
                                                       folder):
    home = tmp_path / "home"
    synced = home / folder
    synced.mkdir(parents=True, exist_ok=True)
    cfg = write_config(tmp_path, corpus, synced / "catalog.db")

    rc, out = report(cfg)
    assert rc == 1, "a torn catalogue is not a warning"
    assert "FAIL" in out
    assert folder in out
    # The evidence is shown, so an operator can overrule a wrong guess.
    assert str(synced) in out
    # And the fix is named, not just the fault.
    assert "[index] db" in out


def test_a_catalogue_outside_any_sync_folder_passes(tmp_path, corpus):
    home = tmp_path / "home"
    (home / "Dropbox").mkdir(parents=True, exist_ok=True)
    cfg = write_config(tmp_path, corpus, tmp_path / "state" / "catalog.db")

    rc, out = report(cfg)
    assert "outside any sync folder" in out
    assert "FAIL" not in out


def test_a_sync_folder_that_does_not_exist_is_not_invented(tmp_path, corpus):
    """sync_roots() only reports folders that are actually there. A machine
    with no sync client must produce no finding at all -- a diagnostic that
    cries wolf is one people learn to skip."""
    cfg = write_config(tmp_path, corpus, tmp_path / "state" / "catalog.db")
    assert not doctor.sync_roots()
    _, out = report(cfg)
    assert "Dropbox" not in out


def test_a_symlink_into_a_sync_folder_is_still_caught(tmp_path, corpus):
    """Resolved on both sides -- an unresolved comparison is exactly how a
    database ends up somewhere its owner did not put it."""
    from conftest import symlink_or_skip
    home = tmp_path / "home"
    synced = home / "Dropbox"
    synced.mkdir(parents=True, exist_ok=True)
    link = tmp_path / "state"
    link.parent.mkdir(parents=True, exist_ok=True)
    symlink_or_skip(link, synced)

    cfg = write_config(tmp_path, corpus, link / "catalog.db")
    rc, out = report(cfg)
    assert rc == 1
    assert "Dropbox" in out


@pytest.mark.skipif(os.name != "nt", reason="the env var is Windows-only")
def test_onedrive_is_found_through_its_environment_variable(tmp_path,
                                                            monkeypatch):
    """Windows publishes OneDrive's real location, tenancy name included --
    "OneDrive - Contoso" is not something a literal list can hold."""
    elsewhere = tmp_path / "Some Tenancy Folder"
    elsewhere.mkdir()
    monkeypatch.setenv("OneDrive", str(elsewhere))
    assert doctor.in_sync_folder(elsewhere / "catalog.db")


# ------------------------------------------------------------ other checks

def test_a_catalogue_inside_an_indexed_root_never_reaches_doctor(tmp_path,
                                                                 corpus):
    """That one is refused by config.load, so doctor cannot run to report
    it. Pinned here so nobody adds a second check for it downstream: a
    guard placed after the one that already fired can never be reached, and
    its test passes for the wrong reason."""
    with pytest.raises(config_mod.ConfigError, match="INSIDE"):
        write_config(tmp_path, corpus, corpus / "catalog.db")


def test_a_missing_root_is_a_failure(tmp_path):
    missing = tmp_path / "not_here"
    cfg = write_config(tmp_path, missing, tmp_path / "state" / "catalog.db")
    rc, out = report(cfg)
    assert rc == 1
    assert "does not exist" in out


def test_an_unreadable_root_names_the_platform_fix(tmp_path, corpus,
                                                   monkeypatch):
    """macOS denies Documents to a process without Full Disk Access. The
    operator cannot act on "cannot be read" alone."""
    real = os.scandir

    def denied(path=".", *a, **kw):
        if str(path) == str(corpus):
            raise PermissionError(13, "Operation not permitted")
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", denied)
    cfg = write_config(tmp_path, corpus, tmp_path / "state" / "catalog.db")
    rc, out = report(cfg)
    assert rc == 1
    assert "cannot be read" in out
    assert "Full Disk Access" in out


def test_no_catalogue_yet_is_a_warning_not_a_failure(tmp_path, corpus):
    cfg = write_config(tmp_path, corpus, tmp_path / "state" / "catalog.db")
    rc, out = report(cfg)
    assert rc == 0
    assert "no catalogue yet" in out
    assert "shelfmark refresh" in out


def test_an_evicted_corpus_is_explained(tmp_path, corpus, built):
    con = sqlite3.connect(built.db)
    try:
        con.execute("UPDATE files SET evicted = 1")
        con.commit()
    finally:
        con.close()
    _, out = report(built)
    assert "cloud-evicted" in out
    assert "never" in out and "opened" in out


@pytest.fixture
def no_msg_reader(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def missing(name, *a, **kw):
        if name == "extract_msg":
            raise ImportError("no", name=name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", missing)


def test_no_reader_is_suggested_for_a_format_the_corpus_lacks(built,
                                                              no_msg_reader):
    """An install instruction for a format the operator does not have is
    noise that trains them to skim the rest."""
    _, out = report(built)
    assert "shelfmark[msg]" not in out


def test_a_missing_reader_is_reported_when_the_corpus_has_that_format(
        cfg, corpus, no_msg_reader):
    from shelfmark import refresh
    (corpus / "Mail").mkdir(exist_ok=True)
    (corpus / "Mail" / "a.msg").write_bytes(b"x")
    assert refresh.run(cfg) == 0

    _, out = report(cfg)
    assert "shelfmark[msg]" in out
    assert ".msg files are catalogued" in out


def test_the_reader_check_is_silent_before_the_first_refresh(tmp_path, corpus,
                                                             no_msg_reader):
    """Counted from the catalogue, so there is nothing to say until one
    exists -- and the missing catalogue is already its own finding."""
    (corpus / "Mail").mkdir(exist_ok=True)
    (corpus / "Mail" / "a.msg").write_bytes(b"x")
    cfg = write_config(tmp_path, corpus, tmp_path / "state" / "catalog.db")
    _, out = report(cfg)
    assert "shelfmark[msg]" not in out
    assert "no catalogue yet" in out


def test_a_healthy_setup_exits_zero_and_says_so(built):
    rc, out = report(built)
    assert rc == 0
    assert "nothing to fix" in out or "need attention" in out
    assert "FAIL" not in out
