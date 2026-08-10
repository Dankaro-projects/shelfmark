"""shelfmark command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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
# Path-prefix rules producing two INDEPENDENT axes:
#   rights       = may I REUSE it (OWN / REFERENCE / RESTRICTED)
#   confidential = may it LEAVE (a deck you authored for a client is OWN
#                  and confidential at the same time)
# FIRST MATCH WINS, in exactly the order the lists appear below. The order
# is engine-owned, not configurable; `shelfmark config` prints it together
# with the number of files each rule currently claims. Two things outrank
# every list here: [privacy] patterns (file becomes RESTRICTED), then an
# [authors].client match on the author fields or the path (REFERENCE,
# confidential). Several outcomes below also depend on whether an
# [authors].own or .tool regex matched the file's author ("authored by
# us") — that is why these cannot be a flat prefix -> value table.
own_confidential_prefixes = []    # -> OWN, must not leave (e.g. invoices).
                                  #    Checked BEFORE own_prefixes so it may
                                  #    name a subtree nested inside one.
own_prefixes = []                 # -> OWN, may leave
reference_prefixes = []           # -> REFERENCE, may leave
neutral_prefixes = []             # scratch/captures -> OWN if authored by
                                  #    us, else UNKNOWN (not work product)
client_roots = []                 # engagement trees -> always confidential;
                                  #    OWN if authored by us (your method on
                                  #    their engagement), else REFERENCE
work_under_personal = []          # work misfiled under a personal root —
                                  #    same outcomes as client_roots
personal_roots = []               # personal admin -> confidential; OWN if
                                  #    authored by us, else RESTRICTED (held
                                  #    out of the work-facing index)
# A file no rule claims falls back to: OWN if authored by us, REFERENCE if
# it carries any other author, else UNKNOWN — and UNKNOWN is held back from
# shareable answers until reviewed (never-reviewed is not cleared).

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

[misses]
# Searches that found nothing are logged locally so you can tell a coverage
# gap from a phrasing problem — see `shelfmark misses`. Never sent anywhere.
enabled = true
keep = 500                   # ring size; older entries are dropped

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


# Extensions that mean "a person filed a document here" when init probes a
# candidate root. Deliberately narrower than DEFAULT_EXT_FALLBACK: code,
# config, fonts, archives and video are what makes a SOURCE tree score like
# a corpus, which is the mistake this probe exists to catch.
_PROBE_DOC_TYPES = frozenset({
    "deck", "document", "spreadsheet", "pdf", "note", "email", "image",
    "vector", "diagram",
})


def _count_documents(root, doc_exts, skip_dirs, depth, budget) -> int:
    """Stat-only document census of one tree. Depth-capped, skip-pruned,
    symlink-refusing (the roots are the trust boundary from the very first
    command), permission-tolerant: an unreadable subtree counts zero
    rather than failing init."""
    n = 0
    try:
        with os.scandir(root) as entries:
            for e in entries:
                if budget[0] <= 0:
                    return n
                budget[0] -= 1
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if depth > 0 and e.name not in skip_dirs \
                                and not e.name.startswith("."):
                            n += _count_documents(e.path, doc_exts, skip_dirs,
                                                  depth - 1, budget)
                    elif os.path.splitext(e.name)[1].lower() in doc_exts:
                        n += 1
                except OSError:
                    continue
    except OSError:
        pass
    return n


def _set_primary_root(cfg_path, chosen) -> str:
    """Rewrite the just-written template's primary root to `chosen`.

    Only ever called on a file this same command wrote seconds ago, so the
    template literal is guaranteed present. Written ~-relative when the
    folder is under home (readable, and survives a home rename); escaped
    via json.dumps, which is valid TOML basic-string escaping."""
    home = Path.home()
    try:
        display = "~/" + chosen.relative_to(home).as_posix()
    except ValueError:
        display = str(chosen)
    # utf-8 on both sides: config.load reads utf-8, and the locale default
    # this replaces is cp1252 on Windows -- a config written one way and
    # read the other garbles any non-ASCII folder name.
    text = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        text.replace('path = "~/Documents"', f"path = {json.dumps(display)}",
                     1), encoding="utf-8")
    return display


def _probe_written_root(cfg_path, ask=None) -> None:
    """Look at the root the config just named; when the guess missed, offer
    the folders that look like the real corpus and let ONE answer fix it.

    init writes `path = "~/Documents"` unconditionally, and on plenty of
    machines that folder is empty or absent — after which the first
    refresh succeeds over nothing. The sweep is evidence the engine
    already has, so it proposes (counts shown, best candidate as the
    default); the OPERATOR decides, and the decision lands in config.toml
    where editing a file reverses it. Non-interactive runs (no TTY) keep
    the warning-only behaviour — a hook must never hang on a prompt.
    `ask(prompt, default) -> str` is injected so the flow is testable
    without a terminal, mirroring review.run()."""
    try:
        c = config_mod.load(cfg_path)
    except ConfigError:
        return                    # a hand-edited config is not our guess
    doc_exts = {e for e, t in c.ext_fallback.items() if t in _PROBE_DOC_TYPES}
    root = c.primary_root.path
    budget = [20000]              # entries statted, shared across the sweep
    n = _count_documents(root, doc_exts, c.skip_dirs, 4, budget) \
        if root.is_dir() else None
    if n:
        return                    # the default landed; nothing to say
    if n is None:
        print(f"\nWARNING: {root} does not exist on this machine.",
              file=sys.stderr)
    else:
        print(f"\nWARNING: {root} exists but holds no document files — "
              f"a refresh now would build an empty catalogue.",
              file=sys.stderr)
    home = Path.home()
    candidates = []
    try:
        with os.scandir(home) as entries:
            for e in entries:
                try:
                    if (e.is_dir(follow_symlinks=False)
                            and not e.name.startswith(".")
                            and e.name not in c.skip_dirs
                            and Path(e.path) != root):
                        k = _count_documents(e.path, doc_exts, c.skip_dirs,
                                             3, budget)
                        if k:
                            candidates.append((k, e.name))
                except OSError:
                    continue
    except OSError:
        pass
    candidates = sorted(candidates, reverse=True)[:6]

    interactive = ask is not None or sys.stdin.isatty()
    if not candidates or not interactive:
        for k, name in candidates:
            print(f"  candidate: {home / name}  (~{k} document files)",
                  file=sys.stderr)
        print(f"  Point [[roots]] in {cfg_path} at the folder(s) that hold "
              f"your documents.", file=sys.stderr)
        return

    print("\nThese folders do hold documents:", file=sys.stderr)
    for i, (k, name) in enumerate(candidates, 1):
        unit = "document file" if k == 1 else "document files"
        print(f"  {i}. {home / name}  (~{k} {unit})", file=sys.stderr)
    ask = ask or (lambda prompt, default: input(prompt) or default)
    try:
        got = ask(f"Index which folder? [1] — a number, a path, or 'k' to "
                  f"keep {root}: ", "1").strip()
    except (EOFError, KeyboardInterrupt):
        # isatty() said interactive, but the read still hit end-of-input —
        # a pty with nothing on stdin, which is how CI and agent shells run
        # this. The config was already written, so an uncaught EOFError left
        # a config behind AND exited non-zero: the install looked failed but
        # was half-done, and re-running hit "config already exists".
        print(f"\n  No answer read — kept {root}.", file=sys.stderr)
        print(f"  Set [[roots]] in {cfg_path} to the folder you want indexed.",
              file=sys.stderr)
        return

    chosen = None
    if got.lower() in ("k", "keep"):
        pass
    elif got.isdigit() and 1 <= int(got) <= len(candidates):
        chosen = home / candidates[int(got) - 1][1]
    else:
        p = Path(got).expanduser()
        if p.is_dir():
            chosen = p
        else:
            print(f"  {got!r} is not a folder here — kept {root}.",
                  file=sys.stderr)
    if chosen is None:
        print(f"  Kept {root}; edit {cfg_path} to change it.",
              file=sys.stderr)
        return
    display = _set_primary_root(cfg_path, chosen)
    print(f"  Root set to {display}. Edit {cfg_path} any time to change it.",
          file=sys.stderr)


def cmd_init(args) -> int:
    path = config_mod.resolve_config_path(args.config)
    if path.exists() and not args.force:
        print(f"Config already exists at {path} (use --force to overwrite).")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    print(f"Wrote {path}")
    _probe_written_root(path)
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

    # The seven [rights] lists are evaluated in an engine-owned order that
    # used to be discoverable only by reading rights.derive(). Print the
    # order, and — when there is a catalogue — how many files each rule
    # currently claims, attributed by the SAME function that classifies
    # them, so this table can never drift from behaviour.
    from collections import Counter

    from . import rights as rights_mod
    claims: Counter = Counter()
    have_db = cfg.db.exists()
    if have_db:
        import sqlite3
        try:
            con = sqlite3.connect(cfg.ro_uri, uri=True)
            try:
                for p, a, la in con.execute(
                        "SELECT path, author, last_author FROM files"):
                    claims[rights_mod._derive_explained(p, a, la, cfg)[3]] += 1
            finally:
                con.close()
        except sqlite3.Error:
            have_db = False
    print()
    print("rights precedence (first match wins"
          + ("; files each rule claims now):" if have_db else "):"))
    for label in rights_mod.PRECEDENCE:
        if have_db:
            print(f"  {claims.get(label, 0):>7,}  {label}")
        else:
            print(f"           {label}")
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


def cmd_misses(args) -> int:
    from . import misses
    cfg = _load(args)
    if args.clear:
        try:
            cfg.miss_log.unlink()
            print("Miss log cleared.")
        except FileNotFoundError:
            print("No miss log to clear.")
        return 0
    print(misses.report(cfg, limit=args.limit))
    return 0


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
    from . import catalog, freshness
    cfg = _load(args)
    if not cfg.db.exists():
        print(f"No catalogue at {cfg.db} — run `shelfmark refresh` first.")
        return 1
    con = sqlite3.connect(cfg.db)
    con.row_factory = sqlite3.Row
    try:
        print(catalog.stats(con))
        # The census above reads the catalogue; this line checks it AGAINST
        # THE DISK. It used to exist only behind the MCP server, which left
        # the CLI structurally blind to drift — an operator who never ran an
        # agent had no way to learn the index had stopped matching reality.
        print()
        print(freshness.freshness_line(con, cfg))
    finally:
        con.close()
    return 0


def cmd_doctor(args) -> int:
    from . import doctor
    return doctor.run(_load(args))


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


def cmd_hook(args) -> int:
    """Claude Code hook adapter: refresh, then speak ONLY when something is
    wrong — as hook JSON on stdout, the one channel a hook actually has.

    This exists because the README used to teach
    `shelfmark refresh --if-needed >/dev/null 2>&1` — and since every
    refusal and failure speaks on stderr, that redirect discarded the only
    delivery of the news. A catalogue stayed silently wrong for four days
    behind exactly that line. The product owns the hook now, so nobody
    hand-rolls the silencing again.

    session-start: full refresh, then the freshness line — the same check
    the MCP server runs — delivered to the operator (systemMessage) and the
    agent (additionalContext) when it is not a plain tick.
    stop: refresh --if-needed, and report only a non-ok status. No walk —
    this runs at every turn end and must stay near-free.

    Always exits 0: a hook must never break the session, and shelfmark
    being absent or unconfigured on a machine is normal."""
    import io
    import json
    import sqlite3
    from contextlib import redirect_stderr, redirect_stdout

    def say(payload: dict) -> None:
        print(json.dumps(payload, ensure_ascii=False))

    # Not _load(): that raises SystemExit(2) on a config error, which would
    # escape `except Exception` and break the exit-0 contract.
    try:
        cfg = config_mod.load(getattr(args, "config", None))
    except Exception as exc:                      # noqa: BLE001
        say({"systemMessage": f"shelfmark: cannot load config — {exc}"})
        return 0

    from . import freshness, refresh
    quiet = io.StringIO()
    try:
        with redirect_stderr(quiet), redirect_stdout(quiet):
            if args.event == "stop":
                refresh.run_if_needed(cfg)
            else:
                refresh.run(cfg)
    except Exception as exc:                      # noqa: BLE001
        say({"systemMessage":
             f"shelfmark: refresh raised {type(exc).__name__}: {exc}"})
        return 0

    if args.event == "stop":
        try:
            st = json.loads(cfg.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if st.get("state") != "ok":
            n = int(st.get("consecutive_failures") or 0)
            streak = (f" ({n} consecutive runs since "
                      f"{st.get('failing_since', '?')})" if n >= 2 else "")
            say({"systemMessage":
                 f"shelfmark: {st.get('state')} — "
                 f"{st.get('detail', '?')}{streak}"})
        return 0

    if not cfg.db.exists():
        say({"systemMessage": f"shelfmark: no catalogue at {cfg.db} — "
                              f"run `shelfmark refresh`"})
        return 0
    con = sqlite3.connect(cfg.db)
    con.row_factory = sqlite3.Row
    try:
        line = freshness.freshness_line(con, cfg)
    except Exception as exc:                      # noqa: BLE001
        line = f"⚠ freshness check failed: {exc}"
    finally:
        con.close()
    if not line.startswith("✓"):
        say({"systemMessage": line,
             "hookSpecificOutput": {
                 "hookEventName": "SessionStart",
                 "additionalContext": f"shelfmark catalogue status: {line}"}})
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


def _survive_legacy_console() -> None:
    """Never let a filename crash the command that reports it.

    Windows' default ANSI codepage is still 1252 outside the console, so a
    redirected `shelfmark stats > out.txt` encodes with cp1252 and raises
    UnicodeEncodeError on the first character the codepage lacks. One file is
    enough: a name stored decomposed (NFD) carries U+0301 COMBINING ACUTE
    ACCENT, which cp1252 cannot represent even though the precomposed "á"
    can. The traceback replaced the whole report.

    Only the error handler is changed, never the encoding. Re-encoding the
    stream as UTF-8 would hand mojibake to a consumer that asked for cp1252;
    backslashreplace keeps the output readable, keeps it honest about what it
    could not encode, and loses nothing a reader needs. Paths are NOT
    normalised to NFC anywhere: the catalogue key has to reopen the file, and
    on NTFS the name is whatever bytes created it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass                      # not a reconfigurable text stream


def main(argv=None) -> None:
    _survive_legacy_console()
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

    p = sub.add_parser("doctor", help="check the setup for the problems that "
                                      "fail silently")
    p.set_defaults(fn=cmd_doctor)

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

    p = sub.add_parser("misses",
                       help="what searches found nothing — the evidence for "
                            "whether content extraction is worth building")
    p.add_argument("--clear", action="store_true", help="delete the miss log")
    p.add_argument("--limit", type=int, default=15,
                   help="how many terms to list (default 15)")
    p.set_defaults(fn=cmd_misses)

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

    p = sub.add_parser("hook",
                       help="Claude Code hook adapter: refresh, then speak "
                            "only when the catalogue has bad news (hook "
                            "JSON on stdout; always exits 0)")
    p.add_argument("event", choices=["session-start", "stop"])
    p.set_defaults(fn=cmd_hook)

    args = ap.parse_args(argv)
    raise SystemExit(args.fn(args))


if __name__ == "__main__":
    main()
