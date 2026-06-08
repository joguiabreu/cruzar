"""AC19: Earned and Spent include only cash-account transactions; investment-account
transactions (a brokerage buy and an in-account dividend) are excluded.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import metrics
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.persist import persist_statement


def _account(conn: sqlite3.Connection, account_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, 'EUR', ?)",
        (f"bank-{account_type}", f"{account_type} acct", f"{account_type}-m",
         "manual", account_type, "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _stmt(txns: list[ParsedTransaction]) -> ParsedStatement:
    return ParsedStatement(
        currency="EUR", period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
        closing_balance=Decimal("0.00"), transactions=txns,
    )


def test_ac19_earned_spent_exclude_investment_accounts(tmp_path: Path) -> None:
    conn = connect(tmp_path / "es.db")
    try:
        init_schema(conn)
        checking = _account(conn, "checking")
        broker = _account(conn, "brokerage")
        persist_statement(conn, checking, _stmt([
            ParsedTransaction(1, date(2026, 5, 10), Decimal("2000.00"), "SALARY"),
            ParsedTransaction(2, date(2026, 5, 12), Decimal("-50.00"), "COMPRA SHOP"),
        ]))
        persist_statement(conn, broker, _stmt([
            ParsedTransaction(1, date(2026, 5, 20), Decimal("-500.00"), "Compra ETF"),
            ParsedTransaction(2, date(2026, 5, 25), Decimal("10.00"), "Dividend"),
        ]))
        conn.commit()

        # cash only: brokerage buy (−500) and dividend (+10) excluded.
        assert metrics.earned(conn, "2026-05", fetch=None) == Decimal("2000.00")
        assert metrics.spent(conn, "2026-05", fetch=None) == Decimal("-50.00")
    finally:
        conn.close()
