#!/usr/bin/env bash
# setup_db.sh — bootstrap the Dorj database layer.
# Run from project root: bash setup_db.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
# 1. Directory layout
# ---------------------------------------------------------------------------
mkdir -p app/db/migrations
touch app/db/__init__.py app/db/migrations/__init__.py

# ---------------------------------------------------------------------------
# 2. app/version.py
# ---------------------------------------------------------------------------
cat > app/version.py << 'EOF'
"""Version constants for the Dorj pipeline."""

PIPELINE_VERSION = "scorer=v1,extractor=v1,searcher=v1"
EOF

# ---------------------------------------------------------------------------
# 3. app/db/migrations/001_initial.sql
# ---------------------------------------------------------------------------
cat > app/db/migrations/001_initial.sql << 'EOF'
-- =========================================================================
-- Dorj — schema v1
-- =========================================================================


-- 1. sites — reference table for the three supported sites
-- -------------------------------------------------------------------------

CREATE TABLE sites (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE,
    home TEXT    NOT NULL
);

INSERT INTO sites (name, home) VALUES
    ('soft98',      'https://soft98.ir'),
    ('p30download', 'https://p30download.ir'),
    ('yasdl',       'https://www.yasdl.com');


-- 2. systems — every Windows branch we support
-- -------------------------------------------------------------------------

CREATE TABLE systems (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT    NOT NULL UNIQUE,
    os         TEXT    NOT NULL CHECK (os IN ('windows')),
    os_version TEXT    NOT NULL,
    arch       TEXT    NOT NULL CHECK (arch IN ('x86', 'x64', 'arm64'))
);

INSERT INTO systems (key, os, os_version, arch) VALUES
    ('windows|7|x86',    'windows', '7',    'x86'),
    ('windows|7|x64',    'windows', '7',    'x64'),
    ('windows|8|x86',    'windows', '8',    'x86'),
    ('windows|8|x64',    'windows', '8',    'x64'),
    ('windows|8.1|x86',  'windows', '8.1',  'x86'),
    ('windows|8.1|x64',  'windows', '8.1',  'x64'),
    ('windows|10|x86',   'windows', '10',   'x86'),
    ('windows|10|x64',   'windows', '10',   'x64'),
    ('windows|10|arm64', 'windows', '10',   'arm64'),
    ('windows|11|x86',   'windows', '11',   'x86'),
    ('windows|11|x64',   'windows', '11',   'x64'),
    ('windows|11|arm64', 'windows', '11',   'arm64');


-- 3. queries — unique normalized search queries
-- -------------------------------------------------------------------------

CREATE TABLE queries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_text TEXT    NOT NULL UNIQUE,
    raw_text        TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);


-- 4. links — product pages and download links
-- -------------------------------------------------------------------------

CREATE TABLE links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT    NOT NULL UNIQUE,
    type       TEXT    NOT NULL CHECK (type IN ('product', 'download')),
    site_id    INTEGER NOT NULL REFERENCES sites(id),
    parent_id  INTEGER REFERENCES links(id) ON DELETE CASCADE,
    filename   TEXT,
    title      TEXT,
    text       TEXT,
    section    TEXT,
    first_seen TEXT    NOT NULL
);

CREATE INDEX idx_links_site_type ON links(site_id, type);
CREATE INDEX idx_links_parent    ON links(parent_id);


-- 5. solutions — cached answer per (query, system, pipeline version)
-- -------------------------------------------------------------------------

CREATE TABLE solutions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id         INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    system_id        INTEGER NOT NULL REFERENCES systems(id),
    pipeline_version TEXT    NOT NULL,
    status           TEXT    NOT NULL CHECK (status IN ('ok', 'unsupported')),
    quality_score    REAL    NOT NULL DEFAULT 0.0,
    confidence       REAL    NOT NULL DEFAULT 1.0
                             CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at       TEXT    NOT NULL,
    last_verified    TEXT,
    blocked_until    TEXT,
    UNIQUE (query_id, system_id, pipeline_version)
);

CREATE INDEX idx_solutions_blocked
    ON solutions(blocked_until) WHERE blocked_until IS NOT NULL;


