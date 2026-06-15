"""Report metrics (Summary, Section 1) — pure read-only computations over the DB.

Earned/Spent are cash-account flows for a month; Net Worth is the period-end stock
(cash closing balances + holdings value) as of a month-end, converted to base (EUR)
at that month-end's rate (ADR-16 / ADR-5). Portfolio Δ is total return net of
external contributions over investment accounts (ADR-14). No writes and no rendering
here — that keeps these trivially testable; ``report.py`` formats the results.
"""

from __future__ import annotations

import calendar
import re
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


@dataclass(frozen=True)
class Delta:
    """A month's Portfolio Δ (EUR). ``flagged`` marks a gross figure — at least one
    investment account can't emit cash flows, so its contributions are undetected
    (ADR-14). ``portfolio_delta`` returns ``None`` (rendered ``—``) when no prior
    snapshot exists."""

    value: Decimal
    flagged: bool


def month_end(ym: str) -> date:
    """Last calendar day of a ``YYYY-MM`` month."""
    year, month = (int(part) for part in ym.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def as_of(ym: str, today: date | None = None) -> date:
    """The valuation date for ``ym``: its month-end, but never past ``today`` (ADR-5/16).

    For a completed month this is ``month_end(ym)`` unchanged. For the IN-PROGRESS month
    the month-end is in the future, where no FX rate can exist — so we value as-of
    ``today`` instead, the latest date with a fetchable rate and the real latest snapshot.
    ``today`` defaults to the wall clock; callers that need determinism inject it."""
    return min(month_end(ym), today or date.today())


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


def earned(
    conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None, today: date | None = None
) -> Decimal:
    """Cash-account inflows (amount > 0, not transfers) in ``ym``, in EUR."""
    return _flow(conn, ym, positive=True, fetch=fetch, today=today)


def spent(
    conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None, today: date | None = None
) -> Decimal:
    """Cash-account outflows (amount < 0, not transfers) in ``ym``, in EUR (negative)."""
    return _flow(conn, ym, positive=False, fetch=fetch, today=today)


def net(
    conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None, today: date | None = None
) -> Decimal:
    """Net cash flow in ``ym``, in EUR: ``earned + spent`` (Spent is negative, so
    this is what stayed in the cash accounts). Pure composition — no new query."""
    return earned(conn, ym, fetch=fetch, today=today) + spent(conn, ym, fetch=fetch, today=today)


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


def _prev_month_end(ym: str) -> date:
    """Month-end of the calendar month before ``ym`` (ADR-14 D2: month-to-month)."""
    year, month = (int(part) for part in ym.split("-"))
    prev = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
    return month_end(prev)


def iv(conn: sqlite3.Connection, on: date, *, fetch: Fetcher | None) -> Decimal:
    """Total investment value (ADR-14) at month-end ``on``: over investment accounts,
    the latest uninvested cash ``closing_balance`` ≤ on plus the latest holdings
    snapshot ≤ on, each converted to EUR at ``on``'s rate. Securities + cash means
    internal buys/sells net to zero, so they don't pollute the figure."""
    end = on.isoformat()
    total = Decimal(0)
    accounts = conn.execute(
        "SELECT id, currency FROM accounts "
        f"WHERE account_type IN ({','.join('?' * len(_INVESTMENT_TYPES))})",
        _INVESTMENT_TYPES,
    ).fetchall()
    for acct in accounts:
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
            for h in conn.execute(
                "SELECT value, currency FROM holdings_snapshot "
                "WHERE account_id = ? AND snapshot_date = ?",
                (acct["id"], latest["d"]),
            ).fetchall():
                total += fx.convert(conn, Decimal(h["value"]), h["currency"], on, fetch=fetch)
    return total


def net_contrib(
    conn: sqlite3.Connection, ym: str, patterns: list[str], *, fetch: Fetcher | None,
    today: date | None = None,
) -> Decimal:
    """Net EXTERNAL cash flows into investment accounts in ``ym`` (ADR-14), in EUR.
    A txn counts iff its account ``emits_cash_flows`` AND it is either a detected
    transfer (``is_transfer``, e.g. a checking→brokerage pair) or its description
    matches an ``investment_flow_pattern`` (an external deposit/withdrawal). Internal
    trades are excluded. Inbound is positive, outbound negative."""
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    rows = conn.execute(
        "SELECT a.currency AS currency, t.amount AS amount, t.is_transfer AS is_transfer, "
        "t.description_raw AS description_raw FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_INVESTMENT_TYPES))}) "
        "AND a.emits_cash_flows = 1 AND t.superseded = 0 AND substr(t.date, 1, 7) = ?",
        (*_INVESTMENT_TYPES, ym),
    ).fetchall()
    by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for row in rows:
        external = row["is_transfer"] or any(
            c.search(row["description_raw"]) for c in compiled
        )
        if external:
            by_currency[row["currency"]] += Decimal(row["amount"])
    end = as_of(ym, today)
    return sum(
        (fx.convert(conn, amount, currency, end, fetch=fetch) for currency, amount in by_currency.items()),
        Decimal(0),
    )


def portfolio_delta(
    conn: sqlite3.Connection, ym: str, *, patterns: list[str], fetch: Fetcher | None,
    today: date | None = None,
) -> Delta | None:
    """Portfolio Δ for ``ym`` (ADR-14): ``(IV_end − IV_prev) − NetContrib``, in EUR.
    Returns ``None`` (rendered ``—``) when no prior snapshot exists. ``flagged`` is set
    when an investment account can't emit cash flows, so its contributions are
    undetected and its slice of Δ is gross. ``end`` is the ``as_of`` date (capped at
    ``today`` for the in-progress month); ``prev`` is always a past month-end."""
    end = as_of(ym, today)
    prev = _prev_month_end(ym)
    prior = conn.execute(
        "SELECT 1 FROM holdings_snapshot h JOIN accounts a ON h.account_id = a.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_INVESTMENT_TYPES))}) "
        "AND h.snapshot_date <= ? LIMIT 1",
        (*_INVESTMENT_TYPES, prev.isoformat()),
    ).fetchone()
    if prior is None:
        return None  # no prior snapshot → "—"
    flagged = _has_gross_account(conn, end)
    gross = iv(conn, end, fetch=fetch) - iv(conn, prev, fetch=fetch)
    return Delta(gross - net_contrib(conn, ym, patterns, fetch=fetch, today=today), flagged)


def _has_gross_account(conn: sqlite3.Connection, on: date) -> bool:
    """Any investment account that can't emit cash flows yet holds value by ``on``
    (a snapshot or a statement ≤ on) — its contributions are undetectable (ADR-14)."""
    end = on.isoformat()
    row = conn.execute(
        "SELECT 1 FROM accounts a WHERE a.emits_cash_flows = 0 "
        f"AND a.account_type IN ({','.join('?' * len(_INVESTMENT_TYPES))}) AND ("
        "  EXISTS (SELECT 1 FROM holdings_snapshot h "
        "          WHERE h.account_id = a.id AND h.snapshot_date <= ?) "
        "  OR EXISTS (SELECT 1 FROM statements s "
        "             WHERE s.account_id = a.id AND s.period_end <= ?)) LIMIT 1",
        (*_INVESTMENT_TYPES, end, end),
    ).fetchone()
    return row is not None


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


def spending_by_category(
    conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None, today: date | None = None
) -> list[tuple[str, Decimal]]:
    """This month's cash spending grouped by category, in EUR (most-spent first).

    Same filter as ``spent`` (cash accounts, amount < 0, not transfer, not superseded),
    joined to ``merchants.category`` — spending with no matched merchant is bucketed as
    ``Uncategorized`` so nothing is dropped and the totals sum to ``spent(ym)``. Summed
    per (category, currency) then converted to EUR at the ``as_of`` rate (ADR-5),
    most-spent first."""
    return _group_spend(conn, ym, "COALESCE(m.category, 'Uncategorized')", fetch=fetch, today=today)


def spending_by_merchant(
    conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None, today: date | None = None
) -> list[tuple[str, Decimal]]:
    """This month's cash spending grouped by matched merchant name, in EUR (most-spent
    first). Same filter/method as ``spending_by_category``; spending with no matched
    merchant is bucketed as ``Uncategorized``."""
    return _group_spend(conn, ym, "COALESCE(m.name, 'Uncategorized')", fetch=fetch, today=today)


def income_by_source(
    conn: sqlite3.Connection, ym: str, *, fetch: Fetcher | None, today: date | None = None
) -> list[tuple[str, Decimal]]:
    """This month's cash income grouped by source, in EUR (most-earned first). Source =
    matched merchant name else the raw description (the Earning Detail convention). Same
    filter as ``earned``; the totals sum to ``earned(ym)``."""
    rows = conn.execute(
        "SELECT a.currency AS currency, t.amount AS amount, "
        "COALESCE(m.name, t.description_raw) AS source FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "LEFT JOIN merchants m ON t.merchant_id = m.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "AND t.is_transfer = 0 AND t.superseded = 0 "
        "AND t.amount NOT LIKE '-%' AND t.amount != '0.00' "
        "AND substr(t.date, 1, 7) = ?",
        (*_CASH_TYPES, ym),
    ).fetchall()
    totals = _convert_grouped(conn, rows, key="source", ym=ym, fetch=fetch, today=today)
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))  # most-earned first


