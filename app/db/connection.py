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
