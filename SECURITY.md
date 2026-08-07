# Security policy

## Reporting a vulnerability

Please report privately, not as a public issue:

**[Open a private security advisory](https://github.com/Dankaro-projects/shelfmark/security/advisories/new)**

Include what you did, what happened, and what you expected. A reproduction
against a synthetic corpus is ideal — please do not send files from a real
document tree.

You will get an acknowledgement within a few days. This is a small project,
so a fix may take longer than that; you will be told either way rather than
left waiting.

## What counts as a vulnerability here

Shelfmark reads a filesystem and answers questions about it over MCP, so the
interesting failures are about **containment** and **disclosure** rather than
memory safety.

In scope:

- **Escaping the configured roots.** Anything that causes shelfmark to read,
  hash or catalogue a file outside the trees named in `[[roots]]`. Symlinks
  are not followed and the catalogue is refused inside any root; a way past
  either is a vulnerability.
- **Disclosing RESTRICTED material.** Any tool returning the path, name,
  metadata or content of a file classified RESTRICTED, or otherwise
  revealing where it lives. `corpus_stats()` discloses one corpus-wide count
  of sealed files by design — anything beyond that is a bug.
- **Writing where it should only read.** The MCP server opens the database
  read-only; a write through it is a vulnerability.
- **Anything leaving the machine.** Shelfmark makes no network calls. If you
  find one, that is a vulnerability regardless of what it sends.
- **Content read that should not be.** The file catalogue never opens a
  document for its text. `shelfmark hash` and email ingestion do open files,
  and both must skip symlinks and anything matching your privacy patterns.

Out of scope:

- Vulnerabilities in `mcp`, SQLite, or other dependencies — report those
  upstream. Tell us anyway if shelfmark's usage makes one exploitable.
- A misconfigured `config.toml` that points a root somewhere you did not
  intend. Roots are the trust boundary and are yours to declare.
- Metadata a user can already read with `ls`. Shelfmark indexes filenames
  and paths by design; surfacing them to an agent you connected is the
  product, not a leak.

## Supported versions

The latest release on PyPI. Fixes go into a new release rather than being
backported.

## What shelfmark does with your data

It runs locally, makes no network calls, and writes only to the catalogue
directory you configure. Document contents are never copied into the
catalogue. Two commands do open files — `shelfmark hash` (content hashes for
duplicate detection) and optional email ingestion (message bodies, so
`search_emails` works) — and both skip symlinks and anything matching your
secret/private patterns.

Searches that return nothing are logged locally to `misses.jsonl` beside the
catalogue so you can tell a coverage gap from a phrasing problem. That file
never leaves your machine, is capped, and can be disabled with
`[misses] enabled = false` or cleared with `shelfmark misses --clear`.
