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
    # ADR-14: parsers default to cash-flow-capable; only IB (monthly summary, no
    # per-deposit lines) is seeded 0 in sources.yaml. The DEFAULT 1 keeps existing
    # rows capable, matching the fresh-DDL default.
    _add_column_if_missing(
        conn, "accounts", "emits_cash_flows", "INTEGER NOT NULL DEFAULT 1"
    )
    _make_cost_basis_nullable(conn)


def _make_cost_basis_nullable(conn: sqlite3.Connection) -> None:
    """Drop NOT NULL on holdings_snapshot.cost_basis (some brokers don't report it).

    SQLite has no ALTER COLUMN, so this rebuilds the table. Runs only when the
    column is still NOT NULL, so it's idempotent. Must run AFTER the currency
    migration so the copied column list matches the rebuilt table.
    """
    info = list(conn.execute("PRAGMA table_info(holdings_snapshot)"))
    cost_basis = next((c for c in info if c[1] == "cost_basis"), None)
    if cost_basis is None or cost_basis[3] == 0:  # c[3] == notnull flag
        return
    conn.executescript(
        """
        PRAGMA foreign_keys=OFF;
        CREATE TABLE holdings_snapshot__new (
            account_id    INTEGER NOT NULL REFERENCES accounts(id),
            statement_id  INTEGER NOT NULL REFERENCES statements(id),
            symbol        TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            quantity      TEXT NOT NULL,
            cost_basis    TEXT,
            value         TEXT NOT NULL,
            currency      TEXT NOT NULL,
            PRIMARY KEY (account_id, symbol, snapshot_date)
        );
        INSERT INTO holdings_snapshot__new
            SELECT account_id, statement_id, symbol, snapshot_date,
                   quantity, cost_basis, value, currency
            FROM holdings_snapshot;
        DROP TABLE holdings_snapshot;
        ALTER TABLE holdings_snapshot__new RENAME TO holdings_snapshot;
        PRAGMA foreign_keys=ON;
        """
    )
    logger.info("schema migration: holdings_snapshot.cost_basis is now nullable")


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        logger.info("schema migration: added %s.%s", table, column)
