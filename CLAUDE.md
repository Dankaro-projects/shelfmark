# shelfmark

Product repo for **shelfmark** — a local, privacy-first document catalogue +
MCP server. README.md is the operator-facing document; this file is for
working on the code.

## The bet

Every incumbent sells access to complexity. The retrieval stacks sell
knobs — chunk size, overlap, embedding model, top-k, a reranker to repair
what the retriever got wrong. The document-management systems sell an
administration console: taxonomy editors, permission matrices, workflow
builders. Desktop search sells a query syntax and calls it power. All of it
hands the user machinery and lets that count as capability.

**Radical simplicity on top of genuine sophistication is the harder
engineering problem, and the one nobody in this category has solved.**

Here that reads: `corpus_stats()`, then `browse_folder()`. Nothing to
chunk, no embedding model to pick, no index server to run, no query
language to learn, no tuning pass that decides whether the answers are any
good. Underneath it: a walk kept incremental on (size, mtime, cloud
residency), prune guards that refuse rather than guess, governance
invariants re-asserted after every run, and an FTS layer that survives the
hyphenated identifiers real corpora are full of. The user should never meet
any of that.

A constraint on the code, not a slogan:

- **The sophistication goes in the engine; the surface stays small.** Five
  MCP tools, one config file, one command to stay current. A caller should
  never need to know how any of it works, or be handed a flag to compensate
  for something the engine could have decided.
- **A new option is a failure to decide.** Before adding a flag, a filter,
  or a config key, establish that the right answer genuinely varies by
  operator. If it does not, pick it and defend the choice in a comment.
  Corpus vocabulary varies — that is what config is for. Correct behaviour
  does not. `--force` earns its place only because the engine provably
  cannot tell an unreadable root from a real deletion.
- **Answer the question, do not expose the machinery.** `browse_folder`
  answers "what do I have here"; it does not hand back a query builder. A
  cut list says it was cut, an unknown filter says it is a bad filter, and
  `corpus_stats()` volunteers its own freshness — none of that is a status
  field left for the caller to interpret. An index answering confidently
  from a frozen snapshot is the failure this whole design exists to
  prevent.
- **Simple is not thin.** Dropping a feature is not simplicity if it moves
  the work onto the operator. Both fixes in this repo's recent history
  *added* engine complexity to remove operator burden — detecting
  placeholder slide titles is more code than storing whatever OOXML hands
  you, and explaining a refused prune is more code than `exit 1`. That is
  the trade this project accepts, every time.
- **The refusals are the shape of the bet, not a backlog.** "No embeddings"
  and "no content extraction" are decisions. Revisit them when real misses
  cluster into evidence — never because a competitor lists the feature.
  `misses.py` is what supplies that evidence, and the load-bearing part is
  the reachable/unreachable split: a raw count of misses only proves people
  search. Function words are stopped for the same reason — "said" can never
  be in a filename, so leaving it in votes for content extraction on the
  strength of grammar.

## Non-negotiables

- **This repo must stay free of operator data.** No real `config.toml`, no
  catalogue (`*.db`), no personal names, paths, or client vocabulary — not
  in code, not in comments, not in tests. Everything corpus-specific enters
  through config.
- **Governance invariants live in the engine, not the caller**: RESTRICTED
  rows never reach `files_fts` or any MCP tool; `rights` (may I reuse) and
  `confidential` (may it leave) are independent axes; `shareable` means
  *positively* classified. `refresh.py` asserts all of this after every
  run — keep those assertions at the call site.
- **The roots are the filesystem trust boundary.** Symlinks are not
  followed, the catalogue is refused inside any root, and both checks
  compare RESOLVED paths — unresolved comparison is defeated by `..` and by
  a symlinked root. An independent review of 0.1.0 found both holes; the
  regressions live in `tests/test_boundary.py`.
- **Anchor every regex alternative on both sides**, and remember a rule
  edit does not relabel existing rows (the builder is incremental) —
  `shelfmark reclassify` exists for that. See `rules.py` header.
- **A guard that declines to act must say so on stderr.** The log and
  `REFRESH_STATUS.json` are for the next process; the operator who typed
  the command reads neither, and a bare non-zero exit teaches nothing.
- **Never write rights into the data.** `review` writes path prefixes to
  config and lets `rights.apply` derive from them, so every answer is
  reversible by editing a file. Inference may seed a *default* the operator
  sees; it may not decide. Thin evidence stays quiet — a governance default
  that is wrong gets pressed through by Enter.
