"""Report writer — Spending Detail section only (this slice).

One file per calendar month: reports/cruzar-YYYY-MM.md. Reports are derived and
regenerable (ADR-3); this is read-only w.r.t. the DB (AC13). Summary,
Investment Detail and Needs-Categorization sections are later slices.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_CASH_TYPES = ("checking", "savings")


def _months_with_spending(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(t.date, 1, 7) AS ym "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "ORDER BY ym",
        _CASH_TYPES,
    ).fetchall()
    return [row["ym"] for row in rows]


def _spending_rows(conn: sqlite3.Connection, year_month: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT t.date AS date, t.amount AS amount, a.currency AS currency, "
        "t.description_raw AS description_raw, m.name AS merchant_name, "
        "m.category AS category "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "LEFT JOIN merchants m ON t.merchant_id = m.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "AND t.is_transfer = 0 "
        "AND t.amount LIKE '-%' "  # debits are signed Decimal strings; negative => leading '-'
        "AND substr(t.date, 1, 7) = ? "
        "ORDER BY t.date DESC, t.id DESC",
        (*_CASH_TYPES, year_month),
    ).fetchall()


def _render_month(rows: list[sqlite3.Row], year_month: str) -> str:
    lines = [
        f"# Cruzar — {year_month}",
        "",
        "## Spending Detail",
        "",
        "| Date | Amount | Currency | Merchant | Category |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        merchant = row["merchant_name"] or row["description_raw"]
        category = row["category"] or ""
        lines.append(
            f"| {row['date']} | {row['amount']} | {row['currency']} | {merchant} | {category} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_reports(conn: sqlite3.Connection, reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    for year_month in _months_with_spending(conn):
        rows = _spending_rows(conn, year_month)
        content = _render_month(rows, year_month)
        (reports_dir / f"cruzar-{year_month}.md").write_text(content, encoding="utf-8")
