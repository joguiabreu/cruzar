"""SQLite connection + idempotent schema init. SQLite is the source of truth
(ADR-3); the DDL lives in schema.sql.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if absent. Idempotent (CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
