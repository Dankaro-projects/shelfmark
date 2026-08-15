"""shelfmark MCP server — makes the catalogue reachable from MCP clients.

Run standalone:
    shelfmark-mcp            (or: shelfmark serve)

Register with Claude Code:
    claude mcp add shelfmark -s user -- shelfmark-mcp

Governance is enforced HERE, not left to the caller:
  * rights='RESTRICTED' is NEVER returned by any tool. No flag overrides it.
  * Emails with sensitive=1 are never returned.
  * Email bodies are only returned when the caller explicitly asks
    (`include_body=True`) and are truncated.
  * The database is opened read-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import warnings
from datetime import datetime, timezone

# pydantic-settings emits an IncompleteFieldDefinitionWarning about mcp's
# own `lifespan` field, when the Settings model is built. Upstream and
# harmless, but for a stdio server it lands in the client's log on every
# startup, where it reads as a fault in this tool. Narrow filter: this one
# message, only around mcp's import and construction.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r".*incomplete definition.*")
    from mcp.server.fastmcp import FastMCP

from . import __version__
from . import config as config_mod
from . import freshness as freshness_mod
from .catalog import is_evicted
from .config import Config, extended

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r".*incomplete definition.*")
    mcp = FastMCP("shelfmark")
# FastMCP takes no version argument, and the lowlevel server it wraps
# defaults the handshake's serverInfo.version to the mcp PACKAGE's own —
# so every client displayed shelfmark as whatever mcp release it shipped
# with. Same class as the 0.2.0 --version lie, on the other entry point:
# the version is read from installed metadata, never left for a bystander
# to fill in. Verified against both declared mcp bounds; the mcp-range CI
# job asserts it stays true at the floor.
mcp._mcp_server.version = __version__

_CFG: Config | None = None


def cfg() -> Config:
    global _CFG
    if _CFG is None:
        _CFG = config_mod.load()
    return _CFG


def connect():
    con = sqlite3.connect(cfg().ro_uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def has_table(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,)).fetchone() is not None


# --------------------------------------------------------------------------
# query plumbing
# --------------------------------------------------------------------------

def fts_strict(raw):
    """Quote every bare token. Always a syntactically valid FTS5 query.

    Real corpora are full of identifiers — ACME-2026-014, Form 303 — whose
    hyphens and colons blow up the parser unquoted."""
    tokens = [t for t in re.split(r"\s+", (raw or "").strip()) if t]
    out = []
    for t in tokens:
        prefix = t.endswith("*")
        core = (t[:-1] if prefix else t).replace('"', "")
        if not core:
            continue
        out.append(f'"{core}"*' if prefix else f'"{core}"')
    return " ".join(out) or None


def fts_query(raw):
    """Make an arbitrary user string safe for FTS5 MATCH.

    Returns (preferred, strict). `preferred` keeps FTS operators when the
    caller clearly meant them (AND/OR/NOT/NEAR, quoted phrases, prefix *);
    `strict` is the always-parseable fallback. Two forms because the operator
    path hands the raw string straight to FTS5 — anything malformed reaches
    the parser and comes back as an engine error instead of an answer (a
    trailing quote returns "unterminated string"). Callers go through
    fetch_fts(), which falls back to `strict` on exactly that."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    strict = fts_strict(raw)
    if '"' in raw or re.search(r"\b(AND|OR|NOT|NEAR)\b", raw):
        return raw, strict
    return strict, strict


def fetch_fts(con, sql, count_sql, match, strict, params, limit):
    """Run an FTS search, retrying once with the fully-quoted query.

    Returns (rows, total, used_query, error)."""
    candidates = [m for m in (match, strict) if m]
    seen, tried = set(), []
    for m in candidates:
        if m not in seen:
            seen.add(m)
            tried.append(m)
    err = None
    for m in tried:
        try:
            rows = con.execute(sql, [m, *params, limit]).fetchall()
            total = con.execute(count_sql, [m, *params]).fetchone()[0]
            return rows, total, m, None
        except sqlite3.OperationalError as e:
            err = e
    return None, 0, None, f"Query error: {err}"


def bad_year_range(year_from, year_to):
    """An inverted range matches nothing — report the caller mistake as one
    instead of blaming the index with a plain 'No matches'."""
    if year_from and year_to and int(year_from) > int(year_to):
        return (f"Impossible year range: year_from={int(year_from)} is after "
                f"year_to={int(year_to)}, so nothing can match. Swap them.")
    return None


