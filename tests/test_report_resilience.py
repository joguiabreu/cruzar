"""The report must never crash because an FX rate is unavailable: a missing rate
degrades the affected cell to 'n/a' and the file is still written (SPEC FX
degradation). Regression guard for the FX-fetch crash.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import report
from cruzar.db import connect, init_schema
from cruzar.fx import FxError


def _boom(on: date, quote: str) -> Decimal:
    raise FxError("provider down")


def test_summary_renders_na_when_fx_unavailable(tmp_path: Path) -> None:
    conn = connect(tmp_path / "r.db")
    try:
        init_schema(conn)
        checking = conn.execute(
            "INSERT INTO accounts(institution, name, account_match, source_type, "
            "account_type, currency, created_at) VALUES "
            "('b', 'c', 'c', 'manual', 'checking', 'EUR', '2026-01-01T00:00:00+00:00')"
        ).lastrowid
        cstmt = conn.execute(
            "INSERT INTO statements(account_id, period_start, period_end, "
            "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', '100.00', 'x')",
            (checking,),
        ).lastrowid
        conn.execute(
            "INSERT INTO transactions(statement_id, date, amount, description_raw, "
            "intra_statement_seq, content_hash) VALUES (?, '2026-05-10', '-5.00', 'X', 1, 'h')",
            (cstmt,),
        )
        broker = conn.execute(
            "INSERT INTO accounts(institution, name, account_match, source_type, "
            "account_type, currency, created_at) VALUES "
            "('d', 'b', 'b', 'manual', 'brokerage', 'EUR', '2026-01-01T00:00:00+00:00')"
        ).lastrowid
        bstmt = conn.execute(
            "INSERT INTO statements(account_id, period_start, period_end, "
            "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', '0.00', 'y')",
            (broker,),
        ).lastrowid
        conn.execute(
            "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, snapshot_date, "
            "quantity, cost_basis, value, currency) "
            "VALUES (?, ?, 'AAAA', '2026-05-31', '1', NULL, '100.00', 'USD')",
            (broker, bstmt),
        )
        conn.commit()

        # No cached USD rate + a fetch that always fails → report must still write.
        report.write_reports(conn, tmp_path / "out", fetch=_boom)
        content = (tmp_path / "out" / "cruzar-2026-05.md").read_text(encoding="utf-8")
        assert "n/a" in content          # Net Worth degraded, not crashed
        assert "-5.00" in content        # EUR Spent still computed
    finally:
        conn.close()
