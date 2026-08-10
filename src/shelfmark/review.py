"""Turn the rights model from a TOML schema into a short list of questions.

Most files carry no OOXML author, so authorship classifies nothing for them
and path rules are the only mechanism that can -- yet they ship empty.
Inference cannot fill them in: authorship evidence is too thin to justify a
rule over a whole subtree, so this asks instead, biggest win first, and
stops whenever the operator does.

Answers become path prefixes in config, never rights values in the data.
Governance stays declared, and any answer is undone by editing the config
and re-running `shelfmark rights` -- no walk, nothing lost.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

# Generator names that are not people -- never offered as "is this you?".
TOOL_AUTHORS = re.compile(
    r"pptxgenjs|python-pptx|openpyxl|pandoc|libreoffice|microsoft|word|excel"
    r"|powerpoint|acrobat|canva|gamma|un-?named|authorised user|unknown",
    re.I)

# What each answer means in config terms. The two axes stay separate.
CHOICES: list[tuple[str, str, str]] = [
    ("own", "own_prefixes",
     "mine, and it may leave (my own method, templates, writing)"),
    ("own-private", "own_confidential_prefixes",
     "mine, but it never leaves (invoices, internal finance)"),
    ("reference", "reference_prefixes",
     "someone else's material I may reference (reports, papers, decks I was sent)"),
    ("client", "client_roots",
     "an engagement tree — confidential by default, sub-folders are clients"),
    ("personal", "personal_roots",
     "personal admin, held out of the work index"),
    ("scratch", "neutral_prefixes",
     "captures, scratch, downloads — not work product either way"),
    ("skip", "",
     "leave it unclassified for now"),
]
BY_KEY = {k: col for k, col, _ in CHOICES}

MIN_FILES = 5              # below this, a question costs more than it settles
MIN_AUTHORED = 5           # fewer authored files than this proves nothing
MIN_AUTHORED_SHARE = 0.10  # ...and neither does a handful in a huge subtree
DOMINANCE = 4              # how lopsided authorship must be to seed a default


@dataclass
class Question:
    prefix: str
    unclassified: int
    total: int
    doc_types: list[tuple[str, int]] = field(default_factory=list)
    context: str | None = None
    authors: list[tuple[str, int]] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    suggestion: str | None = None
    why: str | None = None


@dataclass
class Plan:
    questions: list[Question]
    author_hint: tuple[str, int] | None      # the likely "you", and its count
    unclassified: int
    total: int


def _rows(con, sql, *params):
    return con.execute(sql, params).fetchall()


def _candidate_prefixes(con) -> list[str]:
    """Roots and second-level folders that still hold unclassified rows.

    Depth is capped: the point is to settle a lot with one answer. Anything
    finer is a config edit, not a fortieth question."""
    out = []
    for (root,) in _rows(con, "SELECT DISTINCT root FROM files"
                              " WHERE rights='UNKNOWN'"):
        if root:
            out.append(root)
    for (p,) in _rows(con, """
            SELECT DISTINCT substr(path, 1,
                     instr(substr(path, instr(path,'/')+1), '/') + instr(path,'/') - 1)
            FROM files WHERE rights='UNKNOWN' AND path LIKE '%/%/%'"""):
        if p and p.count("/") == 1 and len(p.split("/")[-1]):
            out.append(p)
    return out


def _evidence(con, prefix: str, own_re) -> Question:
    like = prefix + "/%"
    total, = _rows(con, "SELECT COUNT(*) FROM files WHERE path LIKE ?", like)[0]
    unc, = _rows(con, "SELECT COUNT(*) FROM files WHERE path LIKE ?"
                      " AND rights='UNKNOWN'", like)[0]
    q = Question(prefix=prefix, unclassified=unc, total=total)
    q.doc_types = [(t or "—", n) for t, n in _rows(con,
        "SELECT doc_type, COUNT(*) n FROM files WHERE path LIKE ?"
        " GROUP BY 1 ORDER BY n DESC LIMIT 4", like)]
    ctx = _rows(con, "SELECT context_type, COUNT(*) n FROM files WHERE path LIKE ?"
                     " AND context_type IS NOT NULL GROUP BY 1 ORDER BY n DESC"
                     " LIMIT 1", like)
    q.context = ctx[0][0] if ctx else None
    q.authors = [(a, n) for a, n in _rows(con,
        "SELECT author, COUNT(*) n FROM files WHERE path LIKE ?"
        " AND author IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 4", like)]
    q.samples = [f for (f,) in _rows(con,
        "SELECT filename FROM files WHERE path LIKE ? AND rights='UNKNOWN'"
        " ORDER BY bytes DESC LIMIT 3", like)]

    # Seed a default only where authorship is both substantial and lopsided.
    # The share floor matters: nine authored files in a folder of 350 would
    # otherwise default to "reference" on 2% evidence, and a wrong default
    # gets pressed through by Enter.
    people = [(a, n) for a, n in q.authors if not TOOL_AUTHORS.search(a)]
    authored = sum(n for _, n in people)
    if authored >= MIN_AUTHORED and total and authored / total >= MIN_AUTHORED_SHARE:
        mine = sum(n for a, n in people if own_re and own_re.search(a))
        others = authored - mine
        share = f"{authored} of {total} files here carry an author"
        if mine >= others * DOMINANCE and mine:
            q.suggestion = "own"
            q.why = f"{share}, and {mine} of those are yours"
        elif others >= mine * DOMINANCE and others:
            names = ", ".join(a for a, _ in people[:2])
            q.suggestion = "reference"
            q.why = f"{share}, mostly other people ({names})"
    return q


def plan(cfg: Config, limit: int = 12, own_author: str | None = None) -> Plan:
    """Rank the questions worth asking. Pure: no prompting, no writing.

    `own_author` overrides the config's own-author pattern, which is empty
    at onboarding -- until it is known, nothing can be recognised as the
    operator's own."""
    own_re = cfg.own_author_re
    if own_author:
        own_re = re.compile(re.escape(own_author), re.I)
    con = sqlite3.connect(cfg.ro_uri, uri=True)
    try:
        total, = _rows(con, "SELECT COUNT(*) FROM files")[0]
        unclassified, = _rows(con, "SELECT COUNT(*) FROM files"
                                   " WHERE rights='UNKNOWN'")[0]

        hint = None
        for a, n in _rows(con, "SELECT author, COUNT(*) n FROM files"
                               " WHERE author IS NOT NULL GROUP BY 1"
                               " ORDER BY n DESC LIMIT 8"):
            if not TOOL_AUTHORS.search(a):
                hint = (a, n)
                break

        cands = [_evidence(con, p, own_re)
                 for p in set(_candidate_prefixes(con))]
    finally:
        con.close()

    # Greedy and non-overlapping: answering a parent settles its children, so
    # a child must not then be asked about as well.
    chosen: list[Question] = []
    taken: list[str] = []
    for q in sorted(cands, key=lambda x: -x.unclassified):
        if q.unclassified < MIN_FILES:
            continue
        if any(q.prefix == t or q.prefix.startswith(t + "/") for t in taken):
            continue
        taken.append(q.prefix)
        chosen.append(q)
        if len(chosen) >= limit:
            break
    return Plan(questions=chosen, author_hint=hint,
                unclassified=unclassified, total=total)