def undated_corpus(con, year_from, year_to):
    """A year filter over a corpus that carries no authored dates excludes
    every row, whatever the query — so 'No matches' is a statement about the
    filter, not about the corpus, and reads as the opposite of the truth.

    authored_date comes from OOXML metadata, which is only read when the file
    can be opened. A cloud-synced tree that is mostly evicted therefore dates
    almost nothing, and every year-filtered search returns empty while the
    same search without the filter returns hundreds. Left unexplained, the
    caller concludes the documents do not exist.

    Only reached when a search already came back empty, so the count costs
    nothing on the path that found something.
    """
    if not (year_from or year_to):
        return None
    dated, total = con.execute(
        "SELECT COUNT(authored_date), COUNT(*) FROM files").fetchone()
    if dated or not total:
        return None
    return (f"No file in this catalogue carries an authored date, so any "
            f"year filter excludes all {total} of them — this result says "
            f"nothing about what the corpus holds. Search again without "
            f"year_from/year_to.\n"
            f"Dates come from OOXML metadata, which is only read when a file "
            f"can be opened; a cloud-synced tree that is still evicted has "
            f"none to read.")


def clamp(limit, default=20):
    """Bound a caller-supplied limit. 0 and negatives mean 'unspecified' and
    fall back to the default — collapsing them to 1 returns a single row
    under a header that reads like a deliberate cut."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, cfg().max_limit)


# How far ahead REFRESH_STATUS.json may be timestamped before this process
# stops trusting its own arithmetic. Generous: a refresh writing on one
# machine and a server reading on another routinely differ by seconds, and
# calling that a broken clock would be noise.
CLOCK_SKEW_TOLERANCE_HOURS = 1.0


def index_warning() -> str | None:
    """One line of health, for the tools that are not corpus_stats.

    None when there is nothing to say -- a banner on every healthy call is
    noise. Cheap by construction: reads REFRESH_STATUS.json, never walks.
    The authoritative disk comparison stays in corpus_stats(). What is left
    here is what the background refresh cannot heal by itself: it never
    ran, it is failing, or it has stopped firing."""
    status_path = cfg().status_path
    if not status_path.exists():
        return ("⚠ this catalogue has never been refreshed — results are "
                "empty or stale. Run `shelfmark refresh`.")
    try:
        st = json.loads(status_path.read_text(encoding="utf-8"))
        n = int(st.get("consecutive_failures") or 0)
        streak = (f" — {n} consecutive runs since "
                  f"{st.get('failing_since', '?')}" if n >= 2 else "")
        if st.get("state") == "degraded":
            # Completed run, full walk — but the prune kept rows it could
            # not vouch for, and they answer queries. Not FAILED: rights
            # were re-applied and the run finished. Repeated on every call
            # until resolved, because news that stops being news is how
            # this once stayed quiet for four days.
            return (f"⚠ the catalogue is DEGRADED ({st.get('detail', '?')}"
                    f"{streak}) — it lists rows the last refresh could not "
                    f"verify on disk. Call corpus_stats() for the disk "
                    f"comparison.")
        if st.get("state") != "ok":
            return (f"⚠ the last refresh FAILED ({st.get('detail', '?')}"
                    f"{streak}) — answers below come from the previous "
                    f"snapshot.")
        ts = datetime.strptime(st["finished_utc"], "%Y-%m-%dT%H:%M:%SZ") \
                     .replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        # A future timestamp reads as negative age and would pass the
        # staleness check forever, disabling the one signal that the
        # refresher has stopped. Unverifiable is its own answer.
        if age_h < -CLOCK_SKEW_TOLERANCE_HOURS:
            return (f"⚠ REFRESH_STATUS.json is timestamped {-age_h:.1f} h in "
                    f"the future — this machine's clock disagrees with "
                    f"whatever wrote it, so index age cannot be judged.")
        if age_h > cfg().stale_after_hours:
            ago = (f"{age_h:.1f} h" if age_h < 48 else f"{age_h / 24:.1f} days")
            return (f"⚠ the index was last refreshed {ago} ago — anything "
                    f"changed since is missing. Call corpus_stats() for the "
                    f"disk comparison.")
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return f"⚠ REFRESH_STATUS.json is unreadable ({exc}) — freshness unknown."

    # A recent 'ok' status over an EMPTY catalogue is the one healthy-looking
    # state that still produces the confident wrong answer: every tool
    # replies "no matches" and the caller concludes the material does not
    # exist. One EXISTS probe (O(1), no walk) overrides the clean bill of
    # health. An email-only catalogue is legitimately file-empty, so both
    # tables have to be empty before this fires.
    try:
        con = connect()
        try:
            empty = not con.execute(
                "SELECT EXISTS(SELECT 1 FROM files)").fetchone()[0]
            if empty and has_table(con, "emails"):
                empty = not con.execute(
                    "SELECT EXISTS(SELECT 1 FROM emails)").fetchone()[0]
        finally:
            con.close()
        if empty:
            return ("⚠ the catalogue is EMPTY — the last refresh ran but "
                    "indexed nothing, so \"no matches\" means \"nothing "
                    "indexed\", not \"it does not exist\". The configured "
                    "root is wrong, unmounted, or holds no documents; call "
                    "corpus_stats() for which, and check [[roots]] in "
                    "config.toml.")
    except sqlite3.Error:
        pass                                      # no db yet — already warned
    return None


def with_warning(body: str) -> str:
    """Prefix a tool's answer with its health note, when there is one."""
    warn = index_warning()
    return f"{warn}\n\n{body}" if warn else body


