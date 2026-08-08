"""Backfill rights + confidentiality across the catalogue from config rules.

Why this is separate from the builder: the builder is incremental, so it only
touches files whose (size, mtime, residency) changed. Changing a rights rule
must re-derive every row — this runs as pure SQL over the DB with no
filesystem walk: seconds, not minutes, and safe to re-run.

ALWAYS run this after a build, never before: the builder's upsert writes its
coarser authorship-only rights (rights=excluded.rights) and would overwrite
this backfill for every file it touches. `shelfmark refresh` pairs the two
and asserts afterwards.

Two axes, deliberately distinct — conflating them once put most of a corpus
into RESTRICTED and gutted the index:

    rights        — may I REUSE this? (OWN / REFERENCE / RESTRICTED)
    confidential  — may it LEAVE? (0 = fine, 1 = in confidence)
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from .config import Config, has_prefix


# Evaluation order of the seven prefix lists plus the author signals, as
# label strings. `shelfmark config` prints them in THIS order with the
# number of files each currently claims — the order is engine-owned and not
# configurable: precedence that cannot be misconfigured beats precedence an
# operator has to get right.
PRECEDENCE = (
    "[privacy] secret_patterns / private_paths",
    "[authors] client (author field or path)",
    "[rights] own_confidential_prefixes",
    "[rights] own_prefixes",
    "[rights] reference_prefixes",
    "[rights] neutral_prefixes",
    "[rights] client_roots",
    "[rights] work_under_personal",
    "[rights] personal_roots",
    "(fallback) authored by us",
    "(fallback) any other author",
    "(fallback) unclaimed",
)


def _derive_explained(path: str, author: str | None, last_author: str | None,
                      cfg: Config) -> tuple[str, int, int, str]:
    """(rights, sensitive, confidential, claimed_by) for one catalogue path.

    The single source of truth for BOTH the backfill and `shelfmark
    config`'s per-list counts — a separate attribution path would drift
    from behaviour the first time a branch moved. `claimed_by` is always
    one of PRECEDENCE."""
    sensitive = int(bool(cfg.secret_re.search(path) or
                         (cfg.private_re and cfg.private_re.search(path))))
    if sensitive:
        return "RESTRICTED", 1, 1, PRECEDENCE[0]

    # --- authorship signals first, they are the strongest evidence ---------
    if cfg.client_author_re:
        for a in (author, last_author):
            if a and cfg.client_author_re.search(a):
                return "REFERENCE", 0, 1, PRECEDENCE[1]
        if cfg.client_author_re.search(path):
            return "REFERENCE", 0, 1, PRECEDENCE[1]

    authored_by_us = False
    if cfg.own_author_re:
        for a in (author, last_author):
            if a and cfg.own_author_re.search(a):
                authored_by_us = True
    if author and cfg.tool_author_re and cfg.tool_author_re.search(author):
        authored_by_us = True

    # --- path-based ownership ----------------------------------------------
    if has_prefix(path, cfg.own_confidential_prefixes):
        return "OWN", 0, 1, PRECEDENCE[2]
    if has_prefix(path, cfg.own_prefixes):
        return "OWN", 0, 0, PRECEDENCE[3]
    if has_prefix(path, cfg.reference_prefixes):
        return "REFERENCE", 0, 0, PRECEDENCE[4]
    if has_prefix(path, cfg.neutral_prefixes):
        return ("OWN" if authored_by_us else "UNKNOWN"), 0, 0, PRECEDENCE[5]

    # Anything under an engagement root is confidential by default: client
    # material does not leave unless positively cleared.
    #
    # Unattributed client material is REFERENCE, not RESTRICTED. Only OOXML
    # files carry an author, so "no author" mostly means "is a PDF or image",
    # which is not evidence of sensitivity. RESTRICTED drops a row from the
    # search index entirely; `confidential` already stops it leaving.
    if has_prefix(path, cfg.client_roots):
        if authored_by_us:
            return "OWN", 0, 1, PRECEDENCE[6]   # our method, their engagement
        return "REFERENCE", 0, 1, PRECEDENCE[6] # theirs/unattributed, in conf.
    if has_prefix(path, cfg.work_under_personal):
        if authored_by_us:
            return "OWN", 0, 1, PRECEDENCE[7]
        return "REFERENCE", 0, 1, PRECEDENCE[7]

    if has_prefix(path, cfg.personal_roots):
        # Genuinely personal admin. Not secret, but not work product either —
        # keep it out of the work-facing index.
        return ("OWN" if authored_by_us else "RESTRICTED"), 0, 1, PRECEDENCE[8]

    if authored_by_us:
        return "OWN", 0, 0, PRECEDENCE[9]
    if author:
        return "REFERENCE", 0, 0, PRECEDENCE[10]
    return "UNKNOWN", 0, 0, PRECEDENCE[11]


def derive(path: str, author: str | None, last_author: str | None,
           cfg: Config) -> tuple[str, int, int]:
    """Return (rights, sensitive, confidential) for one catalogue path."""
    return _derive_explained(path, author, last_author, cfg)[:3]


def apply(cfg: Config, dry_run: bool = False) -> dict:
    """Re-derive rights/sensitive/confidential for every row; sync files_fts."""
    con = sqlite3.connect(cfg.db)
    try:
        rows = con.execute(
            "SELECT id, path, author, last_author, rights, sensitive FROM files"
        ).fetchall()

        before = Counter(r[4] for r in rows)
        after: Counter = Counter()
        conf = 0
        changes: list[tuple] = []
        moved: Counter = Counter()

        for fid, path, author, last_author, old_rights, _old_sens in rows:
            r, sens, c = derive(path, author, last_author, cfg)
            after[r] += 1
            conf += c
            if r != old_rights:
                moved[f"{old_rights} -> {r}"] += 1
            changes.append((r, sens, c, fid))

        report = {"rows": len(rows), "before": dict(before),
                  "after": dict(after), "confidential": conf,
                  "transitions": dict(moved.most_common(12))}

        if dry_run:
            report["dry_run"] = True
            return report

        con.executemany(
            "UPDATE files SET rights=?, sensitive=?, confidential=? WHERE id=?",
            changes,
        )
        # Restricted material must not be searchable via the metadata index.
        con.execute("DELETE FROM files_fts WHERE rowid IN "
                    "(SELECT id FROM files WHERE rights='RESTRICTED')")
        # And the reverse: a file that STOPS being RESTRICTED has to come
        # back. The builder only writes files_fts for rows it touches
        # (incremental), so a de-restricted file whose bytes never change
        # again would stay permanently unsearchable — visible to browse and
        # get_file, absent from search. `shelfmark refresh` asserts the two
        # counts agree.
        con.execute("""
            INSERT INTO files_fts(rowid, path, filename, doc_type, author,
                                  company, ooxml_title, titles)
            SELECT f.id, f.path, f.filename, COALESCE(f.doc_type,''),
                   COALESCE(f.author,''), COALESCE(f.company,''),
                   COALESCE(f.ooxml_title,''),
                   COALESCE((SELECT group_concat(title, ' ' || char(10))
                             FROM (SELECT title FROM slide_titles
                                   WHERE file_id = f.id ORDER BY idx)), '')
            FROM files f
            WHERE f.rights != 'RESTRICTED'
              AND f.id NOT IN (SELECT rowid FROM files_fts)
        """)
        # Rebuild the practice-library view to respect the confidential axis.
        con.execute("DROP VIEW IF EXISTS v_own_ip")
        con.execute("""
            CREATE VIEW v_own_ip AS
            SELECT path, doc_type, author, confidential,
                   substr(mtime,1,10) AS modified,
                   ROUND(bytes/1048576.0,1) AS mb
            FROM files
            WHERE rights='OWN' AND sensitive=0
              AND doc_type IN ('deck','report','proposal','model','research',
                               'brand','roadmap','sow','deck-source','note')
            ORDER BY confidential, mtime DESC""")
        con.commit()
        return report
    finally:
        con.close()


def format_report(rep: dict) -> str:
    out = [f"rows: {rep['rows']}", "", "rights BEFORE -> AFTER"]
    for k in ("OWN", "REFERENCE", "RESTRICTED", "UNKNOWN"):
        out.append(f"  {k:<11} {rep['before'].get(k, 0):>6}  ->  "
                   f"{rep['after'].get(k, 0):>6}")
    out.append(f"\nconfidential (must not leave): {rep['confidential']}")
    if rep["transitions"]:
        out.append("\ntransitions:")
        for k, v in rep["transitions"].items():
            out.append(f"  {v:>6}  {k}")
    if rep.get("dry_run"):
        out.append("\n[dry run -- nothing written]")
    return "\n".join(out)
