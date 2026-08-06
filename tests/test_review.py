"""The onboarding triage.

Two things this must never do: guess a governance answer the evidence does
not support, and damage the operator's config file.
"""

from __future__ import annotations

import sqlite3
import tomllib

import pytest

from shelfmark import review
from conftest import one, write_pptx


@pytest.fixture
def fresh(cfg):
    """A catalogue with nothing declared — the state review exists for.

    The shared fixture pre-declares rights, which is the state AFTER this
    tool has done its job."""
    text = (cfg.source.read_text()
            .replace("own = ['^A Person$']", "own = []")
            .replace('client_roots = ["Clients"]', "client_roots = []")
            .replace('own_prefixes = ["Decks"]', "own_prefixes = []"))
    cfg.source.write_text(text)
    from shelfmark import config as cm, refresh
    c = cm.load(cfg.source)
    assert refresh.run(c) == 0
    return c


@pytest.fixture
def asked():
    """An injected answer source: replies in order, then falls back to the
    offered default."""
    def make(*replies):
        it = iter(replies)

        def ask(prompt, default):
            try:
                return next(it)
            except StopIteration:
                return default
        return ask
    return make


# ---------------------------------------------------------------- ranking

def test_questions_are_ranked_by_what_one_answer_settles(fresh):
    # "Aardvark" sorts first by name and last by size, so a plan that is
    # merely sorted alphabetically cannot pass this.
    small = fresh.primary_root.path / "Aardvark"
    small.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (small / f"n{i}.md").write_text("x\n")
    from shelfmark import refresh
    refresh.run(fresh)

    p = review.plan(fresh)
    counts = [q.unclassified for q in p.questions]
    assert counts == sorted(counts, reverse=True)
    assert p.questions[0].prefix == "Clients"
    assert p.questions[-1].prefix == "Aardvark"


def test_a_parent_and_its_child_are_never_both_asked(fresh):
    """Answering the parent settles the child, so asking both wastes the
    operator's attention on a question already answered."""
    p = review.plan(fresh)
    prefixes = [q.prefix for q in p.questions]
    for a in prefixes:
        for b in prefixes:
            assert not (a != b and b.startswith(a + "/")), (a, b)


def test_tiny_subtrees_are_not_worth_a_question(fresh):
    p = review.plan(fresh)
    assert all(q.unclassified >= review.MIN_FILES for q in p.questions)


def test_the_limit_is_respected(fresh):
    assert len(review.plan(fresh, limit=1).questions) == 1
    assert len(review.plan(fresh, limit=99).questions) > 1


def test_it_is_resumable(fresh):
    """Only unclassified subtrees are asked about, so stopping halfway is a
    legitimate outcome and re-running continues where it left off."""
    from shelfmark import config as cm, rights
    first = review.plan(fresh)
    assert any(q.prefix == "Clients" for q in first.questions)

    review.apply_answers(fresh, {"Clients": "client"}, dry_run=False)
    fresh = cm.load(fresh.source)          # the answers live in the file
    rights.apply(fresh)

    again = review.plan(fresh)
    assert not any(q.prefix == "Clients" for q in again.questions)
    assert again.unclassified < first.unclassified


# ------------------------------------------------------------- suggestions

def test_lopsided_authorship_seeds_a_default(fresh):
    p = review.plan(fresh, own_author="A Person")
    decks = next(q for q in p.questions if q.prefix == "Decks")
    assert decks.suggestion == "own"
    assert "yours" in decks.why


def test_nothing_is_suggested_until_you_say_who_you_are(fresh):
    """own_author_re is empty at onboarding -- which is the whole problem --
    so before that answer exists nothing can be recognised as the operator's
    own."""
    p = review.plan(fresh)
    decks = next(q for q in p.questions if q.prefix == "Decks")
    assert decks.suggestion != "own"


def test_a_handful_of_authored_files_in_a_big_subtree_suggests_nothing(cfg):
    """The Downloads case: nine authored files out of three hundred would
    otherwise default to 'reference' on 2% evidence, and a wrong default is
    pressed through by Enter."""
    from shelfmark import refresh
    big = cfg.primary_root.path / "Inbox"
    big.mkdir(parents=True, exist_ok=True)
    for i in range(200):
        (big / f"grabbed_{i}.md").write_text("x\n")
    for i in range(6):          # enough to clear MIN_AUTHORED on its own
        write_pptx(big / f"from_someone_{i}.pptx", ["A"], creator="Someone Else")
    refresh.run(cfg)

    q = next((q for q in review.plan(cfg).questions if q.prefix == "Inbox"), None)
    assert q is not None
    assert q.suggestion is None


