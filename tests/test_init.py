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


# ---------------------------------------------------- the interactive fix

def probe(tmp_path, answer):
    """Run the interactive probe against the config init just wrote, with a
    canned answer, and return the resulting primary root."""
    from shelfmark import config as config_mod
    cfg_file = tmp_path / "cfg" / "config.toml"
    cli._probe_written_root(cfg_file, ask=lambda prompt, default: answer)
    return config_mod.load(cfg_file).primary_root.path


def test_enter_accepts_the_best_candidate(tmp_path, home, capsys):
    """Dummy-proof means the default answer is the right one: Enter takes
    the top candidate the sweep proposed — the engine proposes with
    evidence on screen, the operator decides by accepting it."""
    paper = home / "Paperwork"
    paper.mkdir()
    for i in range(3):
        (paper / f"invoice_{i}.docx").write_text("x")
    run_init(tmp_path, capsys)
    assert probe(tmp_path, "1") == paper
    # and the config file itself carries the reversible, readable form
    assert '"~/Paperwork"' in (tmp_path / "cfg" / "config.toml").read_text()


def test_a_number_picks_that_candidate(tmp_path, home, capsys):
    a, b = home / "Archive", home / "Paperwork"
    for d, count in ((a, 2), (b, 5)):
        d.mkdir()
        for i in range(count):
            (d / f"doc_{i}.docx").write_text("x")
    run_init(tmp_path, capsys)
    # Paperwork (5 docs) ranks 1, Archive (2 docs) ranks 2
    assert probe(tmp_path, "2") == a


def test_a_typed_path_is_used(tmp_path, home, capsys):
    paper = home / "Paperwork"
    paper.mkdir()
    (paper / "doc.docx").write_text("x")
    other = tmp_path / "elsewhere"
    other.mkdir()
    run_init(tmp_path, capsys)
    assert probe(tmp_path, str(other)) == other


def test_keep_declines_the_proposal(tmp_path, home, capsys):
    paper = home / "Paperwork"
    paper.mkdir()
    (paper / "doc.docx").write_text("x")
    run_init(tmp_path, capsys)
    assert probe(tmp_path, "k") == home / "Documents"


def test_a_bad_answer_keeps_the_config_untouched(tmp_path, home, capsys):
    """A typo must not write a root that does not exist — the config stays
    on the announced default rather than inheriting the typo."""
    paper = home / "Paperwork"
    paper.mkdir()
    (paper / "doc.docx").write_text("x")
    run_init(tmp_path, capsys)
    assert probe(tmp_path, "/no/such/folder") == home / "Documents"


def test_end_of_input_keeps_the_default_instead_of_crashing(tmp_path, home,
                                                            capsys):
    """isatty() can say interactive while the first read still hits EOF --
    a pty with nothing on stdin, which is how CI and agent shells run this.
    The config is already written by then, so an uncaught EOFError left a
    config behind AND exited non-zero: the install looked failed but was
    half-done, and re-running hit 'config already exists'."""
    from shelfmark import config as config_mod
    paper = home / "Paperwork"
    paper.mkdir()
    (paper / "doc.docx").write_text("x")
    run_init(tmp_path, capsys)

    def eof(prompt, default):
        raise EOFError

    cfg_file = tmp_path / "cfg" / "config.toml"
    cli._probe_written_root(cfg_file, ask=eof)          # must not raise
    assert config_mod.load(cfg_file).primary_root.path == home / "Documents"
    assert "No answer read" in capsys.readouterr().err


def test_interrupting_the_prompt_keeps_the_default(tmp_path, home, capsys):
    """Ctrl-C at the prompt has the same shape: the operator declined, which
    is not a reason to abandon a config that is already on disk."""
    from shelfmark import config as config_mod
    paper = home / "Paperwork"
    paper.mkdir()
    (paper / "doc.docx").write_text("x")
    run_init(tmp_path, capsys)

    def interrupted(prompt, default):
        raise KeyboardInterrupt

    cfg_file = tmp_path / "cfg" / "config.toml"
    cli._probe_written_root(cfg_file, ask=interrupted)
    assert config_mod.load(cfg_file).primary_root.path == home / "Documents"


def test_init_does_not_follow_symlinks_out_of_the_probe(tmp_path, home,
                                                        capsys):
    """The roots are the trust boundary from the very first command."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.docx").write_text("x")
    docs = home / "Documents"
    docs.mkdir()
    from conftest import symlink_or_skip
    symlink_or_skip(docs / "link", outside)
    rc, out, err = run_init(tmp_path, capsys)
    # the only documents are behind the symlink, so the probe sees none
    assert "holds no document files" in err