-- 6. solution_links — many-to-many between solutions and download links
-- -------------------------------------------------------------------------

CREATE TABLE solution_links (
    solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    link_id     INTEGER NOT NULL REFERENCES links(id)     ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    PRIMARY KEY (solution_id, link_id),
    UNIQUE (solution_id, rank)
);

CREATE INDEX idx_solution_links_link ON solution_links(link_id);


-- 7. events — append-only tracking log
-- -------------------------------------------------------------------------

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL,
    user_id     TEXT,
    event_type  TEXT    NOT NULL CHECK (event_type IN (
                    'search', 'extract', 'download', 'install', 'ai'
                )),
    query_id    INTEGER REFERENCES queries(id) ON DELETE SET NULL,
    link_id     INTEGER REFERENCES links(id)   ON DELETE SET NULL,
    system_id   INTEGER REFERENCES systems(id) ON DELETE SET NULL,
    data        TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL
);

CREATE INDEX idx_events_job       ON events(job_id);
CREATE INDEX idx_events_type_time ON events(event_type, created_at);
CREATE INDEX idx_events_link      ON events(link_id);
CREATE INDEX idx_events_user      ON events(user_id);


-- 8. user_installs — per-user installation history
-- -------------------------------------------------------------------------

CREATE TABLE user_installs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT    NOT NULL,
    query_id       INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    link_id        INTEGER REFERENCES links(id) ON DELETE SET NULL,
    system_id      INTEGER NOT NULL REFERENCES systems(id),
    app_name       TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('success', 'failed')),
    install_method TEXT    NOT NULL CHECK (install_method IN ('silent', 'gui', 'ai')),
    version        TEXT,
    installed_at   TEXT    NOT NULL
);

CREATE INDEX idx_user_installs_user  ON user_installs(user_id, installed_at);
CREATE INDEX idx_user_installs_query ON user_installs(query_id);
EOF

# ---------------------------------------------------------------------------
# 4. app/db/connection.py
# ---------------------------------------------------------------------------
cat > app/db/connection.py << 'EOF'
"""Database connection and migration runner."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# Project root = dorj/  (three levels up from app/db/connection.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_DIR = _PROJECT_ROOT / "data"
_DB_FILE = _DB_DIR / "dorj.db"
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _connect() -> sqlite3.Connection:
    """Open a connection with the project's PRAGMAs applied."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def _migration_files() -> list[tuple[int, str, Path]]:
    """Return (version, name, path) tuples sorted by version."""
    out: list[tuple[int, str, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        stem = path.stem
        parts = stem.split("_", 1)
        try:
            version = int(parts[0])
        except ValueError:
            continue
        name = parts[1] if len(parts) > 1 else stem
        out.append((version, name, path))
    return out


def migrate() -> None:
    """Apply all pending migrations in order. Idempotent."""
    conn = _connect()
    try:
        _ensure_migrations_table(conn)
        already_applied = _applied_versions(conn)

        for version, name, path in _migration_files():
            if version in already_applied:
                continue

            sql = path.read_text(encoding="utf-8")
            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (version, name, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    """Return an open connection. Caller is responsible for closing."""
    return _connect()
EOF

# ---------------------------------------------------------------------------
# 5. app/db/__init__.py
# ---------------------------------------------------------------------------
cat > app/db/__init__.py << 'EOF'
"""Database layer."""

from app.db.connection import get_connection, migrate

__all__ = ["get_connection", "migrate"]
EOF

# ---------------------------------------------------------------------------
# 6. Run migration
# ---------------------------------------------------------------------------
echo "→ Running migration..."
uv run python -c "from app.db import migrate; migrate()"

echo ""
echo "→ Tables:"
sqlite3 data/dorj.db ".tables"

echo ""
echo "→ Systems count:"
sqlite3 data/dorj.db "SELECT COUNT(*) FROM systems;"

echo ""
echo "→ Sites:"
sqlite3 data/dorj.db "SELECT * FROM sites;"

echo ""
echo "→ Migrations:"
sqlite3 data/dorj.db "SELECT * FROM schema_migrations;"

echo ""
echo "✅ Database ready at data/dorj.db"
