"""SQLite connection + idempotent schema init. SQLite is the source of truth
(ADR-3); the DDL lives in schema.sql.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait up to 5s for a transient lock instead of failing instantly. A
    # persistent holder (e.g. the DB open in a GUI browser) will still error —
    # close other connections to the DB before running.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if absent, then apply additive migrations. Idempotent."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive schema migrations for DBs created before a column existed.

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a new column
    in schema.sql is invisible to an already-created DB. Each step is guarded by a
    column check, so this is idempotent and safe to run every start.
    """
    _add_column_if_missing(
        conn, "holdings_snapshot", "currency",
        # NOT NULL needs a default for ALTER; the table is populated only by code
        # that always supplies currency, so the default never reaches real rows.
        "TEXT NOT NULL DEFAULT 'EUR'",
    )


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        logger.info("schema migration: added %s.%s", table, column)
