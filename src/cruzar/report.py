"""Report writer — Summary (Section 1) + Spending Detail (Section 2).

One file per calendar month with activity: reports/cruzar-YYYY-MM.md. Reports are
derived and regenerable (ADR-3); this is read-only w.r.t. the DB (AC13). Section 1
shows up to the last 12 months (descending) as of each report's month, in EUR
(ADR-16, FX at the persisted period-end rate). Investment Detail and
Needs-Categorization sections are later slices.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from decimal import Decimal
from functools import partial
from pathlib import Path

from cruzar import metrics
from cruzar.fx import Fetcher, FxError

logger = logging.getLogger(__name__)

_CASH_TYPES = ("checking", "savings")
_SUMMARY_MONTHS = 12
_CENTS = Decimal("0.01")


def _eur(value: Decimal) -> str:
    return str(value.quantize(_CENTS))


def _cell(compute: Callable[[], Decimal], *, what: str, ym: str) -> str:
    """Render a metric, degrading to 'n/a' if its FX rate is unavailable rather
    than crashing the whole report (SPEC FX degradation)."""
    try:
        return _eur(compute())
    except FxError:
        logger.warning("FX unavailable for %s %s; rendering 'n/a'", ym, what)
        return "n/a"


def _summary_section(
    conn: sqlite3.Connection, up_to: str, all_months: list[str], *, fetch: Fetcher | None
) -> list[str]:
    rows = [m for m in all_months if m <= up_to][:_SUMMARY_MONTHS]  # all_months is desc
    lines = [
        "## Summary",
        "",
        "| Month | Earned | Spent | Net Worth |",
        "| --- | --- | --- | --- |",
    ]
    for ym in rows:
        end = metrics.month_end(ym)
        earned = _cell(partial(metrics.earned, conn, ym, fetch=fetch), what="Earned", ym=ym)
        spent = _cell(partial(metrics.spent, conn, ym, fetch=fetch), what="Spent", ym=ym)
        net_worth = _cell(
            partial(metrics.net_worth, conn, end, fetch=fetch), what="Net Worth", ym=ym
        )
        lines.append(f"| {ym} | {earned} | {spent} | {net_worth} |")
    lines.append("")
    return lines


def _spending_section(conn: sqlite3.Connection, year_month: str) -> list[str]:
    rows = conn.execute(
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
    lines = [
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
    return lines


def write_reports(
    conn: sqlite3.Connection, reports_dir: Path, *, fetch: Fetcher | None = None
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    months = metrics.months_available(conn)
    for year_month in months:
        lines = [f"# Cruzar — {year_month}", ""]
        lines += _summary_section(conn, year_month, months, fetch=fetch)
        lines += _spending_section(conn, year_month)
        (reports_dir / f"cruzar-{year_month}.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
    logger.info("wrote %d report(s) to %s", len(months), reports_dir)