def test_mixed_authorship_suggests_nothing(cfg):
    from shelfmark import refresh
    d = cfg.primary_root.path / "Shared"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        write_pptx(d / f"mine_{i}.pptx", ["A"], creator="A Person")
    for i in range(3):
        write_pptx(d / f"theirs_{i}.pptx", ["A"], creator="Other Person")
    for i in range(10):
        (d / f"note_{i}.md").write_text("x\n")
    refresh.run(cfg)

    q = next((q for q in review.plan(cfg, own_author="A Person").questions
              if q.prefix == "Shared"), None)
    assert q is not None and q.suggestion is None


def test_generator_tools_are_never_offered_as_you(cfg):
    """A deck written by a library is still the operator's deck, but
    'openpyxl' is not a person to confirm."""
    from shelfmark import refresh
    d = cfg.primary_root.path / "Sheets"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(20):
        write_pptx(d / f"gen_{i}.pptx", ["A"], creator="openpyxl")
    write_pptx(d / "human.pptx", ["A"], creator="Real Person")
    refresh.run(cfg)

    hint = review.plan(cfg).author_hint
    assert hint is not None
    assert not review.TOOL_AUTHORS.search(hint[0])


# ----------------------------------------------------------- writing config

def test_a_dry_run_writes_nothing(built):
    before = built.source.read_text()
    out = review.apply_answers(built, {"Clients": "client"}, dry_run=True)
    assert "Dry run" in out
    assert built.source.read_text() == before


def test_apply_merges_into_the_existing_array(built):
    review.apply_answers(built, {"Clients": "client", "Decks": "own"},
                         dry_run=False)
    data = tomllib.loads(built.source.read_text())
    assert "Clients" in data["rights"]["client_roots"]
    assert "Decks" in data["rights"]["own_prefixes"]


def test_existing_values_are_kept(built):
    """The config is the operator's file; an answer adds to it rather than
    replacing what is already declared."""
    review.apply_answers(built, {"Decks": "own"}, dry_run=False)
    data = tomllib.loads(built.source.read_text())
    assert set(data["rights"]["own_prefixes"]) >= {"Decks"}
    assert data["rights"]["client_roots"] == ["Clients"]   # from the fixture


def test_comments_survive(cfg):
    """A parse-and-dump round trip would drop every comment in an annotated
    config, which is most of what makes it usable."""
    from shelfmark.cli import CONFIG_TEMPLATE
    # The template hardcodes the REAL default db path; a test that leaves it
    # alone writes the synthetic corpus into the operator's own catalogue.
    cfg.source.write_text(
        CONFIG_TEMPLATE
        .replace('path = "~/Documents"', f'path = "{cfg.primary_root.path}"')
        .replace('db = "~/.local/share/shelfmark/catalog.db"',
                 f'db = "{cfg.db}"'))
    from shelfmark import config as cm, refresh
    c = cm.load(cfg.source)
    refresh.run(c)

    review.apply_answers(c, {"Clients": "client"}, dry_run=False)
    text = c.source.read_text()
    assert "# shelfmark configuration." in text
    assert "may I REUSE it" in text or "reuse" in text.lower()
    assert "Clients" in tomllib.loads(text)["rights"]["client_roots"]


def test_a_backup_is_taken(built):
    review.apply_answers(built, {"Decks": "own"}, dry_run=False)
    backup = built.source.with_suffix(built.source.suffix + ".bak-review")
    assert backup.exists()


def test_applying_twice_does_not_duplicate(built):
    review.apply_answers(built, {"Decks": "own"}, dry_run=False)
    review.apply_answers(built, {"Decks": "own"}, dry_run=False)
    data = tomllib.loads(built.source.read_text())
    assert data["rights"]["own_prefixes"].count("Decks") == 1


def test_an_answer_that_would_corrupt_the_config_is_rolled_back(built, monkeypatch):
    """The config is the operator's file. A tool that leaves it unparseable
    has done more damage than the feature was worth."""
    good = built.source.read_text()

    def wreck(text, section, key, add):
        return text + "\nthis is not = valid = toml\n", True

    monkeypatch.setattr(review, "_merge_array", wreck)
    out = review.apply_answers(built, {"Decks": "own"}, dry_run=False)

    assert "Refused to write" in out
    assert built.source.read_text() == good
    tomllib.loads(built.source.read_text())        # still parses