# --------------------------------------------------------------- config write

_ARRAY = r"^(?P<lead>[ \t]*{key}[ \t]*=[ \t]*)\[(?P<body>[^\[\]\n]*)\](?P<tail>.*)$"


def _quote(v: str, literal: bool) -> str:
    """TOML-quote a value.

    Regexes go in literal strings: re.escape() emits backslashes (a space
    becomes '\\ '), and in a basic string that is an invalid escape which
    makes the whole file unparseable. Values containing a single quote
    cannot be literal, so those fall back to escaped basic strings."""
    if literal and "'" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _merge_array(text: str, section: str, key: str, add: list[str],
                 literal: bool = False) -> tuple[str, bool]:
    """Add values to a TOML array inside `section`, creating what is missing.

    Line-based, not parse-and-dump: a round trip would drop every comment in
    the config. Handles key-present, section-present-key-absent, and
    neither. A key in any other form (multi-line array) is left alone and
    reported rather than rewritten half-understood.
    """
    lines = text.splitlines()
    trail = "\n" if text.endswith("\n") else ""
    pat = re.compile(_ARRAY.format(key=re.escape(key)))
    header = f"[{section}]"

    start = end = None                 # bounds of the section's body
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if start is None and s == header:
                start = i + 1
            elif start is not None and end is None:
                end = i
    if start is not None and end is None:
        end = len(lines)

    if start is None:                              # no such section
        block = [""] if lines and lines[-1].strip() else []
        block += [header, f"{key} = [{', '.join(_quote(v, literal) for v in add)}]"]
        return "\n".join(lines + block) + trail, True

    for i in range(start, end):
        m = pat.match(lines[i])
        if m:
            have = [v.strip().strip("'\"") for v in m.group("body").split(",")
                    if v.strip()]
            merged = have + [v for v in add if v not in have]
            body = ", ".join(_quote(v, literal) for v in merged)
            lines[i] = f'{m.group("lead")}[{body}]{m.group("tail")}'
            return "\n".join(lines) + trail, True

    # Only safe if the key is genuinely absent: a duplicate key makes the
    # file unparseable.
    if re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=", "\n".join(lines[start:end]),
                 re.M):
        return text, False
    body = ", ".join(_quote(v, literal) for v in add)
    lines.insert(end, f"{key} = [{body}]")
    return "\n".join(lines) + trail, True


