"""AC22 / ADR-16: Net Worth at month-end = Σ cash closing_balance + Σ holdings
value over non-closed accounts, each converted to EUR at the month-end rate. A
closed account is excluded from the latest row but present in earlier rows.

Offline: a seeded fx_rate + fetch=None (the suite never hits the network).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import metrics
from cruzar.db import connect, init_schema


def _account(
    conn: sqlite3.Connection, account_type: str, currency: str, closed_at: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"bank-{account_type}", f"{account_type} acct", f"{account_type}-m",
         "manual", account_type, currency, "2026-01-01T00:00:00+00:00", closed_at),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(conn: sqlite3.Connection, account_id: int, period_end: str, closing: str) -> int:
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, closing_balance, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        (account_id, period_end, period_end, closing, "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _holding(
    conn: sqlite3.Connection, account_id: int, statement_id: int, snapshot: str,
    value: str, currency: str,
) -> None:
    conn.execute(
        "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, snapshot_date, "
        "quantity, cost_basis, value, currency) VALUES (?, ?, 'AAAA', ?, '1', NULL, ?, ?)",
        (account_id, statement_id, snapshot, value, currency),
    )


def _rate(conn: sqlite3.Connection, on: str, quote: str, rate: str) -> None:
    conn.execute(
        "INSERT INTO fx_rates(date, base_currency, quote_currency, rate) VALUES (?, 'EUR', ?, ?)",
        (on, quote, rate),
    )


def test_ac22_net_worth_with_fx_and_closed_account(tmp_path: Path) -> None:
    conn = connect(tmp_path / "nw.db")
    try:
        init_schema(conn)
        checking = _account(conn, "checking", "EUR")
        broker = _account(conn, "brokerage", "EUR")
        savings = _account(conn, "savings", "EUR", closed_at="2026-04-15")

        _statement(conn, checking, "2026-05-31", "1000.00")
        bstmt = _statement(conn, broker, "2026-05-31", "50.00")  # uninvested cash
        _holding(conn, broker, bstmt, "2026-05-31", "100.00", "USD")
        _statement(conn, savings, "2026-03-31", "500.00")
        _rate(conn, "2026-05-31", "USD", "2.00")  # 100 USD = 50 EUR
        conn.commit()

        # May: 1000 + 50 cash + 100/2 holding; savings is closed → excluded.
        assert metrics.net_worth(conn, date(2026, 5, 31), fetch=None) == Decimal("1100.00")
        # March: only savings has a statement ≤ then, and it's still open → 500.
        assert metrics.net_worth(conn, date(2026, 3, 31), fetch=None) == Decimal("500.00")
    finally:
        conn.close()
