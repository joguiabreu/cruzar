"""Unit tests for is_transfer detection (ADR-15). Guards the D1 salary trap and
detection idempotency (which underpins AC1)."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import transfers
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.persist import persist_statement

_PATTERNS = ["TRF P/", "TRF MB WAY", "Trf imediata", "TRANSF SEPA"]


def _setup_single_account(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("testbank", "checking acct", "checking", "manual", "checking", "EUR",
         "2026-01-01T00:00:00+00:00"),
    )
    persist_statement(conn, 1, ParsedStatement(
        currency="EUR",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
        closing_balance=Decimal("0.00"),
        transactions=[
            ParsedTransaction(1, date(2026, 5, 5), Decimal("-200.00"), "TRF P/ Moey"),
            ParsedTransaction(2, date(2026, 5, 22), Decimal("1000.00"), "TRANSFERENCIA - VENCIMENTO"),
        ],
    ))
    conn.commit()
    return conn


def _flag(conn: sqlite3.Connection, desc: str) -> int:
    row = conn.execute(
        "SELECT is_transfer FROM transactions WHERE description_raw = ?", (desc,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_step1_marks_transfer_but_not_salary(db_path: Path) -> None:
    conn = _setup_single_account(db_path)
    try:
        transfers.detect(conn, _PATTERNS)
        assert _flag(conn, "TRF P/ Moey") == 1
        # Specific patterns never match the salary line — the D1 carve-out.
        assert _flag(conn, "TRANSFERENCIA - VENCIMENTO") == 0
    finally:
        conn.close()


def test_detect_is_idempotent(db_path: Path) -> None:
    conn = _setup_single_account(db_path)
    try:
        transfers.detect(conn, _PATTERNS)
        first = [
            (r[0], r[1]) for r in conn.execute(
                "SELECT id, is_transfer FROM transactions ORDER BY id"
            ).fetchall()
        ]
        transfers.detect(conn, _PATTERNS)
        second = [
            (r[0], r[1]) for r in conn.execute(
                "SELECT id, is_transfer FROM transactions ORDER BY id"
            ).fetchall()
        ]
        assert first == second
    finally:
        conn.close()
