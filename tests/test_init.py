"""init must not hand over a guess without saying whether it landed.

The shipped template points at ~/Documents unconditionally. On a machine
where that folder is absent or empty, the first refresh then succeeds over
nothing — so init itself probes the root it just wrote and, when the guess
missed, names the folders that look like the real corpus. Stat-only,
skip-pruned, symlink-refusing, no prompt, no flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shelfmark import cli


@pytest.fixture
def home(tmp_path, monkeypatch):
    """conftest already points HOME and SHELFMARK_CONFIG into the sandbox;
    this returns the sandbox home for building synthetic layouts."""
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    return h


def run_init(tmp_path, capsys):
    with pytest.raises(SystemExit) as ex:
        cli.main(["--config", str(tmp_path / "cfg" / "config.toml"), "init"])
    out = capsys.readouterr()
    return ex.value.code, out.out, out.err


def test_init_warns_when_the_default_root_is_missing(tmp_path, home, capsys):
    rc, out, err = run_init(tmp_path, capsys)
    assert rc == 0                       # the config is still written
    assert "does not exist" in err
    assert str(home / "Documents") in err


def test_init_warns_when_the_default_root_is_document_empty(tmp_path, home,
                                                            capsys):
    docs = home / "Documents"
    docs.mkdir()
    (docs / "helper.py").write_text("print('code is not a corpus')\n")
    rc, out, err = run_init(tmp_path, capsys)
    assert rc == 0
    assert "holds no document files" in err
    assert "empty catalogue" in err


def test_init_names_the_folders_that_look_like_the_corpus(tmp_path, home,
                                                          capsys):
    paper = home / "Paperwork"
    paper.mkdir()
    for i in range(3):
        (paper / f"invoice_{i}.docx").write_text("x")
    rc, out, err = run_init(tmp_path, capsys)
    assert f"candidate: {paper}" in err
    assert "3 document files" in err


def test_init_candidates_do_not_score_build_output(tmp_path, home, capsys):
    """A source tree whose only 'documents' sit in node_modules must not be
    proposed — that is the exact mistake the probe exists to catch."""
    proj = home / "some-project" / "node_modules" / "docs"
    proj.mkdir(parents=True)
    (proj / "readme.pdf").write_text("x")
    rc, out, err = run_init(tmp_path, capsys)
    assert "some-project" not in err


def test_init_stays_quiet_when_the_default_root_has_documents(tmp_path, home,
                                                              capsys):
    docs = home / "Documents"
    docs.mkdir()
    (docs / "report.docx").write_text("x")
    rc, out, err = run_init(tmp_path, capsys)
    assert "WARNING" not in err


def test_init_does_not_follow_symlinks_out_of_the_probe(tmp_path, home,
                                                        capsys):
    """The roots are the trust boundary from the very first command."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.docx").write_text("x")
    docs = home / "Documents"
    docs.mkdir()
    (docs / "link").symlink_to(outside)
    rc, out, err = run_init(tmp_path, capsys)
    # the only documents are behind the symlink, so the probe sees none
    assert "holds no document files" in err
