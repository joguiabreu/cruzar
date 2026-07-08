"""AC23: conversational queries (``cruzar ask``) reconcile with the ledger (ADR-17).

``analytics.run`` over a ``QuerySpec`` returns figures equal to the independently-summed
ledger total for the resolved period — across spend total, spend by category, spend by
merchant, and income. Day-level periods (plan 025): an explicit day window sums only the
in-window lines, a full-month day window equals the month total, and ``last_month``
resolves to the PREVIOUS calendar month (not the current one). Fully offline; figures are
Decimal and computed in Python (the model only picks the query). Obviously-fake data.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import analytics, metrics
from cruzar.analytics import (
    IncomeTotal,
    Period,
    SpendByCategory,
    SpendByMerchant,
    SpendTotal,
)
from cruzar.db import connect, init_schema

# (ym, day, amount, merchant, category, source) — obviously-fake, round figures.
# Spending is negative; the single income line is positive with no merchant.
_LEDGER = [
    ("2026-06", 5, "-10.00", "Acme Coffee", "Dining", None),
    ("2026-06", 15, "-20.00", "Acme Coffee", "Dining", None),
    ("2026-06", 25, "-30.00", "Globex Market", "Groceries", None),
    ("2026-06", 28, "-40.00", "Acme Coffee", "Dining", None),
    ("2026-06", 15, "1000.00", None, None, "SALARY"),
    ("2026-07", 5, "-500.00", None, None, None),  # next month — must be excluded by "last month"
]


def _sum(*, ym: str | None = None, lo: date | None = None, hi: date | None = None,
         merchant: str | None = None, category: str | None = None, spend: bool = True) -> Decimal:
    """Independently sum the seed ledger for a window/filter (the oracle for `run`)."""
    total = Decimal(0)
    for r_ym, day, amount, m, c, _src in _LEDGER:
        d = date(int(r_ym[:4]), int(r_ym[5:7]), day)
        amt = Decimal(amount)
        if (amt < 0) != spend:
            continue
        if ym is not None and r_ym != ym:
            continue
        if lo is not None and not (lo <= d <= hi):  # type: ignore[operator]
            continue
        if merchant is not None and m != merchant:
            continue
        if category is not None and c != category:
            continue
        total += amt
    return total


def _insert(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    cur = conn.execute(sql, params)
    assert cur.lastrowid is not None
    return cur.lastrowid


def _seed(conn: sqlite3.Connection) -> None:
    acct = _insert(
        conn,
        "INSERT INTO accounts(institution, name, account_match, source_type, account_type, "
        "currency, created_at) VALUES ('Bank', 'Checking', 'checking', 'manual', 'checking', "
        "'EUR', '2026-01-01T00:00:00+00:00')",
        (),
    )
    merchants: dict[str, int] = {}
    for _ym, _day, _amt, name, category, _src in _LEDGER:
        if name and name not in merchants:
            conn.execute("INSERT INTO categories(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (category,))
            merchants[name] = _insert(conn, "INSERT INTO merchants(name, category) VALUES (?, ?)", (name, category))
    stmts: dict[str, int] = {}
    for i, (ym, day, amount, name, _cat, source) in enumerate(_LEDGER):
        if ym not in stmts:
            stmts[ym] = _insert(
                conn,
                "INSERT INTO statements(account_id, period_start, period_end, closing_balance, "
                "created_at) VALUES (?, ?, ?, '0.00', 'x')",
                (acct, f"{ym}-01", metrics.month_end(ym).isoformat()),
            )
        conn.execute(
            "INSERT INTO transactions(statement_id, date, amount, description_raw, "
            "intra_statement_seq, merchant_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (stmts[ym], f"{ym}-{day:02d}", amount, source or f"T{i}", i,
             merchants.get(name) if name else None, f"h{i}"),
        )
    conn.commit()


def test_ac23_spend_reconciles_full_month_and_day_window(tmp_path: Path) -> None:
    conn = connect(tmp_path / "ac23.db")
    try:
        init_schema(conn)
        _seed(conn)
        today = date(2026, 7, 10)  # June is a completed month
        june, vac_lo, vac_hi = "2026-06", date(2026, 6, 10), date(2026, 6, 30)

        # Whole-month total == the independent June sum == the month-grained metric.
        full = analytics.run(conn, SpendTotal(metric="spend_total",
                             period=Period(start="2026-06", end="2026-06")), today=today, fetch=None)
        assert full.scalar == _sum(ym=june) == Decimal("-100.00")
        assert full.scalar == metrics.spent(conn, june, fetch=None)  # full-month clip is a no-op

        # The vacation window (10th–30th) sums only in-window lines — the 5th is excluded.
        vac = analytics.run(conn, SpendTotal(metric="spend_total",
                            period=Period(start="2026-06-10", end="2026-06-30")), today=today, fetch=None)
        assert vac.scalar == _sum(lo=vac_lo, hi=vac_hi) == Decimal("-90.00")
        assert vac.period == ("2026-06-10", "2026-06-30")  # day-precise span (D5)
    finally:
        conn.close()


def test_ac23_grouped_and_income_reconcile(tmp_path: Path) -> None:
    conn = connect(tmp_path / "ac23g.db")
    try:
        init_schema(conn)
        _seed(conn)
        today = date(2026, 7, 10)
        vac = Period(start="2026-06-10", end="2026-06-30")

        # By merchant, day-windowed: Acme = 15th+28th only (the 5th is out of window).
        by_m = dict(analytics.run(conn, SpendByMerchant(metric="spend_by_merchant", period=vac),
                                  today=today, fetch=None).rows or [])
        assert by_m["Acme Coffee"] == _sum(lo=date(2026, 6, 10), hi=date(2026, 6, 30), merchant="Acme Coffee")
        assert by_m["Acme Coffee"] == Decimal("-60.00")  # 20 + 40
        assert by_m["Globex Market"] == Decimal("-30.00")

        # By category over the whole month reconciles with the independent sum.
        by_c = dict(analytics.run(conn, SpendByCategory(metric="spend_by_category",
                    period=Period(start="2026-06", end="2026-06")), today=today, fetch=None).rows or [])
        assert by_c["Dining"] == _sum(ym="2026-06", category="Dining") == Decimal("-70.00")
        assert by_c["Groceries"] == _sum(ym="2026-06", category="Groceries") == Decimal("-30.00")

        # Income total for June.
        inc = analytics.run(conn, IncomeTotal(metric="income_total",
                            period=Period(start="2026-06", end="2026-06")), today=today, fetch=None)
        assert inc.scalar == _sum(ym="2026-06", spend=False) == Decimal("1000.00")
    finally:
        conn.close()


def test_ac23_last_month_is_previous_calendar_month(tmp_path: Path) -> None:
    """The bug this fixes: 'last month' must be the PREVIOUS month, never the current one,
    and never leak the next month's spending."""
    conn = connect(tmp_path / "ac23lm.db")
    try:
        init_schema(conn)
        _seed(conn)
        today = date(2026, 7, 10)  # 'last month' == June; July's -500 must be excluded

        result = analytics.run(conn, SpendTotal(metric="spend_total", period=Period(last_month=True)),
                               today=today, fetch=None)
        assert result.scalar == _sum(ym="2026-06") == Decimal("-100.00")
        assert result.period == ("2026-06-01", "2026-06-30")
        assert result.scalar != _sum(ym="2026-07")  # did not resolve to the current month
    finally:
        conn.close()
