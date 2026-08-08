# Changelog

Notable changes per release. Dates are UTC.

## 0.4.0 — 2026-08-09

### Added
- **`shelfmark init` is now interactive when its guess misses.** If
  `~/Documents` is absent or holds no documents and you are at a terminal,
  init lists the home folders that *do* hold documents (with counts) and a
  single answer — Enter for the best candidate, a number, a path, or `k`
  to keep the default — rewrites the config's primary root. The engine
  proposes with evidence on screen; the operator decides; the decision
  lands in `config.toml` where editing a file reverses it. Non-interactive
  runs (hooks, CI) keep the warning-only behaviour and never hang on a
  prompt.
- The repo now carries `server.json` and a workflow that publishes each
  release to the official MCP registry (GitHub OIDC, no stored token —
  mirroring the PyPI Trusted Publishing setup).

### Changed
- README: real captured install-to-connected transcript in the quickstart,
  badges, and the MCP registry ownership marker.

## 0.3.0 — 2026-08-09

### Changed
- **The default skip list now covers common build output** (`build`, `dist`,
  `target`, `.tox`, `.gradle`, `Pods`, `DerivedData`, and other tool
  caches). An install report measured 34,788 files walked for 711 wanted on
  a dev machine; the gap was almost entirely these directories. **After
  upgrading, the first refresh may refuse its prune or fail the coverage
  floor** — rows for newly skipped files are still catalogued but no longer
  walked, which looks identical to a mass deletion. That refusal is the
  guard working; re-run with `--force` to accept the new skip list. Matching
  stays case-sensitive: `Build/` (a person's folder) is still indexed.
- `shelfmark init` now probes the root it just wrote. A missing or
  document-empty `~/Documents` is announced on stderr, with a short census
  of home folders that do hold documents — the config is written either way.
- The MCP server no longer walks the whole corpus on every client start.
  The first background tick goes through the same `max_age_seconds` gate as
  every later one; startup staleness is bounded by the gate exactly as
  steady-state staleness already was.
- **`shelfmark config` now explains the rights precedence.** The seven
  `[rights]` lists are evaluated in an engine-owned order that used to be
  discoverable only by reading `derive()`; `config` now prints that order
  with the number of files each rule currently claims, attributed by the
  same function that classifies them, and the config template documents
  each list's full outcome (including the authorship conditionals). The
  template now lists the keys in evaluation order.
- Catalogue keys are derived through one forward-slash-normalising helper,
  ahead of any Windows support (`str(Path)` would write backslashed keys
  there, which no rule or prefix would ever match). A catalogue built by a
  hypothetical pre-fix Windows run would need `shelfmark build --rebuild`.

### Fixed
- **An empty catalogue no longer reports `✓ index fresh`.** Zero indexed
  files with a configured root that is missing, unmounted, or entirely
  skip-ruled is now a loud state in `corpus_stats()` *and* a banner on
  every other tool — previously the exact confident-wrong-answer the
  freshness machinery exists to prevent shipped as the out-of-the-box
  default. Found by an external install report (G-01).
- **`get_file` was an existence oracle for sealed paths** (G-05). A
  RESTRICTED path now answers byte-identically to an absent one; LIKE
  metacharacters in the caller's path are escaped; the "it IS on disk"
  hint no longer fires for paths outside the resolved roots (`..` probing)
  or for uncatalogued files the rules would seal; `corpus_stats` headline
  and type breakdowns no longer fold sealed rows into their totals.
- The refresh summary now names corrupt files (capped at ten, each with its
  diagnosis) instead of printing a bare count.
- The unreadable-root guidance no longer recommends macOS Full Disk Access
  on other platforms.

## 0.2.1 — 2026-08-07

### Added
- `SECURITY.md` with a private disclosure path and an explicit scope — for a
  tool that reads filesystems, "what counts as a vulnerability" is worth
  stating: escaping the configured roots, disclosing RESTRICTED material,
  writing through the read-only handle, or any network call at all.
- `CONTRIBUTING.md` and this changelog. Both ship in the sdist alongside the
  tests.

### Fixed
- `shelfmark --version` reported `0.1.0` on a 0.2.0 install (the fix landed
  in 0.2.0's tree but after the tag).

## 0.2.0 — 2026-08-07

### Added
- **`shelfmark misses`** — searches that found nothing are logged locally so
  the "should content extraction ever be built?" question runs on evidence.
  The report splits misses into terms that appear *nowhere* in your
  filenames, paths, authors or titles (unreachable however phrased — the
  shape content extraction fixes) and terms that are in the corpus but were
  not reached (a phrasing or filter problem). Local only, capped, disabled
  with `[misses] enabled = false`.

### Fixed
- `shelfmark --version` reported `0.1.0` on a 0.2.0 install. The version is
  now read from installed metadata rather than restated in `__init__.py`, so
  the two cannot drift.

### Security
- Email ingestion applied no privacy rules: it globbed `*.msg` / `*.pst`
  straight off disk, so an archive under a subtree matching your
  secret/private patterns was opened and its **message bodies indexed** — by
  the one feature that does read content. It now honours the same patterns
  the catalogue uses, and reports what it skipped.

## 0.1.3 — 2026-08-07

### Changed
- Package summary aligned with the README and repository description.

## 0.1.2 — 2026-08-07

### Changed
- README rewritten to lead with the problem — agents cannot use what they
  cannot discover, and handing one the filesystem moves the work of finding
  and filtering into the context window. Reference material unchanged.

## 0.1.1 — 2026-08-06

Fixes for two filesystem trust-boundary defects found by an independent
review of 0.1.0. Both are reasons to upgrade before indexing anything
sensitive.

### Security
- **Symlinks were followed.** A link inside a root read as an ordinary file,
  so it was catalogued and `shelfmark hash` *opened* it — a link to
  `/etc/passwd` was indexed and its bytes hashed. Symlinks are now skipped,
  files and directories, and the count is reported rather than dropped
  silently. To index another tree, add it as an extra root.
- **The catalogue could index itself.** The location check ran against the
  primary root only, so a database placed in an *extra* root catalogued its
  own `.db`, `-wal`, `-shm`, status file, lock and log — with the corpus
  growing on every refresh. The check now covers every root and compares
  resolved paths, so `..` and a symlinked root no longer slip past it.
- **`corpus_stats()` disclosed sealed material.** A per-root RESTRICTED
  breakdown told a caller which folder held secrets and how many there were.
  Only a single corpus-wide count remains.

### Fixed
- `mcp>=1.2,<2` was a guess. Measured, 1.8–1.13 raise `TypeError` on import
  against current `pydantic-settings` — the range promised versions that did
  not work. The floor is now **1.14**, and CI runs the floor and the latest.
- Suppressed an upstream `pydantic-settings` warning that appeared in the
  MCP client's log on every startup and read as a fault in shelfmark.

### Added
- `tests/` now ships in the source distribution, so the suite can be re-run
  from the release artifact instead of taken on trust.

## 0.1.0 — 2026-08-06

First public release. Local, privacy-first, metadata-only document
catalogue with an MCP server: `corpus_stats`, `browse_folder`,
`search_docs`, `get_file`, `search_emails`. Two-axis rights model,
guarded incremental refresh, self-refreshing MCP server, and
`shelfmark review` for onboarding.
