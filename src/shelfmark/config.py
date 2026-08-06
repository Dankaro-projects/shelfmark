"""Configuration: where the corpus lives and how it is classified.

Everything operator-specific comes from a TOML file, resolved in this order:

    1. explicit --config flag / `path=` argument
    2. $SHELFMARK_CONFIG
    3. ~/.config/shelfmark/config.toml

The catalogue location can additionally be overridden with $SHELFMARK_DB
(useful for the MCP server registration).

One rule the config cannot relax: the catalogue must live OUTSIDE any
cloud-synced folder. It is a mutating binary database; putting it inside
iCloud Drive / Dropbox / OneDrive thrashes sync on every refresh.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import rules

ENV_CONFIG = "SHELFMARK_CONFIG"
ENV_DB = "SHELFMARK_DB"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "shelfmark" / "config.toml"
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "shelfmark" / "catalog.db"


class ConfigError(Exception):
    """Bad or missing configuration. Message is operator-facing."""


@dataclass(frozen=True)
class Root:
    """One indexed tree. The primary root has label '' and catalogue paths
    are stored relative to it; extra roots are stored under 'label/…' so
    they can never collide with primary-relative paths."""
    label: str
    path: Path


def _compile(pattern_list: list[str], flags=re.I) -> re.Pattern | None:
    parts = [p for p in pattern_list if p]
    if not parts:
        return None
    try:
        return re.compile("|".join(f"(?:{p})" for p in parts), flags)
    except re.error as exc:
        raise ConfigError(f"Bad regex in config: {exc}") from exc


def _norm_prefixes(items: list[str]) -> tuple[str, ...]:
    """Normalise path prefixes to end with '/' so startswith() cannot match
    half a folder name ('Work/Acme' matching 'Work/AcmeCorp-old/')."""
    out = []
    for p in items:
        p = str(p).strip().strip("/")
        if p:
            out.append(p + "/")
    return tuple(out)


def has_prefix(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel.startswith(p) or rel == p[:-1] for p in prefixes)


@dataclass
class Config:
    source: Path | None
    db: Path
    roots: tuple[Root, ...]

    skip_dirs: frozenset[str]
    skip_names: frozenset[str]

    secret_re: re.Pattern
    private_re: re.Pattern | None
    own_author_re: re.Pattern | None
    client_author_re: re.Pattern | None
    tool_author_re: re.Pattern | None

    # rights: path-prefix rules (all catalogue-relative, trailing-/ normalised)
    own_prefixes: tuple[str, ...] = ()
    own_confidential_prefixes: tuple[str, ...] = ()
    reference_prefixes: tuple[str, ...] = ()
    neutral_prefixes: tuple[str, ...] = ()
    client_roots: tuple[str, ...] = ()
    personal_roots: tuple[str, ...] = ()
    work_under_personal: tuple[str, ...] = ()

    # facets
    facet_work_roots: frozenset[str] = frozenset()
    facet_personal_roots: frozenset[str] = frozenset()
    facet_reanchor: tuple[str, ...] = ()

    # classification (compiled, config merged over defaults)
    doc_type_rules: list = field(default_factory=list)
    ext_fallback: dict = field(default_factory=dict)
    context_rules: list = field(default_factory=list)

    # refresh behaviour
    max_age_seconds: int = 900
    prune_ceiling_pct: int = 2
    prune_min_rows: int = 50
    coverage_floor_pct: int = 80
    stale_after_hours: float = 3.0

    # server behaviour
    max_limit: int = 100
    body_chars: int = 1500

    # folder cards: a subtree earns a card if it is big, or dense in
    # reusable material even when small
    cards_min_files: int = 100
    cards_min_open: int = 20

    # ------------------------------------------------------------- derived

    @property
    def primary_root(self) -> Root:
        return next(r for r in self.roots if r.label == "")

    @property
    def extra_roots(self) -> dict[str, Path]:
        return {r.label: r.path for r in self.roots if r.label}

    @property
    def status_path(self) -> Path:
        return self.db.parent / "REFRESH_STATUS.json"

    @property
    def dirty_marker(self) -> Path:
        return self.db.parent / ".dirty"

    @property
    def lock_dir(self) -> Path:
        return self.db.parent / ".refresh.lock"

    @property
    def log_path(self) -> Path:
        return self.db.parent / "logs" / "refresh.log"

    def abs_path(self, rel: str) -> Path:
        """Absolute on-disk location of a catalogue path.

        Not every row is primary-root-relative: extra-root rows carry a label
        prefix and live elsewhere. Joining those onto the primary root yields
        a path that does not exist."""
        for label, base in self.extra_roots.items():
            if rel == label or rel.startswith(label + "/"):
                return base / rel[len(label):].lstrip("/")
        return self.primary_root.path / rel


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def resolve_config_path(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG_PATH


def load(explicit: str | os.PathLike | None = None) -> Config:
    path = resolve_config_path(explicit)
    if not path.exists():
        raise ConfigError(
            f"No config found at {path}.\n"
            f"Run `shelfmark init` to create one, or point "
            f"${ENV_CONFIG} at an existing file."
        )
    data = _load_toml(path)

    # ---- roots ----------------------------------------------------------
    raw_roots = data.get("roots") or []
    if not raw_roots:
        raise ConfigError(f"{path}: at least one [[roots]] entry is required.")
    roots: list[Root] = []
    for r in raw_roots:
        p = Path(str(r.get("path", ""))).expanduser()
        if not str(p):
            raise ConfigError(f"{path}: a [[roots]] entry is missing `path`.")
        label = str(r.get("label", "")).strip().strip("/")
        roots.append(Root(label=label, path=p))
    primaries = [r for r in roots if r.label == ""]
    if len(primaries) != 1:
        raise ConfigError(
            f"{path}: exactly one [[roots]] entry must have no label "
            f"(the primary root); found {len(primaries)}."
        )
    labels = [r.label for r in roots if r.label]
    if len(labels) != len(set(labels)):
        raise ConfigError(f"{path}: extra-root labels must be unique.")

    # ---- index location -------------------------------------------------
    idx = data.get("index", {})
    db = Path(os.environ.get(ENV_DB)
              or idx.get("db", DEFAULT_DB_PATH)).expanduser()
    primary = primaries[0].path
    try:
        db.relative_to(primary)
        raise ConfigError(
            f"{path}: index.db ({db}) sits INSIDE the primary root "
            f"({primary}). The catalogue is a mutating binary DB and must "
            f"live outside any indexed or cloud-synced tree."
        )
    except ValueError:
        pass

    # ---- scanning -------------------------------------------------------
    scan = data.get("scan", {})
    skip_dirs = frozenset(rules.DEFAULT_SKIP_DIRS) | frozenset(
        scan.get("skip_dirs", []))
    skip_names = frozenset(rules.DEFAULT_SKIP_NAMES) | frozenset(
        scan.get("skip_names", []))

    # ---- privacy --------------------------------------------------------
    priv = data.get("privacy", {})
    extra_secret = _compile(priv.get("secret_patterns", []))
    if extra_secret:
        secret_re = re.compile(
            f"(?:{rules.DEFAULT_SECRET_RE.pattern})|(?:{extra_secret.pattern})",
            re.I)
    else:
        secret_re = rules.DEFAULT_SECRET_RE
    private_re = _compile(priv.get("private_paths", []))

    # ---- authors --------------------------------------------------------
    auth = data.get("authors", {})
    own_author_re = _compile(auth.get("own", []))
    client_author_re = _compile(auth.get("client", []))
    extra_tools = _compile(auth.get("tool", []))
    if extra_tools:
        tool_author_re = re.compile(
            f"(?:{rules.DEFAULT_TOOL_AUTHOR_RE.pattern})|(?:{extra_tools.pattern})",
            re.I)
    else:
        tool_author_re = rules.DEFAULT_TOOL_AUTHOR_RE

    # ---- rights ---------------------------------------------------------
    ri = data.get("rights", {})

    # ---- facets ---------------------------------------------------------
    fa = data.get("facets", {})

    # ---- doc types ------------------------------------------------------
    dt = data.get("doc_types", {})
    disabled = set(dt.get("disable", []))
    user_rules = [(str(n), str(p)) for n, p in dt.get("rules", [])]
    merged_rules = user_rules + [
        (n, p) for n, p in rules.DEFAULT_DOC_TYPE_RULES if n not in disabled]
    try:
        doc_type_rules = [(n, re.compile(p, re.I)) for n, p in merged_rules]
    except re.error as exc:
        raise ConfigError(f"{path}: bad [doc_types] rule regex: {exc}") from exc
    ext_fallback = dict(rules.DEFAULT_EXT_FALLBACK)
    ext_fallback.update({str(k).lower(): str(v)
                         for k, v in dt.get("ext", {}).items()})

    # ---- context types --------------------------------------------------
    ct = data.get("context_types", {})
    user_ctx = [(str(n), str(p)) for n, p in ct.get("rules", [])]
    merged_ctx = user_ctx + [
        (n, p) for n, p in rules.DEFAULT_CONTEXT_RULES
        if n not in set(ct.get("disable", []))]
    try:
        context_rules = [(n, re.compile(p, re.I)) for n, p in merged_ctx]
    except re.error as exc:
        raise ConfigError(f"{path}: bad [context_types] rule regex: {exc}") from exc

    # ---- refresh / server / cards tunables -------------------------------
    rf = data.get("refresh", {})
    sv = data.get("server", {})
    cd = data.get("cards", {})

    return Config(
        source=path,
        db=db,
        roots=tuple(roots),
        skip_dirs=skip_dirs,
        skip_names=skip_names,
        secret_re=secret_re,
        private_re=private_re,
        own_author_re=own_author_re,
        client_author_re=client_author_re,
        tool_author_re=tool_author_re,
        own_prefixes=_norm_prefixes(ri.get("own_prefixes", [])),
        own_confidential_prefixes=_norm_prefixes(
            ri.get("own_confidential_prefixes", [])),
        reference_prefixes=_norm_prefixes(ri.get("reference_prefixes", [])),
        neutral_prefixes=_norm_prefixes(ri.get("neutral_prefixes", [])),
        client_roots=_norm_prefixes(ri.get("client_roots", [])),
        personal_roots=_norm_prefixes(ri.get("personal_roots", [])),
        work_under_personal=_norm_prefixes(ri.get("work_under_personal", [])),
        facet_work_roots=frozenset(fa.get("work_roots", [])),
        facet_personal_roots=frozenset(fa.get("personal_roots", [])),
        facet_reanchor=_norm_prefixes(fa.get("reanchor", [])),
        doc_type_rules=doc_type_rules,
        ext_fallback=ext_fallback,
        context_rules=context_rules,
        max_age_seconds=int(rf.get("max_age_seconds", 900)),
        prune_ceiling_pct=int(rf.get("prune_ceiling_pct", 2)),
        prune_min_rows=int(rf.get("prune_min_rows", 50)),
        coverage_floor_pct=int(rf.get("coverage_floor_pct", 80)),
        stale_after_hours=float(rf.get("stale_after_hours", 3)),
        max_limit=int(sv.get("max_limit", 100)),
        body_chars=int(sv.get("body_chars", 1500)),
        cards_min_files=int(cd.get("min_files", 100)),
        cards_min_open=int(cd.get("min_own_open", 20)),
    )
