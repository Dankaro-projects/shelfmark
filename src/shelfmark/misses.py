"""Record searches that found nothing, so the roadmap runs on evidence.

The README refuses embeddings and content extraction, and says the way to
revisit that is to "use it, note what you couldn't find, and let real misses
decide". Nothing collected the misses, so the decision had no data behind
it and would eventually get made on a competitor's feature list instead.

Local only. Misses land in a capped file beside the catalogue and are never
sent anywhere; the MCP database is opened read-only and stays that way.

The question this has to answer is narrow: **could metadata search ever
have found it?** A term that appears in no filename, path, author, title or
slide title in the whole corpus was unreachable however it was phrased, and
that is the shape content extraction fixes. A term that IS in the corpus
but still missed was a phrasing or filter problem, which is a different
repair. Counting misses without that split just proves people search.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}")

# Function words. These are not merely noise in the ranking -- they bias the
# verdict. A word like "did" or "said" will never appear in a filename, so
# left in it counts as "unreachable" and pushes the report toward "reopen
# content extraction" on the strength of grammar rather than evidence.
STOP = {
    # articles, conjunctions, prepositions
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "any", "all", "into", "over", "under", "per", "via", "but", "not", "nor",
    "out", "off", "than", "then", "there", "here", "some", "such", "each",
    # question words
    "how", "what", "when", "where", "which", "who", "whom", "why", "whose",
    # pronouns / possessives
    "our", "ours", "its", "you", "your", "yours", "his", "her", "hers",
    "their", "theirs", "them", "they", "she", "him", "its", "one", "ones",
    # auxiliaries and very common verbs
    "was", "were", "been", "being", "are", "have", "has", "had", "having",
    "can", "could", "will", "would", "shall", "should", "may", "might",
    "must", "did", "does", "done", "doing", "get", "got", "say", "said",
    "says", "make", "made", "take", "took", "give", "gave", "know", "knew",
    "about", "just", "also", "very", "more", "most", "much", "many",
    # Spanish equivalents (the built-in rules are bilingual)
    "des", "del", "las", "los", "una", "unos", "unas", "por", "con", "para",
    "que", "como", "cuando", "donde", "sobre", "entre", "desde", "hasta",
    "esta", "este", "esto", "esos", "esas", "fue", "son", "han", "hay",
}


def terms(query: str) -> list[str]:
    return [w.lower() for w in WORD.findall(query or "")
            if w.lower() not in STOP]


def record(cfg: Config, query: str, filters: dict | None = None,
           stale: bool = False) -> None:
    """Append one miss. Never raises: a failure to log must not fail a search."""
    if not cfg.misses_enabled or not (query or "").strip():
        return
    try:
        line = json.dumps({
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "query": query,
            "filters": {k: v for k, v in (filters or {}).items() if v},
            # A miss against a stale index is not evidence about coverage.
            "stale": bool(stale),
        }, ensure_ascii=False)
        p = cfg.miss_log
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        _trim(p, cfg.misses_keep)
    except OSError:
        pass


def _trim(p: Path, keep: int) -> None:
    """Cap the file, amortised: rewrite only when it has drifted well past."""
    try:
        if keep <= 0:
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > keep * 3 // 2:
            p.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load(cfg: Config) -> list[dict]:
    try:
        raw = cfg.miss_log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in raw:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _in_corpus(con, term: str) -> bool:
    """Does the term appear anywhere in the indexed metadata?

    Asked of files_fts, which is the same index search_docs uses — so this
    answers "was it reachable", not "is it on disk somewhere". RESTRICTED
    rows are absent from that index by design, so sealed material cannot
    leak in through this check either."""
    try:
        row = con.execute(
            "SELECT 1 FROM files_fts WHERE files_fts MATCH ? LIMIT 1",
            (f'"{term}"',)).fetchone()
        return row is not None
    except Exception:                                # noqa: BLE001
        return False


def report(cfg: Config, limit: int = 15) -> str:
    import sqlite3

    rows = load(cfg)
    if not rows:
        if not cfg.misses_enabled:
            return ("Miss logging is off ([misses] enabled = false), so there "
                    "is nothing to report.")
        return ("No misses recorded yet — every search so far returned "
                "something. Nothing to decide on.")

    fresh = [r for r in rows if not r.get("stale")]
    stale_n = len(rows) - len(fresh)
    span = f"{rows[0]['at'][:10]} → {rows[-1]['at'][:10]}"

    out = [f"{len(rows):,} searches returned nothing   ({span})"]
    if stale_n:
        out.append(f"  {stale_n} of them ran against a stale index and are "
                   f"excluded below — a miss you caused by not refreshing "
                   f"says nothing about coverage.")
    if not fresh:
        return "\n".join(out)

    freq: dict[str, int] = {}
    for r in fresh:
        for t in set(terms(r["query"])):
            freq[t] = freq.get(t, 0) + 1

    con = sqlite3.connect(f"file:{cfg.db}?mode=ro", uri=True)
    try:
        unreachable = {t: n for t, n in freq.items() if not _in_corpus(con, t)}
    finally:
        con.close()

    out += ["", "Most-missed terms:"]
    for t, n in sorted(freq.items(), key=lambda kv: -kv[1])[:limit]:
        mark = "  (nowhere in your metadata)" if t in unreachable else ""
        out.append(f"  {n:>4}  {t}{mark}")

    total, un = len(freq), len(unreachable)
    pct = un * 100 // total if total else 0
    out += ["", f"{un} of {total} distinct terms ({pct}%) appear nowhere in "
                f"your filenames,",
            "paths, authors, titles or slide titles."]

    # The verdict is the point. Thresholds are deliberately coarse: this is
    # meant to say "you now have evidence" or "you do not yet", not to
    # pretend a percentage settles a design question.
    out.append("")
    if len(fresh) < 20:
        out.append("Too few misses to conclude anything yet. Keep using it.")
    elif pct >= 50:
        out.append("These are mostly things metadata search could NEVER have "
                   "found, however phrased.")
        out.append("That is the pattern the README says should reopen content "
                   "extraction — not embeddings.")
    elif pct >= 20:
        out.append("A mixed picture: some genuine coverage gaps, but most "
                   "misses were reachable material")
        out.append("that the query or filters did not reach. Worth reading the "
                   "list before changing anything.")
    else:
        out.append("Almost everything missed IS in the catalogue, so these were "
                   "phrasing and filter")
        out.append("problems, not coverage gaps. Content extraction would not "
                   "have helped.")
    return "\n".join(out)