def resolve_facet(con, column, value, table="files",
                  visible="rights != 'RESTRICTED'"):
    """Map a caller-supplied facet value onto one that exists in the catalogue.

    Returns (resolved_value, None) or (None, error_message). An unknown value
    must not fall through to "No matches" — that reads exactly like a genuine
    absence, so a wrong-case root looks identical to an empty corpus. Case is
    corrected silently; anything else is reported with the real values.

    `column`, `table` and `visible` are always our own literals, never caller
    input. `visible` keeps the suggestion list inside what the caller may
    see: never leak a RESTRICTED client name or a sensitive sender via a
    hint."""
    vals = [r[0] for r in con.execute(
        f"SELECT DISTINCT {column} FROM {table}"
        f" WHERE {visible} AND COALESCE({column},'') != ''"
        f" ORDER BY {column}")]
    if value in vals:
        return value, None
    folded = {v.lower(): v for v in vals}
    if value.lower() in folded:
        return folded[value.lower()], None
    near = [v for v in vals
            if value.lower() in v.lower() or v.lower() in value.lower()]
    if near:
        return None, (f"Unknown {column} {value!r}. Did you mean: "
                      f"{', '.join(near[:8])}?")
    return None, (f"Unknown {column} {value!r} — nothing in the catalogue carries "
                  f"it, so this is a bad filter, not an empty result. "
                  f"Known values: {', '.join(vals[:25])}"
                  + (" …" if len(vals) > 25 else ""))


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

@mcp.tool()
def search_docs(
    query: str,
    root: str = "",
    client: str = "",
    doc_type: str = "",
    context_type: str = "",
    shareable_only: bool = False,
    own_only: bool = False,
    year_from: int = 0,
    year_to: int = 0,
    path_contains: str = "",
    limit: int = 20,
) -> str:
    """Full-text search over the document catalogue.

    Searches file paths, filenames, authors, company, OOXML titles and slide
    titles. This index holds METADATA ONLY — document body text is not
    indexed (emails are the exception, see search_emails). So query the way
    a filename or a slide title would read, not the way a sentence inside
    the file would.

    Args:
        query: search terms. Identifiers with hyphens/colons are safe.
        root: restrict to a top-level root folder.
        client: restrict to a client/second-level label.
        doc_type: what the file IS — deck, document, spreadsheet, pdf, note,
            code, image, report, contract, invoice...
        context_type: what its FOLDER is for — engagement-prep, delivery,
            research, brand, governance, admin, training, pitch, event,
            archive.
        shareable_only: only files that may leave — confidential=0 AND rights
            positively classified (OWN or REFERENCE). Unreviewed rows
            (rights='UNKNOWN') are held back: they default to confidential=0,
            so filtering on that axis alone would let never-reviewed files
            out.
        own_only: only files the operator authored (rights='OWN').
        year_from / year_to: filter on authored_date (OOXML created). mtime
            is deliberately not used — bulk copies and consolidations destroy
            it as a dating signal.
        path_contains: substring filter on the path.
        limit: max rows (default 20, capped).

    RESTRICTED material is never returned.
    """
    match, strict = fts_query(query)
    if not match:
        return "Empty query."
    bad = bad_year_range(year_from, year_to)
    if bad:
        return bad

    con = connect()
    where = ["f.rights != 'RESTRICTED'"]
    params = []
    for col, val in (("root", root), ("client", client),
                     ("doc_type", doc_type), ("context_type", context_type)):
        if not val:
            continue
        resolved, err = resolve_facet(con, col, val)
        if err:
            con.close()
            return err
        where.append(f"f.{col} = ?")
        params.append(resolved)
    if shareable_only:
        # confidential=0 alone is NOT shareable: an unreviewed row carries
        # rights='UNKNOWN' with confidential=0 — a default, not a clearance.
        # Shareable means positively classified.
        where.append("f.confidential = 0 AND f.rights IN ('OWN','REFERENCE')")
    if own_only:
        where.append("f.rights = 'OWN'")
    if year_from:
        where.append("f.authored_date >= ?")
        params.append(f"{int(year_from)}-01-01")
    if year_to:
        where.append("f.authored_date <= ?")
        params.append(f"{int(year_to)}-12-31")
    if path_contains:
        where.append("f.path LIKE ?")
        params.append(f"%{path_contains}%")

    sql = f"""
        SELECT f.path, f.doc_type, f.context_type, f.rights, f.confidential,
               substr(COALESCE(f.authored_date,''),1,10) AS authored,
               f.author,
               snippet(files_fts, 6, '[', ']', '…', 12) AS title_hit,
               bm25(files_fts) AS score
        FROM files_fts
        JOIN files f ON f.id = files_fts.rowid
        WHERE files_fts MATCH ? AND {' AND '.join(where)}
        ORDER BY score
        LIMIT ?
    """
    count_sql = f"""
        SELECT count(*)
        FROM files_fts
        JOIN files f ON f.id = files_fts.rowid
        WHERE files_fts MATCH ? AND {' AND '.join(where)}
    """
    try:
        rows, total, used, err = fetch_fts(con, sql, count_sql, match, strict,
                                           params, clamp(limit))
        # Asked while the handle is open, and only when there is an emptiness
        # to explain.
        undated = None if (rows or err) else undated_corpus(
            con, year_from, year_to)
    finally:
        con.close()
    if err:
        return err
    if undated:
        # Not recorded as a miss, for the same reason a bad filter is not:
        # the filter excluded everything, so this is no evidence about what
        # the corpus lacks.
        return with_warning(undated)

    if not rows:
        # Recorded here and NOT on the earlier returns: a bad filter, an
        # impossible year range and an FTS error are already explained to
        # the caller and are not evidence about what the corpus lacks.
        # Logging them would bury the real misses in noise.
        try:
            from . import misses
            misses.record(cfg(), query, filters={
                "root": root, "client": client, "doc_type": doc_type,
                "context_type": context_type, "path_contains": path_contains,
                "shareable_only": shareable_only, "own_only": own_only,
                "year_from": year_from, "year_to": year_to,
            }, stale=index_warning() is not None)
        except Exception:                            # noqa: BLE001
            pass                                     # never fail a search
        # Where a stale index does its worst: an empty result reads as
        # "you do not have this".
        return with_warning(
            f"No matches for {used!r}. Remember: body text is NOT indexed — "
            f"try filename-style or slide-title-style terms, or widen filters.")

    # Say when the list is cut. "100 match(es)" over 195 hits reads as complete.
    if total > len(rows):
        head = (f"showing {len(rows)} of {total} match(es) for {used!r} "
                f"— ranked by relevance, cut at limit={clamp(limit)}; "
                f"narrow with filters or raise limit (cap {cfg().max_limit}):")
    else:
        head = f"{len(rows)} match(es) for {used!r}:"
    out = [head, ""]
    for r in rows:
        flags = []
        if r["rights"]:
            flags.append(r["rights"])
        if r["confidential"]:
            flags.append("CONFIDENTIAL")
        # The slide-title column is newline-joined, and snippet() returns the
        # raw text — so an un-collapsed hit breaks one indented bullet into
        # six unindented lines and the result list stops being readable.
        hit = " ".join((r["title_hit"] or "").split())
        out.append(f"- {r['path']}")
        meta = [x for x in (r["doc_type"], r["context_type"],
                            r["authored"] or None, r["author"]) if x]
        out.append(f"    {' · '.join(meta)}  [{', '.join(flags)}]")
        if hit:
            out.append(f"    titles: {hit}")
    return with_warning("\n".join(out))


