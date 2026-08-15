---
name: shelfmark-find-context
description: Find relevant, governed local documents through the Shelfmark MCP tools. Use when locating files by filename, title, author, client, project, document type, folder purpose, date, or rights status, especially when the result may be shared outside the machine.
---

# Find Shelfmark Context

Find the smallest useful set of documents while respecting catalogue freshness and sharing boundaries.

## Search Workflow

1. Call `corpus_stats()` first. Note freshness, available roots, document types, context types, and the rights split before forming a query.
2. If the user names a folder or only describes a broad area, call `browse_folder()` to learn the catalogue's actual structure and labels.
3. Call `search_docs()` with filename-style or title-style terms. Shelfmark indexes paths, filenames, authors, company metadata, OOXML titles, and slide titles, not document body text.
4. Add only filters supported by the corpus summary or prior results. Use `root`, `client`, `doc_type`, `context_type`, authored-year bounds, or `path_contains` to narrow a large result set.
5. Call `get_file()` on the strongest candidates to confirm rights, confidentiality, authorship, dates, slide titles, duplicate copies, and on-disk status.
6. Return a short ranked list with exact catalogue paths, why each result fits, and any governance or freshness caveat.

## Apply Governance

- Set `shareable_only=true` whenever paths or derived material may leave the user's local environment. This excludes confidential and unreviewed `UNKNOWN` material.
- Set `own_only=true` only when the user specifically needs material classified as authored by them.
- Never infer or probe `RESTRICTED` material. Shelfmark deliberately makes restricted paths indistinguishable from absent paths.
- Do not describe metadata matches as proof of document-body content. Ask to open a selected file with a separately authorized file-reading tool when its contents are needed.

## Recover From Weak Results

- Remove unsupported filters before broadening the query.
- Try filename, project, organization, author, or slide-title vocabulary rather than sentence fragments from a document.
- Use `browse_folder()` when terminology is uncertain.
- State that a true no-result search is recorded locally by Shelfmark as product feedback; it is not sent anywhere.
