# Changelog

Notable changes per release. Dates are UTC.

## Unreleased

### Fixed
- **`doctor` now reports whether the last refresh actually worked.** It
  checked whether the setup *could* work and never opened
  `REFRESH_STATUS.json`, so a catalogue that had been failing for days got
  a clean bill of health from the one command whose stated job is "the
  problems that fail silently" — the same detection-without-delivery shape
  as the four-day field failure, in the tool built to catch it. Found while
  writing the README walkthrough: every other surface (the tool prefixes,
  `stats`, the hook, `--report`) already carried the failure; `doctor` was
  the one that did not. A failed refresh is now `FAIL` and exits non-zero, a
  degraded one warns, and both carry the streak — "113 consecutive runs
  since Tuesday" sends the operator somewhere different from "it failed".
  The remedy names the likely reason they are only hearing about it now:
  something is discarding the refresh's stderr.

### Added
- **`shelfmark doctor --report`** — the same verdicts as JSON, with the
  corpus taken out, for pasting into an issue. Nothing here phones home,
  which is the product working as intended and also why a stranger's
  broken catalogue is invisible to anyone who could help: the failure that
  motivated the streak fields ran for four days in a log nobody opened. A
  local-only tool gets one channel, and it is the operator's clipboard.
  The report carries version, platform and architecture, root and rule
  *counts*, the guard thresholds in force, the catalogue's size and rights
  split, the status state with its failure streak, and every finding as a
  fixed verdict code — never a path, a filename, a root label or a string
  from the config. The refresh's `detail` is classified into one of five
  kinds rather than copied, because it quotes the machine (the
  unreadable-folder detail names the folder), and an unrecognised detail
  becomes `other` rather than passing through: a vague report is a smaller
  failure than somebody's folder structure in a public issue. Redaction is
  structural rather than remembered — `report()` copies no free text, so a
  check added later cannot leak through it, and a test plants a canary in
  the root name, the filename and the config and fails if it surfaces. On
  a 33k-file corpus the whole thing is about 1.5 KB.

  `doctor`'s "no flags" rule stands: no flag changes what is checked, and
  this one changes only who the output is for.

## 0.4.7 — 2026-08-11

### Fixed
- **An unreadable folder now degrades the status instead of reporting
  ok/clean.** 0.4.6 stopped the prune from deleting rows under a folder
  the walk could not open, but the run still wrote `state: ok` — so the
  stop hook stayed silent, no streak accumulated, and the only delivery
  was a terminal line a hook-run refresh discards. Verified before the
  fix: ten knowingly-unreachable files, an "ok" status, a silent hook.
  The run now writes `degraded` with the folder named in the detail, and
  every repeating surface (tool prefixes, corpus_stats, the hook, the
  streak) carries it until the folder opens again or is skipped in config.
- **The drift line no longer calls unreachable files "deleted".** The walk
  counts the folders it cannot open, and disk_drift discarded that
  knowledge: rows underneath were lumped into the deleted count and the
  banner prescribed `shelfmark refresh` — the one thing that cannot reach
  them. Missing rows are now partitioned into deleted and unreachable,
  each with its own remedy, and "N unreachable (under M folder(s) the walk
  cannot open)" says what is actually wrong.

## 0.4.6 — 2026-08-11

### Added
- **`shelfmark doctor`** — checks the setup for the problems that otherwise
  fail silently, on all three platforms. The headline is the one nothing
  enforced: **the catalogue sitting inside a cloud-synced folder.** The
  config comments call that mandatory, but a comment is not a check, and
  the operator who most needs the rule is the one who did not read the
  file. A sync client uploads the database on every write, and one that
  copies the `.db`, `-wal` and `-shm` at different moments restores a torn
  database that opens and answers wrongly. Sync folders are discovered at
  runtime — OneDrive publishes its own location including the tenancy name,
  macOS keeps third-party clients under `~/Library/CloudStorage`, and the
  conventional home-relative names cover the rest — because a literal list
  cannot hold "OneDrive - Contoso". The matched folder is printed as
  evidence rather than asserted, since detection is a guess and a
  diagnostic that cries wolf is one people learn to skip. It also reports
  unreadable roots (naming Full Disk Access on macOS, where the denial is
  what an empty walk is usually hiding), a non-writable database folder, a
  mostly-evicted corpus and what that costs, mostly-UNKNOWN rights, and any
  email format present in the catalogue with no reader installed. No flags:
  everything it reports it can determine. Exit 1 on anything fatal, so it
  works in a setup script.