@mcp.tool()
def browse_folder(prefix: str = "", limit: int = 40) -> str:
    """Show what is inside a folder: subfolders, file counts, and facet mix.

    Use this to orient before searching — it answers 'what do I have here',
    which full-text search structurally cannot. Call with no prefix to list
    the top-level roots.

    Args:
        prefix: path prefix relative to the primary root, e.g. 'Work' or
            'Work/AcmeCo'. No leading slash.
        limit: max subfolders to list.
    """
    prefix = (prefix or "").strip().strip("/")
    con = connect()
    try:
        if prefix:
            like = f"{prefix}/%"
            rows = con.execute(
                "SELECT path, doc_type, context_type, rights, confidential, bytes,"
                " substr(COALESCE(authored_date,''),1,4) yr"
                " FROM files WHERE rights != 'RESTRICTED' AND path LIKE ?",
                (like,),
            ).fetchall()
            cut = len(prefix) + 1
        else:
            rows = con.execute(
                "SELECT path, doc_type, context_type, rights, confidential, bytes,"
                " substr(COALESCE(authored_date,''),1,4) yr"
                " FROM files WHERE rights != 'RESTRICTED'"
            ).fetchall()
            cut = 0
    finally:
        con.close()

    if not rows:
        return f"Nothing catalogued under {prefix!r} (or it is all RESTRICTED)."

    children = {}
    docs, ctxs, years = {}, {}, []
    own_open = 0
    total_bytes = 0
    for r in rows:
        rest = r["path"][cut:]
        name = rest.split("/", 1)[0] if "/" in rest else "(files here)"
        c = children.setdefault(name, {"n": 0, "bytes": 0})
        c["n"] += 1
        c["bytes"] += r["bytes"] or 0
        docs[r["doc_type"] or "—"] = docs.get(r["doc_type"] or "—", 0) + 1
        ctxs[r["context_type"] or "—"] = ctxs.get(r["context_type"] or "—", 0) + 1
        if r["yr"]:
            years.append(r["yr"])
        if r["rights"] == "OWN" and not r["confidential"]:
            own_open += 1
        total_bytes += r["bytes"] or 0

    def fmt_bytes(n):
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB":
                return f"{n:.1f}{u}"
            n /= 1024

    def top(d, k=6):
        return ", ".join(f"{a} {b}" for a, b in
                         sorted(d.items(), key=lambda kv: -kv[1])[:k])

    label = prefix or "(all roots)"
    out = [f"# {label}",
           f"{len(rows):,} files · {fmt_bytes(total_bytes)} · "
           f"{own_open:,} own+shareable"]
    if years:
        out.append(f"authored: {min(years)}–{max(years)}")
    out += ["",
            f"is (doc_type):  {top(docs)}",
            f"for (context):  {top(ctxs)}",
            "",
            "## Contents"]
    cap = clamp(limit, default=40)
    ordered = sorted(children.items(), key=lambda kv: -kv[1]["n"])
    for name, c in ordered[:cap]:
        out.append(f"  {c['n']:>6,}  {fmt_bytes(c['bytes']):>8}  {name}")
    if len(ordered) > cap:
        out.append(f"  … +{len(ordered) - cap} more")
    return with_warning("\n".join(out))