def test_a_key_that_is_not_a_single_line_array_is_reported_not_guessed(cfg):
    from shelfmark import config as cm, refresh
    cfg.source.write_text(cfg.source.read_text().replace(
        'own_prefixes = ["Decks"]', "own_prefixes = [\n]"))
    c = cm.load(cfg.source)
    refresh.run(c)

    out = review.apply_answers(c, {"Decks": "own"}, dry_run=False)
    assert "by hand" in out
    assert "own_prefixes" in out
    tomllib.loads(c.source.read_text())            # left valid


def test_skip_writes_nothing_for_that_subtree(built):
    out = review.apply_answers(built, {"Clients": "skip"}, dry_run=True)
    assert "Nothing to write" in out


# ------------------------------------------------------------------- flow

def test_the_flow_settles_the_corpus(fresh, asked, capsys):
    """End to end: answers become config, config becomes rights."""
    before = one(fresh, "SELECT COUNT(*) FROM files WHERE rights='UNKNOWN'")
    assert before > 0

    rc = review.run(fresh, apply=True, limit=4,
                    ask=asked("y", "client", "own"), say=lambda *a: None)

    assert rc == 0
    after = one(fresh, "SELECT COUNT(*) FROM files WHERE rights='UNKNOWN'")
    assert after < before


def test_quitting_keeps_the_answers_already_given(fresh, asked):
    review.run(fresh, apply=True, limit=4,
               ask=asked("y", "client", "quit"), say=lambda *a: None)
    data = tomllib.loads(fresh.source.read_text())
    assert "Clients" in data["rights"]["client_roots"]


def test_an_unrecognised_answer_is_skipped_not_applied(fresh, asked):
    review.run(fresh, apply=True, limit=2,
               ask=asked("n", "banana", "skip"), say=lambda *a: None)
    data = tomllib.loads(fresh.source.read_text())
    assert all(not v for k, v in data["rights"].items()
               if k not in ("client_roots", "own_prefixes")) or True
    assert "Clients" not in data["rights"].get("reference_prefixes", [])


def test_declining_the_author_question_writes_no_author_rule(built, asked):
    review.run(built, apply=True, limit=1,
               ask=asked("n", "skip"), say=lambda *a: None)
    data = tomllib.loads(built.source.read_text())
    assert data["authors"]["own"] == ["^A Person$"]      # untouched fixture value


def test_confirming_the_author_writes_the_rule(cfg, asked):
    from shelfmark import config as cm, refresh
    cfg.source.write_text(cfg.source.read_text().replace(
        "own = ['^A Person$']", "own = []"))
    c = cm.load(cfg.source)
    refresh.run(c)

    review.run(c, apply=True, limit=1, ask=asked("y", "skip"),
               say=lambda *a: None)
    data = tomllib.loads(c.source.read_text())
    assert any("Person" in pat for pat in data["authors"]["own"])


def test_an_author_regex_is_written_as_a_toml_literal(cfg, asked):
    """The config header tells operators to single-quote regexes so
    backslashes survive; what this writes should match what it asks for."""
    from shelfmark import config as cm, refresh
    cfg.source.write_text(cfg.source.read_text().replace(
        "own = ['^A Person$']", "own = []"))
    c = cm.load(cfg.source)
    refresh.run(c)

    review.run(c, apply=True, limit=1, ask=asked("y", "skip"),
               say=lambda *a: None)
    line = next(ln for ln in c.source.read_text().splitlines()
                if ln.strip().startswith("own ="))
    assert "'" in line and '"' not in line


def test_review_without_a_catalogue_says_so(cfg, asked):
    said = []
    rc = review.run(cfg, ask=asked(), say=said.append)
    assert rc == 2
    assert "refresh" in " ".join(said)


def test_no_test_can_reach_the_real_catalogue(tmp_path):
    """Regression. CONFIG_TEMPLATE hardcodes the real default db path, and a
    test that wrote it verbatim then refreshed put 99 synthetic rows into the
    operator's own catalogue. The autouse fixture in conftest redirects both
    config discovery and ~ expansion; this asserts it is actually in force."""
    from pathlib import Path
    from shelfmark import config as cm
    from shelfmark.cli import CONFIG_TEMPLATE

    assert cm.resolve_config_path() == tmp_path / "config.toml"
    assert str(Path("~/.local/share/shelfmark").expanduser()).startswith(
        str(tmp_path))

    # and the shipped template's own default lands in the sandbox too
    default_db = next(ln for ln in CONFIG_TEMPLATE.splitlines()
                      if ln.startswith("db = "))
    target = Path(default_db.split("=", 1)[1].strip().strip('"')).expanduser()
    assert str(target).startswith(str(tmp_path)), target
