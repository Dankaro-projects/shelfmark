"""The filesystem trust boundary.

The configured roots ARE the boundary. Two ways it leaked, both found by an
independent review of 0.1.0 and both fixed here: a symlink walked straight
out of a root, and a catalogue placed inside a root indexed itself.
"""

from __future__ import annotations

import os
import sqlite3
import textwrap

import pytest

from shelfmark import config as cm
from shelfmark.config import ConfigError
from conftest import one, rows, symlink_or_skip, toml_str


def write_config(path, db, roots):
    body = f'[index]\ndb = {toml_str(db)}\n'
    for label, p in roots:
        body += "\n[[roots]]\n"
        if label:
            body += f'label = "{label}"\n'
        body += f'path = {toml_str(p)}\n'
    path.write_text(body)
    return path


# --------------------------------------------------------------- symlinks

@pytest.fixture
def linked(tmp_path):
    """A root containing symlinks that point outside it."""
    corpus, outside = tmp_path / "corpus", tmp_path / "outside"
    (corpus / "sub").mkdir(parents=True)
    outside.mkdir()
    (corpus / "real.md").write_text("in the root\n")
    (outside / "secret.md").write_text("outside the root, and private\n")
    (outside / "deep").mkdir()
    (outside / "deep" / "buried.md").write_text("deeper still\n")
    symlink_or_skip(corpus / "link_file.md", outside / "secret.md")
    symlink_or_skip(corpus / "link_dir", outside / "deep")
    symlink_or_skip(corpus / "sub" / "nested_link.md", outside / "secret.md")
    cfg = cm.load(write_config(tmp_path / "c.toml", tmp_path / "cat.db",
                               [("", corpus)]))
    return cfg, outside


def test_a_symlinked_file_is_not_indexed(linked):
    """A link to /etc/passwd inside a root reads as an ordinary file."""
    cfg, _ = linked
    from shelfmark import refresh
    assert refresh.run(cfg) == 0

    paths = [p for (p,) in rows(cfg, "SELECT path FROM files")]
    assert paths == ["real.md"]


def test_a_symlinked_directory_is_not_descended(linked):
    cfg, _ = linked
    from shelfmark import refresh
    refresh.run(cfg)
    assert one(cfg, "SELECT COUNT(*) FROM files WHERE path LIKE 'link_dir%'") == 0


def test_content_outside_the_root_is_never_read(linked):
    """The sharper form: `hash` opens files, so a followed symlink does not
    merely list a foreign path, it reads its bytes."""
    cfg, outside = linked
    from shelfmark import hashes, refresh
    refresh.run(cfg)
    hashes.backfill(cfg)

    import hashlib
    foreign = hashlib.sha256((outside / "secret.md").read_bytes()).hexdigest()
    stored = {h for (h,) in rows(cfg, "SELECT sha256 FROM files"
                                      " WHERE sha256 IS NOT NULL")}
    assert foreign not in stored


def test_the_skip_is_reported_not_silent(linked, capsys):
    cfg, _ = linked
    from shelfmark import refresh
    capsys.readouterr()
    refresh.run(cfg)
    assert "symlink" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows construct")
def test_a_junction_directory_is_not_descended(tmp_path):
    """The Windows twin of the symlinked-directory test. A junction is not a
    symlink — os.path.islink says False, so walk's followlinks refusal never
    fires — but it leaves the root exactly the same way. Unlike symlinks,
    creating one needs no privilege, which makes it the escape an ordinary
    Windows account can actually plant."""
    import _winapi
    from shelfmark import refresh
    corpus, outside = tmp_path / "corpus", tmp_path / "outside"
    corpus.mkdir(); outside.mkdir()
    (corpus / "real.md").write_text("in the root\n")
    (outside / "secret.md").write_text("outside the root\n")
    _winapi.CreateJunction(str(outside), str(corpus / "junc"))
    cfg = cm.load(write_config(tmp_path / "c.toml", tmp_path / "cat.db",
                               [("", corpus)]))
    assert refresh.run(cfg) == 0
    paths = [p for (p,) in rows(cfg, "SELECT path FROM files")]
    assert paths == ["real.md"]


def test_a_tree_you_mean_to_index_is_an_extra_root(tmp_path):
    """The boundary widens by saying so in config, not by planting a link."""
    from shelfmark import refresh
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "one.md").write_text("x\n")
    (b / "two.md").write_text("y\n")
    cfg = cm.load(write_config(tmp_path / "c.toml", tmp_path / "cat.db",
                               [("", a), ("b", b)]))
    refresh.run(cfg)
    assert sorted(p for (p,) in rows(cfg, "SELECT path FROM files")) == \
        ["b/two.md", "one.md"]


# ------------------------------------------------------- catalogue location