@mcp.tool()
def search_emails(
    query: str,
    sender_domain: str = "",
    year_from: int = 0,
    year_to: int = 0,
    include_body: bool = False,
    limit: int = 20,
) -> str:
    """Full-text search over the archived email corpus, if one has been
    ingested (shelfmark can ingest .pst / .msg archives). Bodies included.
    Call corpus_stats() for the exact count.

    Args:
        query: search terms; matches subject, sender, recipients, attachment
            names and body text.
        sender_domain: filter, e.g. 'example.com'.
        year_from / year_to: filter on sent_year.
        include_body: return a truncated body excerpt as well as headers.
        limit: max rows (default 20, capped).

    Messages flagged sensitive are never returned.
    """
    match, strict = fts_query(query)
    if not match:
        return "Empty query."
    bad = bad_year_range(year_from, year_to)
    if bad:
        return bad

    con = connect()
    if not has_table(con, "emails"):
        con.close()
        return ("No email corpus in this catalogue. Ingest .pst/.msg archives "
                "with `shelfmark ingest-email` first.")
    where = ["COALESCE(e.sensitive,0) = 0"]
    params = []
    if sender_domain:
        # Same silent-empty trap as search_docs' facets: an unknown domain
        # reads exactly like "no such mail". Suggestions stay inside the
        # non-sensitive slice.
        resolved, err = resolve_facet(con, "sender_domain", sender_domain,
                                      table="emails",
                                      visible="COALESCE(sensitive,0) = 0")
        if err:
            con.close()
            return err
        where.append("e.sender_domain = ?")
        params.append(resolved)
    if year_from:
        where.append("e.sent_year >= ?")
        params.append(int(year_from))
    if year_to:
        where.append("e.sent_year <= ?")
        params.append(int(year_to))

    sql = f"""
        SELECT e.id, e.subject, e.sender_name, e.sender_email, e.sent_utc,
               e.recipient_domains, e.has_attachments, e.attachment_names,
               bm25(emails_fts) AS score
        FROM emails_fts
        JOIN emails e ON e.id = emails_fts.rowid
        WHERE emails_fts MATCH ? AND {' AND '.join(where)}
        ORDER BY score
        LIMIT ?
    """
    count_sql = f"""
        SELECT count(*)
        FROM emails_fts
        JOIN emails e ON e.id = emails_fts.rowid
        WHERE emails_fts MATCH ? AND {' AND '.join(where)}
    """
    body_chars = cfg().body_chars
    try:
        rows, total, used, err = fetch_fts(con, sql, count_sql, match, strict,
                                           params, clamp(limit))
        bodies = {}
        if not err and include_body and rows:
            ids = [r["id"] for r in rows]
            qs = ",".join("?" * len(ids))
            for b in con.execute(
                f"SELECT email_id, body FROM email_bodies WHERE email_id IN ({qs})",
                ids,
            ):
                bodies[b["email_id"]] = b["body"]
    finally:
        con.close()
    if err:
        return err

    if not rows:
        return f"No email matches for {used!r}."

    if total > len(rows):
        head = (f"showing {len(rows)} of {total} email(s) for {used!r} "
                f"— ranked by relevance, cut at limit={clamp(limit)}:")
    else:
        head = f"{len(rows)} email(s) for {used!r}:"
    out = [head, ""]
    for r in rows:
        sent = (r["sent_utc"] or "")[:10]
        out.append(f"- [{sent}] {r['subject'] or '(no subject)'}")
        out.append(f"    from {r['sender_name'] or ''} <{r['sender_email'] or ''}>"
                   f" → {r['recipient_domains'] or ''}")
        if r["has_attachments"] and r["attachment_names"]:
            out.append(f"    attachments: {r['attachment_names'][:200]}")
        if include_body:
            body = (bodies.get(r["id"]) or "").strip()
            if body:
                body = re.sub(r"\s+", " ", body)
                # Say when the excerpt stops. An unmarked cut ends mid-word
                # and reads as the whole message.
                if len(body) > body_chars:
                    out.append(f"    {body[:body_chars]}"
                               f" … [excerpt — {body_chars} of {len(body):,} chars]")
                else:
                    out.append(f"    {body}")
    return "\n".join(out)


