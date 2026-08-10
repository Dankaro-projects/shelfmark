"""Cloud-residency detection, on every platform the walk can land on.

A dataless placeholder satisfies stat's size and mtime like a real file, so
residency is the one input the (size, mtime) incremental skip cannot see —
get it wrong and the catalogue hashes ghosts or marks real files evicted.
The platform signals differ completely: POSIX/macOS reveal eviction through
allocation (st_blocks), Windows through recall attributes on the stat
result. These tests drive is_evicted with stand-in stat results carrying
exactly the attribute surface each platform provides, so the Windows branch
is exercised by every CI leg, not only the Windows one.
"""

from types import SimpleNamespace as NS

from shelfmark.catalog import is_evicted

# Win32 file-attribute bits, spelled out because the stat module only names
# some of them (and only on Windows).
OFFLINE = 0x00001000
RECALL_ON_OPEN = 0x00040000
RECALL_ON_DATA = 0x00400000
ARCHIVE = 0x00000020
PINNED = 0x00080000


# ----------------------------------------------------------- POSIX / macOS

def test_posix_dataless_file_is_evicted():
    assert is_evicted(NS(st_size=100, st_blocks=0))


def test_posix_allocated_file_is_materialised():
    assert not is_evicted(NS(st_size=100, st_blocks=8))


def test_posix_empty_file_is_not_evicted():
    """Zero blocks on a zero-byte file is just an empty file."""
    assert not is_evicted(NS(st_size=0, st_blocks=0))


# ----------------------------------------------------------------- Windows

def test_windows_recall_on_data_access_is_evicted():
    """The bit OneDrive sets on a dataless placeholder."""
    assert is_evicted(NS(st_size=100, st_file_attributes=ARCHIVE | RECALL_ON_DATA))


def test_windows_recall_on_open_is_evicted():
    assert is_evicted(NS(st_size=100, st_file_attributes=RECALL_ON_OPEN))


def test_windows_offline_is_evicted():
    assert is_evicted(NS(st_size=100, st_file_attributes=OFFLINE))


def test_windows_hydrated_file_is_materialised():
    """PINNED ('always keep on this device') without recall bits is a fully
    local file."""
    assert not is_evicted(NS(st_size=100, st_file_attributes=ARCHIVE | PINNED))


def test_windows_attributes_overrule_a_zero_block_count():
    """An NTFS sparse file can allocate zero blocks while fully local; if the
    attributes are present they are authoritative and the POSIX block test
    must not run."""
    assert not is_evicted(
        NS(st_size=100, st_file_attributes=ARCHIVE, st_blocks=0))


# ----------------------------------------------------- neither signal exists

def test_no_signal_reads_as_materialised():
    """A filesystem reporting neither attributes nor blocks: claiming
    eviction with no evidence would freeze every file out of hashing."""
    assert not is_evicted(NS(st_size=100))


# ------------------------------------------------------- the junction guard

def test_a_junction_flagged_directory_is_pruned_and_counted(tmp_path,
                                                            monkeypatch):
    """Platform-independent proof the walk consults the junction guard.

    On POSIX _is_junction is constitutionally False, so fake it and require
    the walk to both refuse the subtree and report the refusal in the
    symlinks count — the real-junction twin lives in test_boundary and runs
    where junctions exist."""
    from shelfmark import catalog
    from shelfmark import config as cm
    from conftest import toml_str

    corpus = tmp_path / "corpus"
    (corpus / "junc").mkdir(parents=True)
    (corpus / "real.md").write_text("x\n")
    (corpus / "junc" / "outside.md").write_text("y\n")
    f = tmp_path / "c.toml"
    f.write_text(f'[index]\ndb = {toml_str(tmp_path / "cat.db")}\n\n'
                 f'[[roots]]\npath = {toml_str(corpus)}\n')
    cfg = cm.load(f)

    monkeypatch.setattr(catalog, "_is_junction",
                        lambda path: path.endswith("junc"))
    stats = {}
    keys = [rel for _, rel in catalog.walk(cfg, stats=stats)]
    assert keys == ["real.md"]
    assert stats["symlinks"] == 1