- **Windows paths over 260 characters are now catalogued, not just
  reported.** Win32 refuses them with ERROR_PATH_NOT_FOUND unless the
  machine-wide `LongPathsEnabled` key is set, which needs an administrator
  — so "enable it and re-run" is advice a large share of operators cannot
  take, and the files are perfectly readable through the `\\?\` prefix,
  which needs no privilege at all. The walk, the hash backfill and
  `get_file`'s residency check now address the filesystem through that
  prefix. Catalogue keys are unchanged: keys are taken relative to the same
  prefixed base, so it cancels out and never reaches the database. The
  prefix is for syscalls only — `abs_path()` still returns the plain form,
  because that is what gets printed and what containment checks compare,
  and a prefixed path compares unequal to every root.

### Fixed
- **A folder the walk could not open was silently dropped, and the prune
  then deleted its rows.** `os.walk` was called without `onerror`, so a
  directory that failed to open yielded nothing and raised nothing: every
  file under it vanished from the run, looked stale to the prune, and was
  deleted from the catalogue while still on disk — after which the index
  reported itself fresh. Windows reaches this with no permission being
  wrong, since a path over MAX_PATH raises ERROR_PATH_NOT_FOUND unless
  long paths are enabled machine-wide. The walk now counts unreadable
  directories and names them, `build` warns, and the builder stamps the
  rows under those folders as seen so the prune cannot delete files it
  never looked at. Scoped to those subtrees deliberately: skipping the
  whole prune would mean one permanently unreadable folder — a root-owned
  directory, `lost+found`, a macOS `.Trashes` — disables pruning for the
  entire corpus forever, so the rest of the tree still prunes normally. A
  root that will not open at all (macOS TCC denies a documents folder to
  background processes exactly this way) scopes to the whole root, which
  the coverage floor then reports as before.
- **One filename could crash every CLI report.** Windows' ANSI codepage is
  still 1252 outside the console, so redirected output encoded with cp1252
  and raised `UnicodeEncodeError` on the first character it lacked. A name
  stored decomposed (NFD) carries U+0301, which cp1252 cannot represent
  even though the precomposed form can — so a single such file replaced
  the whole of `shelfmark stats` with a traceback. Output streams now use
  `backslashreplace`. Only the error handler changed: re-encoding as UTF-8
  would hand mojibake to a consumer that asked for cp1252, and paths are
  still never normalised, because the catalogue key has to reopen the file.
- **`init` crashed on end-of-input after writing the config.** `isatty()`
  can report an interactive terminal while the first read still hits EOF —
  a pty with nothing on stdin, which is how CI and agent shells run it. The
  uncaught `EOFError` exited non-zero with the config already on disk, so
  the install looked failed but was half-done and re-running hit "config
  already exists". EOF and Ctrl-C now keep the announced default and say so.
- **`year_from`/`year_to` returned "No matches" over a corpus with no
  authored dates.** `authored_date` comes from OOXML metadata, which is
  only read when a file can be opened, so a cloud-synced tree that is
  mostly evicted dates almost nothing and every year-filtered search came
  back empty while the same search without the filter returned hundreds.
  The filter now says it excluded everything, in the same family as
  `bad_year_range`, and the search is not recorded as a miss — a filter
  that excluded the whole corpus is no evidence about what the corpus
  lacks.
- **`stats` said no `[authors]` rule could reach the files without saying
  why.** On a mostly-evicted tree that reads as an instruction to write
  `[authors]` rules, which is the one thing that cannot work: authorship
  and authored dates are read from inside the file, and an evicted file is
  never opened. The next-step block now names eviction as the cause when
  it is the cause, and says that path rules are unaffected.

### Changed
- **The email extras are split into `[msg]` and `[pst]`.** `extract-msg`
  resolves to wheels; `libpff-python` publishes none and compiles from C
  source, so it fails outright on any machine without a toolchain. Joining
  them meant a folder of `.msg` files could not be read without a compiler
  that `.msg` never required. `[email]` still installs both. The missing-
  reader message now names the extra that installs that one reader, and
  says up front that `.pst` support builds from source.

## 0.4.5 — 2026-08-10

### Added
- **The status file now carries the failure streak.** A failure that has
  repeated 113 times is not the same event as one that just happened, and
  overwriting REFRESH_STATUS.json each run erased the difference — run
  113 read identically to run 1. Any non-ok run (failed or degraded) now
  records `failing_since` and `consecutive_failures`, carried forward
  until a clean run clears them by omission; absent keys mean "no streak
  recorded", so files written by older versions stay readable. Every
  delivery surface renders it — corpus_stats, the per-tool warning
  prefix, and the hook — as "N consecutive runs since T", and only from
  the second run: "1 consecutive run" is noise wearing the costume of
  information.
- A regression test pins the four-day field failure end to end: subset
  rename → run 2 degraded (prune refused) → later runs failed (coverage
  floor, inflated by the refused rows) → one streak, one start time — and
  pins that the log's FAIL line never again asserts "root unreadable" as
  the cause (the 0.2.0 sentence lived in the banner AND the log; the
  banner fix shipped in 0.4.4, the log had dropped it earlier, and both
  are now held dead by tests).

## 0.4.4 — 2026-08-10

The theme: the trust signal was the least finished surface in the product.
A field failure — a renamed folder that left 19,157 phantom rows answering
queries for four days — was *detected* by every guard and *delivered* by
none of them. Found by an external review of 0.4.2 in production use.

### Fixed
- **The CANNOT-VERIFY banner no longer asserts a cause.** The ratio behind
  it cannot tell an OS denial from a mass move — a renamed root looks
  identical — yet the banner said "the OS is denying this process the
  document root" as fact, blaming macOS permissions for a rename, four
  days running. It now names both possibilities with a remedy for each,
  and appends the last-refresh detail (the one branch that dropped it):
  CANNOT VERIFY plus "prune REFUSED" is what a rename actually looks like.
- **A refused prune now leaves the status `degraded`, not `ok`.** The run
  completed, but the catalogue knowingly lists rows whose files were gone,
  and they answer queries. Every tool now carries a DEGRADED prefix (not
  FAILED — rights were re-applied) on every call until it is resolved:
  news that stops being news is how this stayed quiet. A missing extra
  root remains `ok` with the detail as news — a laptop away from its NAS
  is normal life, not a degradation.

### Added
- **`shelfmark stats` now ends with the disk-comparison line.** The only
  code comparing catalogued rows against files on disk lived behind the
  MCP server, so an operator who never ran an agent could not be told.
  The freshness machinery moved to its own module (`freshness.py`); the
  server re-exports it unchanged.
- **`shelfmark hook session-start|stop` — the product owns the hook now.**
  The README used to teach `refresh --if-needed >/dev/null 2>&1`, and
  since every refusal speaks on stderr, that redirect discarded the only
  delivery of the news. The adapter is silent when healthy, emits Claude
  Code hook JSON (systemMessage for the operator, additionalContext for
  the agent) when not, and always exits 0. The README recipe now uses it
  and says why the silencing form must not come back.

## 0.4.3 — 2026-08-10

### Fixed
- The MCP handshake's `serverInfo.version` now reports shelfmark's own
  version. `FastMCP` takes no version argument and its lowlevel server
  defaults to the `mcp` package's — so clients displayed shelfmark as
  whatever mcp release it shipped with (0.4.2 introduced itself as
  "1.29.0"). Same class as the 0.2.0 `--version` bug, on the other entry
  point; the version is read from installed metadata, and the mcp-range CI
  job now asserts the handshake value at both declared bounds.

## 0.4.2 — 2026-08-10

### Fixed
- **Windows: cloud-residency detection now works.** `is_evicted` read
  `st_blocks`, which does not exist on Windows, so every OneDrive
  placeholder read as materialised — and got hashed, yielding the
  empty-string digest guard at best and ghost duplicates at worst. It now
  checks the Win32 recall attributes (`OFFLINE`, `RECALL_ON_OPEN`,
  `RECALL_ON_DATA_ACCESS`) first, which are authoritative there; POSIX
  block-count behaviour is unchanged. One function now serves the builder,
  the server's `get_file` residency line, and the standalone probe — the
  three previously separate copies could drift.
- **Windows: the refresh lock no longer kills what it checks.** The
  stale-lock probe was `os.kill(pid, 0)` — a probe on POSIX, but on
  Windows any signal outside the two console events calls
  `TerminateProcess`: a second refresh would have terminated a live one
  mid-prune and then reported it running. Windows now asks via
  `OpenProcess`, which only asks.
- **Windows: directory junctions are refused by the walk.** A junction
  crosses the root boundary exactly like a symlink but is not one
  (`os.path.islink` says False on 3.12+, so the walk descended it) — and
  unlike symlinks, creating one needs no privilege. Junction directories
  are now pruned before descent and reported in the skipped-links count.
- **Text files are read and written as UTF-8 everywhere.** Config, status,
  MAP.md, cards and the pid file relied on the locale encoding, which on
  Windows is cp1252 — `shelfmark init` would write a config that
  `config.load` (already UTF-8) could garble on the first non-ASCII folder
  name, and MAP.md/cards crashed outright on characters outside Latin-1.

### Added
- CI now runs the suite on `windows-latest` alongside macOS and Linux, at
  both ends of the Python range. The matrix entry is the support claim —
  the residency, lock and junction behaviours above are tested with
  platform stand-ins on every leg, and natively on the Windows one.

## 0.4.1 — 2026-08-09

### Fixed
- MCP registry publication: correct namespace casing in the README
  ownership marker (`io.github.Dankaro-projects/…` — the registry matches
  it case-sensitively against the published PyPI README), current schema
  URL in `server.json`, and Go arch names in the publisher download. With
  these, every release now lands on registry.modelcontextprotocol.io
  automatically.

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
