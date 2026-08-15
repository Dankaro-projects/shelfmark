# shelfmark

[![PyPI](https://img.shields.io/pypi/v/shelfmark)](https://pypi.org/project/shelfmark/)
[![CI](https://github.com/Dankaro-projects/shelfmark/actions/workflows/ci.yml/badge.svg)](https://github.com/Dankaro-projects/shelfmark/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/shelfmark)](https://pypi.org/project/shelfmark/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Give AI agents the right context, not your entire filesystem.**

Shelfmark turns years of scattered documents into a governed context map for
AI agents. An agent can discover what exists, understand what kind of
material it is, and select the documents relevant to a task — before
spending context opening files.

No document migration. No duplicated content store. No need for a perfect
folder structure.

```sh
uv tool install shelfmark          # or: pipx install shelfmark
```

*Local by design · metadata only · governed discovery · built for MCP*

---

## Your best knowledge is probably sitting in your folders

Reports, presentations, models, research, proposals and working documents
accumulate over years. Some are carefully organised. Others sit inside
crowded project folders, old archives, download directories, or collections
that made sense only at the time.

The value is still there. The problem is that agents cannot use what they
cannot discover — and giving an agent unrestricted filesystem access does
not solve that. It transfers the work of finding, interpreting and filtering
thousands of files into the context window.

Shelfmark gives the agent a map first.

## Context is the scarce resource

An agent does not need every document. It needs to know what exists, what is
likely to matter, where it came from, and whether it should be used at all.

Shelfmark is a discovery layer between the agent and your files. The agent
searches the catalogue, narrows the field, and requests only the material the
task actually needs — so an existing document estate becomes working context
without turning the filesystem into one enormous prompt.

## A catalogue of pointers, not another document store

Shelfmark builds a local SQLite catalogue of **references and derived
metadata**: paths, filenames, formats, sizes, document types, authors,
dates, classifications, selected Office properties, presentation titles, and
optional content hashes.

It does not copy your documents into the catalogue. It does not index
document body text. It does not create a second repository to govern,
synchronise and maintain. Your files stay where they are; the catalogue
points at them and describes what can be established from their metadata.

**Finding a document does not expose its contents.**

## Turn messy folders into usable agent context

Shelfmark does not require a designed information architecture. Point it at
accumulated project files, forgotten archives, or folders where documents
have simply been dropped over the years.

It builds an inventory from signals that already exist — filenames,
extensions, paths, authorship, dates, Office properties, and your own
classification rules — creating a virtual structure across the material.
Agents can then search by document type, client, project, purpose, author,
year or location without anything being moved or renamed.

A messy folder can become navigable even when it never becomes tidy.

Shelfmark also stays honest about the limits of metadata. A file called
`final7.pdf` with no useful properties cannot reveal its meaning without
someone reading it. Shelfmark reports what is known rather than inventing
certainty — the same reason it drops placeholder slide titles instead of
listing twelve headings a deck does not have.

## Built for selective context

- **Discover before opening.** Search thousands of references before
  deciding which few files deserve attention.
- **Preserve the context window.** Concise catalogue results instead of
  whole documents that may not be relevant.
- **Build on previous work.** Reports, models, proposals, research and
  methods stay discoverable across new tasks and future engagements.
- **Keep provenance visible.** Path, date, author, document type and
  surrounding context help an agent judge relevance.
- **Reduce blind exploration.** Structured search instead of repeatedly
  walking directories and inspecting files one at a time.
- **Separate discovery from access.** Shelfmark helps identify material;
  opening the original remains a separate, controllable decision.

## Your roots are the trust boundary

Shelfmark walks only the roots you configure.

**Symlinks are not followed.** A link inside a root reads as an ordinary
file and would otherwise walk straight out of the tree you configured — and
`hash` opens files. Skipped links are reported, never silently dropped. To
index another tree, add it as an extra root: the boundary widens by saying
so in config, not by planting a link.

The catalogue is refused inside *any* root, because a database that indexes
itself grows on every refresh. Both checks compare resolved paths, so `..`
and a symlinked root cannot slip past them.

## Governance belongs in the retrieval layer

Not every useful document should be treated the same way. Shelfmark
separates two questions that usually get confused:

- **Who owns or may reuse this?** → `rights`: `OWN` / `REFERENCE` /
  `RESTRICTED`
- **May this document leave its current context?** → `confidential`: `0` / `1`

A method may belong to you while the client deliverable containing it stays
confidential. Modelling the two separately lets agents discover reusable
knowledge without treating everything discoverable as freely shareable.

Files matching your private/secret patterns become RESTRICTED: no tool
returns their path, name, metadata or content, no argument overrides it,
they are never opened for hashing, and the database is opened read-only.
`corpus_stats()` reports a single corpus-wide count of sealed files and
nothing else about them — not which root, not which folder. That count is
the one thing disclosed, deliberately: silence about it would misrepresent
the size of the corpus.

Governance is applied by the catalogue, not left to the wording of a prompt.

## Know whether the map can be trusted

A search result is only useful if the agent knows the catalogue is current.

The MCP server keeps its own index current while it runs, so nothing has to
be scheduled and no agent has to remember. When it cannot — never built,
stale, a failed refresh, a clock it cannot reason from, or an index that no
longer agrees with the filesystem — **every tool says so above its answer**,
and `corpus_stats()` compares index against disk in full.

An old snapshot is never presented as complete knowledge.

## Designed for knowledge-intensive work

| | |
|---|---|
| **Consultants and advisors** | Find previous analyses, proposals, frameworks and deliverables without exposing unrelated client material. |
| **Researchers and analysts** | Navigate large collections of reports, datasets and source material through consistent metadata. |
| **Product and strategy teams** | Reconnect decisions, research, roadmaps and previous thinking across projects and time. |
| **Studios and independents** | Turn years of accumulated work into reusable context while keeping control over client files and IP. |
| **Agent builders** | Give local agents a governed discovery layer over MCP. |

## How it works

1. **Point Shelfmark at your existing folders.** One or more roots. Files
   stay where they are.
2. **Build the local catalogue.** It walks the permitted roots, extracts
   available metadata, applies classification rules, and writes references
   into SQLite.
3. **Review ownership and confidentiality.** `shelfmark review` asks a few
   questions about your own folders and writes the answers to config.
4. **Connect an MCP-compatible agent.** It searches, browses and inspects
   catalogue records through structured tools.
5. **Retrieve only what matters.** The agent identifies the relevant
   artefacts before any separate content access takes place.

## Install

```sh
uv tool install shelfmark          # or: pipx install shelfmark
# from a checkout:
uv tool install /path/to/shelfmark
```

Python ≥ 3.11. macOS, Linux and Windows — the full suite runs on all three
in CI, including the Windows-specific behaviours (OneDrive placeholder
detection, junction refusal at the root boundary).

### Codex plugin

Install the MCP server and its catalogue-management, context-finding, and
archive-research skills as one versioned plugin:

```sh
codex plugin marketplace add Dankaro-projects/shelfmark
codex plugin add shelfmark@shelfmark
```

Then create the local catalogue once with `uvx shelfmark init` followed by
`uvx shelfmark refresh`. The plugin starts the pinned Shelfmark MCP server
over stdio; document paths and metadata stay on the local machine.

To install the three companion skills without the MCP configuration:

```sh
pnpm dlx skills add Dankaro-projects/shelfmark --full-depth
```

Email ingestion is optional, and the extra you want depends on the format
you have. `.msg` resolves to wheels everywhere; `.pst` needs
`libpff-python`, which publishes no wheels and compiles from C source, so
it requires a build toolchain (Visual C++ Build Tools, `build-essential`,
or the Xcode command line tools):

```sh
uv tool install "shelfmark[msg]"      # .msg — no compiler needed
uv tool install "shelfmark[pst]"      # .pst — compiles from C source
uv tool install "shelfmark[email]"    # both
```

## Quickstart

Three commands, and `init` finds your documents for you — when the default
root misses, it sweeps for the folders that do hold documents and one
keypress fixes the config:

```text
$ shelfmark init
Wrote ~/.config/shelfmark/config.toml

WARNING: ~/Documents does not exist on this machine.

These folders do hold documents:
  1. ~/Paperwork  (~6 document files)
  2. ~/Downloads  (~1 document file)
Index which folder? [1] — a number, a path, or 'k' to keep ~/Documents:
  Root set to ~/Paperwork. Edit ~/.config/shelfmark/config.toml any time.

$ shelfmark refresh
cataloguing ~/Paperwork -> ~/.local/share/shelfmark/catalog.db
seen 6  new 6  updated 0  unchanged 0  rematerialised 0
evicted 0  corrupt 0  restricted 0

$ claude mcp add shelfmark -s user -- shelfmark-mcp
```

That's install to connected. The agent's first call then looks like this
(a real `corpus_stats()` answer over the corpus above):

```text
# shelfmark corpus
6 files · 0.0 GB
✓ index fresh — matches disk, last refresh 0 min ago

## Roots
root                     files  own+shareable
Clients                      4              0
Decks                        2              0

## Rights × confidential
  REFERENCE    may leave                5
  UNKNOWN      unreviewed → held        1

## doc_type (what files ARE)
  report 4, deck 2
```

Optional but worth the five minutes: `shelfmark review` asks a few
questions about your biggest unclassified subtrees and writes the answers
to config, so rights stop being UNKNOWN; `shelfmark stats` prints the
census any time; `shelfmark config` shows every rights rule in the order
it is checked, with the number of files each one currently claims.

Then in a session: `corpus_stats()` to orient, `browse_folder()` to
navigate, `search_docs()` / `get_file()` to find and inspect.

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

## Configuration

Everything corpus-specific lives in `config.toml` — the code ships with
neutral defaults only. Resolution order: `--config` flag →
`$SHELFMARK_CONFIG` → `~/.config/shelfmark/config.toml`. See
`config.example.toml` for the full annotated reference. Highlights:

| Section | What it controls |
|---|---|
| `[[roots]]` | The trees to index. One unlabelled primary root; extra roots get a label prefix. |
| `[index]` | Where the SQLite catalogue lives. **Must be outside every indexed root and outside cloud-synced folders** — it is a mutating binary DB, and this is enforced. |
| `[privacy]` | Regexes for secrets and private subtrees → RESTRICTED. Built-ins already cover `.env`, key/cert files, `id_rsa`, backup codes, identity documents. |
| `[authors]` | Regexes for your own name/company, for client authors, and for generator tools — drives OWN/REFERENCE classification from OOXML authorship. |
| `[rights]` | Path-prefix rules for the two-axis model: `rights` (may I reuse it) × `confidential` (may it leave). |
| `[facets]` | Which top-level folders count as work/personal; where client/project names sit in the path. |
| `[doc_types]` / `[context_types]` | Extra filename/folder rules, checked before the built-in bilingual (EN/ES) defaults; built-ins can be disabled by name. |

### What `shareable_only` means

`shareable_only=True` means *positively* classified: `confidential=0 AND
rights IN (OWN, REFERENCE)`. Never-reviewed files are held back — unreviewed
is not the same as cleared.

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

### When something is wrong and nothing says so

```sh
shelfmark doctor
```

Checks the setup for the failures that stay quiet: a catalogue inside a
cloud-synced folder (a sync client uploads it on every write, and one that
copies the `.db`, `-wal` and `-shm` at different moments restores a torn
database that opens and answers *wrongly*), a root the process cannot read
— on macOS usually Full Disk Access, which is what an empty walk is hiding
— a database folder that is not writable, a corpus that is mostly
cloud-evicted and what that costs you, and any email format sitting in the
catalogue with no reader installed.

It names the fix rather than the fault, prints the evidence behind each
guess so you can overrule it, and exits non-zero on anything fatal so it
can gate a setup script.

No flag changes what is checked. The one flag there is changes only who
the output is for:

```sh
shelfmark doctor --report
```

The same verdicts as JSON, with the corpus taken out — counts, states and
verdict codes, never a path, a filename, a root label or a config string.
Because nothing here phones home, a broken catalogue on your machine is
invisible to anyone who could help; this is the one channel a local-only
tool gets, and at roughly 1.5 KB it fits in an issue. It carries the
failure streak, which is the difference between "it is broken" and "it has
been broken since Tuesday, 113 runs".

The whole shape, nothing elided:

```json
{
  "shelfmark": "0.4.7", "python": "3.13.13",
  "platform": "darwin", "machine": "arm64", "release": "25.5.0",
  "roots": {"configured": 3, "readable": 3},
  "catalogue": {"files": 32942, "evicted": 0,
                "rights": {"OWN": 3933, "REFERENCE": 27818,
                           "RESTRICTED": 955, "UNKNOWN": 236}},
  "status": {"state": "ok", "detail_kind": "clean",
             "failing_since": null, "consecutive_failures": 0},
  "findings": [{"code": "reader_missing_msg", "status": "warn",
                "count": 2318}],
  "worst": "warn"
}
```

The redaction is not a convention anyone has to remember: the report
copies no free text, so a check added later cannot leak through it, and a
test plants a canary string in the root name, the filename and the config
and fails if it comes out the other end.

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

### A worked example: somebody renames a folder

This is the failure the guards exist for, start to finish. A 200-file
corpus: 60 documents in `Clients/Acme`, 140 in `Admin`. Every line below is
real output; only the paths are shortened.

**1 — a clean build.**

```text
$ shelfmark refresh
cataloguing ~/Paperwork -> ~/.local/share/shelfmark/catalog.db
seen 200  new 200  updated 0  unchanged 0  rematerialised 0
evicted 0  corrupt 0  restricted 0
```

**2 — `Clients/Acme` is renamed to `Clients/ACME Corp`.** Nothing was
deleted, but every catalogued path under the old name now points at
nothing, and the walk finds 60 files it has never seen:

```text
$ shelfmark refresh
seen 200  new 60  updated 0  unchanged 140  rematerialised 0

prune REFUSED — 60 of 200 rows are no longer on disk, over the 2% ceiling.
  Nothing was deleted; the index still lists them. A real deletion, an
  unreadable subtree, or an upgrade widening the default skip list all look
  like this. Check what went missing, then re-run with --force to accept it.
```

The refusal is the right call: from here a rename and a mass deletion are
the same event. The status is now `degraded`, not `ok` — the run finished,
but the catalogue knowingly lists 60 rows whose files are gone.

**3 — run it again** and the catalogue is carrying both copies, so the
coverage floor takes over from the prune ceiling:

```text
$ shelfmark refresh
FAILED — walk saw 200/260 catalogued files, below the 80% floor — the root
was unreadable to this process, that many files really went away, or an
upgrade widened the default skip list. Index NOT updated; re-run with
--force if the deletion or the new skip list is right.
```

It stays failed on every subsequent run. It does not quieten down.

**4 — meanwhile, what an agent gets.** No tool answers as if nothing
happened; the warning leads, and the streak is in it:

```text
⚠ the last refresh FAILED (walk saw 200/260 catalogued files, below the 80%
floor … — 2 consecutive runs since 2026-08-11T08:46:01Z) — answers below
come from the previous snapshot.

showing 3 of 120 match(es) for '"acme"':

- Clients/Acme/acme_note_055.md
    note  [UNKNOWN]
```

Note the paths: it is still answering, and answering from the old names.
That is why the banner is not optional.

**5 — ask what is wrong.**

```text
$ shelfmark doctor
  [ok  ]  root (primary) is readable
  [ok  ]  the catalogue is outside any sync folder
  [ok  ]  the corpus is materialised on disk
  [FAIL]  the last refresh FAILED — 2 consecutive runs since 2026-08-11T08:46:01Z
     walk saw 200/260 catalogued files, below the 80% floor — …
     -> The refresh already said this on stderr; if you are only seeing it now,
       whatever runs it is discarding its output.

2 of 5 checks need attention.
$ echo $?
1
```

**6 — the files really did move, so accept it.**

```text
$ shelfmark refresh --force
seen 200  new 0  updated 0  unchanged 200  rematerialised 0
--force: accepting coverage 200/260 (below the 80% floor)
--force: pruned 60 rows, over the 2% ceiling (backup: catalog.db.bak-preprune)
```

**7 — confirm, and note that `stats` ends with a real walk, not a clock:**

```text
$ shelfmark doctor
  [ok  ]  the last refresh completed cleanly
1 of 5 checks need attention.

$ shelfmark stats
✓ index fresh — matches disk, last refresh 0 min ago
```

The one warning left is `100% of the catalogue has UNKNOWN rights`, which
is true of any corpus before `shelfmark review` has run — see
[Getting rights set](#getting-rights-set-shelfmark-review).

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
    "SessionStart": [{"hooks": [{"type": "command",
      "command": "shelfmark hook session-start", "timeout": 60}]}],
    "PostToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit",
      "hooks": [{"type": "command", "command": "shelfmark mark-dirty"}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "shelfmark hook stop"}]}]
  }
}
```

`mark-dirty` drops a marker only when a write landed under an indexed root
(near-free on every other write); `hook stop` picks it up at the end of the
turn. `hook session-start` refreshes and then checks the index against the
disk — the same check the MCP server runs — telling you and the agent only
when something is wrong.

Do not wrap these in `>/dev/null 2>&1`. An earlier revision of this page
taught exactly that, and it is how a catalogue stayed silently wrong for
four days: every refusal and failure the refresh reports goes to stderr,
so the redirect discarded the only delivery of the news. `shelfmark hook`
is silent when the catalogue is healthy and always exits 0 — there is
nothing left to suppress.

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

On Windows, Task Scheduler running the same command is the equivalent; no
scheduled-run recipe has been field-tested there yet, so if you set one up,
the coverage assertion is again what stands between a broken schedule and a
silently stale index.

## Content hashing and duplicates

The refresh never reads file contents (it must stay fast). To populate
hashes for duplicate detection:

```sh
shelfmark hash                   # reads every unhashed, non-sensitive file
shelfmark hash --limit 2000      # chip away at it
```

Sensitive rows are never opened, and neither are symlinks. On cloud-synced
trees, dataless placeholder files are skipped — reading one silently yields
the hash of the empty string, which would make distinct files look
identical. Re-run after large materialisations.

## What makes Shelfmark different

- **Desktop search** helps a *person* find text inside files.
- **Document management systems** require documents to be imported and
  managed inside a new environment.
- **Retrieval systems** parse, chunk and copy document content into search
  indexes or vector stores.
- **Filesystem tools** give an agent direct access and leave every discovery
  decision to the agent.

Shelfmark sits at a different layer: a local, governed, metadata-based map
of the documents you already have, built specifically for selective agent
context.

## What Shelfmark does not try to be

It is not a document management system. It does not replace your
filesystem. It does not require embeddings. It does not reorganise your
folders. It does not claim to understand content it has never read. And it
does not assume every discoverable document is safe to share.

It gives agents a better starting point.

These are decisions, not a backlog:

- **No embeddings.** Metadata FTS + facets first.
- **No content extraction.** Body text stays out of the index by design.

### Letting real misses decide

Those two refusals should be revisited on evidence, not on a competitor's
feature list — so shelfmark records the searches that found nothing, locally,
and tells you what they mean:

```sh
shelfmark misses            # the evidence
shelfmark misses --clear    # forget it
```

The report answers one question: **could metadata search ever have found
it?** A term appearing in no filename, path, author, title or slide title
was unreachable however it was phrased — that is the shape content
extraction fixes. A term that *is* in your corpus but still missed was a
phrasing or filter problem, which is a different repair. Without that split,
a miss log only proves that people search.

```
73 searches returned nothing   (2026-06-02 → 2026-08-07)

