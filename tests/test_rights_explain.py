"""The rights precedence is engine-owned — and must be legible.

Seven prefix lists whose evaluation order lived only inside derive() were
the hardest part of setup in the first external install report. The fix is
not a configurable rule table (three of the outcomes are authorship-
conditional, and precedence an operator can misconfigure is worse than
precedence they cannot touch) — it is making the engine explain itself:
`shelfmark config` prints the order with the number of files each rule
currently claims, attributed by the same function that classifies them.
"""

from __future__ import annotations

import re

import pytest

from shelfmark import cli
from shelfmark.rights import PRECEDENCE, _derive_explained, derive


def test_own_confidential_nests_inside_own(tmp_path, monkeypatch):
    """own_confidential_prefixes is checked first so it can carve a
    never-leaves subtree out of an otherwise shareable one. Reordering the
    checks in _derive_explained must fail here."""
    from shelfmark import config as config_mod
    f = tmp_path / "c.toml"
    root = tmp_path / "r"
    root.mkdir()
    f.write_text(f'[index]\ndb = "{tmp_path / "c.db"}"\n\n'
                 f'[[roots]]\npath = "{root}"\n\n'
                 f'[rights]\nown_prefixes = ["Decks"]\n'
                 f'own_confidential_prefixes = ["Decks/Invoices"]\n')
    c = config_mod.load(f)
    assert derive("Decks/Invoices/inv_2026.pdf", None, None, c) == ("OWN", 0, 1)
    assert derive("Decks/pitch.pptx", None, None, c) == ("OWN", 0, 0)


def test_every_classification_is_attributed(built, rows_helper=None):
    """claimed_by is total: every row lands on exactly one PRECEDENCE label,
    so the config table's counts always sum to the corpus."""
    from conftest import rows
    for r in rows(built, "SELECT path, author, last_author FROM files"):
        label = _derive_explained(r[0], r[1], r[2], built)[3]
        assert label in PRECEDENCE, r[0]


def test_config_prints_precedence_with_live_counts(built, capsys):
    with pytest.raises(SystemExit) as ex:
        cli.main(["config"])
    assert ex.value.code == 0
    out = capsys.readouterr().out
    assert "first match wins" in out
    # conftest classifies Decks/* via own_prefixes and Clients/* via
    # client_roots — both lines must carry a real count, not a zero.
    own = re.search(r"^\s+([\d,]+)\s+\[rights\] own_prefixes$", out, re.M)
    cli_ = re.search(r"^\s+([\d,]+)\s+\[rights\] client_roots$", out, re.M)
    assert own and int(own.group(1).replace(",", "")) > 0
    assert cli_ and int(cli_.group(1).replace(",", "")) > 0
    # and the order on screen is the engine's order
    positions = [out.find(label) for label in PRECEDENCE]
    assert all(p >= 0 for p in positions)
    assert positions == sorted(positions)


def test_config_without_a_catalogue_still_prints_the_order(cfg, capsys):
    cfg.db.unlink(missing_ok=True)
    with pytest.raises(SystemExit) as ex:
        cli.main(["config"])
    assert ex.value.code == 0
    out = capsys.readouterr().out
    assert "first match wins" in out
    for label in PRECEDENCE:
        assert label in out
