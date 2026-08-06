"""shelfmark command-line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import config as config_mod
from .config import ConfigError

CONFIG_TEMPLATE = '''\
# shelfmark configuration.
#
# Regexes: use single-quoted TOML strings ('...') so backslashes survive.
# Path prefixes are catalogue-relative (no leading slash) and match whole
# folder names.

[index]
# Where the SQLite catalogue lives. MUST be outside every indexed root and
# outside any cloud-synced folder (iCloud/Dropbox/OneDrive) — it is a
# mutating binary DB and would thrash sync on every refresh.
db = "~/.local/share/shelfmark/catalog.db"

# The tree to index. Exactly one root has no label (the primary root);
# catalogue paths are stored relative to it. Add more [[roots]] blocks with
# a label for trees that live elsewhere — their paths are stored under
# "<label>/…".
[[roots]]
path = "~/Documents"

# [[roots]]
# label = "notes"
# path = "~/notes"

[scan]
# Appended to the built-in skip list (node_modules, .git, venv, …).
skip_dirs = []
skip_names = []

[privacy]
# Extra regexes for credential-like files (built-ins already cover .env,
# key/cert extensions, id_rsa, backup codes, passports…). Matches become
# RESTRICTED: catalogued for your own local search, never returned by any
# MCP tool, never opened for hashing or extraction.
secret_patterns = []
# Path regexes for personal/private subtrees, e.g. '^Personal/', 'payslip'.
private_paths = []

[authors]
# Regexes matched against OOXML author fields. "own" marks files you or
# your company authored; "client" marks authors that must never resolve to
# OWN (e.g. a client who shares a name fragment with you). "tool" extends
# the built-in list of generator tools (pptxgenjs, openpyxl, pandoc, …).
own = []
client = []
tool = []

[rights]
# Path-prefix rules, checked in this order after the author signals.
# rights = may I REUSE it (OWN/REFERENCE/RESTRICTED);
# confidential = may it LEAVE (independent axis — a deck you authored for a
# client is OWN and confidential at the same time).
own_prefixes = []                 # yours, shareable
own_confidential_prefixes = []    # yours, but never leaves (e.g. invoices)
reference_prefixes = []           # third-party material, fine to reference
neutral_prefixes = []             # scratch/captures: not work product
client_roots = []                 # engagement trees: confidential by default
personal_roots = []               # personal admin: held out of the work index
work_under_personal = []          # work subtrees misfiled under a personal root

[facets]
# Top-level folders that count as work (client = 2nd path segment,
# project = 3rd) or personal. Everything else is domain "other".
work_roots = []
personal_roots = []
# Subtree prefixes that re-anchor client extraction below them (work
# archives filed inside a personal root).
reanchor = []

[doc_types]
# Extra filename rules, checked BEFORE the built-ins: [["type", 'regex'], …]
# Anchor short alternatives on BOTH sides — see the README.
rules = []
# Built-in rule names to disable for this corpus (e.g. "roadmap" if the
# word "sprint" means something else in your files).
disable = []
# Extension → type additions/overrides: { ".xyz" = "mytype" }
[doc_types.ext]

[context_types]
rules = []
disable = []

[refresh]
max_age_seconds = 900        # --if-needed refreshes when older than this
prune_ceiling_pct = 2        # refuse to prune more than this share of rows
prune_min_rows = 50          # …when it is also more than this many rows
coverage_floor_pct = 80      # fail if the walk sees less than this share
stale_after_hours = 3        # corpus_stats warns beyond this age

[server]
max_limit = 100              # hard cap on any tool's result list
body_chars = 1500            # email body excerpt length

[cards]
# `shelfmark cards` gives a subtree its own card when it is big, or dense
# in reusable material even when small. Lower these for small corpora.
min_files = 100
min_own_open = 20
'''


def _load(args):
    try:
        return config_mod.load(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"shelfmark: {exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_init(args) -> int:
    path = config_mod.resolve_config_path(args.config)
    if path.exists() and not args.force:
        print(f"Config already exists at {path} (use --force to overwrite).")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE)
    print(f"Wrote {path}")
    print()
    print("Next steps:")
    print(f"  1. Edit {path} — set your [[roots]] and, if you want")
    print("     rights/confidentiality classification, the [rights] and")
    print("     [authors] rules.")
    print("  2. shelfmark refresh          # first build of the catalogue")
    print("  3. shelfmark stats            # see what it found")
    print("  4. claude mcp add shelfmark -s user -- shelfmark-mcp")
    return 0


def cmd_config(args) -> int:
    cfg = _load(args)
    print(f"config:  {cfg.source}")
    print(f"db:      {cfg.db}")
    for r in cfg.roots:
        label = r.label or "(primary)"
        print(f"root:    {label:<14} {r.path}"
              + ("" if r.path.is_dir() else "   [MISSING]"))
    print(f"rules:   {len(cfg.doc_type_rules)} doc-type, "
          f"{len(cfg.context_rules)} context")
    print(f"privacy: private_paths {'set' if cfg.private_re else 'not set'}, "
          f"own authors {'set' if cfg.own_author_re else 'not set'}")
    return 0


def cmd_build(args) -> int:
    from . import catalog
    cfg = _load(args)
    catalog.build(cfg, rebuild=args.rebuild, do_hash=not args.no_hash)
    if args.stats:
        import sqlite3
        con = sqlite3.connect(cfg.db)
        print(catalog.stats(con))
        con.close()
    return 0


def cmd_rights(args) -> int:
    from . import rights
    cfg = _load(args)
    rep = rights.apply(cfg, dry_run=args.dry_run)
    print(rights.format_report(rep))
    return 0


def cmd_refresh(args) -> int:
    from . import refresh
    cfg = _load(args)
    if args.if_needed:
        return refresh.run_if_needed(cfg, force=args.force)
    return refresh.run(cfg, force=args.force)


def cmd_review(args) -> int:
    from . import review
    return review.run(_load(args), apply=args.apply, limit=args.limit)


def cmd_reclassify(args) -> int:
    from . import reclassify
    cfg = _load(args)
    if args.what in ("doc-types", "all"):
        print(reclassify.doc_types(cfg, apply=args.apply))
    if args.what in ("facets", "all"):
        print(reclassify.facets(cfg, apply=args.apply))
    return 0


def cmd_hash(args) -> int:
    from . import hashes
    cfg = _load(args)
    return hashes.backfill(cfg, limit=args.limit, dry_run=args.dry_run)


def cmd_stats(args) -> int:
    import sqlite3
    from . import catalog
    cfg = _load(args)
    if not cfg.db.exists():
        print(f"No catalogue at {cfg.db} — run `shelfmark refresh` first.")
        return 1
    con = sqlite3.connect(cfg.db)
    print(catalog.stats(con))
    con.close()
    return 0


def cmd_serve(args) -> int:
    from . import server
    if args.config:
        server._CFG = config_mod.load(args.config)
    server.cfg()  # fail early on bad config
    if args.selftest:
        server.selftest()
        return 0
    server.mcp.run()
    return 0


def cmd_map(args) -> int:
    from . import mapgen
    from pathlib import Path
    cfg = _load(args)
    out = Path(args.out).expanduser() if args.out else None
    print(mapgen.write(cfg, out_path=out, to_stdout=args.stdout), end="")
    if not args.stdout:
        print()
    return 0


def cmd_cards(args) -> int:
    from . import cards
    from pathlib import Path
    cfg = _load(args)
    out = Path(args.out).expanduser() if args.out else None
    if args.todo:
        print(cards.todo(cfg, out_path=out))
    else:
        print(cards.write(cfg, out_path=out))
    return 0


def cmd_ingest_email(args) -> int:
    from . import emails
    cfg = _load(args)
    return emails.ingest(cfg, prefix=args.prefix, era=args.era,
                         bodies=args.bodies)


def cmd_dedupe_emails(args) -> int:
    from . import emails
    cfg = _load(args)
    print(emails.dedupe(cfg, apply=args.apply))
    return 0


def cmd_mark_dirty(args) -> int:
    """For editor/agent hooks: read a tool payload on stdin and drop the
    dirty marker if a written path falls under an indexed root. Near-free,
    never fails the caller."""
    import json
    import pathlib
    try:
        cfg = _load(args)
        data = json.load(sys.stdin)
        ti = data.get("tool_input") or {}
        paths = [ti.get("file_path"), ti.get("notebook_path"), ti.get("path")]
        for e in ti.get("edits") or []:
            if isinstance(e, dict):
                paths.append(e.get("file_path"))
        watched = [r.path for r in cfg.roots]
        for p in filter(None, paths):
            rp = pathlib.Path(p).expanduser().resolve()
            if any(rp.is_relative_to(w) for w in watched):
                cfg.dirty_marker.parent.mkdir(parents=True, exist_ok=True)
                cfg.dirty_marker.touch()
                return 0
    except Exception:  # noqa: BLE001 — a hook must never break the tool call
        pass
    return 0


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="shelfmark",
        description="Local, privacy-first document catalogue + MCP server.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config", help="path to config.toml "
                    "(default: $SHELFMARK_CONFIG or ~/.config/shelfmark/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write a starter config.toml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("config", help="show the resolved configuration")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("build", help="walk the tree and update the catalogue")
    p.add_argument("--rebuild", action="store_true",
                   help="drop and rebuild the catalogue tables (forces a "
                        "full re-walk; on cloud-synced trees this pulls "
                        "every evicted file back down)")
    p.add_argument("--no-hash", action="store_true",
                   help="skip content hashing (much faster first pass)")
    p.add_argument("--stats", action="store_true")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("rights",
                       help="re-derive rights/confidentiality from config "
                            "rules (runs automatically inside refresh)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_rights)

    p = sub.add_parser("refresh",
                       help="build + rights + prune + assertions — the "
                            "normal way to keep the index current")
    p.add_argument("--if-needed", action="store_true",
                   help="only when a write landed in the tree or the index "
                        "is older than refresh.max_age_seconds")
    p.add_argument("--force", action="store_true",
                   help="accept a short walk and prune past the ceiling — "
                        "use after confirming the missing files really are "
                        "gone, not just unreadable")
    p.set_defaults(fn=cmd_refresh)

    p = sub.add_parser("review",
                       help="answer a few questions about your folders so "
                            "rights/confidentiality stop being UNKNOWN")
    p.add_argument("--apply", action="store_true",
                   help="write the answers into config.toml (default: dry run)")
    p.add_argument("--limit", type=int, default=12,
                   help="how many subtrees to ask about (default 12)")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("reclassify",
                       help="re-apply classification rules to catalogued "
                            "rows after a rule edit (no filesystem walk)")
    p.add_argument("what", choices=["doc-types", "facets", "all"])
    p.add_argument("--apply", action="store_true",
                   help="write changes (default is a dry run)")
    p.set_defaults(fn=cmd_reclassify)

    p = sub.add_parser("hash",
                       help="backfill sha256 for rows that never got hashed "
                            "(reads file contents — run on demand)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_hash)

    p = sub.add_parser("stats", help="corpus census from the catalogue")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("serve", help="run the MCP server (stdio)")
    p.add_argument("--selftest", action="store_true",
                   help="exercise every tool once and exit")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("map",
                       help="regenerate MAP.md — whole-corpus overview "
                            "(written next to the DB)")
    p.add_argument("--out", help="write somewhere else")
    p.add_argument("--stdout", action="store_true")
    p.set_defaults(fn=cmd_map)

    p = sub.add_parser("cards",
                       help="regenerate FOLDER_CARDS.md — per-subtree cards; "
                            "hand-written answers are carried over")
    p.add_argument("--out", help="read/write somewhere else")
    p.add_argument("--todo", action="store_true",
                   help="list cards still unanswered and exit")
    p.set_defaults(fn=cmd_cards)

    p = sub.add_parser("ingest-email",
                       help="ingest .pst/.msg archives under a folder "
                            "(requires: pip install 'shelfmark[email]')")
    p.add_argument("prefix",
                   help="folder under the primary root holding the archives")
    p.add_argument("--era", default="archive",
                   help="provenance label stored on each message")
    p.add_argument("--bodies", action="store_true",
                   help="also store message bodies (default: headers only)")
    p.set_defaults(fn=cmd_ingest_email)

    p = sub.add_parser("dedupe-emails",
                       help="collapse messages ingested from more than one "
                            "archive file (content-identity)")
    p.add_argument("--apply", action="store_true",
                   help="write changes (default is a dry run; takes a backup)")
    p.set_defaults(fn=cmd_dedupe_emails)

    p = sub.add_parser("mark-dirty",
                       help="hook helper: reads a tool payload on stdin, "
                            "drops the dirty marker when a write touched an "
                            "indexed root")
    p.set_defaults(fn=cmd_mark_dirty)

    args = ap.parse_args(argv)
    raise SystemExit(args.fn(args))


if __name__ == "__main__":
    main()
