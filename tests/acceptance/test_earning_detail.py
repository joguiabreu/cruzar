"""Earning Detail section lists cash-account income for the month (the itemised
counterpart of Earned): inflows only, excluding spending, transfers, and
investment-account rows; and the listed amounts reconcile to metrics.earned.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import metrics, report
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


def _earning_block(content: str) -> str:
    # Just the Earning Detail section: from its header to the next "## " section
    # (Investment Detail / Needs Categorization now follow it).
    after = content.split("## Earning Detail", 1)[1]
    return after.split("\n## ", 1)[0]


def test_earning_detail_lists_income_and_reconciles(tmp_path: Path) -> None:
    conn = connect(tmp_path / "e.db")
    try:
        init_schema(conn)
        checking = _account(conn, "checking")
        persist_statement(conn, checking, _stmt([
            ParsedTransaction(1, date(2026, 5, 22), Decimal("2000.00"), "VENCIMENTO salary"),
            ParsedTransaction(2, date(2026, 5, 3), Decimal("1.20"), "Flatex Interest"),
            ParsedTransaction(3, date(2026, 5, 12), Decimal("-50.00"), "COMPRA SHOP"),
            ParsedTransaction(4, date(2026, 5, 8), Decimal("100.00"), "TRF IN FROM SELF"),
        ]))
        broker = _account(conn, "brokerage")
        persist_statement(conn, broker, _stmt([
            ParsedTransaction(1, date(2026, 5, 25), Decimal("10.00"), "Dividend in broker"),
        ]))
        # mark the own-transfer inflow (would otherwise count as income)
        conn.execute(
            "UPDATE transactions SET is_transfer = 1 WHERE description_raw = 'TRF IN FROM SELF'"
        )
        conn.commit()

        report.write_reports(conn, tmp_path / "out", fetch=None)
        block = _earning_block(
            (tmp_path / "out" / "cruzar-2026-05.md").read_text(encoding="utf-8")
        )

        assert "VENCIMENTO salary" in block and "Flatex Interest" in block
        assert "COMPRA SHOP" not in block          # spending excluded
        assert "TRF IN FROM SELF" not in block      # transfer excluded
        assert "Dividend in broker" not in block    # investment account excluded
        # reconciles to Earned for the month
        assert metrics.earned(conn, "2026-05", fetch=None) == Decimal("2001.20")
    finally:
        conn.close()
