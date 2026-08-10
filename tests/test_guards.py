"""The guards around pruning.

A wrong prune is the most expensive failure in the product -- the rows are
only recoverable from a backup -- so these tests care about one thing above
all: when the engine is unsure, does it leave the catalogue alone?
"""

from __future__ import annotations

import json

from conftest import one


def test_clean_refresh_is_idempotent(built):
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    assert refresh.run(built) == 0
    assert one(built, "SELECT COUNT(*) FROM files") == before


def test_small_deletion_is_pruned(built):
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    (built.primary_root.path / "Clients" / "Beta" / "research"
     / "market_note_1.md").unlink()

    assert refresh.run(built) == 0
    assert one(built, "SELECT COUNT(*) FROM files") == before - 1
    assert one(built, "SELECT COUNT(*) FROM files WHERE path LIKE '%market_note_1.md'") == 0


def test_a_deletion_over_the_ceiling_is_refused(built):
    """The prune ceiling in its own right.

    Sized to stay ABOVE the coverage floor on purpose: otherwise the walk
    assertion fires first and this guard is never actually exercised -- which
    is precisely what a mutation run showed was happening."""
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    doomed = sorted((built.primary_root.path / "Clients" / "Beta"
                     / "research").glob("*.md"))[:12]
    for p in doomed:
        p.unlink()

    seen = before - len(doomed)
    assert seen >= before * built.coverage_floor_pct // 100, \
        "fixture must not trip the coverage floor, or this tests nothing"
    assert len(doomed) > built.prune_min_rows

    assert refresh.run(built) == 0            # not fatal — but nothing deleted
    assert one(built, "SELECT COUNT(*) FROM files") == before


def test_a_refused_prune_is_announced(built, capsys):
    from shelfmark import refresh
    for p in sorted((built.primary_root.path / "Clients" / "Beta"
                     / "research").glob("*.md"))[:12]:
        p.unlink()
    capsys.readouterr()

    refresh.run(built)

    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "--force" in err


def test_force_prunes_past_the_ceiling(built):
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    doomed = sorted((built.primary_root.path / "Clients" / "Beta"
                     / "research").glob("*.md"))[:12]
    for p in doomed:
        p.unlink()

    assert refresh.run(built, force=True) == 0
    assert one(built, "SELECT COUNT(*) FROM files") == before - len(doomed)


def test_mass_deletion_is_refused_and_nothing_is_lost(built, capsys):
    """The headline guard: a big deletion looks exactly like an unreadable
    root, so the engine must refuse and keep every row."""
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    for p in (built.primary_root.path / "Clients" / "Alpha"
              / "engagement").glob("*.md"):
        p.unlink()

    assert refresh.run(built) == 1
    assert one(built, "SELECT COUNT(*) FROM files") == before

    status = json.loads(built.status_path.read_text())
    assert status["state"] == "failed"


def test_refusal_reaches_the_operator(built, capsys):
    """A guard that refuses in silence teaches nothing: the reason must be on
    stderr, not only in the log and the status file."""
    from shelfmark import refresh
    for p in (built.primary_root.path / "Clients" / "Alpha"
              / "engagement").glob("*.md"):
        p.unlink()
    capsys.readouterr()

    refresh.run(built)

    err = capsys.readouterr().err
    assert "FAILED" in err
    # Both causes named -- the check cannot tell them apart, so it must not
    # assert one of them.
    assert "unreadable" in err.lower()
    assert "went away" in err.lower() or "deletion" in err.lower()
    assert "--force" in err


def test_force_accepts_a_real_deletion(built):
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    gone = list((built.primary_root.path / "Clients" / "Alpha"
                 / "engagement").glob("*.md"))
    for p in gone:
        p.unlink()

    assert refresh.run(built, force=True) == 0
    assert one(built, "SELECT COUNT(*) FROM files") == before - len(gone)
    # --force prunes, but never without a backup to come back to.
    assert built.db.with_name(built.db.name + ".bak-preprune").exists()


def test_missing_root_never_prunes_its_rows(built):
    """An unmounted root is indistinguishable from a mass deletion, so its
    rows must survive -- and the run must still succeed, because a laptop
    away from its NAS is normal, not an error."""
    from shelfmark import refresh
    before = one(built, "SELECT COUNT(*) FROM files")
    extra = built.extra_roots["extra"]
    extra.rename(extra.with_name("extra.away"))

    assert refresh.run(built) == 0
    assert one(built, "SELECT COUNT(*) FROM files") == before
    assert one(built, "SELECT COUNT(*) FROM files WHERE root='extra'") == 1

    status = json.loads(built.status_path.read_text())
    assert status["state"] == "ok"
    assert "extra" in status["detail"]


def test_missing_root_is_announced(built, capsys):
    from shelfmark import refresh
    extra = built.extra_roots["extra"]
    extra.rename(extra.with_name("extra.away"))
    capsys.readouterr()

    refresh.run(built)

    err = capsys.readouterr().err
    assert "prune" in err.lower() and "skipped" in err.lower()
    assert "extra" in err


def test_a_returning_root_is_pruned_normally_again(built):
    """The rows survive the absence; they are still pruned once the root is
    back and the files are genuinely gone."""
    from shelfmark import refresh
    extra = built.extra_roots["extra"]
    away = extra.with_name("extra.away")
    extra.rename(away)
    refresh.run(built)
    assert one(built, "SELECT COUNT(*) FROM files WHERE root='extra'") == 1

    away.rename(extra)
    (extra / "loose_note.md").unlink()
    assert refresh.run(built) == 0
    assert one(built, "SELECT COUNT(*) FROM files WHERE root='extra'") == 0


def test_a_refused_prune_leaves_degraded_and_repeats(built):
    """The status a refusal writes must say so on EVERY subsequent run, not
    only the first. On the machine this failure is named after, the one
    refusal message was easy to miss, nothing repeated it, and 19,157
    phantom rows answered queries for four days. A run that keeps rows it
    knows are stale is "degraded", never "ok" — and the next run, seeing
    the same stale rows, must say it again."""
    import shutil
    from shelfmark import refresh
    root = built.primary_root.path
    shutil.move(str(root / "Decks"), str(root / "Archive"))

    for attempt in (1, 2):
        assert refresh.run(built) == 0, f"run {attempt}"
        status = json.loads(built.status_path.read_text())
        assert status["state"] == "degraded", f"run {attempt}"
        assert "REFUSED" in status["detail"], f"run {attempt}"
