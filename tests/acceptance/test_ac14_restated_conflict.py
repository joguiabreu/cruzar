"""AC14: a restated transaction (same identity inputs, differing amount) on a
later statement is surfaced in a Conflicts section, never merged or
double-counted (ADR-8, first-write-wins).

A fixture pair: statement A holds the original line; a later statement B re-lists
it with a corrected amount. Both legs persist via the real persist path (so dedup
runs and proves they are NOT merged); conflicts.detect flags the later leg.
Obviously-fake values throughout (testing conventions).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import conflicts, metrics, report
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.persist import persist_statement


def _account(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES ('placeholder', 'Checking', 'checking', 'manual', 'checking', 'EUR', "
        "'2025-01-01T00:00:00+00:00')"
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement_a() -> ParsedStatement:
    return ParsedStatement(
        currency="EUR",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 1, 31),
        closing_balance=Decimal("-35.00"),
        transactions=[
            ParsedTransaction(1, date(2025, 1, 15), Decimal("-10.00"), "ACME SUBSCRIPTION"),
            ParsedTransaction(2, date(2025, 1, 20), Decimal("-25.00"), "WIDGET STORE"),
        ],
    )


def _statement_b() -> ParsedStatement:
    # A later statement re-lists the Jan 15 charge with a corrected amount (-12.00).
    return ParsedStatement(
        currency="EUR",
        period_start=date(2025, 2, 1),
        period_end=date(2025, 2, 28),
        closing_balance=Decimal("-16.00"),
        transactions=[
            ParsedTransaction(1, date(2025, 1, 15), Decimal("-12.00"), "ACME SUBSCRIPTION"),
            ParsedTransaction(2, date(2025, 2, 3), Decimal("-4.00"), "COFFEE BAR"),
        ],
    )


def test_ac14_restated_conflict(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        account_id = _account(conn)
        persist_statement(conn, account_id, _statement_a())
        persist_statement(conn, account_id, _statement_b())
        conn.commit()

        conflicts.detect(conn)

        # Not merged: both ACME legs survive — 4 rows total.
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 4

        # Exactly one row is superseded: statement B's ACME leg (later, -12.00).
        superseded = conn.execute(
            "SELECT amount, description_raw FROM transactions WHERE superseded = 1"
        ).fetchall()
        assert len(superseded) == 1
        assert superseded[0]["description_raw"] == "ACME SUBSCRIPTION"
        assert superseded[0]["amount"] == "-12.00"

        # Not double-counted: Jan Spent = kept -10.00 + -25.00 = -35.00 (not -47.00).
        assert metrics.spent(conn, "2025-01", fetch=None) == Decimal("-35.00")

        # Surfaced in the Conflicts section of January's report.
        report.write_reports(conn, tmp_path / "out", fetch=None)
        jan = (tmp_path / "out" / "cruzar-2025-01.md").read_text(encoding="utf-8")
        assert "## Conflicts" in jan
        assert "| 2025-01-15 | Checking | ACME SUBSCRIPTION | -10.00 | -12.00 |" in jan
    finally:
        conn.close()


def test_ac14_idempotent_and_no_false_positive_within_statement(tmp_path: Path) -> None:
    """Two independent same-(account,date,description) lines on ONE statement are
    distinct transactions (never coalesced) — not flagged. And a re-run of
    conflicts.detect is idempotent (AC1)."""
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        account_id = _account(conn)
        persist_statement(
            conn,
            account_id,
            ParsedStatement(
                currency="EUR",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 1, 31),
                closing_balance=Decimal("-30.00"),
                transactions=[
                    # Same day/description, different amounts, SAME statement → independent.
                    ParsedTransaction(1, date(2025, 1, 10), Decimal("-10.00"), "ACME SUBSCRIPTION"),
                    ParsedTransaction(2, date(2025, 1, 10), Decimal("-20.00"), "ACME SUBSCRIPTION"),
                ],
            ),
        )
        conn.commit()

        conflicts.detect(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE superseded = 1"
        ).fetchone()[0] == 0

        # Idempotent: re-running changes nothing.
        conflicts.detect(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE superseded = 1"
        ).fetchone()[0] == 0
    finally:
        conn.close()
