"""Populate files.sha256 for materialised rows that never got hashed.

A separate on-demand pass rather than part of `shelfmark refresh`:

The builder's incremental skip short-circuits before hashing, so every file
that was already catalogued keeps whatever sha256 it had at insert time —
NULL, for anything inserted with --no-hash or while a cloud sync engine had
the file evicted. Without this pass, duplicate detection is structurally
dead. The refresh has to stay within a couple of seconds, so it cannot read
the corpus; this does, once, on demand. Re-run after any large cloud
materialisation.

Rows flagged sensitive are never opened: secret/private material is
catalogued by metadata only, and hashing would mean reading the bytes.
"""

from __future__ import annotations

import sqlite3
import sys
import time

from .catalog import sha256_of
from .config import Config


def backfill(cfg: Config, limit: int = 0, dry_run: bool = False) -> int:
    con = sqlite3.connect(cfg.db)
    try:
        rows = con.execute(
            "SELECT path, bytes FROM files"
            " WHERE sha256 IS NULL AND status = 'ok'"
            "   AND COALESCE(sensitive,0) = 0 AND bytes > 0"
            " ORDER BY bytes"
        ).fetchall()
        if limit:
            rows = rows[:limit]

        total_gb = sum(b or 0 for _, b in rows) / 1e9
        print(f"{len(rows):,} rows to hash · {total_gb:.1f} GB", file=sys.stderr)
        if dry_run or not rows:
            return 0

        done = hashed = gone = skipped = 0
        pending: list[tuple[str, str]] = []
        t0 = time.monotonic()

        for rel, _ in rows:
            p = cfg.abs_path(rel)
            try:
                st = p.stat()
            except OSError:
                # Deleted or unreadable since the last walk. Leave the row
                # alone — pruning is the refresh's job and it has the
                # seen_at evidence.
                gone += 1
                done += 1
                continue
            sha = sha256_of(p, st)
            if sha:
                pending.append((sha, rel))
                hashed += 1
            else:
                # None for a dataless cloud file, whose read would yield the
                # empty-string digest and make distinct files look identical.
                # Not an error; just not hashable right now.
                skipped += 1
            done += 1

            if len(pending) >= 500:
                con.executemany("UPDATE files SET sha256=? WHERE path=?", pending)
                con.commit()
                pending.clear()
            if done % 2000 == 0:
                rate = done / max(time.monotonic() - t0, 1e-6)
                print(f"  ... {done:,}/{len(rows):,}  {rate:.0f} files/s",
                      file=sys.stderr)

        if pending:
            con.executemany("UPDATE files SET sha256=? WHERE path=?", pending)
            con.commit()

        dupe_groups, dupe_bytes = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(extra),0) FROM ("
            "  SELECT SUM(bytes) - MAX(bytes) AS extra FROM files"
            "  WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING COUNT(*) > 1)"
        ).fetchone()

        print(f"\nhashed {hashed:,}  unhashable {skipped:,}  vanished {gone:,}"
              f"  in {time.monotonic() - t0:.0f}s", file=sys.stderr)
        print(f"duplicate groups {dupe_groups:,} · "
              f"{(dupe_bytes or 0) / 1e9:.1f} GB redundant", file=sys.stderr)
        return 0
    finally:
        con.close()