def _group_spend(
    conn: sqlite3.Connection, ym: str, key_expr: str, *, fetch: Fetcher | None,
    today: date | None = None,
) -> list[tuple[str, Decimal]]:
    rows = conn.execute(
        f"SELECT a.currency AS currency, t.amount AS amount, {key_expr} AS grp "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "LEFT JOIN merchants m ON t.merchant_id = m.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "AND t.is_transfer = 0 AND t.superseded = 0 AND t.amount LIKE '-%' "
        "AND substr(t.date, 1, 7) = ?",
        (*_CASH_TYPES, ym),
    ).fetchall()
    totals = _convert_grouped(conn, rows, key="grp", ym=ym, fetch=fetch, today=today)
    return sorted(totals.items(), key=lambda kv: (kv[1], kv[0]))  # most-spent first


def _convert_grouped(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    key: str,
    ym: str,
    fetch: Fetcher | None,
    today: date | None = None,
) -> dict[str, Decimal]:
    """Sum native amounts per (group, currency), then convert each to EUR at the
    ``as_of`` rate (ADR-5) and total per group."""
    by_grp_cur: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
    for row in rows:
        by_grp_cur[(row[key], row["currency"])] += Decimal(row["amount"])
    end = as_of(ym, today)
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for (grp, currency), amount in by_grp_cur.items():
        totals[grp] += fx.convert(conn, amount, currency, end, fetch=fetch)
    return totals


def _flow(
    conn: sqlite3.Connection, ym: str, *, positive: bool, fetch: Fetcher | None,
    today: date | None = None,
) -> Decimal:
    rows = conn.execute(
        "SELECT a.currency AS currency, t.amount AS amount FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        f"WHERE a.account_type IN ({','.join('?' * len(_CASH_TYPES))}) "
        "AND t.is_transfer = 0 AND t.superseded = 0 AND substr(t.date, 1, 7) = ?",
        (*_CASH_TYPES, ym),
    ).fetchall()
    by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for row in rows:
        amount = Decimal(row["amount"])
        if (amount > 0) is positive and amount != 0:
            by_currency[row["currency"]] += amount
    end = as_of(ym, today)
    return sum(
        (fx.convert(conn, amount, currency, end, fetch=fetch) for currency, amount in by_currency.items()),
        Decimal(0),
    )
