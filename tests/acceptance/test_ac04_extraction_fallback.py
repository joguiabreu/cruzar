"""AC4(a) + AC16: the LLM is invoked for extraction when pdfplumber recovers <50%
of expected columns, and only then. A degraded-layout PDF trips
``ExtractionFallback``; the pipeline hands the raw text to the LLM extractor, whose
output flows through the normal persist path. A clean statement never calls it.

Offline: the extractor is always a fake injected into ``ingest_inbox`` — no test
reaches Ollama. The extraction→ParsedStatement boundary (sign/Decimal) is unit
tested separately against ``extract.to_parsed_statement``.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cruzar import pipeline
from cruzar.db import connect, init_schema
from cruzar.extract import LlmError, to_parsed_statement
from cruzar.models import ParsedStatement, ParsedTransaction

FIXTURES = Path(__file__).parent.parent / "fixtures"
_DEGRADED_PDF = FIXTURES / "activobank_degraded" / "statement.pdf"
_CLEAN_PDF = FIXTURES / "activobank" / "statement.pdf"


# --- canned extraction the fake returns (stands in for the LLM's output) ----------
_EXTRACTED = ParsedStatement(
    currency="EUR",
    period_start=date(2025, 3, 1),
    period_end=date(2025, 3, 31),
    closing_balance=Decimal("1947.50"),
    transactions=[
        ParsedTransaction(1, date(2025, 3, 5), Decimal("-10.00"), "EXAMPLE SUBSCRIPTION"),
        ParsedTransaction(2, date(2025, 3, 9), Decimal("2000.00"), "EXAMPLE SALARY"),
        ParsedTransaction(3, date(2025, 3, 18), Decimal("-42.50"), "EXAMPLE GROCER"),
    ],
)


class _Spy:
    """Counts extract() calls; returns the canned statement."""

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, text: str) -> ParsedStatement:
        self.calls += 1
        return _EXTRACTED


class _Raise:
    """Fails loudly if extraction is called (clean statement must not fall back)."""

    def extract(self, text: str) -> ParsedStatement:
        raise AssertionError("the LLM extractor must not be called for a clean layout")


class _Boom:
    """Simulates an unusable extraction (malformed twice / transport failure)."""

    def extract(self, text: str) -> ParsedStatement:
        raise LlmError("unusable extraction")


def _account(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES ('activobank', 'Checking', 'inbox', 'manual', 'checking', 'EUR', "
        "'2025-01-01T00:00:00+00:00')"
    )
    conn.commit()


def _inbox_with(tmp_path: Path, pdf: Path) -> Path:
    inbox = tmp_path / "inbox"
    (inbox / "inbox").mkdir(parents=True)  # folder name 'inbox' == account_match
    shutil.copy(pdf, inbox / "inbox" / "statement.pdf")
    return inbox


def test_ac04_extraction_fallback_invokes_llm(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        _account(conn)
        spy = _Spy()
        pipeline.ingest_inbox(conn, _inbox_with(tmp_path, _DEGRADED_PDF), extractor=spy)

        assert spy.calls == 1  # AC16: the fallback invoked the LLM
        rows = conn.execute(
            "SELECT date, amount, description_raw FROM transactions ORDER BY intra_statement_seq"
        ).fetchall()
        assert [(r["date"], r["amount"], r["description_raw"]) for r in rows] == [
            ("2025-03-05", "-10.00", "EXAMPLE SUBSCRIPTION"),
            ("2025-03-09", "2000.00", "EXAMPLE SALARY"),
            ("2025-03-18", "-42.50", "EXAMPLE GROCER"),
        ]
        status = conn.execute("SELECT status FROM processed_files").fetchone()["status"]
        assert status == "ok"
    finally:
        conn.close()


def test_ac04_clean_statement_skips_extraction(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        _account(conn)
        # _Raise blows up if called; a clean layout resolves >=50% columns, so it isn't.
        pipeline.ingest_inbox(conn, _inbox_with(tmp_path, _CLEAN_PDF), extractor=_Raise())
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 11
    finally:
        conn.close()


def test_ac04_unusable_extraction_is_extraction_failed(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        _account(conn)
        pipeline.ingest_inbox(conn, _inbox_with(tmp_path, _DEGRADED_PDF), extractor=_Boom())
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        status = conn.execute("SELECT status FROM processed_files").fetchone()["status"]
        assert status == "extraction_failed"
    finally:
        conn.close()


def test_ac04_fallback_without_extractor_is_extraction_failed(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        _account(conn)
        # LLM disabled (extractor=None): a degraded layout can't be read → flagged.
        pipeline.ingest_inbox(conn, _inbox_with(tmp_path, _DEGRADED_PDF), extractor=None)
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        status = conn.execute("SELECT status FROM processed_files").fetchone()["status"]
        assert status == "extraction_failed"
    finally:
        conn.close()


def test_to_parsed_statement_applies_sign_and_parses() -> None:
    """The boundary: direction→sign, strings→Decimal, ISO→date, seq from order (ADR-1
    — Python does the sign, never the model)."""

    class _Line:
        def __init__(self, d: str, desc: str, amt: str, direction: str) -> None:
            self.date = d
            self.description = desc
            self.amount = amt
            self.direction = direction

    statement = to_parsed_statement(
        currency="EUR",
        period_start="2025-03-01",
        period_end="2025-03-31",
        closing_balance="1947.50",
        lines=[
            _Line("2025-03-05", "EXAMPLE SUBSCRIPTION", "10.00", "debit"),
            _Line("2025-03-09", "EXAMPLE SALARY", "2000.00", "credit"),
        ],
    )
    assert statement.closing_balance == Decimal("1947.50")
    assert [(t.intra_statement_seq, t.amount) for t in statement.transactions] == [
        (1, Decimal("-10.00")),
        (2, Decimal("2000.00")),
    ]


def test_to_parsed_statement_rejects_malformed() -> None:
    class _Line:
        date = "2025-03-05"
        description = "X"
        amount = "not-a-number"
        direction = "debit"

    with pytest.raises(LlmError):
        to_parsed_statement(
            currency="EUR",
            period_start="2025-03-01",
            period_end="2025-03-31",
            closing_balance="1947.50",
            lines=[_Line()],
        )
