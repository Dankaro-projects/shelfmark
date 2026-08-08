"""Built-in classification defaults.

Everything here is corpus-neutral. Anything operator-specific — private path
patterns, author names, ownership prefixes — lives in config.toml, never in
code. Config-supplied rules are merged in by `shelfmark.config`.

Hard-won rules for anyone editing these:

* Anchor every short regex alternative on BOTH sides. Unanchored `rfi`
  matched inside "Dockerfile" and inside the Spanish word "perfil";
  unanchored `tender` matched inside an unrelated proper noun. Before
  adding or deleting an alternative, list the filenames it actually matches.
* Doc-type rules run on the FILENAME only, never the whole path. Path
  matching lets one folder brand all of its children (a "Meeting …" folder
  turned every spreadsheet inside it into meeting minutes). Folder meaning
  is a separate dimension: context_type.
* A rule edit does not relabel already-catalogued files — the builder is
  incremental. Follow every rule change with `shelfmark reclassify`.
"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ scanning

# Exact-matched per directory NAME, anywhere in the tree. Every entry earns
# its place by being (a) machine-written, (b) never where a person files a
# document, and (c) common enough that walking it quietly poisons a first
# index — an install report on a dev machine measured 34,788 files walked
# for 711 wanted, the gap almost entirely build output the defaults missed:
#   build, dist                              JS / Python / Gradle output
#   target                                   Rust and Maven output
#   .tox .mypy_cache .pytest_cache .ruff_cache   Python tool caches
#   .gradle, .idea                           Gradle state, JetBrains metadata
#   .terraform                               provider binaries
#   .parcel-cache, .turbo                    JS bundler caches
#   DerivedData, Pods                        Xcode output, CocoaPods deps
# Deliberately NOT skipped: vendor/, bin/, out/ — all three are real
# document-folder names in real corpora (a design vendor's folder, a "bin"
# of scans, an "out" tray), and cutting them silently is worse than walking
# a build tree loudly. An operator whose projects use them adds them to
# [scan].skip_dirs in config. Matching stays case-sensitive on purpose:
# "Build" (a person's folder) is not "build" (a tool's).
DEFAULT_SKIP_DIRS = frozenset({
    "node_modules", "venv", ".venv", "__pycache__", ".git", ".next",
    ".playwright-mcp", "site-packages", ".Trash", ".cache",
    "build", "dist", "target", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".gradle", ".idea", ".terraform", ".parcel-cache",
    ".turbo", "DerivedData", "Pods",
})
DEFAULT_SKIP_NAMES = frozenset({".DS_Store", ".localized", "Icon\r"})

# Filenames are exact-matched against the skip list, so churning machine
# names need a prefix rule instead. All three are artefacts of a file being
# open or half-deleted, never documents:
#   ~$        Office lock/owner files
#   .~lock.   LibreOffice lock files
#   .fuse_hidden  FUSE's stand-in for a file deleted while still open —
#                 these accumulate, and being byte-identical they also show
#                 up as spurious duplicate groups
DEFAULT_SKIP_PREFIXES = ("~$", ".~lock.", ".fuse_hidden")

# ----------------------------------------------------------------- sensitivity

# Credentials and identity documents. Matched files become RESTRICTED and are
# flagged so no later feature (hashing, content extraction) ever opens them.
#
# Two details that were learned the hard way:
#  * `.env` must be `(\.|$)`-terminated, not bare `$`: .env.production and
#    .env.local hold live secrets and slip through otherwise.
#  * `.key` is NOT in the extension list. On macOS it is overwhelmingly Apple
#    Keynote; real private keys are matched by NAME instead.
DEFAULT_SECRET_RE = re.compile(
    r"(^|/)\.env(\.|$)|\.env$"
    r"|\.(p12|pem|kdbx|jks|ovpn|pfx|asc|gpg|keystore|ppk)$"
    r"|(^|/)(id_rsa|id_ed25519|id_ecdsa|id_dsa)(\.|$)"
    r"|(private|server|client|secret)[-_.]?key"
    r"|backup-?codes?|2fa|recovery-?codes?|\bDNI\b|passport|pasaporte"
    r"|credentials?\.(json|yml|yaml|txt)$|service[-_]?account.*\.json$",
    re.I,
)

# --------------------------------------------------------------- tool authors

# OOXML `creator` strings written by generation tooling rather than a person.
# A file whose author is a library was produced by the operator's own pipeline.
DEFAULT_TOOL_AUTHOR_RE = re.compile(
    r"pptxgenjs|openpyxl|python-docx|python-pptx|reportlab|matplotlib|"
    r"libreoffice|pandoc",
    re.I,
)

# ------------------------------------------------------------------ doc types

# What a file IS, from its filename. First match wins, so order matters.
# Bilingual EN/ES by default; extend or override per-corpus in config.toml.
DEFAULT_DOC_TYPE_RULES: list[tuple[str, str]] = [
    ("proposal",   r"proposal|propuesta|oferta|\bbid\b|\btenders?\b|\brfp\b|\brfi\b"),
    ("sow",        r"\bsow\b|scope of work|statement of work|engagement letter"),
    ("contract",   r"contract|contrato|\bmsa\b|\bnda\b|agreement|acuerdo|convenio|addendum|anexo"),
    ("invoice",    r"invoice|factura|\bfra\b|remittance|abono|presupuesto|quote"),
    # `\b1[:-]1\b`, never `1-?1`: the unanchored form matches "11" anywhere
    # (homepage-11.html, 11.jpg) and once mislabelled 7,197 files as minutes.
    ("minutes",    r"minutes|\bacta\b|\bnotes\b|meeting|steering|standup|\b1[:-]1\b|1-on-1"),
    ("report",     r"report|informe|memoria|whitepaper|white paper|\bstudy\b|estudio|analysis|análisis"),
    ("deck",       r"deck|presentation|presentaci|slides|pitch|keynote|webinar"),
    # NB `epic` is deliberately absent: in at least one real corpus every
    # `epic` match was a proper noun, none an agile epic. If your corpus is
    # agile-flavoured, add it back via [doc_types] rules in config.toml.
    ("roadmap",    r"roadmap|backlog|sprint|user stor"),
    ("model",      r"\bmodel\b|\bmodels\b|forecast|budget|\bpnl\b|p&l|valuation|cashflow|cash flow|sizing|\btam\b"),
    ("brand",      r"brand|logo|identity|identidad|style ?guide|design system|sistema de dise|\bkit\b"),
    # \bresume\b, not bare `resume`: bare would swallow Spanish "resumen".
    ("cv",         r"\bcv\b|\bresume\b|curriculum|\bbio\b|\bprofile\b"),
    ("research",   r"research|market|competitive|benchmark|landscape|survey|encuesta"),
    ("legal",      r"legal|compliance|gdpr|policy|política|terms|privacy"),
    ("data",       r"\bdata\b|dataset|export|dump|registry"),
]

# Fallback when no filename rule fires: the extension says what it is.
DEFAULT_EXT_FALLBACK: dict[str, str] = {
    ".pptx": "deck", ".ppt": "deck", ".key": "deck",
    ".xlsx": "spreadsheet", ".xls": "spreadsheet", ".csv": "data",
    ".docx": "document", ".doc": "document", ".pages": "document",
    ".pdf": "pdf", ".msg": "email", ".eml": "email",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".heic": "image",
    ".webp": "image", ".gif": "image", ".tif": "image",
    ".svg": "vector", ".ai": "vector", ".eps": "vector",
    ".mp4": "video", ".mov": "video", ".avi": "video",
    ".md": "note", ".txt": "note", ".html": "web",
    ".zip": "archive", ".7z": "archive",
    ".rtf": "document", ".odt": "document", ".epub": "document",
    ".yml": "config", ".yaml": "config", ".toml": "config", ".ini": "config",
    ".json": "data", ".sql": "data", ".db": "data", ".sqlite": "data",
    ".py": "code", ".js": "code", ".ts": "code", ".tsx": "code",
    ".jsx": "code", ".sh": "code", ".mjs": "code",
    # Fonts get their own type: ext-locked but without a fallback they all
    # sink to `code`, and "fa-brands-400.ttf" must never reach `brand`.
    ".ttf": "font", ".otf": "font", ".woff": "font", ".woff2": "font",
    ".eot": "font",
    ".vsd": "diagram", ".vsdx": "diagram", ".drawio": "diagram",
}

# Extensions whose semantics come from the FORMAT, never the filename.
# Without this, report.json / report.css / render-report.mjs all become
# doc_type='report' and pollute every document-facing view with build junk.
FORMAT_WINS_EXT = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".lock",
    ".css", ".scss", ".less", ".html", ".htm", ".xml",
    ".sql", ".db", ".sqlite", ".log", ".map", ".env",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".heic", ".tif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
})

# Build files whose meaning is in the NAME, not the extension — they either
# have none (Dockerfile, Makefile) or carry an arbitrary suffix that parses
# as one (Dockerfile.cloud-run reads as ext=".cloud-run").
FORMAT_WINS_NAME = re.compile(
    r"^(dockerfile|makefile|procfile|jenkinsfile|vagrantfile|gemfile|rakefile|"
    r"brewfile|caddyfile|justfile|\.env|\.gitignore|\.dockerignore)",
    re.I,
)

# ------------------------------------------------------------- context types

# What a FOLDER is for. A "Meeting <place> <date>" folder is an engagement
# prep workspace — the material inside is briefing context for it, not
# minutes of it. Its own dimension so a spreadsheet stays a spreadsheet
# while remaining findable as engagement prep.
DEFAULT_CONTEXT_RULES: list[tuple[str, str]] = [
    ("engagement-prep", r"meeting|workshop|offsite|off-site|session|visita|visit\b"),
    ("governance",      r"steering|board|committee|comite|comité|governance|qbr\b"),
    ("pitch",           r"pitch|proposal|propuesta|bid\b|tender|rfp"),
    ("event",           r"conference|summit|expo|fair|feria|webinar|congres|forum|foro"),
    ("training",        r"training|curso|course|academy|lecturer|master|máster"),
    ("delivery",        r"deliverab|final report|entrega|handover"),
    ("research",        r"research|market|competitive|benchmark|landscape|due dilig|\bdd\b"),
    ("brand",           r"brand|identity|identidad|design system|kit\b|assets"),
    ("admin",           r"invoice|factura|contract|contrato|legal|\bhr\b|payroll|nomina"),
    ("archive",         r"archive|archivo|old|backup|historic|antigua?s?"),
    ("inbox",           r"inbox|unsorted|downloads|misc|varios|temp\b|tmp\b"),
]

# --------------------------------------------------------------------- misc

VERSION_RE = re.compile(
    r"\b(v\d+(?:\.\d+)*|final|draft|borrador|rev\d+|copy|copia)\b", re.I)
DATE_IN_NAME_RE = re.compile(
    r"(20\d{2})[-_.]?(0[1-9]|1[0-2])?[-_.]?(0[1-9]|[12]\d|3[01])?")

OOXML_EXT = frozenset({".pptx", ".docx", ".xlsx"})