@mcp.tool()
def get_file(path: str) -> str:
    """Full catalogue record for one file: rights, confidentiality,
    authorship, OOXML metadata, slide titles, duplicates, and whether it is
    materialised locally or evicted to cloud storage.

    Args:
        path: catalogue path exactly as returned by search_docs. Usually
            relative to the primary root; rows under an extra root carry
            that root's label as their prefix.
    """
    # A leading slash used to miss both the exact match and the LIKE
    # fallback, answering "Not in catalogue" for a path that plainly is.
    path = (path or "").strip().lstrip("/")
    if not path:
        return "No path given."
    con = connect()
    try:
        # The caller's input reaches a LIKE pattern, so its metacharacters
        # are escaped: an unescaped "%" would match every row and turn the
        # suffix fallback into an enumeration primitive.
        esc = (path.replace("\\", "\\\\")
                   .replace("%", "\\%").replace("_", "\\_"))
        r = con.execute(
            "SELECT * FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (path, f"%{esc}"),
        ).fetchone()
        if not r or r["rights"] == "RESTRICTED":
            # A sealed row answers EXACTLY like an absent one — same
            # string, same with_warning wrapping. The old "withheld by
            # policy" refusal confirmed a guessed path exists, one probe at
            # a time, after corpus_stats went to real trouble to hide that
            # the subtree is even there. Announcing a refusal is for the
            # operator at the CLI; an MCP caller gets indistinguishability.
            if not r:
                # "Not in catalogue" and "not on disk" are different
                # answers, so an absent path earns one stat — but only when
                # BOTH guards pass. The rules check keeps the hint from
                # confirming un-catalogued files in sealed subtrees; the
                # containment check keeps `..` from probing arbitrary
                # machine paths through a tool bound to the roots.
                from .config import _inside
                from .rights import derive
                c = cfg()
                sealed_by_rule = derive(path, None, None, c)[0] == "RESTRICTED"
                try:
                    disk_probe = c.abs_path(path)
                    # Containment is decided on the plain path — _inside
                    # resolves both sides, and a \\?\ path would compare
                    # unequal to every root, quietly answering "outside" for
                    # everything. Only the existence check is prefixed, so a
                    # file deeper than MAX_PATH is found rather than denied.
                    on_disk = (not sealed_by_rule
                               and any(_inside(disk_probe, root.path)
                                       for root in c.roots)
                               and extended(disk_probe).exists())
                except Exception:                 # noqa: BLE001
                    on_disk = False
                if on_disk:
                    return with_warning(
                        f"Not in the catalogue yet, but it IS on disk: {path}\n\n"
                        f"The index has not caught up with this file. It will be "
                        f"picked up by the next refresh.")
            return with_warning(f"Not in catalogue: {path}")
        titles = con.execute(
            "SELECT title FROM slide_titles WHERE file_id = ? LIMIT 40",
            (r["id"],),
        ).fetchall()
        dupes = []
        if r["sha256"]:
            dupes = con.execute(
                "SELECT path FROM files WHERE sha256 = ? AND id != ? LIMIT 10",
                (r["sha256"], r["id"]),
            ).fetchall()
    finally:
        con.close()

    # Residency is checked against the filesystem, not trusted from the row —
    # one syscall, and true even between refreshes.
    disk = cfg().abs_path(r["path"])
    try:
        # Prefixed for the syscall only; `disk` stays plain because it is
        # printed below as the absolute location.
        st = extended(disk).stat()
        on_disk = "yes"
        materialised = not is_evicted(st)
    except OSError:
        on_disk = "no"
        materialised = None

    out = [r["path"], ""]
    fields = [
        ("is (doc_type)", r["doc_type"]),
        ("folder is for", r["context_type"]),
        ("rights", r["rights"]),
        ("confidential", "yes" if r["confidential"] else "no"),
        ("client", r["client"]),
        ("project", r["project"]),
        ("author", r["author"]),
        ("last author", r["last_author"]),
        ("company", r["company"]),
        ("OOXML title", r["ooxml_title"]),
        ("authored", r["authored_date"]),
        ("slides", r["slide_count"]),
        ("bytes", r["bytes"]),
        ("evicted (cloud)", "no" if materialised else
                            "yes" if materialised is False else "—"),
        ("status", r["status"]),
        ("note", r["note"]),
    ]
    for k, v in fields:
        if v not in (None, "", 0) or k in ("confidential", "evicted (cloud)"):
            out.append(f"{k:>18}: {v}")
    if on_disk == "no":
        out.append(f"{'ON DISK':>18}: no — catalogued but not at the path below; "
                   f"it was deleted or moved since the last refresh")
    if titles:
        out += ["", "slide titles:"]
        out += [f"  - {t['title']}" for t in titles]
    if dupes:
        out += ["", f"identical copies ({len(dupes)}):"]
        out += [f"  - {d['path']}" for d in dupes]
    out += ["", f"absolute: {disk}"]
    return with_warning("\n".join(out))


