---
name: shelfmark-manage-catalogue
description: Set up, inspect, refresh, review, diagnose, or repair a local Shelfmark document catalogue. Use when installing Shelfmark, choosing indexed roots, correcting rights classifications, checking freshness, connecting its MCP server, or responding to a failed or degraded refresh.
---

# Manage a Shelfmark Catalogue

Maintain the local catalogue without weakening its filesystem and governance safeguards.

## Workflow

1. Run `shelfmark --version` and `shelfmark doctor` to establish the installed version and current state. If Shelfmark is absent and installation is part of the request, install it with `uv tool install shelfmark`.
2. Run `shelfmark config` before changing roots or classification rules. Treat the displayed config path as the authoritative file.
3. Use `shelfmark init` only when no config exists. Do not overwrite an existing config with `--force` unless the user explicitly asks to replace it.
4. Run `shelfmark refresh` after setup or a deliberate config change. Preserve stderr in automation because refresh warnings and refusals are operational output.
5. Run `shelfmark review` first as a dry run. Use `shelfmark review --apply` only when the user wants the proposed classifications written.
6. Finish with `shelfmark doctor` and `shelfmark stats`. Report the config location, indexed roots, freshness, rights split, and any unresolved finding.

## Handle a Refused Refresh

- Treat a short walk or excessive-prune refusal as protection against accidental catalogue loss.
- Inspect `shelfmark doctor`, the resolved roots from `shelfmark config`, and filesystem availability before changing anything.
- Use `shelfmark refresh --force` only after confirming that the missing files were intentionally moved or deleted. State that this accepts the new smaller corpus and may prune catalogue rows.
- Do not hide failures, discard stderr, or report a stale catalogue as healthy.

## Connect the MCP Server

Prefer the plugin's bundled MCP configuration. For clients configured manually, launch `shelfmark serve` or the compatibility entry point `shelfmark-mcp` over stdio.

After connecting, call `corpus_stats()` to confirm that the client reaches the intended catalogue and that the index is fresh.
