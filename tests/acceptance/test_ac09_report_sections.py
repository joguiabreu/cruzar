"""AC9: the report contains Summary, Spending Detail, Earning Detail, Investment
Detail in that order. Plus Investment Detail content: per-position rows in native
currency, Δ vs cost where cost is known (n/a otherwise), and an EUR Grand Total.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cruzar import report
from cruzar.db import connect, init_schema


def _account(conn: sqlite3.Connection, name: str, account_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, 'EUR', ?)",
        (name, name, name, "manual", account_type, "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(conn: sqlite3.Connection, account_id: int, closing: str) -> int:
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, "
        "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', ?, 'x')",
        (account_id, closing),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _section_order(content: str) -> list[str]:
    return [ln[3:] for ln in content.splitlines() if ln.startswith("## ")]


def test_ac09_sections_present_in_order_with_investment_detail(tmp_path: Path) -> None:
    conn = connect(tmp_path / "r.db")
    try:
        init_schema(conn)
        checking = _account(conn, "Checking", "checking")
        cstmt = _statement(conn, checking, "100.00")
        conn.execute(
            "INSERT INTO transactions(statement_id, date, amount, description_raw, "
            "intra_statement_seq, content_hash) VALUES (?, '2026-05-10', '-5.00', 'X', 1, 'h')",
            (cstmt,),
        )
        broker = _account(conn, "Broker", "brokerage")
        bstmt = _statement(conn, broker, "0.00")
        # one holding WITH cost basis (USD), one WITHOUT (EUR, Degiro-style)
        conn.executemany(
            "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, snapshot_date, "
            "quantity, cost_basis, value, currency) VALUES (?, ?, ?, '2026-05-31', ?, ?, ?, ?)",
            [
                (broker, bstmt, "USD1", "2", "300.00", "360.00", "USD"),
                (broker, bstmt, "EUR1", "30", None, "1500.00", "EUR"),
            ],
        )
        conn.execute(
            "INSERT INTO fx_rates(date, base_currency, quote_currency, rate) "
            "VALUES ('2026-05-31', 'EUR', 'USD', '2.00')"
        )
        conn.commit()

        report.write_reports(conn, tmp_path / "out", fetch=None)
        content = (tmp_path / "out" / "cruzar-2026-05.md").read_text(encoding="utf-8")

        # 'X' (-5.00) is an uncategorized, non-transfer cash txn, so the optional
        # Needs-Categorization section appears last (AC9).
        assert _section_order(content) == [
            "Summary", "Spending Detail", "Spending by Category", "Earning Detail",
            "Investment Detail", "Needs Categorization",
        ]
        inv = content.split("## Investment Detail", 1)[1]
        assert "### Broker" in inv
        assert "| USD1 | 2 | USD | 300.00 | 360.00 | 60.00 | 20.0% |" in inv
        assert "| EUR1 | 30 | EUR | n/a | 1500.00 | n/a | n/a |" in inv
        # Grand Total EUR = 360 USD / 2 + 1500 EUR = 180 + 1500 = 1680.00
        assert "### Grand Total (EUR)" in inv and "1680.00" in inv
    finally:
        conn.close()


def test_ac09_cash_only_report_has_empty_investment_detail(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        checking = _account(conn, "Checking", "checking")
        cstmt = _statement(conn, checking, "100.00")
        conn.execute(
            "INSERT INTO transactions(statement_id, date, amount, description_raw, "
            "intra_statement_seq, content_hash) VALUES (?, '2026-05-10', '-5.00', 'X', 1, 'h')",
            (cstmt,),
        )
        conn.commit()

        report.write_reports(conn, tmp_path / "out", fetch=None)
        content = (tmp_path / "out" / "cruzar-2026-05.md").read_text(encoding="utf-8")
        assert "## Investment Detail" in content
        assert "_No investment holdings._" in content
    finally:
        conn.close()
