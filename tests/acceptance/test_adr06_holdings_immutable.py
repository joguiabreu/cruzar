"""ADR-6: holdings_snapshot is immutable — INSERT only, never UPDATE/DELETE.

Two statements reporting the same (account, symbol, snapshot_date) — e.g. an
overlapping or corrected statement — must leave the FIRST snapshot intact: no
duplicate row, and the value is not overwritten by the later statement. Each
holding stores its own native currency (plan_007 D1).

Statement-level reprocessing of the *same file* is idempotent at the pipeline
layer (processed_files); this isolates the persistence-level immutability guard.

Synthetic, obviously-fake values; fresh temp DB.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar.db import connect, init_schema
from cruzar.models import ParsedHolding, ParsedStatement
from cruzar.persist import persist_statement


def _add_account(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("testbroker", "broker acct", "testbroker", "manual", "brokerage", "EUR",
         "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(period_start: date, value: Decimal) -> ParsedStatement:
    # snapshot_date = period_end is held fixed; only value differs between runs.
    return ParsedStatement(
        currency="EUR",
        period_start=period_start,
        period_end=date(2026, 5, 31),
        closing_balance=Decimal("250.00"),
        transactions=[],
        holdings=[
            ParsedHolding("AAAA", Decimal("10"), Decimal("500.00"), value, "EUR"),
            ParsedHolding("BBBB", Decimal("4"), Decimal("1200.00"), Decimal("1500.00"), "USD"),
        ],
    )


def test_adr06_holdings_snapshot_insert_only(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        init_schema(conn)
        account = _add_account(conn)

        # First statement establishes the snapshot (AAAA value 550.00).
        persist_statement(conn, account, _statement(date(2026, 5, 1), Decimal("550.00")))
        # A later statement with the SAME snapshot_date reports a different value;
        # it must NOT overwrite or duplicate the existing immutable snapshot.
        persist_statement(conn, account, _statement(date(2026, 4, 1), Decimal("999.99")))
        conn.commit()

        rows = conn.execute(
            "SELECT symbol, quantity, cost_basis, value, currency FROM holdings_snapshot "
            "WHERE account_id = ? AND snapshot_date = ? ORDER BY symbol",
            (account, "2026-05-31"),
        ).fetchall()

        assert [tuple(r) for r in rows] == [
            ("AAAA", "10", "500.00", "550.00", "EUR"),  # original value, not 999.99
            ("BBBB", "4", "1200.00", "1500.00", "USD"),
        ]
        total = conn.execute(
            "SELECT COUNT(*) FROM holdings_snapshot WHERE account_id = ?", (account,)
        ).fetchone()[0]
        assert total == 2  # no duplicate rows from the second statement
    finally:
        conn.close()