def apply_answers(cfg: Config, answers: dict[str, str],
                  own_author: str | None = None,
                  dry_run: bool = True) -> str:
    """Write the answers into config.toml as path prefixes.

    Backed up first, re-parsed after, restored if the result is not valid
    TOML."""
    out: list[str] = []
    by_key: dict[str, list[str]] = {}
    for prefix, choice in answers.items():
        col = BY_KEY.get(choice)
        if col:
            by_key.setdefault(col, []).append(prefix)

    if not by_key and not own_author:
        return "Nothing to write — every subtree was skipped."

    for col, prefixes in sorted(by_key.items()):
        for p in sorted(prefixes):
            out.append(f"  [rights].{col:27} += {p!r}")
    if own_author:
        out.append(f"  [authors].own{'':22} += {own_author!r}")

    if dry_run:
        out.append("\nDry run. Re-run with --apply to write, then "
                   "`shelfmark rights` re-derives (no walk).")
        return "\n".join(out)

    path = cfg.source
    if path is None or not Path(path).exists():
        return ("Cannot write: no config file was loaded. The lines above go "
                "under [rights] in your config.toml.")
    path = Path(path)
    backup = path.with_suffix(path.suffix + ".bak-review")
    shutil.copy2(path, backup)

    text = path.read_text(encoding="utf-8")
    unwritten: list[str] = []
    for col, prefixes in by_key.items():
        text, ok = _merge_array(text, "rights", col, prefixes)
        if not ok:
            unwritten.append(f'{col} = {prefixes}')
    if own_author:
        text, ok = _merge_array(text, "authors", "own",
                                [f"^{re.escape(own_author)}$"], literal=True)
        if not ok:
            unwritten.append(f'own = ["^{re.escape(own_author)}$"]')

    path.write_text(text, encoding="utf-8")
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        shutil.copy2(backup, path)
        return (f"Refused to write: the result was not valid TOML ({exc}). "
                f"Your config is unchanged.")

    out.append(f"\nwrote {path}  (backup: {backup.name})")
    if unwritten:
        out.append("\nThese keys are not single-line arrays in your config, so "
                   "they were left alone — add them by hand:")
        out += [f"  {u}" for u in unwritten]
    return "\n".join(out)


# ------------------------------------------------------------------ rendering

def render(q: Question, index: int, of: int) -> str:
    types = ", ".join(f"{t} {n}" for t, n in q.doc_types)
    lines = [
        "",
        f"[{index}/{of}] {q.prefix}",
        f"      {q.unclassified} unclassified of {q.total} files"
        + (f" · folder reads as: {q.context}" if q.context else ""),
        f"      is: {types}" if types else "",
    ]
    if q.authors:
        who = ", ".join(f"{a} ({n})" for a, n in q.authors)
        lines.append(f"      authored by: {who}")
    else:
        lines.append("      authored by: nothing here carries an author")
    if q.samples:
        lines.append(f"      e.g. {' · '.join(s[:38] for s in q.samples)}")
    if q.why:
        lines.append(f"      → suggests {q.suggestion}: {q.why}")
    return "\n".join(x for x in lines if x != "")


