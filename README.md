# shelfmark

A local, privacy-first document catalogue with an MCP server. Point it at
your document tree(s) and it gives Claude Code — or any MCP client —
structured, governed search over what you have: by filename, author, slide
title, document type, folder purpose, client, and year.

**Metadata only, by design.** No document body text is ever extracted or
indexed, so nothing in the catalogue can leak file *content* into a prompt.
What is indexed: paths, filenames, sizes, OOXML properties (author, company,
title, slide titles), content hashes, and classification facets derived from
your own rules.

Slide titles come from the deck's own properties, and most decks never set
them: PowerPoint files in `Slide 1 … Slide 12`, generator libraries
(pptxgenjs, python-pptx, HTML→deck exporters) do the same, and PowerPoint's
own filler is `PowerPoint Presentation`. Those are dropped rather than
indexed — a deck with no real headings reports none, instead of twelve
titles it does not have. Genuine titles on the same deck are kept, at their
original slide numbers.

## Why not just grep / Spotlight / embeddings?

- **Structure, not just matching.** `browse_folder` answers "what do I
  have here" — which full-text search structurally cannot. Facets separate
  what a file *is* (`doc_type`) from what its folder is *for*
  (`context_type`), and who may reuse it (`rights`) from whether it may
  leave (`confidential`).
- **Governance enforced in the server, not the prompt.** Files matching
  your private/secret patterns are RESTRICTED: never returned by any tool,
  no argument overrides it, and the DB is opened read-only.
- **Current without being told, honest when it is not.** The MCP server
  refreshes its own index while it runs, so nobody schedules anything and
  no agent has to remember. When it cannot — never refreshed, refresh
  failing, clock unusable — *every* tool says so above its answer, and
  `corpus_stats()` compares index against disk in full. An index that
  silently stops updating still answers confidently from a frozen
  snapshot, which is worse than no index at all.
- **Identifier-safe search.** Real corpora are full of `ACME-2026-014`
  style identifiers whose hyphens break naive FTS. Queries are quoted and
  retried so a stray quote returns results, not a parser error.

## Install

```sh
uv tool install shelfmark          # or: pipx install shelfmark
# from a checkout:
uv tool install /path/to/shelfmark
```

Python ≥ 3.11. macOS and Linux. Email ingestion is optional and pulls extra
dependencies: `uv tool install "shelfmark[email]"`.

## Quickstart

```sh
shelfmark init                     # writes ~/.config/shelfmark/config.toml
$EDITOR ~/.config/shelfmark/config.toml   # set your [[roots]]
shelfmark refresh                  # first build (add --no-hash for a fast pass)
shelfmark review                   # answer a few questions -> rights get set
shelfmark stats                    # census of what it found
```

Register with Claude Code:

```sh
claude mcp add shelfmark -s user -- shelfmark-mcp
```

Then in a session: `corpus_stats()` to orient, `browse_folder()` to
navigate, `search_docs()` / `get_file()` to find and inspect.

## Configuration

Everything corpus-specific lives in `config.toml` — the code ships with
neutral defaults only. Resolution order: `--config` flag →
`$SHELFMARK_CONFIG` → `~/.config/shelfmark/config.toml`. See
`config.example.toml` for the full annotated reference. Highlights:

| Section | What it controls |
|---|---|
| `[[roots]]` | The trees to index. One unlabelled primary root; extra roots get a label prefix. |
| `[index]` | Where the SQLite catalogue lives. **Must be outside every indexed root and outside cloud-synced folders** — it is a mutating binary DB. |
| `[privacy]` | Regexes for secrets and private subtrees → RESTRICTED. Built-ins already cover `.env`, key/cert files, `id_rsa`, backup codes, identity documents. |
| `[authors]` | Regexes for your own name/company, for client authors, and for generator tools — drives OWN/REFERENCE classification from OOXML authorship. |
| `[rights]` | Path-prefix rules for the two-axis model: `rights` (may I reuse it) × `confidential` (may it leave). |
| `[facets]` | Which top-level folders count as work/personal; where client/project names sit in the path. |
| `[doc_types]` / `[context_types]` | Extra filename/folder rules, checked before the built-in bilingual (EN/ES) defaults; built-ins can be disabled by name. |

### The two-axis rights model

`rights` and `confidential` are separate axes, deliberately:

- `rights` — may I **reuse** it? `OWN` / `REFERENCE` / `RESTRICTED`
- `confidential` — may it **leave**? `0` / `1`

A deck you authored for a client is `OWN` (the method is yours) *and*
confidential (that artefact does not leave). Conflating the two is how a
corpus ends up mostly-RESTRICTED and unsearchable. `shareable_only=True`
means *positively* classified: `confidential=0 AND rights IN
(OWN, REFERENCE)` — never-reviewed files are held back.

### Getting rights set: `shelfmark review`

Most files carry no OOXML author — a corpus is mostly PDFs, markdown and
images — so authorship classifies almost nothing and **path rules are the
only mechanism that can**. They ship empty, which is why a fresh catalogue
is mostly `UNKNOWN` and `shareable_only` comes back nearly empty.

That knowledge is yours, not the corpus's, so `review` asks for it — biggest
win first, with what the catalogue knows on screen:

```
[1/8] Projects
      493 unclassified of 662 files · folder reads as: pitch
      is: note 202, document 89, code 88, pdf 51
      authored by: R. Okonjo (31), A. Lindqvist (12), openpyxl (9)
      e.g. programme-overview.pptx · phase-two-proposal.pptx
      answer [skip]:
```

Answer `own`, `own-private`, `reference`, `client`, `personal`, `scratch`
or `skip`. On a ~1,900-file corpus, five answers settled 80% of the
unclassified files and eight settled 90%.

