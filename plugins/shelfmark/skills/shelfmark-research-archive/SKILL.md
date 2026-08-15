---
name: shelfmark-research-archive
description: Build a provenance-first source map or research brief from a local Shelfmark document and email archive. Use for timelines, project histories, prior-work discovery, evidence gathering, or briefing preparation where source dates, authorship, rights, and confidentiality must remain visible.
---

# Research a Shelfmark Archive

Build an evidence trail before writing conclusions, and distinguish metadata evidence from content evidence.

## Research Workflow

1. Call `corpus_stats()` and record freshness, corpus coverage, available facets, and whether an email corpus exists.
2. Break the question into a few concrete entities, identifiers, date ranges, and likely folders. Search documents with filename-style or title-style terms.
3. Use `browse_folder()` to understand a promising project's surrounding files instead of treating isolated search hits as the whole archive.
4. Refine `search_docs()` with known facets and dates. Call `get_file()` on leading results to capture exact path, author, authored date, rights, confidentiality, titles, duplicate copies, and residency.
5. When an email corpus is available and correspondence is relevant, call `search_emails()` without bodies first. Narrow by sender domain or year, then use `include_body=true` only for the small set needed as evidence.
6. If document-body evidence is required, ask to open selected paths with a separately authorized local file-reading tool. Shelfmark document search and `get_file()` expose metadata, not document bodies.
7. Produce the requested output with a source list that preserves exact paths or email headers, dates, and governance labels.

## Evidence Rules

- Treat document metadata, filenames, and slide titles as discovery evidence, not proof of claims contained in a file.
- Treat returned email excerpts as partial content and mark truncation when Shelfmark does.
- Label conclusions as confirmed, inferred, or unresolved. Tie confirmed claims to content actually read.
- Use `shareable_only=true` for document searches when the brief or source list may leave the machine. Do not include `UNKNOWN`, confidential, or restricted material in an external-facing output.
- Mention stale-index warnings and result truncation because both limit completeness.

## Output Shape

Keep the result compact:

1. Answer or executive summary.
2. Timeline or grouped findings when useful.
3. Sources with exact provenance and governance status.
4. Gaps, conflicts, and searches that produced no evidence.
