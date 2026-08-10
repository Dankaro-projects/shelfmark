"""Generate FOLDER_CARDS.md — one card per subtree that earns one.

The catalogue can derive structure. It can never derive **intent**: is this
folder live or dead, whose is it, what happens next. That is the only thing
worth writing by hand, so this generates everything else and leaves exactly
four blank lines per card for a human.

Re-running is SAFE. Only the block between the GEN markers is rewritten;
hand-written answers are parsed out of the existing file and carried over.
Cards for subtrees that no longer qualify are kept at the bottom under
"Retired", never deleted — a hand-written line is more expensive than disk.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .config import Config

GEN_OPEN = "<!-- generated:do-not-edit -->"
GEN_CLOSE = "<!-- /generated -->"

# The four questions. Key -> prompt shown in the card.
QUESTIONS = [
    ("What", "one line — what this folder actually is"),
    ("Status", "live / dormant / dead / archive-only / reference-only"),
    ("Whose", "mine / company / client <name> / third-party"),
    ("Next", "the action, or `none`"),
]
UNANSWERED = "?"

# Filenames that jog no memory whatsoever. Every repo has them.
GENERIC = re.compile(
    r"^(readme|claude|cursor|agents|summary|notes?|todo|index|license|changelog|"
    r"contributing|requirements|package(-lock)?|tsconfig|robots(\s*\d+)?|"
    r"\.gitignore|setup|main|config|settings|untitled|document\d*|new\s+\w+)"
    r"(\s*\(?\d+\)?)?\.\w+$",
    re.I,
)


def human(n):
    n = n or 0
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f}{u}"
        n /= 1024


def subtree_key(path, root):
    rest = path[len(root) + 1:] if path.startswith(root + "/") else ""
    if "/" not in rest:
        return f"{root}/(loose files)"
    return f"{root}/{rest.split('/', 1)[0]}"


def collect(con):
    rows = con.execute(
        """SELECT path, root, bytes, doc_type, context_type, rights,
                  confidential, authored_date, btime, client, author,
                  filename, ext
           FROM files WHERE rights != 'RESTRICTED'"""
    ).fetchall()

    agg = defaultdict(lambda: {
        "n": 0, "bytes": 0, "own_open": 0, "conf": 0,
        "doc": defaultdict(int), "ctx": defaultdict(int),
        "clients": defaultdict(int), "authors": defaultdict(int),
        "years": [], "btimes": [], "notable": [],
    })
    for r in rows:
        root = r["root"] or "(none)"
        a = agg[subtree_key(r["path"], root)]
        a["n"] += 1
        a["bytes"] += r["bytes"] or 0
        if r["rights"] == "OWN" and not r["confidential"]:
            a["own_open"] += 1
        if r["confidential"]:
            a["conf"] += 1
        a["doc"][r["doc_type"] or "—"] += 1
        a["ctx"][r["context_type"] or "—"] += 1
        if r["client"]:
            a["clients"][r["client"]] += 1
        if r["author"]:
            a["authors"][r["author"]] += 1
        if r["authored_date"]:
            a["years"].append(r["authored_date"][:4])
        if r["btime"]:
            a["btimes"].append(r["btime"][:4])
        # memory joggers: named documents only. Generic repo furniture
        # (README.md, requirements.txt) jogs nothing, so it is dropped.
        if (r["doc_type"] in ("deck", "document", "report", "proposal", "model",
                              "spreadsheet", "contract", "note", "research")
                and not GENERIC.match(r["filename"] or "")):
            a["notable"].append((r["authored_date"] or "", r["filename"],
                                 r["doc_type"]))
    return agg


def top(d, k=4):
    return ", ".join(f"{a} {b}" for a, b in
                     sorted(d.items(), key=lambda kv: -kv[1])[:k]) or "—"


def gen_block(key, a):
    years = sorted(y for y in a["years"] if y)
    lines = [
        f"`{a['n']:,}` files · {human(a['bytes'])} · "
        f"**{a['own_open']:,} own+shareable** · {a['conf']:,} confidential",
    ]
    if years:
        # OOXML created date — true authorship.
        lines.append(f"authored {years[0]}–{years[-1]} "
                     f"(last **{years[-1]}**, {len(years)} dated)")
    else:
        # No OOXML metadata (code, PDFs, images). btime survives bulk
        # consolidations that destroy mtime, but it records COPY events —
        # a mass restore stamps thousands of files with one date — so
        # present it as "on disk since", never as authorship.
        bt = sorted(b for b in a["btimes"] if b)
        if bt:
            lines.append(f"no authorship metadata · on disk since "
                         f"{bt[0]}–{bt[-1]} <sub>(copy dates, not authorship)</sub>")
    lines += [
        "",
        f"- is (doc_type): {top(a['doc'])}",
        f"- for (context): {top(a['ctx'])}",
    ]
    if a["clients"]:
        lines.append(f"- client tags: {top(a['clients'])}")
    if a["authors"]:
        lines.append(f"- authors: {top(a['authors'], 3)}")

    # Two or fewer distinctive names is noise, not a jogger — show nothing.
    notable = sorted(a["notable"], key=lambda t: t[0], reverse=True)[:5]
    if len(notable) > 2:
        lines += ["", "<sub>named documents — memory joggers:</sub>"]
        for date, name, dt in notable:
            d = f"{date[:10]}  " if date else ""
            lines.append(f"<sub>· {d}{name}  ({dt})</sub>")
    return "\n".join(lines)


def parse_existing(path: Path):
    """Return {subtree_key: {question: answer}} from a previous run."""
    if not path.exists():
        return {}
    answers = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        h = re.match(r"^##\s+`?([^`]+?)`?\s*$", line)
        if h and not line.startswith("###"):
            current = h.group(1).strip()
            if current.lower().startswith(("retired", "how to use",
                                           "still unanswered")):
                current = None
            else:
                answers.setdefault(current, {})
            continue
        if current:
            m = re.match(r"^\*\*(\w+):\*\*\s*(.*)$", line)
            if m and m.group(1) in dict(QUESTIONS):
                answers[current][m.group(1)] = m.group(2).strip()
    return answers


def render_card(key, a, prior):
    out = [f"## `{key}`", "", GEN_OPEN, gen_block(key, a), GEN_CLOSE, ""]
    for q, hint in QUESTIONS:
        val = (prior.get(key, {}) or {}).get(q, "").strip()
        if not val:
            val = f"{UNANSWERED}  <!-- {hint} -->"
        out.append(f"**{q}:** {val}")
    out.append("")
    return "\n".join(out)


def todo(cfg: Config, out_path: Path | None = None) -> str:
    dest = out_path or cfg.db.parent / "FOLDER_CARDS.md"
    prior = parse_existing(dest)
    if not prior:
        return f"{dest} not found — run `shelfmark cards` first."
    open_cards = [k for k, v in prior.items()
                  if any(a.startswith(UNANSWERED) or not a
                         for a in v.values()) or len(v) < len(QUESTIONS)]
    done = len(prior) - len(open_cards)
    out = [f"{done}/{len(prior)} cards answered."]
    for k in open_cards:
        missing = [q for q, _ in QUESTIONS
                   if prior[k].get(q, "").startswith(UNANSWERED)
                   or not prior[k].get(q)]
        out.append(f"  {k}  (missing: {', '.join(missing)})")
    return "\n".join(out)


def write(cfg: Config, out_path: Path | None = None) -> str:
    dest = out_path or cfg.db.parent / "FOLDER_CARDS.md"
    con = sqlite3.connect(cfg.ro_uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        agg = collect(con)
    finally:
        con.close()

    qualifying = {k: a for k, a in agg.items()
                  if a["n"] >= cfg.cards_min_files
                  or a["own_open"] >= cfg.cards_min_open}
    ordered = sorted(qualifying.items(),
                     key=lambda kv: (-kv[1]["own_open"], -kv[1]["n"]))

    prior = parse_existing(dest)
    # Count cards with a real answer carried over, not merely cards that
    # existed.
    carried = sum(
        1 for k, v in prior.items()
        if k in qualifying and any(
            a and not a.startswith(UNANSWERED) for a in v.values())
    )

    doc = [
        "# Folder cards",
        "",
        "One card per subtree that earns one. The generated block is refreshed by",
        "`shelfmark cards`; **the four answers below it are yours** and are",
        "carried across regenerations. Answer them once and search stops being the",
        "only way to find anything.",
        "",
        f"Cards are ordered by own+shareable IP, densest first — answer from the top",
        f"and you cover the {sum(a['own_open'] for a in qualifying.values()):,} files",
        "that actually matter before the archive noise.",
        "",
        "Progress: `shelfmark cards --todo`",
        "",
        "---",
        "",
    ]
    for key, a in ordered:
        doc.append(render_card(key, a, prior))
        doc.append("---")
        doc.append("")

    retired = sorted(k for k in prior if k not in qualifying)
    if retired:
        doc += ["## Retired", "",
                "No longer meet the threshold. Answers kept — a hand-written line is",
                "more expensive than the disk it sits on.", ""]
        for k in retired:
            doc.append(f"### `{k}`")
            for q, _ in QUESTIONS:
                v = prior[k].get(q, "").strip()
                if v and not v.startswith(UNANSWERED):
                    doc.append(f"**{q}:** {v}")
            doc.append("")

    dest.write_text("\n".join(doc) + "\n", encoding="utf-8")
    out = [f"wrote {dest}",
           f"  {len(ordered)} cards "
           f"({sum(a['n'] for a in qualifying.values()):,} files, "
           f"{sum(a['own_open'] for a in qualifying.values()):,} own+shareable)"]
    if carried:
        out.append(f"  carried {carried} previously answered card(s)")
    if retired:
        out.append(f"  retired {len(retired)} card(s), answers preserved")
    return "\n".join(out)