```sh
shelfmark review                 # dry run — shows what it would write
shelfmark review --apply         # writes the prefixes, re-derives rights
```

It stops whenever you do, only ever asks about subtrees that are still
unclassified (so re-running continues where you left off), and writes
**config, never rights values in the data** — so any answer is undone by
editing the config and re-running `shelfmark rights`. Where authorship is
lopsided enough to be evidence it offers a default; where it is thin it
stays quiet rather than guessing.

### Editing classification rules

Two things people trip over, learned the hard way:

1. **Anchor short regex alternatives on both sides.** An unanchored `rfi`
   matches inside "Docke**rfi**le" and the Spanish word "pe**rfi**l". Before
   adding or deleting an alternative, list the filenames it actually
   matches.
2. **A rule edit does not relabel existing files.** The builder is
   incremental, so after any rule change run:

   ```sh
   shelfmark reclassify all          # dry run — shows what would change
   shelfmark reclassify all --apply
   ```

   Do **not** use `build --rebuild` for this: it re-walks everything (and
   on cloud-synced trees drags every evicted file back down) when the
   filenames are already in the DB.

## Keeping it current

```sh
shelfmark refresh                # build + rights + prune + assertions
shelfmark refresh --if-needed    # only if a write landed or the index is old
```

`refresh` is incremental by (size, mtime, cloud-residency) — a couple of
seconds over a ~30k-file tree — and it asserts its own correctness on every
run: walk coverage (an OS-denied walk must not read as success), guarded
pruning of deleted files (refused when it looks like a mass deletion or an
unmounted root), and governance invariants (no RESTRICTED row in the search
index, no unsealed secret, no unsearchable non-RESTRICTED row). Results
land in `REFRESH_STATUS.json` next to the DB, and the MCP server reports
them on every `corpus_stats()` call.

Any guard that declines to touch the index says so on stderr and exits
non-zero — you never get a clean-looking summary hiding a refusal.

### When a guard stops you

The two size guards cannot tell "the root was unreadable" from "those files
really were deleted" — both look like a short walk. So they refuse, name
both possibilities, and leave the index untouched:

```
FAILED — walk saw 63/123 catalogued files, below the 80% floor — either the
root was unreadable to this process or that many files really went away.
Index NOT updated; re-run with --force if the deletion was real.
```

Check which it was. If the files are genuinely gone:

```sh
shelfmark refresh --force        # accept the short walk, prune past the ceiling
```

`--force` backs the catalogue up to `catalog.db.bak-preprune` before
deleting anything. If instead a root was merely unmounted or unreadable,
fix that and refresh normally — the rows are still there.

### You do not schedule this

The MCP server keeps its own index current. It is spawned by your client,
lives for the whole session, and refreshes once on startup and then
whenever `refresh.max_age_seconds` has passed. Nothing to install, no
timer to configure, no command to remember.

That works because a resident process the client already starts is a
better trigger than a scheduler: it inherits the same file access the
client has, and it is running exactly when you are asking questions. If
you maintain the catalogue some other way, `shelfmark-mcp
--no-auto-refresh` leaves it alone.

Editor hooks are now an optimisation, not the mechanism — they cut the
delay between an agent writing a file and that file being searchable, from
one refresh interval to the end of the turn. With Claude Code, in
`~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit",
      "hooks": [{"type": "command", "command": "shelfmark mark-dirty"}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "shelfmark refresh --if-needed >/dev/null 2>&1"}]}]
  }
}
```

`mark-dirty` drops a marker only when a write landed under an indexed root
(near-free on every other write); `refresh --if-needed` picks it up at the
end of the turn.

### If you also want it current with no client running

Only worth it for non-MCP use, or an instant first query on a very large
tree. On Linux, a `systemd --user` timer running `shelfmark refresh
--if-needed` is clean.

On macOS, **the obstacle is TCC, not launchd**. A LaunchAgent is denied
`~/Documents`, `~/Desktop` and `~/Downloads` by default; `os.walk` swallows
the error, so the build walks a handful of files and **exits 0** — every
layer reports success while the index never updates. Grant Full Disk Access
to the interpreter that runs the job and a LaunchAgent is fine. The
coverage assertion catches the ungranted case either way: the refresh
fails loudly rather than quietly indexing nothing.

## Content hashing and duplicates

The refresh never reads file contents (it must stay fast). To populate
hashes for duplicate detection:

```sh
shelfmark hash                   # reads every unhashed, non-sensitive file
shelfmark hash --limit 2000      # chip away at it
```

Sensitive rows are never opened. On cloud-synced trees, dataless
placeholder files are skipped — reading one silently yields the hash of the
empty string, which would make distinct files look identical. Re-run after
large materialisations.

## MCP tools

| Tool | What it answers |
|---|---|
| `corpus_stats()` | What is here overall + an honest freshness line. Call first. |
| `browse_folder(prefix)` | What is inside a folder: counts, sizes, facet mix. |
| `search_docs(query, …)` | Metadata full-text search with facet filters. |
| `get_file(path)` | Full record for one file: rights, authorship, slide titles, identical copies, on-disk status. |
| `search_emails(query, …)` | Full-text over an ingested .pst/.msg email archive (optional). |

Result lists always say when they are cut (`showing 100 of 195 …`), unknown
filter values are reported as bad filters with suggestions (never as an
empty corpus), and excerpts mark their truncation point.

## What is deliberately not built

- **No embeddings.** Metadata FTS + facets first; use it, note what you
  *couldn't* find, and let real misses decide. If misses cluster on "I know
  what it said, not what it was called", the fix is content extraction, not
  embeddings.
- **No content extraction.** Body text stays out of the index by design.

## License

MIT — see [LICENSE](LICENSE). Free to use, modify and redistribute.