Most-missed terms:
    9  abatement
    7  timeline      (nowhere in your metadata)
    6  commitments   (nowhere in your metadata)

41 of 62 distinct terms (66%) appear nowhere in your filenames,
paths, authors, titles or slide titles.

These are mostly things metadata search could NEVER have found, however
phrased. That is the pattern the README says should reopen content
extraction — not embeddings.
```

Bad filters, impossible year ranges and searches against a stale index are
**not** recorded as misses: each is already explained to the caller, and
logging them would bury the real signal. It stays local, is capped, and
never leaves your machine. Turn it off with `[misses] enabled = false` —
recording your own search terms is a privacy-affecting choice, and whether
that is acceptable genuinely varies by corpus.

## FAQ

**Does Shelfmark upload my documents?**
No. It runs locally and builds a local catalogue. Document contents are not
copied into it.

**What does the catalogue contain?**
File references and derived metadata: paths, filenames, types, sizes, dates,
authors, classifications, selected Office properties, presentation titles,
and optional content hashes.

**Does it read document contents?**
The file catalogue does not index body text. Two deliberate exceptions:
`shelfmark hash` opens files to compute content hashes for duplicate
detection, and optional **email ingestion** can index message bodies so
`search_emails` works. Both are opt-in commands, both skip symlinks, and
both honour your privacy rules — a file or archive matching your
secret/private patterns is never opened by either.

**Do I need to reorganise my folders first?**
No. Better filenames and metadata improve classification, but no formal
structure is required.

**Can it understand every badly named file?**
No. A file with a meaningless name and no embedded metadata stays hard to
identify without reading it. Shelfmark says what it knows rather than
guessing.

**Why not just give an agent filesystem access?**
Filesystem access lets an agent *open* files. Shelfmark helps it decide
which files are worth opening — and which should stay out of its results
entirely.

**Why not a vector database?**
A vector database is for semantic retrieval *from contents*. Shelfmark
solves the earlier problem: discovering and governing what exists, before
deciding what content should be processed at all. They are compatible; this
one comes first.

**Which agents can use it?**
Any client that supports local MCP servers. Register with
`claude mcp add shelfmark -s user -- shelfmark-mcp`, or point your client at
the `shelfmark-mcp` command.

## Development

```sh
uv run --group dev pytest        # the suite ships in the sdist, so this
                                 # runs from the release artifact too
```

The test corpus — including its OOXML files — is synthesised on every run;
no fixture binaries are committed and no real document is ever read.

## License

MIT — see [LICENSE](LICENSE). Free to use, modify and redistribute.

<!-- MCP registry ownership marker — the registry validates the PyPI
     package by finding this name in the published README. -->
mcp-name: io.github.Dankaro-projects/shelfmark
