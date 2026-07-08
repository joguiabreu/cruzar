"""A parser failure must mark the file `parse_failed` and let the run continue — not
crash the whole pipeline (regression: only ActivoBankParseError was caught, so a
RevolutParseError escaped and aborted the run). Any ParserError subclass is handled.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from cruzar import pipeline
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement
from cruzar.parsers.revolut import RevolutParseError

_FIXTURE_PDF = Path(__file__).parent / "fixtures" / "activobank" / "statement.pdf"


def _account(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES ('revolut', 'R', 'revolut', 'manual', 'checking', 'EUR', "
        "'2025-01-01T00:00:00+00:00')"
    )
    conn.commit()


def test_parser_error_is_recorded_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        _account(conn)
        inbox = tmp_path / "inbox"
        (inbox / "revolut").mkdir(parents=True)
        shutil.copy(_FIXTURE_PDF, inbox / "revolut" / "s.pdf")  # any PDF; parser is stubbed

        def _boom(_path: str | Path) -> ParsedStatement:
            raise RevolutParseError("unparseable layout")

        def _get_parser(_institution: str) -> object:
            return _boom

        monkeypatch.setattr(pipeline, "get_parser", _get_parser)

        # Must NOT raise — the run completes despite the parser failure.
        pipeline.ingest_inbox(conn, inbox)

        status = conn.execute("SELECT status FROM processed_files").fetchone()["status"]
        assert status == "parse_failed"
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    finally:
        conn.close()