def test_a_catalogue_inside_the_primary_root_is_refused(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    with pytest.raises(ConfigError, match="INSIDE"):
        cm.load(write_config(tmp_path / "c.toml", corpus / "cat.db",
                             [("", corpus)]))


def test_a_catalogue_inside_an_EXTRA_root_is_refused(tmp_path):
    """The check used to look at the primary root only. A catalogue in an
    extra root indexed itself -- db, -wal, -shm, status, log and lock -- and
    the corpus grew on every refresh."""
    corpus, extra = tmp_path / "corpus", tmp_path / "extra"
    corpus.mkdir(); extra.mkdir()
    with pytest.raises(ConfigError, match="INSIDE"):
        cm.load(write_config(tmp_path / "c.toml", extra / "cat.db",
                             [("", corpus), ("extra", extra)]))


def test_the_refusal_names_the_offending_root(tmp_path):
    corpus, extra = tmp_path / "corpus", tmp_path / "extra"
    corpus.mkdir(); extra.mkdir()
    with pytest.raises(ConfigError) as exc:
        cm.load(write_config(tmp_path / "c.toml", extra / "cat.db",
                             [("", corpus), ("extra", extra)]))
    assert "extra" in str(exc.value)


def test_a_dotdot_path_cannot_smuggle_the_catalogue_in(tmp_path):
    """Unresolved comparison was defeated by `..`."""
    corpus = tmp_path / "corpus"; (corpus / "sub").mkdir(parents=True)
    sneaky = corpus / "sub" / ".." / "cat.db"
    with pytest.raises(ConfigError, match="INSIDE"):
        cm.load(write_config(tmp_path / "c.toml", sneaky, [("", corpus)]))


def test_a_symlinked_root_cannot_smuggle_the_catalogue_in(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "link"; symlink_or_skip(link, real)
    with pytest.raises(ConfigError, match="INSIDE"):
        cm.load(write_config(tmp_path / "c.toml", real / "cat.db",
                             [("", link)]))


def test_a_catalogue_outside_every_root_is_accepted(tmp_path):
    corpus, extra = tmp_path / "corpus", tmp_path / "extra"
    corpus.mkdir(); extra.mkdir()
    cfg = cm.load(write_config(tmp_path / "c.toml", tmp_path / "cat.db",
                               [("", corpus), ("extra", extra)]))
    assert cfg.db == tmp_path / "cat.db"


def test_the_walk_never_yields_the_catalogues_own_files(tmp_path, monkeypatch):
    """Defence in depth: config refuses this, but if a root is ever added
    after the fact the walk must still not catalogue the database."""
    from shelfmark import catalog
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "real.md").write_text("x\n")
    cfg = cm.load(write_config(tmp_path / "c.toml", tmp_path / "cat.db",
                               [("", corpus)]))
    # move the catalogue inside the root behind the validator's back
    object.__setattr__(cfg, "db", corpus / "cat.db")
    for name in ("cat.db", "cat.db-wal", "cat.db-shm", "REFRESH_STATUS.json",
                 ".dirty"):
        (corpus / name).write_text("x")

    keys = [rel for _, rel in catalog.walk(cfg)]
    assert keys == ["real.md"]


# ------------------------------------------------------- what MCP discloses

@pytest.fixture
def vault(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "Vault").mkdir(parents=True)
    (corpus / "ok.md").write_text("ordinary\n")
    (corpus / "Vault" / ".env").write_text("SECRET=1\n")
    (corpus / "Vault" / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n")
    cfg = cm.load(write_config(tmp_path / "c.toml", tmp_path / "cat.db",
                               [("", corpus)]))
    from shelfmark import refresh
    refresh.run(cfg)
    return cfg


def test_corpus_stats_does_not_name_the_folder_holding_secrets(vault, monkeypatch):
    """It used to print `Vault  2 files  0 own+shareable  2 restricted`,
    which tells a caller where the secrets are and how many there are."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", vault)
    out = server.corpus_stats()

    assert "Vault" not in out
    assert one(vault, "SELECT COUNT(*) FROM files WHERE rights='RESTRICTED'") == 2


def test_corpus_stats_still_admits_that_material_is_withheld(vault, monkeypatch):
    """Silence would be its own dishonesty: the caller should know the
    corpus is larger than what it can see."""
    from shelfmark import server
    monkeypatch.setattr(server, "_CFG", vault)
    out = server.corpus_stats()
    assert "SEALED" in out and "withheld" in out


def test_the_operator_facing_stats_may_still_show_detail(vault):
    """`shelfmark stats` runs locally against the operator's own machine;
    it is corpus_stats() over MCP that must not disclose."""
    from shelfmark import catalog
    con = sqlite3.connect(vault.db)
    try:
        assert "RESTRICTED" in catalog.stats(con)
    finally:
        con.close()


# ------------------------------------------------------------------ version

def test_the_reported_version_matches_the_packaged_one():
    """0.2.0 shipped while __init__ still said 0.1.0, so `--version` lied to
    anyone filing a bug. Reading it from installed metadata makes the drift
    structurally impossible; this catches a regression to a literal."""
    import tomllib
    from pathlib import Path
    import shelfmark

    root = Path(__file__).resolve().parent.parent
    declared = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert shelfmark.__version__ == declared
