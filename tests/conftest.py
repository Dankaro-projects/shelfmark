"""Fixtures: a synthetic corpus, built from nothing, on every run.

No binary fixtures are committed and no real document is ever read. The
OOXML files are assembled here as zips containing only docProps/ -- which
is also all the probe ever opens, so a fixture that satisfies these tests
exercises exactly the surface the product uses.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
import zipfile
from pathlib import Path

import pytest


def toml_str(p) -> str:
    """A TOML basic-string literal for a path, quotes included.

    f'path = "{p}"' breaks on Windows: str(p) interpolates backslashes into
    a basic string, where \\U opens a unicode escape and tomllib refuses the
    file. json.dumps escaping is valid TOML basic-string escaping — the same
    trick cli.py uses when it rewrites the shipped config."""
    return json.dumps(str(p))


def symlink_or_skip(link: Path, target) -> None:
    """Create link -> target, or skip where symlinks need privileges
    (Windows without Developer Mode). The junction test in test_boundary is
    what covers the boundary on machines that land in the skip."""
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")

APP_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>{app}</Application>
  <Company>{company}</Company>
  <Slides>{n}</Slides>
  <HeadingPairs>
    <vt:vector size="2" baseType="variant">
      <vt:variant><vt:lpstr>Slide Titles</vt:lpstr></vt:variant>
      <vt:variant><vt:i4>{n}</vt:i4></vt:variant>
    </vt:vector>
  </HeadingPairs>
  <TitlesOfParts>
    <vt:vector size="{n}" baseType="lpstr">{titles}</vt:vector>
  </TitlesOfParts>
</Properties>"""

CORE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
    xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:title>{title}</dc:title>
  <dc:creator>{creator}</dc:creator>
  <cp:lastModifiedBy>{creator}</cp:lastModifiedBy>
  <dcterms:created>{created}</dcterms:created>
</cp:coreProperties>"""


def write_pptx(path: Path, titles: list[str], creator: str = "A Person",
               company: str = "", app: str = "Microsoft Office PowerPoint",
               title: str = "", created: str = "2024-03-01T10:00:00Z") -> Path:
    """A .pptx carrying only the properties the probe reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"<vt:lpstr>{t}</vt:lpstr>" for t in titles)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("docProps/app.xml", APP_XML.format(
            app=app, company=company, n=len(titles), titles=body))
        z.writestr("docProps/core.xml", CORE_XML.format(
            title=title, creator=creator, created=created))
    return path


@pytest.fixture(autouse=True)
def _never_touch_the_real_catalogue(tmp_path, monkeypatch):
    """Point config discovery at the sandbox for every test.

    Without this, anything that calls config.load() with no argument -- or
    uses the shipped CONFIG_TEMPLATE, which hardcodes the real default db
    path -- reads and WRITES the operator's own catalogue. That happened.
    """
    monkeypatch.setenv("SHELFMARK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Windows expanduser ignores HOME (it reads USERPROFILE), so without
    # this the sandbox holds on two platforms and leaks on the third.
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A small tree with the shapes the engine has to get right."""
    root = tmp_path / "corpus"

    # Two client trees, enough rows that a prune ceiling means something.
    for i in range(1, 41):
        p = root / "Clients" / "Alpha" / "engagement" / f"status_report_{i}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"alpha {i}\n")
    for i in range(1, 41):
        p = root / "Clients" / "Beta" / "research" / f"market_note_{i}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"beta {i}\n")

    # Credentials and identity documents -> must seal as RESTRICTED.
    sec = root / "Admin"
    sec.mkdir(parents=True, exist_ok=True)
    (sec / ".env").write_text("API_KEY=nope\n")
    (sec / ".env.production").write_text("API_KEY=nope\n")
    (sec / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n")

    # Decks: real titles, library placeholders, PowerPoint's own filler,
    # uniform titles, and a mixed deck.
    d = root / "Decks"
    write_pptx(d / "real_titles.pptx",
               ["The Challenge", "Our Approach", "Results", "Next Steps"],
               creator="A Person", title="Quarterly Review")
    write_pptx(d / "library_placeholders.pptx",
               [f"Slide {i}" for i in range(1, 13)],
               creator="", company="PptxGenJS", app="PptxGenJS")
    write_pptx(d / "powerpoint_filler.pptx",
               ["PowerPoint Presentation"] * 8)
    write_pptx(d / "uniform_titles.pptx", ["Plantilla"] * 6)
    write_pptx(d / "mixed_titles.pptx",
               ["Slide 1", "The Real Heading", "Slide 3", "Slide 4"])
    write_pptx(d / "method_notes.pptx", ["Method"], creator="A Person")
    write_pptx(d / "template.pptx", ["Template"], creator="A Person")
    for i in range(6):
        (d / f"draft_{i}.md").write_text(f"draft {i}\n")

    # A file whose extension lies: .docx that is not a zip.
    (d / "not_really_a_deck.docx").write_text("# just markdown\n")

    # Junk that must never be catalogued.
    j = root / "Clients" / "Alpha"
    (j / "~$status_report_1.md").write_text("lock\n")
    (j / ".~lock.status_report_1.md#").write_text("lock\n")
    (j / ".fuse_hidden0000001700000003").write_text("ghost\n")
    (root / "Clients" / "Alpha" / "node_modules").mkdir(exist_ok=True)
    (root / "Clients" / "Alpha" / "node_modules" / "index.js").write_text("x\n")

    # Identical content in two places -> one duplicate group.
    (root / "Decks" / "shared_brief.md").write_text("same bytes\n")
    (root / "Clients" / "Beta" / "shared_brief.md").write_text("same bytes\n")

    return root


@pytest.fixture
def extra_root(tmp_path: Path) -> Path:
    p = tmp_path / "extra"
    p.mkdir(parents=True, exist_ok=True)
    (p / "loose_note.md").write_text("elsewhere\n")
    return p


@pytest.fixture
def config_path(tmp_path: Path, corpus: Path, extra_root: Path) -> Path:
    """A config with both root kinds and deliberately small guard limits."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(textwrap.dedent(f"""
        [index]
        db = {toml_str(tmp_path / 'catalog.db')}

        [[roots]]
        path = {toml_str(corpus)}

        [[roots]]
        label = "extra"
        path = {toml_str(extra_root)}

        [authors]
        own = ['^A Person$']
        client = ['^Client Person$']

        [rights]
        client_roots = ["Clients"]
        own_prefixes = ["Decks"]

        [facets]
        work_roots = ["Clients"]

        [refresh]
        prune_ceiling_pct = 2
        prune_min_rows = 10
        coverage_floor_pct = 80
    """).strip() + "\n")
    return cfg


@pytest.fixture
def cfg(config_path: Path):
    from shelfmark import config as config_mod
    return config_mod.load(config_path)


@pytest.fixture
def built(cfg):
    """A refreshed catalogue. Returns the Config."""
    from shelfmark import refresh
    assert refresh.run(cfg) == 0
    return cfg


def rows(cfg, sql: str, *params):
    con = sqlite3.connect(cfg.db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def one(cfg, sql: str, *params):
    return rows(cfg, sql, *params)[0][0]


def hits(out: str) -> list[str]:
    """The result rows of a tool's output, without its prose.

    Assert against these, not the whole string: a 'no matches' message echoes
    the query back, so a naive substring check on the search term passes or
    fails for the wrong reason."""
    return [ln[2:].strip() for ln in out.splitlines() if ln.startswith("- ")]
