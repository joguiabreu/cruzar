"""AC21: a transfer pair (opposite-signed, equal magnitude, two tracked
accounts, ±3 days) carries is_transfer = true on both legs and is excluded from
spending.

Scope note (plan_002 D2): AC21's full text also requires exclusion from Earned
and Spent. Those metrics don't exist until the Summary slice, so this test
asserts the live consumer today — Spending Detail. The Earned/Spent assertion
is to be ADDED HERE when the Summary section lands; do not consider AC21 fully
covered until then.

Synthetic, obviously-fake values only — built directly in the DB because
detection is DB-level logic, distinct from the AC8 parser fixtures.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import report, transfers
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.persist import persist_statement

_PATTERNS = ["TRF P/", "TRF MB WAY", "Trf imediata", "TRANSF SEPA"]


def _add_account(conn: sqlite3.Connection, account_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("testbank", f"{account_type} acct", account_type, "manual",
         account_type, "EUR", "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _stmt(txns: list[ParsedTransaction]) -> ParsedStatement:
    return ParsedStatement(
        currency="EUR",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
        closing_balance=Decimal("0.00"),
        transactions=txns,
    )


def _flag(conn: sqlite3.Connection, desc: str) -> int:
    row = conn.execute(
        "SELECT is_transfer FROM transactions WHERE description_raw = ?", (desc,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_ac21_transfer_pair_excluded(db_path: Path, reports_dir: Path) -> None:
    conn = connect(db_path)
    try:
        init_schema(conn)
        checking = _add_account(conn, "checking")
        savings = _add_account(conn, "savings")

        # Pair legs carry NO transfer keyword, so only step-2 pairing can flag
        # them (proves pairing, not the description rule).
        persist_statement(conn, checking, _stmt([
            ParsedTransaction(1, date(2026, 5, 5), Decimal("-200.00"), "TRF P/ Moey"),
            ParsedTransaction(2, date(2026, 5, 10), Decimal("-100.00"), "MOVE TO ACCOUNT B"),
            ParsedTransaction(3, date(2026, 5, 12), Decimal("-50.00"), "COMPRA SHOP ABC"),
            ParsedTransaction(4, date(2026, 5, 22), Decimal("1000.00"), "TRANSFERENCIA - VENCIMENTO"),
        ]))
        persist_statement(conn, savings, _stmt([
            ParsedTransaction(1, date(2026, 5, 11), Decimal("100.00"), "INCOMING MOVE"),
        ]))
        conn.commit()

        transfers.detect(conn, _PATTERNS)

        # Step 2 — the pair (no description match) is flagged on BOTH legs.
        assert _flag(conn, "MOVE TO ACCOUNT B") == 1
        assert _flag(conn, "INCOMING MOVE") == 1
        # Step 1 — description rule.
        assert _flag(conn, "TRF P/ Moey") == 1
        # Real spending is not a transfer.
        assert _flag(conn, "COMPRA SHOP ABC") == 0
        # Salary carve-out: income is never a transfer (would break Earned/AC19).
        assert _flag(conn, "TRANSFERENCIA - VENCIMENTO") == 0

        report.write_reports(conn, reports_dir)
        spending = (reports_dir / "cruzar-2026-05.md").read_text(encoding="utf-8")
        assert "COMPRA SHOP ABC" in spending          # real spending stays
        assert "MOVE TO ACCOUNT B" not in spending     # paired transfer excluded
        assert "TRF P/ Moey" not in spending           # rule transfer excluded
    finally:
        conn.close()
