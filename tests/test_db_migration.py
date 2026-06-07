"""init_schema must migrate a DB created before the holdings_snapshot.currency
column existed (CREATE TABLE IF NOT EXISTS never alters an existing table).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from cruzar.db import connect, init_schema

# holdings_snapshot exactly as it was BEFORE the currency column (plan_007 D1).
_OLD_HOLDINGS_DDL = """
CREATE TABLE holdings_snapshot (
    account_id    INTEGER NOT NULL,
    statement_id  INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    quantity      TEXT NOT NULL,
    cost_basis    TEXT NOT NULL,
    value         TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol, snapshot_date)
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_init_schema_adds_currency_to_legacy_db(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "legacy.db"
    conn = connect(db_path)
    try:
        conn.executescript(_OLD_HOLDINGS_DDL)
        conn.commit()
        assert "currency" not in _columns(conn, "holdings_snapshot")

        with caplog.at_level(logging.INFO, logger="cruzar.db"):
            init_schema(conn)  # runs the additive migration
        assert "currency" in _columns(conn, "holdings_snapshot")
        # the migration is logged (visible, not silent)
        assert any("holdings_snapshot.currency" in r.message for r in caplog.records)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="cruzar.db"):
            init_schema(conn)  # idempotent: a second run is a no-op
        assert "currency" in _columns(conn, "holdings_snapshot")
        assert not any("holdings_snapshot.currency" in r.message for r in caplog.records)
    finally:
        conn.close()
