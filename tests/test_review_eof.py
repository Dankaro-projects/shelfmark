"""End of input must end the review, not destroy it.

`review` is the command whose entire purpose is collecting answers, and it
tells the operator "stop any time -- every answer is kept". An uncaught
EOFError partway through broke that promise: every answer already given was
thrown away with a traceback. isatty() is no defence, because it can report
an interactive terminal while the first read still hits end-of-input -- a
pty with nothing on stdin, which is how CI and agent shells run it.

These use a corpus with NO rights rules. The shared config classifies its
subtrees already, so `plan()` returns no questions there and every
assertion below would pass without the review ever asking anything.
"""

from __future__ import annotations

import textwrap

import pytest

from shelfmark import config as config_mod
from shelfmark import refresh, review
from conftest import toml_str


@pytest.fixture
def unclassified(tmp_path, corpus):
    """A built catalogue where everything is still UNKNOWN, so there are
    real questions to answer."""
    cfg_file = tmp_path / "unclassified.toml"
    cfg_file.write_text(textwrap.dedent(f"""
        [index]
        db = {toml_str(tmp_path / 'unclassified.db')}

        [[roots]]
        path = {toml_str(corpus)}
    """).strip() + "\n")
    cfg = config_mod.load(cfg_file)
    assert refresh.run(cfg) == 0
    return cfg


def test_the_fixture_really_has_questions(unclassified):
    """Guards the guard: with no questions, every test below would pass
    without the review ever prompting."""
    p = review.plan(unclassified)
    assert p.questions, "no questions -- the tests below prove nothing"
    assert p.unclassified > 0


def answers_then_eof(n: int, answer: str = "personal"):
    """Answer the first `n` prompts, then behave like a closed stdin."""
    state = {"seen": 0}

    def ask(prompt, default):
        if state["seen"] >= n:
            raise EOFError
        state["seen"] += 1
        return answer

    return ask


def test_end_of_input_does_not_crash_the_review(unclassified):
    said: list[str] = []
    assert review.run(unclassified, apply=False, ask=answers_then_eof(0),
                      say=said.append) == 0


def test_ctrl_c_is_treated_the_same_as_end_of_input(unclassified):
    def interrupted(prompt, default):
        raise KeyboardInterrupt

    said: list[str] = []
    assert review.run(unclassified, apply=False, ask=interrupted,
                      say=said.append) == 0


def test_answers_given_before_the_end_of_input_are_kept(unclassified):
    """The promise on screen is 'stop any time, every answer is kept'."""
    said: list[str] = []
    # 1 for the author question, then 2 real answers, then silence.
    rc = review.run(unclassified, apply=True, ask=answers_then_eof(3),
                    say=said.append)
    assert rc == 0
    written = unclassified.source.read_text(encoding="utf-8")
    assert "personal_roots" in written, "the answers given were discarded"


def test_silence_never_claims_authorship(unclassified):
    """"Most files say X, is that you?" defaulting to yes on silence would
    write an ownership claim nobody made -- and inference may seed a default
    the operator sees, never decide a governance answer for them."""
    hint = review.plan(unclassified).author_hint
    assert hint, "fixture should offer an author to claim"

    review.run(unclassified, apply=True, ask=answers_then_eof(0))
    written = unclassified.source.read_text(encoding="utf-8")
    own = [ln for ln in written.splitlines() if ln.strip().startswith("own =")]
    assert all(hint[0] not in ln for ln in own), (
        "an unanswered prompt wrote an ownership claim")


def test_a_fully_answered_run_still_works(unclassified):
    seen: list[str] = []

    def ask(prompt, default):
        seen.append(prompt)
        return "skip"

    said: list[str] = []
    assert review.run(unclassified, apply=False, ask=ask,
                      say=said.append) == 0
    assert len(seen) > 1, "the review asked nothing; the tests above are void"