# --------------------------------------------------------------------------
# freshness
# --------------------------------------------------------------------------

# The measurement (disk_drift) and the message (freshness_line) live in
# freshness.py so the CLI can reach them without importing an MCP server —
# `shelfmark stats` and the hook adapter ask the same questions this server
# does. These wrappers keep the seam the tests patch: freshness_line routes
# its drift through THIS module's disk_drift name, so monkeypatching
# server.disk_drift still fakes the measurement.

def disk_drift(con) -> dict:
    return freshness_mod.disk_drift(con, cfg())


def freshness_line(con) -> str:
    return freshness_mod.freshness_line(con, cfg(),
                                        drift=lambda c: disk_drift(c))


@mcp.tool()
def corpus_stats() -> str:
    """High-level shape of the corpus: roots, rights split, document types,
    folder purposes, and where the reusable material actually sits.

    Call this first in a new session to know what is available before
    searching.

    RESTRICTED material is reported ONLY as a corpus-wide total. Its roots,
    folders, per-root counts and filenames are all withheld: naming the
    folder that holds your secrets is a smaller leak than naming the
    secrets, but it is still a leak, and this is the tool an agent calls
    first.
    """
    con = connect()
    try:
        def q(sql, params=()):
            return con.execute(sql, params).fetchall()

        # Sealed rows stay out of every aggregate below — headline bytes
        # included, since "how big" is still a fact about material this
        # tool has promised not to describe. The corpus-wide sealed COUNT
        # is the single deliberate exception (see the SEALED line).
        total = q("SELECT count(*) c, sum(bytes) b FROM files"
                  " WHERE rights != 'RESTRICTED'")[0]
        # RESTRICTED rows are excluded, not counted. A per-root breakdown
        # says where sealed material lives and how much of it there is, and
        # a root that holds nothing else is named by its mere presence in
        # the table -- so a caller learns "there is a Vault folder with two
        # secrets in it" without seeing a filename. The corpus-wide total is
        # kept, because "some material is withheld" is honest and carries no
        # location.
        roots = q("""SELECT root, count(*) n,
                       sum(rights='OWN' AND confidential=0) own_open
                     FROM files WHERE rights != 'RESTRICTED'
                     GROUP BY root HAVING n > 0 ORDER BY n DESC""")
        sealed = q("SELECT count(*) n FROM files WHERE rights='RESTRICTED'")[0]["n"]
        rights = q("""SELECT rights, confidential, count(*) n FROM files
                      WHERE rights != 'RESTRICTED'
                      GROUP BY rights, confidential ORDER BY n DESC""")
        dts = q("""SELECT COALESCE(NULLIF(doc_type,''),'—') d, count(*) n
                   FROM files WHERE rights != 'RESTRICTED'
                   GROUP BY 1 ORDER BY n DESC LIMIT 12""")
        cts = q("""SELECT COALESCE(NULLIF(context_type,''),'—') c, count(*) n
                   FROM files WHERE rights != 'RESTRICTED'
                   GROUP BY 1 ORDER BY n DESC LIMIT 12""")
        em = None
        if has_table(con, "emails"):
            em = q("SELECT count(*) c, min(sent_year) a, max(sent_year) b"
                   " FROM emails")[0]
        fresh = freshness_line(con)
    finally:
        con.close()

    gb = (total["b"] or 0) / 1e9
    head = f"{total['c']:,} files · {gb:.1f} GB"
    if em and em["c"]:
        head += f" · {em['c']:,} emails ({em['a']}–{em['b']})"
    out = ["# shelfmark corpus",
           head,
           fresh,
           "",
           "## Roots",
           f"{'root':<22}{'files':>8}{'own+shareable':>15}"]
    for r in roots:
        out.append(f"{(r['root'] or '—'):<22}{r['n']:>8,}"
                   f"{r['own_open']:>15,}")
    out += ["", "## Rights × confidential"]
    for r in rights:
        if r["rights"] == "UNKNOWN":
            # confidential=0 here means "never reviewed", not "cleared".
            leave = "unreviewed → held"
        else:
            leave = "may leave" if not r["confidential"] else "must not leave"
        out.append(f"  {r['rights']:<12} {leave:<18} {r['n']:>7,}")
    if sealed:
        # Count only. No root, no folder, no name -- an aggregate says the
        # policy is doing something without saying what it is protecting.
        out.append(f"  {'SEALED':<12} {'withheld entirely':<18} {sealed:>7,}")
    out += ["", "## doc_type (what files ARE)",
            "  " + ", ".join(f"{r['d']} {r['n']:,}" for r in dts),
            "", "## context_type (what FOLDERS are for)",
            "  " + ", ".join(f"{r['c']} {r['n']:,}" for r in cts),
            "",
            "Body text is not indexed for files — only metadata, paths and slide",
            "titles. Emails (if ingested) do have full bodies. RESTRICTED never",
            "surfaces. Use browse_folder() to orient, search_docs() to find."]
    return "\n".join(out)


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------

