"""Report writer — Summary (Section 1) + Spending Detail (Section 2).

One file per calendar month with activity: reports/cruzar-YYYY-MM.md. Reports are
derived and regenerable (ADR-3); this is read-only w.r.t. the DB (AC13). Section 1
shows up to the last 12 months (descending) as of each report's month, in EUR
(ADR-16, FX at the persisted period-end rate), including Portfolio Δ (ADR-14).
The Needs-Categorization section is a later slice.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from decimal import Decimal
from functools import partial
from pathlib import Path

from datetime import date

from cruzar import fx, metrics
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


def _delta_cell(
    conn: sqlite3.Connection, ym: str, patterns: list[str], *, fetch: Fetcher | None
) -> str:
    """Portfolio Δ (ADR-14): '—' when no prior snapshot, the gross flag when
    contributions are undetected, and 'n/a' if its FX rate is unavailable."""
    try:
        delta = metrics.portfolio_delta(conn, ym, patterns=patterns, fetch=fetch)
    except FxError:
        logger.warning("FX unavailable for %s Portfolio Δ; rendering 'n/a'", ym)
        return "n/a"
    if delta is None:
        return "—"
    if delta.flagged:
        return f"{_eur(delta.value)} (gross — contributions undetected)"
    return _eur(delta.value)


def _summary_section(
    conn: sqlite3.Connection,
    up_to: str,
    all_months: list[str],
    patterns: list[str],
    *,
    fetch: Fetcher | None,
) -> list[str]:
    rows = [m for m in all_months if m <= up_to][:_SUMMARY_MONTHS]  # all_months is desc
    lines = [
        "## Summary",
        "",
        "| Month | Earned | Spent | Portfolio Δ | Net Worth |",
        "| --- | --- | --- | --- | --- |",
    ]
    for ym in rows:
        end = metrics.month_end(ym)
        earned = _cell(partial(metrics.earned, conn, ym, fetch=fetch), what="Earned", ym=ym)
        spent = _cell(partial(metrics.spent, conn, ym, fetch=fetch), what="Spent", ym=ym)
        delta = _delta_cell(conn, ym, patterns, fetch=fetch)
        net_worth = _cell(
            partial(metrics.net_worth, conn, end, fetch=fetch), what="Net Worth", ym=ym
        )
        lines.append(f"| {ym} | {earned} | {spent} | {delta} | {net_worth} |")
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


def _earning_section(conn: sqlite3.Connection, year_month: str) -> list[str]:
    """Income counterpart of Spending Detail: cash-account inflows this month.
    Same filter as metrics.earned, itemised — the rows sum to that month's Earned."""
    rows = conn.execute(
        "SELECT t.date AS date, t.amount AS amount, a.currency AS currency, "
        "t.description_raw AS description_raw, m.name AS merchant_name "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "LEFT JOIN merchants m ON t.merchant_id = m.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "AND t.is_transfer = 0 "
        "AND t.amount NOT LIKE '-%' AND t.amount != '0.00' "  # inflows only
        "AND substr(t.date, 1, 7) = ? "
        "ORDER BY t.date DESC, t.id DESC",
        (*_CASH_TYPES, year_month),
    ).fetchall()
    lines = [
        "## Earning Detail",
        "",
        "| Date | Amount | Currency | Source |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        source = row["merchant_name"] or row["description_raw"]
        lines.append(f"| {row['date']} | {row['amount']} | {row['currency']} | {source} |")
    lines.append("")
    return lines


def _investment_section(
    conn: sqlite3.Connection, on: date, *, fetch: Fetcher | None
) -> list[str]:
    """Section 4: per-account holdings (native, with Δ vs cost) + EUR totals."""
    accounts = metrics.investment_holdings(conn, on)
    lines = ["## Investment Detail", ""]
    if not accounts:
        lines += ["_No investment holdings._", ""]
        return lines

    grand_total = Decimal(0)
    grand_ok = True
    for acct in accounts:
        lines += [
            f"### {acct.name}",
            "",
            "| Symbol | Quantity | Currency | Cost Basis | Current Value | Δ Amount | Δ % |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        acct_eur = Decimal(0)
        acct_ok = True
        for h in acct.holdings:
            if h.cost_basis is not None:
                delta = h.value - h.cost_basis
                cost, amt = _eur(h.cost_basis), _eur(delta)
                pct = f"{delta / h.cost_basis * 100:.1f}%" if h.cost_basis != 0 else "n/a"
            else:
                cost = amt = pct = "n/a"
            lines.append(
                f"| {h.symbol} | {h.quantity} | {h.currency} | {cost} | "
                f"{_eur(h.value)} | {amt} | {pct} |"
            )
            try:
                acct_eur += fx.convert(conn, h.value, h.currency, on, fetch=fetch)
            except FxError:
                acct_ok = False
                logger.warning("FX unavailable for %s %s; account total degraded", on, h.symbol)
        lines += [f"| **Total (EUR)** |  |  |  | {_eur(acct_eur) if acct_ok else 'n/a'} |  |  |", ""]
        if acct_ok:
            grand_total += acct_eur
        else:
            grand_ok = False

    lines += [
        "### Grand Total (EUR)",
        "",
        "| Current Value |",
        "| --- |",
        f"| {_eur(grand_total) if grand_ok else 'n/a'} |",
        "",
    ]
    return lines


def write_reports(
    conn: sqlite3.Connection,
    reports_dir: Path,
    *,
    investment_flow_patterns: list[str] | None = None,
    fetch: Fetcher | None = None,
) -> None:
    patterns = investment_flow_patterns or []
    reports_dir.mkdir(parents=True, exist_ok=True)
    months = metrics.months_available(conn)
    for year_month in months:
        lines = [f"# Cruzar — {year_month}", ""]
        lines += _summary_section(conn, year_month, months, patterns, fetch=fetch)
        lines += _spending_section(conn, year_month)
        lines += _earning_section(conn, year_month)
        lines += _investment_section(conn, metrics.month_end(year_month), fetch=fetch)
        (reports_dir / f"cruzar-{year_month}.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
    logger.info("wrote %d report(s) to %s", len(months), reports_dir)
