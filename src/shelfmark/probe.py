#!/usr/bin/env python3
"""Extract Office document structure from OOXML metadata only.

Reads docProps/app.xml + core.xml from pptx/docx/xlsx. Never touches slide,
paragraph or cell content -- so it is fast, and it cannot leak body text.
Emits one JSON object per file.
"""
import json, os, re, sys, zipfile
import xml.etree.ElementTree as ET

VT = '{http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes}'
EP = '{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}'
DC = '{http://purl.org/dc/elements/1.1/}'
CP = '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}'
DCT = '{http://purl.org/dc/terms/}'

# "slide titles" heading, localised
SLIDE_HEADINGS = {
    'slide titles', 'títulos de diapositiva', 'titulos de diapositiva',
    'titluri de diapozitive', 'folientitel', 'titres des diapositives',
}
SHEET_HEADINGS = {'worksheets', 'hojas de cálculo', 'hojas de calculo', 'feuilles de calcul'}

# TitlesOfParts is only as good as the deck's title placeholders, and most
# decks have none. Two placeholder families dominate real corpora:
#   * "Slide 7"                 -- libraries (pptxgenjs, python-pptx, HTML->
#                                  deck exporters) fall back to the slide name
#   * "PowerPoint Presentation" -- PowerPoint's own filler for a slide whose
#                                  title placeholder was never filled
# Storing either is worse than storing nothing: search matches them, and
# get_file() lists twelve "titles" for a deck whose real headings are
# unknown. Drop the placeholders, keep any genuine title on the same deck;
# an all-placeholder deck ends up with no slide titles, which is the truth.
_PRESENTATION = (r'presentation|presentaci[o\u00f3]n|pr[\u00e9e]sentation'
                 r'|pr[\u00e4a]sentation|presentazione'
                 r'|apresenta[\u00e7c][\u00e3a]o|prezentare|prezentacja')
PLACEHOLDER_TITLE_RE = re.compile(
    r'^(?:'
    r'(?:slide|diapositiva|diapositive|diapozitiv|folie|slajd|dia)[\s_-]*\d+'
    r'|(?:microsoft[\s-]*)?powerpoint(?:[\s-]*(?:' + _PRESENTATION + r'))?'
    r'|(?:' + _PRESENTATION + r')[\s-]*(?:de |do |di |von )?powerpoint'
    r')$', re.I)


def drop_placeholder_titles(chunk):
    """Blank the titles that carry no information, keeping every slot.

    Positions are preserved so a dropped title never renumbers the ones
    after it -- the caller uses the index as the slide number.

    The uniformity rule is the backstop: whatever the string says, a deck
    where every slide reports the SAME title is describing its template, not
    its content. That catches placeholder forms this module has never seen,
    in languages it does not enumerate -- which a fixed pattern list cannot.
    Off below three slides: a two-slide deck can legitimately repeat one
    heading, and there is nothing to generalise from."""
    stripped = [(t or '').strip() for t in chunk]
    named = [t for t in stripped if t]
    uniform = len(named) >= 3 and len(set(named)) == 1
    return ['' if (uniform or PLACEHOLDER_TITLE_RE.match(t)) else t
            for t in stripped]


def _text(el):
    return (el.text or '').strip() if el is not None else ''