def menu() -> str:
    return "\n".join(f"      {k:12} {desc}" for k, _, desc in CHOICES)


# No more answers are coming: end of input, or Ctrl-C.
#
# Caught at the call sites rather than wrapped around `input`, because `ask`
# is injected — a guard living inside the default reader protects only the
# path the tests do not take, which is the wrong way round. Neither is an
# error: the flow already treats stopping early as legitimate ("Stop any
# time — every answer is kept"), so both route where `quit` goes rather than
# discarding a session of answers with a traceback.
_STOP = (EOFError, KeyboardInterrupt)


def run(cfg: Config, apply: bool = False, limit: int = 12,
        ask=None, say=print) -> int:
    """Ask, then write. `ask(prompt, default) -> str` is injected so the
    flow is testable without a terminal."""
    ask = ask or (lambda prompt, default: input(prompt) or default)
    if not cfg.db.exists():
        say("No catalogue yet — run `shelfmark refresh` first.")
        return 2
    p = plan(cfg, limit=limit)

    if not p.total:
        say("No catalogue yet — run `shelfmark refresh` first.")
        return 2

    answers: dict[str, str] = {}
    own_author = None

    # Asked before the subtree questions, and before giving up on having
    # any: who you are is worth recording even for a corpus already settled
    # by path rules.
    stopped = False
    if p.author_hint:
        name, n = p.author_hint
        try:
            got = ask(f"Most of the authored files here say '{name}' "
                      f"({n} files). Is that you? [Y/n] ", "y")
        except _STOP:
            # No answer read. "Most files say X, is that you?" defaulting to
            # yes on silence would write an ownership claim nobody made, and
            # ownership is the one thing inference may not decide.
            got, stopped = "n", True
        if got.strip().lower() in ("", "y", "yes"):
            own_author = name
            # Re-plan: until this is known, nothing reads as yours.
            p = plan(cfg, limit=limit, own_author=own_author)

    if not p.questions:
        say(f"Nothing left to review: {p.total:,} files, none unclassified in "
            f"a subtree of {MIN_FILES}+ files.")
        say("")
        say(apply_answers(cfg, {}, own_author=own_author, dry_run=not apply))
        return 0

    settles = sum(q.unclassified for q in p.questions)
    say(f"{p.unclassified:,} of {p.total:,} files are unclassified "
        f"({p.unclassified * 100 // p.total}%).")
    say(f"These {len(p.questions)} answers settle {settles:,} of them "
        f"({settles * 100 // max(p.unclassified, 1)}%). Stop any time — "
        f"every answer is kept.")
    say("\nAnswers:")
    say(menu())

    for i, q in enumerate(p.questions, 1):
        if stopped:
            break
        say(render(q, i, len(p.questions)))
        default = q.suggestion or "skip"
        try:
            got = ask(f"      answer [{default}]: ", default).strip() or default
        except _STOP:
            say("\nStopping here — answers so far are kept.")
            break
        if got in ("quit", "q"):
            say("\nStopping here — answers so far are kept.")
            break
        if got not in BY_KEY:
            say(f"      '{got}' is not one of the answers; skipping this one.")
            continue
        answers[q.prefix] = got

    say("")
    say(apply_answers(cfg, answers, own_author=own_author, dry_run=not apply))

    if apply and (answers or own_author):
        # Reload: the answers were written to the config FILE, and the Config
        # in hand still holds the empty prefixes it was built from. Deriving
        # rights from that would report success and change nothing.
        from . import config as config_mod, rights
        cfg = config_mod.load(cfg.source) if cfg.source else cfg
        rights.apply(cfg)
        con = sqlite3.connect(cfg.ro_uri, uri=True)
        try:
            now, = _rows(con, "SELECT COUNT(*) FROM files"
                              " WHERE rights='UNKNOWN'")[0]
        finally:
            con.close()
        say(f"\nrights re-derived: unclassified {p.unclassified:,} → {now:,} "
            f"({now * 100 // max(p.total, 1)}% of the corpus)")
    return 0
