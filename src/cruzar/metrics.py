"""Report metrics (Summary, Section 1) — pure read-only computations over the DB.

Earned/Spent are cash-account flows for a month; Net Worth is the period-end stock
(cash closing balances + holdings value) as of a month-end, converted to base (EUR)
at that month-end's rate (ADR-16 / ADR-5). No writes and no rendering here — that
keeps these trivially testable; ``report.py`` formats the results.
"""

from __future__ import annotations

import calendar
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from cruzar import fx
from cruzar.fx import Fetcher

_CASH_TYPES = ("checking", "savings")
_INVESTMENT_TYPES = ("brokerage", "retirement")


@dataclass(frozen=True)
class Holding:
    symbol: str
    quantity: Decimal
    currency: str
    cost_basis: Decimal | None  # None when the broker doesn't report it (e.g. Degiro)
    value: Decimal  # market value, native currency


@dataclass(frozen=True)
class AccountHoldings:
    name: str
    holdings: list[Holding]


def month_end(ym: str) -> date:
    """Last calendar day of a ``YYYY-MM`` month."""
    year, month = (int(part) for part in ym.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def months_available(conn: sqlite3.Connection) -> list[str]:
    """All ``YYYY-MM`` months with activity (cash txns, statements, snapshots), newest first."""
    rows = conn.execute(
        "SELECT substr(t.date, 1, 7) AS ym FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "UNION SELECT substr(period_end, 1, 7) FROM statements "
        "UNION SELECT substr(snapshot_date, 1, 7) FROM holdings_snapshot",
        _CASH_TYPES,
    ).fetchall()
    return sorted({row[0] for row in rows}, reverse=True)


def earned(conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None) -> Decimal:
    """Cash-account inflows (amount > 0, not transfers) in ``ym``, in EUR."""
    return _flow(conn, ym, positive=True, fetch=fetch)


def spent(conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None) -> Decimal:
    """Cash-account outflows (amount < 0, not transfers) in ``ym``, in EUR (negative)."""
    return _flow(conn, ym, positive=False, fetch=fetch)


def net_worth(conn: sqlite3.Connection, on: date, *, fetch: Fetcher | None) -> Decimal:
    """Net Worth as of month-end ``on`` (ADR-16): for each account not closed by
    ``on``, the latest cash ``closing_balance`` ≤ on plus the latest holdings
    snapshot ≤ on, each converted to EUR at ``on``'s rate."""
    end = on.isoformat()
    total = Decimal(0)
    for acct in conn.execute("SELECT id, currency, closed_at FROM accounts").fetchall():
        if acct["closed_at"] and date.fromisoformat(acct["closed_at"][:10]) <= on:
            continue  # closed as of this month-end (ADR-16)
        stmt = conn.execute(
            "SELECT closing_balance FROM statements WHERE account_id = ? "
            "AND period_end <= ? ORDER BY period_end DESC LIMIT 1",
            (acct["id"], end),
        ).fetchone()
        if stmt is not None:
            total += fx.convert(conn, Decimal(stmt["closing_balance"]), acct["currency"], on, fetch=fetch)
        latest = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM holdings_snapshot "
            "WHERE account_id = ? AND snapshot_date <= ?",
            (acct["id"], end),
        ).fetchone()
        if latest is not None and latest["d"] is not None:
            holdings = conn.execute(
                "SELECT value, currency FROM holdings_snapshot "
                "WHERE account_id = ? AND snapshot_date = ?",
                (acct["id"], latest["d"]),
            ).fetchall()
            for h in holdings:
                total += fx.convert(conn, Decimal(h["value"]), h["currency"], on, fetch=fetch)
    return total


def investment_holdings(conn: sqlite3.Connection, on: date) -> list[AccountHoldings]:
    """Per investment account, the latest holdings snapshot ≤ ``on`` (Section 4)."""
    end = on.isoformat()
    result: list[AccountHoldings] = []
    accounts = conn.execute(
        "SELECT id, name FROM accounts "
        f"WHERE account_type IN ({','.join('?' * len(_INVESTMENT_TYPES))}) "
        "ORDER BY name",
        _INVESTMENT_TYPES,
    ).fetchall()
    for acct in accounts:
        latest = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM holdings_snapshot "
            "WHERE account_id = ? AND snapshot_date <= ?",
            (acct["id"], end),
        ).fetchone()
        if latest is None or latest["d"] is None:
            continue
        rows = conn.execute(
            "SELECT symbol, quantity, currency, cost_basis, value FROM holdings_snapshot "
            "WHERE account_id = ? AND snapshot_date = ? ORDER BY symbol",
            (acct["id"], latest["d"]),
        ).fetchall()
        holdings = [
            Holding(
                symbol=r["symbol"],
                quantity=Decimal(r["quantity"]),
                currency=r["currency"],
                cost_basis=Decimal(r["cost_basis"]) if r["cost_basis"] is not None else None,
                value=Decimal(r["value"]),
            )
            for r in rows
        ]
        result.append(AccountHoldings(name=acct["name"], holdings=holdings))
    return result


def _flow(
    conn: sqlite3.Connection, ym: str, *, positive: bool, fetch: Fetcher | None
) -> Decimal:
    rows = conn.execute(
        "SELECT a.currency AS currency, t.amount AS amount FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "AND t.is_transfer = 0 AND substr(t.date, 1, 7) = ?",
        (*_CASH_TYPES, ym),
    ).fetchall()
    by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for row in rows:
        amount = Decimal(row["amount"])
        if (amount > 0) is positive and amount != 0:
            by_currency[row["currency"]] += amount
    end = month_end(ym)
    return sum(
        (fx.convert(conn, amount, currency, end, fetch=fetch) for currency, amount in by_currency.items()),
        Decimal(0),
    )
