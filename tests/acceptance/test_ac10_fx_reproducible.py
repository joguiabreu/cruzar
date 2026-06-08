"""AC10: a month-end FX rate is fetched once and persisted, so regenerating the
same month is reproducible — identical Summary output and no second fetch.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import report
from cruzar.db import connect, init_schema


class _Spy:
    def __init__(self, rate: Decimal) -> None:
        self.rate = rate
        self.calls = 0

    def __call__(self, on: date, quote: str) -> Decimal:
        self.calls += 1
        return self.rate


def _account(conn: sqlite3.Connection, account_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, 'EUR', ?)",
        (f"bank-{account_type}", f"{account_type} acct", f"{account_type}-m",
         "manual", account_type, "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def test_ac10_summary_fx_reproducible(tmp_path: Path) -> None:
    db_path = tmp_path / "rep.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        checking = _account(conn, "checking")
        broker = _account(conn, "brokerage")
        cstmt = conn.execute(
            "INSERT INTO statements(account_id, period_start, period_end, "
            "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', '100.00', ?)",
            (checking, "2026-01-01T00:00:00+00:00"),
        ).lastrowid
        conn.execute(
            "INSERT INTO transactions(statement_id, date, amount, description_raw, "
            "intra_statement_seq, content_hash) VALUES (?, '2026-05-10', '-20.00', 'COMPRA', 1, 'h1')",
            (cstmt,),
        )
        bstmt = conn.execute(
            "INSERT INTO statements(account_id, period_start, period_end, "
            "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', '0.00', ?)",
            (broker, "2026-01-01T00:00:00+00:00"),
        ).lastrowid
        conn.execute(
            "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, snapshot_date, "
            "quantity, cost_basis, value, currency) "
            "VALUES (?, ?, 'AAAA', '2026-05-31', '1', NULL, '100.00', 'USD')",
            (broker, bstmt),
        )
        conn.commit()

        spy = _Spy(Decimal("2.00"))
        report.write_reports(conn, tmp_path / "r1", fetch=spy)
        first = spy.calls
        assert first >= 1  # fetched the USD month-end rate on the first run
    finally:
        conn.close()

    # Reopen as a SEPARATE connection (simulates a later `cruzar process`). The rate
    # must have been persisted+committed, so the second run does NOT re-fetch.
    conn2 = connect(db_path)
    try:
        spy2 = _Spy(Decimal("9.99"))  # different rate: if it were used, output would differ
        report.write_reports(conn2, tmp_path / "r2", fetch=spy2)
        assert spy2.calls == 0  # served entirely from the persisted cache
    finally:
        conn2.close()

    f1 = (tmp_path / "r1" / "cruzar-2026-05.md").read_text(encoding="utf-8")
    f2 = (tmp_path / "r2" / "cruzar-2026-05.md").read_text(encoding="utf-8")
    assert f1 == f2 and "Net Worth" in f1  # reproducible across runs, Summary present
