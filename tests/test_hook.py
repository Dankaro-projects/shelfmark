"""The hook adapter: silent when healthy, hook JSON when not, exit 0 always.

`shelfmark hook` exists because the README used to teach
`shelfmark refresh --if-needed >/dev/null 2>&1` — and since every refusal
and failure speaks on stderr, that redirect discarded the only delivery of
the news. A catalogue stayed silently wrong for four days behind exactly
that line. The product owns the hook now; these tests are the contract.
"""

from __future__ import annotations

import json
import shutil

import pytest

from shelfmark import cli


def run_hook(cfg, event, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(cfg.source), "hook", event])
    out, err = capsys.readouterr()
    return exc.value.code, out, err


def test_healthy_session_start_is_silent(built, capsys):
    """No news is the contract: a message on every session start is noise,
    and noise trains the operator to stop reading."""
    code, out, _ = run_hook(built, "session-start", capsys)
    assert code == 0
    assert out == ""


def test_healthy_stop_is_silent(built, capsys):
    code, out, _ = run_hook(built, "stop", capsys)
    assert code == 0
    assert out == ""


def test_a_degraded_catalogue_reaches_operator_and_agent(built, capsys):
    """The four-day failure, replayed through the hook: rename a subtree,
    let the hook's own refresh hit the prune refusal, and require the news
    to come out as hook JSON — systemMessage for the person,
    additionalContext for the agent."""
    root = built.primary_root.path
    shutil.move(str(root / "Decks"), str(root / "Archive"))

    code, out, _ = run_hook(built, "session-start", capsys)
    assert code == 0
    payload = json.loads(out)
    assert "⚠" in payload["systemMessage"]
    assert "REFUSED" in payload["systemMessage"]
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert "REFUSED" in hso["additionalContext"]


def test_stop_reports_a_non_ok_status(built, capsys):
    root = built.primary_root.path
    shutil.move(str(root / "Decks"), str(root / "Archive"))
    built.dirty_marker.write_text("")             # make --if-needed run

    code, out, _ = run_hook(built, "stop", capsys)
    assert code == 0
    payload = json.loads(out)
    assert "degraded" in payload["systemMessage"]


def test_a_broken_config_cannot_break_the_session(tmp_path, capsys, monkeypatch):
    """A hook that exits non-zero or raises takes the whole session hostage
    over a tool problem. Whatever goes wrong, the hook says so in JSON and
    exits 0."""
    bad = tmp_path / "bad.toml"
    bad.write_text("this is [ not toml")
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(bad), "hook", "session-start"])
    out, _ = capsys.readouterr()
    assert exc.value.code == 0
    payload = json.loads(out)
    assert "cannot load config" in payload["systemMessage"]


def test_stop_reports_the_streak(built, capsys):
    """By the second bad run the hook's one line must already say this is
    not the first — escalation by duration, not repetition."""
    import shutil
    from shelfmark import refresh
    root = built.primary_root.path
    shutil.move(str(root / "Decks"), str(root / "Archive"))
    assert refresh.run(built) == 0                # streak run 1 (degraded)
    built.dirty_marker.write_text("")             # make --if-needed run again

    code, out, _ = run_hook(built, "stop", capsys)
    assert code == 0
    payload = json.loads(out)
    assert "2 consecutive runs" in payload["systemMessage"]


def test_stop_reports_an_unreadable_folder(built, capsys, monkeypatch):
    """The silence this closes, verified before the fix: a refresh that hit
    an unreadable folder wrote state "ok", so the stop hook said nothing
    while the index was knowingly incomplete."""
    import os
    d = built.primary_root.path / "sealed_room"
    d.mkdir()
    (d / "note.md").write_text("x\n")
    assert refresh_ok(built)                      # catalogue the folder

    real = os.scandir

    def failing(path=".", *a, **kw):
        if "sealed_room" in str(path):
            raise OSError(13, "Permission denied", str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", failing)
    built.dirty_marker.write_text("")

    code, out, _ = run_hook(built, "stop", capsys)
    assert code == 0
    payload = json.loads(out)
    assert "degraded" in payload["systemMessage"]
    assert "unreadable" in payload["systemMessage"]


def refresh_ok(cfg):
    from shelfmark import refresh
    return refresh.run(cfg) == 0