def selftest() -> None:
    """Exercise every tool once against the live catalogue and print the
    output. Probes are derived from the DB so nothing corpus-specific is
    hardcoded."""
    print(corpus_stats())
    con = connect()
    try:
        top_root = (con.execute(
            "SELECT root FROM files WHERE rights != 'RESTRICTED' "
            "GROUP BY root ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
            or [""])[0]
        a_word = (con.execute(
            "SELECT filename FROM files WHERE rights != 'RESTRICTED' "
            "AND length(filename) > 8 LIMIT 1").fetchone() or ["report"])[0]
    finally:
        con.close()
    probe = re.sub(r"\.[A-Za-z0-9]+$", "", a_word).split()[0]
    print("\n" + "=" * 70 + "\n")
    print(browse_folder(top_root or ""))
    print("\n" + "=" * 70 + "\n")
    print(search_docs(probe, limit=5))
    print("\n" + "=" * 70 + "\n")
    print(search_emails(probe, limit=3))


# How often the keeper wakes, not how often a refresh runs: a tick with
# nothing to do costs one stat. Real work is gated by
# refresh.max_age_seconds, which already exists.
AUTO_REFRESH_TICK_SECONDS = 60

# Consecutive *exceptions* (not refusals) after which the keeper stops. A
# refused prune is normal and must not stop it; a read-only database raises
# forever, and retrying that every minute is noise, not resilience.
AUTO_REFRESH_MAX_ERRORS = 3


def auto_refresh(stop, tick: float = AUTO_REFRESH_TICK_SECONDS) -> None:
    """Keep the catalogue current for as long as this server is alive.

    A resident process the client already starts is a better trigger than a
    scheduler: it inherits the client's file access, and it runs exactly
    when questions are being asked.

    Safe inside stdio MCP -- every refresh path writes to stderr or files,
    never stdout. Concurrency is already handled by refresh's atomic mkdir
    lock, so a second client's keeper declines rather than colliding.
    """
    from . import refresh
    errors = 0
    while not stop.is_set():
        try:
            # Startup is not special. run_if_needed gates on the STATUS
            # FILE's age, which counts wall-clock — including all the time
            # nothing was running — so changes made while the server was
            # down are picked up on the first tick the moment the index is
            # older than max_age_seconds. A recent clean status at startup
            # therefore means at most the staleness window the steady
            # state already accepts every minute of the session; walking
            # the whole corpus on every client launch bought nothing
            # tighter, and its cost is linear in corpus size (worst on
            # cloud mounts). The one blind spot is a file written with a
            # future mtime inside that window — the drift walk in
            # corpus_stats announces it. An operator wanting tighter
            # startup freshness lowers refresh.max_age_seconds, which
            # governs startup and steady state the same way — no separate
            # flag.
            refresh.run_if_needed(cfg())
            errors = 0
        except Exception as exc:                  # noqa: BLE001
            errors += 1
            print(f"shelfmark-mcp: background refresh failed ({exc})",
                  file=sys.stderr)
            if errors >= AUTO_REFRESH_MAX_ERRORS:
                print("shelfmark-mcp: giving up on background refresh; the "
                      "index will go stale and every tool will say so.",
                      file=sys.stderr)
                return
        stop.wait(tick)


def main() -> None:
    ap = argparse.ArgumentParser(prog="shelfmark-mcp")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--selftest", action="store_true",
                    help="run tools once against the DB and exit")
    ap.add_argument("--no-auto-refresh", action="store_true",
                    help="do not keep the index current in the background "
                         "(for a catalogue maintained by something else)")
    a = ap.parse_args()
    global _CFG
    if a.config:
        _CFG = config_mod.load(a.config)
    try:
        c = cfg()
    except config_mod.ConfigError as exc:
        print(f"shelfmark-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(c, do_selftest=a.selftest,
                         no_auto_refresh=a.no_auto_refresh))


def run(c: Config, *, do_selftest: bool = False,
        no_auto_refresh: bool = False) -> int:
    """Run the stdio server for both installed command entry points."""
    global _CFG
    _CFG = c
    if not c.db.exists():
        print(f"shelfmark-mcp: no catalogue at {c.db} — run "
              f"`shelfmark refresh` first.", file=sys.stderr)
        return 2
    if do_selftest:
        selftest()
        return 0

    stop = threading.Event()
    if not no_auto_refresh:
        # Daemon: the keeper must never be the reason the process outlives
        # the client that started it.
        threading.Thread(target=auto_refresh, args=(stop,), daemon=True,
                         name="shelfmark-refresh").start()
    try:
        mcp.run()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    main()
