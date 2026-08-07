"""shelfmark — local, privacy-first document catalogue + MCP server.

Metadata-only indexing of a document tree into SQLite (paths, filenames,
OOXML authorship, slide titles, content hashes), exposed to MCP clients
such as Claude Code. No document body text is ever extracted, so nothing
in the index can leak file content into a prompt.
"""

# Read from installed metadata, not restated here. A hand-maintained copy
# drifts the moment a release forgets it -- 0.2.0 shipped while this said
# 0.1.0, so `shelfmark --version` lied to anyone filing a bug. pyproject is
# the single source of truth; this just reports it.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("shelfmark")
except PackageNotFoundError:        # a source tree that was never installed
    __version__ = "0+unknown"
