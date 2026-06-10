"""Spending by Category (plan 017): this month's cash spending grouped by category,
in EUR, summing to the Summary's Spent. Uncategorized spending is bucketed (not
dropped); rows sort most-spent-first; foreign-currency spend converts at the
month-end rate (ADR-5). Offline.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from cruzar import metrics
from cruzar.db import connect, init_schema

_YM = "2026-05"


def _account(conn: sqlite3.Connection, name: str, currency: str) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES (?, ?, ?, 'manual', 'checking', ?, '2026-01-01T00:00:00+00:00')",
        (name, name, name, currency),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(conn: sqlite3.Connection, account_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, "
        "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', '0.00', 'x')",
        (account_id,),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _merchant(conn: sqlite3.Connection, name: str, category: str) -> int:
    conn.execute("INSERT INTO categories(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (category,))
    cur = conn.execute("INSERT INTO merchants(name, category) VALUES (?, ?)", (name, category))
    assert cur.lastrowid is not None
    return cur.lastrowid


def _txn(
    conn: sqlite3.Connection, statement_id: int, seq: int, amount: str, merchant_id: int | None
) -> None:
    conn.execute(
        "INSERT INTO transactions(statement_id, date, amount, description_raw, "
        "intra_statement_seq, merchant_id, content_hash) "
        "VALUES (?, '2026-05-10', ?, ?, ?, ?, ?)",
        (statement_id, amount, f"T{statement_id}-{seq}", seq, merchant_id, f"h{statement_id}-{seq}"),
    )


def test_spending_by_category_groups_buckets_and_sorts(tmp_path: Path) -> None:
    conn = connect(tmp_path / "s.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking", "EUR")
        stmt = _statement(conn, acct)
        grocer = _merchant(conn, "Grocer", "Groceries")
        streamer = _merchant(conn, "Streamer", "Subscriptions")
        _txn(conn, stmt, 1, "-242.50", grocer)
        _txn(conn, stmt, 2, "-10.00", streamer)
        _txn(conn, stmt, 3, "-42.50", None)  # uncategorized
        _txn(conn, stmt, 4, "2000.00", None)  # a credit — not spending, excluded
        conn.commit()

        result = metrics.spending_by_category(conn, _YM, fetch=None)
        # most-spent-first: -242.50 < -42.50 < -10.00
        assert result == [
            ("Groceries", Decimal("-242.50")),
            ("Uncategorized", Decimal("-42.50")),
            ("Subscriptions", Decimal("-10.00")),
        ]
        # self-reconciling: the rows sum to the Summary's Spent.
        assert sum((amt for _, amt in result), Decimal(0)) == metrics.spent(conn, _YM, fetch=None)
    finally:
        conn.close()


def test_spending_by_category_converts_foreign_currency(tmp_path: Path) -> None:
    conn = connect(tmp_path / "f.db")
    try:
        init_schema(conn)
        eur = _account(conn, "EUR-Checking", "EUR")
        grocer = _merchant(conn, "Grocer", "Groceries")
        _txn(conn, _statement(conn, eur), 1, "-242.50", grocer)
        usd = _account(conn, "USD-Checking", "USD")
        _txn(conn, _statement(conn, usd), 1, "-100.00", grocer)  # -100 USD / 2 = -50 EUR
        conn.execute(
            "INSERT INTO fx_rates(date, base_currency, quote_currency, rate) "
            "VALUES ('2026-05-31', 'EUR', 'USD', '2.00')"
        )
        conn.commit()

        result = metrics.spending_by_category(conn, _YM, fetch=None)
        assert result == [("Groceries", Decimal("-292.50"))]  # -242.50 + -50.00 EUR
        assert sum((amt for _, amt in result), Decimal(0)) == metrics.spent(conn, _YM, fetch=None)
    finally:
        conn.close()