- **No tool answers from a snapshot it cannot vouch for.** The server keeps
  its own index current (`auto_refresh`), and when it cannot, every tool
  prefixes its answer — not just `corpus_stats()`. Adding a tool means
  wiring it through `with_warning()`. Staleness the engine can heal is
  healed; staleness it cannot is announced. Neither is ever left silent.
- **Never name one cause for an ambiguous signal.** A short walk is either
  an unreadable root or a real deletion and the check cannot tell — so it
  reports both and offers `--force`. Same for placeholder slide titles:
  drop what carries no information, rather than assert titles the deck
  does not have.

## Workflow

- After any code change, reinstall the operator tool:
  `uv tool install --force --reinstall .` — the installed tool is a
  snapshot, not an editable install. `.venv/` in this repo is for
  development only. **`--reinstall` is not optional:** with `--force`
  alone, uv serves a cached build whenever the version string is unchanged,
  so edits silently do not land. Confirm with
  `grep <new symbol> "$(uv tool dir)"/shelfmark/lib/python*/site-packages/shelfmark/*.py`.
- To iterate without paying the reinstall each time, run the source tree
  against the installed interpreter:
  `PYTHONPATH=src "$(uv tool dir)"/shelfmark/bin/python -c "import sys; from shelfmark.cli import main; sys.exit(main())" --config <tmp>/config.toml refresh`
- **`reclassify` re-applies rules; it does not re-read files.** Anything
  derived inside `probe.py` (OOXML titles, authorship, slide titles) is
  only recomputed when the file is re-opened, and the builder skips by
  (size, mtime, residency) — so a probe change needs
  `shelfmark build --rebuild`.
- **Tests: `uv run --group dev pytest`.** `tests/conftest.py` builds a
  synthetic corpus from nothing on every run — no fixture binaries are
  committed and no real document is ever read, which is also what keeps
  the no-operator-data rule true of the test suite.
- **A guard is only as good as the test that can fail without it.** Before
  trusting a new test, break the thing it covers and check it goes red:

  ```sh
  # revert the protection, run, restore
  uv run --group dev pytest -q
  ```

  This is not ceremony. The first version of this suite passed 75/75 while
  three protections were mutated out from under it: two assertions were
  written so they could never fail, and one guard was never reached because
  a different guard fired first. Both classes of mistake are invisible from
  a green run.
- `.pst`/`.msg` are proprietary binaries and cannot be honestly synthesised,
  so `tests/test_email_extraction.py` drives the extractors with stand-in
  message objects. They carry exactly the attribute surface the code
  touches — that surface is the contract, so widen the stand-ins when the
  extractor starts reading a new field.
- `config.example.toml` is generated from `CONFIG_TEMPLATE` in
  `src/shelfmark/cli.py`. Edit the template, then regenerate:
  `python -c "from shelfmark.cli import CONFIG_TEMPLATE as t; open('config.example.toml','w').write(t)"`
- **Both `mcp` bounds are tested, not guessed.** 2.x removed
  `mcp.server.fastmcp`, so the ceiling means porting `server.py` first;
  1.8–1.13 raise `TypeError` on import against current pydantic-settings,
  which is why the floor is 1.14. CI runs floor and latest. Re-measure
  before moving either — a range is a claim about versions you have run.

## Releasing

MIT, published to PyPI as **`shelfmark`**. Cut a release by tagging:

```sh
# bump version in pyproject.toml first — the tag must match it
git tag v0.1.0 && git push origin v0.1.0
```

`.github/workflows/release.yml` builds, runs the release gates, and uploads
via **Trusted Publishing** (OIDC — there is no PyPI token in this repo).
One-time setup: add `shelfmark` as a trusted publisher on PyPI for this
repo, workflow `release.yml`, environment `pypi`.

**A published version is permanent.** It can be yanked, never unpublished,
and mirrors copy it within minutes — so everything checkable is checked
before the upload step, not after:

- the tag matches the packaged version
- the sdist carries no `CLAUDE.md`, no `*.db`, no `config.toml`, no
  `REFRESH_STATUS.json` — this file is internal and stays out of releases
- the wheel installs into a clean venv and its entry points run

`[tool.hatch.build.targets.sdist]` uses `only-include`, so a new file at
the repo root is excluded until someone opts it in. The workflow's grep is
the assertion behind that mechanism; keep both. Rehearse anything unusual
on **TestPyPI** first — that is the only place a mistake is free.
