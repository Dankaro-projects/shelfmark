"""SQLite schema for the catalogue.

The `files` table carries two independent axes that must never be conflated:

    rights        — may I REUSE this? (OWN / REFERENCE / RESTRICTED / UNKNOWN)
    confidential  — may it LEAVE? (0 = fine, 1 = in confidence)

A deck authored for a client is OWN (the method is yours to reuse) *and*
confidential (that artefact does not leave). One column cannot say both.

Likewise `doc_type` (what the file IS, from filename then extension) and
`context_type` (what its FOLDER is for) are separate dimensions.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    filename      TEXT NOT NULL,
    root          TEXT,
    client        TEXT,
    project       TEXT,
    depth         INTEGER,
    ext           TEXT,
    bytes         INTEGER,
    evicted       INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'ok',
    mtime         TEXT,
    btime         TEXT,
    name_year     INTEGER,
    version_tag   TEXT,
    doc_type      TEXT,
    doc_type_src  TEXT,
    context_type  TEXT,
    folder_label  TEXT,
    domain        TEXT,
    sha256        TEXT,
    author        TEXT,
    last_author   TEXT,
    company       TEXT,
    ooxml_title   TEXT,
    authored_date TEXT,
    slide_count   INTEGER,
    rights        TEXT DEFAULT 'UNKNOWN',
    sensitive     INTEGER DEFAULT 0,
    confidential  INTEGER DEFAULT 0,
    note          TEXT,
    seen_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_files_root     ON files(root);
CREATE INDEX IF NOT EXISTS ix_files_client   ON files(client);
CREATE INDEX IF NOT EXISTS ix_files_doctype  ON files(doc_type);
CREATE INDEX IF NOT EXISTS ix_files_rights   ON files(rights);
CREATE INDEX IF NOT EXISTS ix_files_sha      ON files(sha256);
CREATE INDEX IF NOT EXISTS ix_files_status   ON files(status);
CREATE INDEX IF NOT EXISTS ix_files_conf     ON files(confidential);

CREATE TABLE IF NOT EXISTS slide_titles (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx     INTEGER NOT NULL,
    title   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_slide_file ON slide_titles(file_id);

-- Metadata-only FTS. Deliberately excludes document body text.
-- NB: do NOT set content=''. A contentless FTS5 table indexes terms but stores
-- no text, so MATCH finds rows while every column reads back empty -- queries
-- look like they return nothing. rowid is set to files.id so results join back.
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    path, filename, doc_type, author, company, ooxml_title, titles
);

-- Saved reports. These ARE the record: query them instead of re-deriving the
-- census with a throwaway script that can drift from the catalogue.

CREATE VIEW IF NOT EXISTS v_types AS
SELECT ext,
       COUNT(*)                              AS files,
       ROUND(SUM(bytes) / 1048576.0, 1)      AS mb,
       SUM(evicted)                          AS evicted,
       SUM(status = 'corrupt')               AS corrupt,
       COUNT(DISTINCT client)                AS clients,
       MIN(substr(mtime, 1, 7))              AS first_seen,
       MAX(substr(mtime, 1, 7))              AS last_seen
FROM files GROUP BY ext ORDER BY files DESC;

CREATE VIEW IF NOT EXISTS v_context AS
SELECT COALESCE(context_type,'(none)') AS context_type, COUNT(*) AS files,
       COUNT(DISTINCT folder_label) AS folders,
       ROUND(SUM(bytes)/1048576.0,1) AS mb
FROM files GROUP BY 1 ORDER BY files DESC;

CREATE VIEW IF NOT EXISTS v_doc_types AS
SELECT COALESCE(doc_type, '(unclassified)')  AS doc_type,
       COUNT(*)                              AS files,
       ROUND(SUM(bytes) / 1048576.0, 1)      AS mb,
       SUM(rights = 'OWN')                   AS own,
       SUM(rights = 'REFERENCE')             AS reference,
       SUM(rights = 'RESTRICTED')            AS restricted
FROM files GROUP BY 1 ORDER BY files DESC;

CREATE VIEW IF NOT EXISTS v_clients AS
SELECT root, client,
       COUNT(*)                              AS files,
       ROUND(SUM(bytes) / 1048576.0, 1)      AS mb,
       COUNT(DISTINCT project)               AS projects,
       COUNT(DISTINCT doc_type)              AS doc_types,
       MIN(substr(mtime, 1, 7))              AS from_month,
       MAX(substr(mtime, 1, 7))              AS to_month
FROM files WHERE client IS NOT NULL
GROUP BY root, client ORDER BY files DESC;

-- Candidate shortlist for a reusable practice library.
CREATE VIEW IF NOT EXISTS v_own_ip AS
SELECT path, doc_type, author, slide_count, substr(mtime, 1, 10) AS modified,
       ROUND(bytes / 1048576.0, 1) AS mb
FROM files
WHERE rights = 'OWN' AND sensitive = 0
  AND doc_type IN ('deck', 'report', 'proposal', 'model', 'research',
                   'brand', 'roadmap', 'sow')
ORDER BY mtime DESC;

-- Files needing a human decision: unreadable or unclassifiable.
CREATE VIEW IF NOT EXISTS v_problems AS
SELECT status, COALESCE(doc_type, '(unclassified)') AS doc_type,
       COUNT(*) AS files, ROUND(SUM(bytes) / 1048576.0, 1) AS mb
FROM files WHERE status <> 'ok' OR doc_type IS NULL
GROUP BY 1, 2 ORDER BY files DESC;

CREATE VIEW IF NOT EXISTS v_dupes AS
SELECT sha256, COUNT(*) AS copies,
       ROUND((SUM(bytes) - MAX(bytes)) / 1048576.0, 1) AS reclaimable_mb,
       MIN(path) AS keep_candidate
FROM files WHERE sha256 IS NOT NULL
GROUP BY sha256 HAVING copies > 1
ORDER BY reclaimable_mb DESC;
"""

# Tables the catalogue builder owns. `--rebuild` drops ONLY these -- other
# modules (email ingest) share the same database file, and unlinking the DB
# would silently destroy their tables.
OWNED_TABLES = ("files_fts", "slide_titles", "files")
OWNED_VIEWS = ("v_types", "v_doc_types", "v_context", "v_clients",
               "v_own_ip", "v_problems", "v_dupes")
