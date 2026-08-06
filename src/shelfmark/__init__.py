"""shelfmark — local, privacy-first document catalogue + MCP server.

Metadata-only indexing of a document tree into SQLite (paths, filenames,
OOXML authorship, slide titles, content hashes), exposed to MCP clients
such as Claude Code. No document body text is ever extracted, so nothing
in the index can leak file content into a prompt.
"""

__version__ = "0.1.0"
