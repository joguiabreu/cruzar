"""AC6: each investment statement creates exactly one holdings_snapshot row per
holding, dated period_end, linked via statement_id; existing rows are never
UPDATEd/DELETEd (re-persist adds nothing). Verified by grouping snapshots by
statement_id. (Immutability on its own is also covered by test_adr06.)
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar.db import connect, init_schema
from cruzar.models import ParsedHolding, ParsedStatement
from cruzar.persist import persist_statement


def _broker(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES ('broker', 'Broker', 'broker', 'manual', 'brokerage', 'EUR', "
        "'2026-01-01T00:00:00+00:00')"
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement() -> ParsedStatement:
    return ParsedStatement(
        currency="EUR",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
        closing_balance=Decimal("0.00"),
        transactions=[],
        holdings=[
            ParsedHolding("AAA", Decimal("2"), Decimal("100.00"), Decimal("120.00"), "EUR"),
            ParsedHolding("BBB", Decimal("5"), None, Decimal("500.00"), "USD"),
        ],
    )


def test_ac06_one_snapshot_row_per_holding(tmp_path: Path) -> None:
    conn = connect(tmp_path / "h.db")
    try:
        init_schema(conn)
        account_id = _broker(conn)
        statement_id = persist_statement(conn, account_id, _statement())
        conn.commit()

        rows = conn.execute(
            "SELECT symbol, snapshot_date, statement_id FROM holdings_snapshot "
            "WHERE statement_id = ?",
            (statement_id,),
        ).fetchall()

        # exactly one row per holding...
        assert Counter(r["symbol"] for r in rows) == {"AAA": 1, "BBB": 1}
        # ...dated period_end, linked via statement_id...
        assert all(r["snapshot_date"] == "2026-05-31" for r in rows)
        assert all(r["statement_id"] == statement_id for r in rows)
        # ...and the FK resolves to the owning statement (one-directional, no orphan).
        owner = conn.execute(
            "SELECT account_id FROM statements WHERE id = ?", (statement_id,)
        ).fetchone()
        assert owner["account_id"] == account_id
        # (Snapshot immutability on re-ingest is covered by test_adr06.)
    finally:
        conn.close()