def parse_app(z):
    """Return (kind->titles dict, slide_count, app_meta)."""
    out, meta = {}, {}
    try:
        root = ET.fromstring(z.read('docProps/app.xml'))
    except (KeyError, ET.ParseError):
        return out, meta

    for tag in ('Slides', 'Pages', 'Words', 'Paragraphs', 'Company', 'Application', 'TotalTime'):
        v = _text(root.find(EP + tag))
        if v:
            meta[tag.lower()] = v

    hp = root.find(EP + 'HeadingPairs')
    tp = root.find(EP + 'TitlesOfParts')
    if hp is None or tp is None:
        return out, meta

    # HeadingPairs: alternating <vt:lpstr>name</vt:lpstr><vt:i4>count</vt:i4>
    pairs, vec = [], hp.find(VT + 'vector')
    if vec is None:
        return out, meta
    kids = list(vec)
    for i in range(0, len(kids) - 1, 2):
        name = _text(kids[i].find(VT + 'lpstr')) or _text(kids[i])
        cnt = _text(kids[i + 1].find(VT + 'i4')) or _text(kids[i + 1])
        try:
            pairs.append((name, int(cnt)))
        except ValueError:
            pass

    tvec = tp.find(VT + 'vector')
    if tvec is None:
        return out, meta
    titles = [_text(e) for e in tvec.findall(VT + 'lpstr')]

    # walk the flat vector using the pair counts as offsets
    off = 0
    for name, cnt in pairs:
        chunk = titles[off:off + cnt]
        off += cnt
        key = name.strip().lower()
        if key in SLIDE_HEADINGS:
            out['slide_titles'] = drop_placeholder_titles(chunk)
        elif key in SHEET_HEADINGS:
            out['sheets'] = chunk
    return out, meta


def parse_core(z):
    meta = {}
    try:
        root = ET.fromstring(z.read('docProps/core.xml'))
    except (KeyError, ET.ParseError):
        return meta
    for k, tag in (('title', DC + 'title'), ('subject', DC + 'subject'),
                   ('creator', DC + 'creator'), ('keywords', CP + 'keywords'),
                   ('lastmod_by', CP + 'lastModifiedBy'), ('revision', CP + 'revision'),
                   ('created', DCT + 'created'), ('modified', DCT + 'modified')):
        v = _text(root.find(tag))
        if v:
            meta[k] = v
    return meta


# Cloud residency. A dataless placeholder satisfies stat's size and mtime
# like a real file; only allocation (POSIX) or the recall attributes
# (Windows) reveal it. This is the single authority -- catalog.py re-exports
# it, and every other caller imports it from there. It lives here because
# probe is the lowest module in the import graph and must stay runnable as
# a standalone script.
#
# macOS: iCloud marks the file itself dataless -- st_blocks drops to 0 while
# st_size stays. Do NOT test for '.icloud' stub files; modern macOS does not
# create them, and an empty stub search reads as 'everything is
# materialised' when it is not.
#
# Windows: st_blocks does not exist, so the old check silently returned
# False and OneDrive eviction was invisible. Cloud filter drivers mark
# placeholders with recall attributes instead. The stat module does not name
# the recall bits, so the ABI-stable Win32 values are spelled here.
_WIN_DATALESS = (
    0x00001000        # FILE_ATTRIBUTE_OFFLINE (HSM / cloud-evicted)
    | 0x00040000      # FILE_ATTRIBUTE_RECALL_ON_OPEN
    | 0x00400000      # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


def is_evicted(st) -> bool:
    """True when the file is a dataless cloud placeholder.

    st_file_attributes is checked first: on Windows it always exists and is
    authoritative (an NTFS sparse file can carry zero allocated blocks while
    fully local, so a block test there would lie in both directions). On
    filesystems reporting neither attributes nor block counts this returns
    False."""
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None:
        return bool(attrs & _WIN_DATALESS)
    blocks = getattr(st, "st_blocks", None)
    return blocks == 0 and st.st_size > 0 if blocks is not None else False


def probe(path):
    rec = {'path': path}
    st = os.stat(path)
    rec['bytes'] = st.st_size
    if is_evicted(st):
        rec['error'] = 'evicted-cloud'
        return rec
    try:
        with zipfile.ZipFile(path) as z:
            parts, app = parse_app(z)
            rec.update(parse_core(z))
            rec.update(app)
            for k, v in parts.items():
                rec[k] = v
    except zipfile.BadZipFile:
        rec['error'] = 'not-a-zip (corrupt/truncated)'
    except Exception as e:                      # noqa: BLE001
        rec['error'] = f'{type(e).__name__}: {e}'
    return rec


if __name__ == '__main__':
    for line in sys.stdin:
        p = line.rstrip('\n')
        if p:
            print(json.dumps(probe(p), ensure_ascii=False))
