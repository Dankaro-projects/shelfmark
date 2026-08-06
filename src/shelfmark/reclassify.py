"""Re-apply classification rules to already-catalogued rows. No FS walk.

Why this exists: the builder is incremental by (size, mtime, residency), so
fixing a doc-type regex or a facet rule does NOT relabel files that have not
changed on disk — the old bad labels persist silently forever. A full
`--rebuild` would fix it too, but it re-walks the whole tree and forces every
evicted cloud file back down, when filename, ext and path are already in the
DB. ALWAYS follow a rule edit with `shelfmark reclassify`.

Keeps `files_fts.doc_type` in sync — the FTS table carries its own copy, and
a stale copy there makes search drift from the catalogue.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from .catalog import classify_doc_type, path_facets
from .config import Config

FACET_COLS = ("root", "client", "project", "depth", "domain")


def doc_types(cfg: Config, apply: bool = False, limit_examples: int = 6) -> str:
    con = sqlite3.connect(cfg.db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, filename, ext, doc_type, doc_type_src FROM files"
        ).fetchall()

        changes = []
        for r in rows:
            new_type, new_src = classify_doc_type(r["filename"] or "",
                                                  r["ext"] or "", cfg)
            if ((new_type or "") != (r["doc_type"] or "")
                    or new_src != (r["doc_type_src"] or "")):
                changes.append((r["id"], r["filename"], r["doc_type"],
                                new_type, r["doc_type_src"], new_src))

        if not changes:
            return f"{len(rows):,} rows checked — nothing to change."

        out = []
        moves = Counter((c[2] or "—", c[3] or "—") for c in changes)
        out.append(f"{len(rows):,} rows checked · {len(changes):,} would change\n")
        out.append(f"{'from':<14} {'to':<14} {'n':>6}")
        out.append("-" * 36)
        for (old, new), n in moves.most_common():
            out.append(f"{old:<14} {new:<14} {n:>6,}")

        out.append("\nexamples:")
        seen = Counter()
        for _id, fn, old, new, _os, _ns in changes:
            k = (old, new)
            if seen[k] < limit_examples:
                seen[k] += 1
                out.append(f"  {old or '—':<12} -> {new or '—':<12}  {fn}")

        if not apply:
            out.append("\nDry run. Re-run with --apply to write.")
            return "\n".join(out)

        # files_fts carries its own copy of doc_type; update both or search drifts.
        with con:
            con.executemany(
                "UPDATE files SET doc_type = ?, doc_type_src = ? WHERE id = ?",
                [(c[3], c[5], c[0]) for c in changes],
            )
            fts_ids = {r[0] for r in con.execute(
                "SELECT rowid FROM files_fts").fetchall()}
            con.executemany(
                "UPDATE files_fts SET doc_type = ? WHERE rowid = ?",
                [(c[3], c[0]) for c in changes if c[0] in fts_ids],
            )

        out.append(f"\napplied to {len(changes):,} rows "
                   f"({sum(1 for c in changes if c[0] in fts_ids):,} also in files_fts)")

        # Governance invariant — assert after any write.
        bad = con.execute(
            """SELECT COUNT(*) FROM files_fts JOIN files f ON f.id=files_fts.rowid
               WHERE f.rights='RESTRICTED'"""
        ).fetchone()[0]
        out.append(f"invariant — RESTRICTED rows in files_fts: {bad} (must be 0)")
        return "\n".join(out)
    finally:
        con.close()


def facets(cfg: Config, apply: bool = False, limit_examples: int = 8) -> str:
    con = sqlite3.connect(cfg.db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, path, filename, root, client, project, depth, domain"
            " FROM files").fetchall()

        changes = []
        for r in rows:
            new = path_facets(r["path"], cfg)
            old = tuple(r[c] for c in FACET_COLS)
            if new != old:
                changes.append((r["id"], r["path"], r["filename"], old, new))

        if not changes:
            return f"{len(rows):,} rows checked — nothing to change."

        out = []
        which = Counter()
        for _id, _p, fn, old, new in changes:
            for i, c in enumerate(FACET_COLS):
                if old[i] != new[i]:
                    # the classic failure: was the old value the filename?
                    was_fn = " (was the filename)" if old[i] == fn else ""
                    which[c + was_fn] += 1

        out.append(f"{len(rows):,} rows checked · {len(changes):,} would change\n")
        out.append(f"{'column':<28} {'n':>6}")
        out.append("-" * 36)
        for col, n in which.most_common():
            out.append(f"{col:<28} {n:>6,}")

        out.append("\nexamples:")
        for _id, p, _fn, old, new in changes[:limit_examples]:
            diff = ", ".join(f"{c}: {old[i]!r} -> {new[i]!r}"
                             for i, c in enumerate(FACET_COLS) if old[i] != new[i])
            out.append(f"  {p}\n      {diff}")

        if not apply:
            out.append("\nDry run. Re-run with --apply to write.")
            return "\n".join(out)

        with con:
            con.executemany(
                "UPDATE files SET root=?, client=?, project=?, depth=?, domain=?"
                " WHERE id=?",
                [(*new, _id) for _id, _p, _fn, _old, new in changes],
            )

        left = con.execute(
            "SELECT COUNT(*) FROM files WHERE client = filename OR project = filename"
        ).fetchone()[0]
        leak = con.execute(
            """SELECT COUNT(*) FROM files_fts JOIN files f ON f.id=files_fts.rowid
               WHERE f.rights='RESTRICTED'"""
        ).fetchone()[0]
        out.append(f"\napplied to {len(changes):,} rows")
        out.append(f"invariant — facet == filename: {left} (must be 0)")
        out.append(f"invariant — RESTRICTED rows in files_fts: {leak} (must be 0)")
        return "\n".join(out)
    finally:
        con.close()
