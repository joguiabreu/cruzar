"""Conversational-query core (plan 018, ADR-17). Tests the analytics catalog —
the figures (Decimal, reconciling with metrics), deterministic period resolution,
and the plan → run → render flow with a fake planner. Fully offline: the planner is
always a fake; no Ollama, no network.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from cruzar import analytics, metrics
from cruzar.analytics import (
    InvestmentPerformance,
    NetWorth,
    Period,
    QuerySpec,
    SpendByCategory,
    SpendTotal,
    Unsupported,
)
from cruzar.db import connect, init_schema


# --- fake planner (the LlmExtractor/categorizer pattern) ----------------------------

class _FixedPlanner:
    def __init__(self, spec: QuerySpec) -> None:
        self._spec = spec
        self.calls = 0

    def plan(self, question: str, today: date) -> QuerySpec:
        self.calls += 1
        return self._spec


# --- period resolution (deterministic; no model) ------------------------------------

def test_resolve_period_relative_and_explicit() -> None:
    today = date(2026, 6, 10)
    d = date
    # Month-grained inputs resolve to the month's first…last day (relatives end at today).
    assert analytics.resolve_period(Period(last_n_months=6), today) == (d(2026, 1, 1), today)
    assert analytics.resolve_period(Period(year=2025), today) == (d(2025, 1, 1), d(2025, 12, 31))
    assert analytics.resolve_period(Period(this_year=True), today) == (d(2026, 1, 1), today)
    assert analytics.resolve_period(Period(last_n_years=1), today) == (d(2025, 7, 1), today)
    assert analytics.resolve_period(Period(start="2025-03", end="2025-05"), today) == (d(2025, 3, 1), d(2025, 5, 31))
    assert analytics.resolve_period(Period(), today) == (d(2025, 7, 1), today)  # default 12m
    # Defensive: a reversed explicit range is swapped, not silently resolved to nothing.
    assert analytics.resolve_period(Period(start="2026-06-10", end="2025-06-10"), today) == (d(2025, 6, 10), d(2026, 6, 10))


def test_resolve_period_day_level() -> None:
    today = date(2026, 6, 19)
    d = date
    # Explicit day range (the vacation case) survives instead of widening to the month.
    assert analytics.resolve_period(Period(start="2026-06-10", end="2026-06-30"), today) == (d(2026, 6, 10), d(2026, 6, 30))
    # last_n_days is inclusive of today: 10 days back == today-9.
    assert analytics.resolve_period(Period(last_n_days=10), today) == (d(2026, 6, 10), today)
    # this_month = 1st → today; last_month = the WHOLE previous calendar month (the bug fix).
    assert analytics.resolve_period(Period(this_month=True), today) == (d(2026, 6, 1), today)
    assert analytics.resolve_period(Period(last_month=True), today) == (d(2026, 5, 1), d(2026, 5, 31))
    # last_month across a year boundary.
    assert analytics.resolve_period(Period(last_month=True), date(2026, 1, 15)) == (d(2025, 12, 1), d(2025, 12, 31))


# --- data builders ------------------------------------------------------------------

def _account(conn: sqlite3.Connection, name: str, account_type: str = "checking") -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES (?, ?, ?, 'manual', ?, 'EUR', '2025-01-01T00:00:00+00:00')",
        (name, name, name, account_type),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(conn: sqlite3.Connection, account_id: int, ym: str, closing: str = "0.00") -> int:
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, closing_balance, "
        "created_at) VALUES (?, ?, ?, ?, 'x')",
        (account_id, f"{ym}-01", metrics.month_end(ym).isoformat(), closing),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _merchant(conn: sqlite3.Connection, name: str, category: str) -> int:
    conn.execute("INSERT INTO categories(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (category,))
    cur = conn.execute("INSERT INTO merchants(name, category) VALUES (?, ?)", (name, category))
    assert cur.lastrowid is not None
    return cur.lastrowid


def _txn(conn: sqlite3.Connection, stmt: int, seq: int, amount: str, merchant_id: int | None) -> None:
    conn.execute(
        "INSERT INTO transactions(statement_id, date, amount, description_raw, "
        "intra_statement_seq, merchant_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (stmt, _date_for(conn, stmt), amount, f"T{stmt}-{seq}", seq, merchant_id, f"h{stmt}-{seq}"),
    )


def _date_for(conn: sqlite3.Connection, stmt: int) -> str:
    row = conn.execute("SELECT period_start FROM statements WHERE id = ?", (stmt,)).fetchone()
    return str(row["period_start"])[:7] + "-15"  # mid-month


# --- catalog correctness ------------------------------------------------------------

def test_spend_by_category_over_range_reconciles(tmp_path: Path) -> None:
    conn = connect(tmp_path / "a.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking")
        dining = _merchant(conn, "Cafe", "Dining")
        for ym, amt in [("2026-01", "-10.00"), ("2026-02", "-15.00"), ("2026-03", "-5.00")]:
            stmt = _statement(conn, acct, ym)
            _txn(conn, stmt, 1, amt, dining)
            _txn(conn, stmt, 2, "-1.00", None)  # an uncategorized euro each month
        conn.commit()

        spec = SpendByCategory(metric="spend_by_category", period=Period(start="2026-01", end="2026-03"))
        result = analytics.run(conn, spec, today=date(2026, 6, 1), fetch=None)
        assert result.rows is not None
        rows = dict(result.rows)
        assert rows["Dining"] == Decimal("-30.00")  # 10+15+5
        assert rows["Uncategorized"] == Decimal("-3.00")
        # reconciles with summing the per-month metric
        per_month = sum(
            (v for ym in ("2026-01", "2026-02", "2026-03")
             for k, v in metrics.spending_by_category(conn, ym, fetch=None) if k == "Dining"),
            Decimal(0),
        )
        assert rows["Dining"] == per_month

        # filtered to one category (case-insensitive) → scalar, canonical name echoed
        filtered = analytics.run(
            conn,
            SpendByCategory(metric="spend_by_category", period=Period(start="2026-01", end="2026-03"), categories=["dining"]),
            today=date(2026, 6, 1), fetch=None,
        )
        assert filtered.subject == "Dining" and filtered.scalar == Decimal("-30.00")
    finally:
        conn.close()


def test_spend_by_multiple_categories_sums_and_echoes(tmp_path: Path) -> None:
    """A concept like 'food' maps to a set of categories; they are summed and both names
    are echoed back so the mapping is visible."""
    conn = connect(tmp_path / "multi.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking")
        dining = _merchant(conn, "Cafe", "Dining")
        grocer = _merchant(conn, "Mart", "Groceries")
        stmt = _statement(conn, acct, "2026-02")
        _txn(conn, stmt, 1, "-20.00", dining)
        _txn(conn, stmt, 2, "-30.00", grocer)
        conn.commit()
        result = analytics.run(
            conn,
            SpendByCategory(metric="spend_by_category", period=Period(start="2026-02", end="2026-02"), categories=["Dining", "Groceries"]),
            today=date(2026, 6, 1), fetch=None,
        )
        assert result.scalar == Decimal("-50.00")
        assert result.subject == "Dining + Groceries"  # mapping echoed back
        assert "Dining + Groceries" in analytics.render(result)
    finally:
        conn.close()


def test_spend_total_sums_months(tmp_path: Path) -> None:
    conn = connect(tmp_path / "b.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking")
        for ym, amt in [("2026-01", "-100.00"), ("2026-02", "-50.00")]:
            _txn(conn, _statement(conn, acct, ym), 1, amt, None)
        conn.commit()
        result = analytics.run(
            conn, SpendTotal(metric="spend_total", period=Period(start="2026-01", end="2026-02")),
            today=date(2026, 6, 1), fetch=None,
        )
        assert result.scalar == Decimal("-150.00")
    finally:
        conn.close()


def test_investment_performance_is_iv_delta_net_contrib(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        init_schema(conn)
        broker = _account(conn, "Broker", "brokerage")
        # Jan snapshot 1000, Feb snapshot 1200 (a price-only +200, no contributions).
        for ym, value in [("2026-01", "1000.00"), ("2026-02", "1200.00")]:
            stmt = _statement(conn, broker, ym)
            conn.execute(
                "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, snapshot_date, "
                "quantity, cost_basis, value, currency) VALUES (?, ?, 'AAA', ?, '1', NULL, ?, 'EUR')",
                (broker, stmt, metrics.month_end(ym).isoformat(), value),
            )
        conn.commit()
        result = analytics.run(
            conn,
            InvestmentPerformance(metric="investment_performance", period=Period(start="2026-02", end="2026-02")),
            today=date(2026, 6, 1), fetch=None, investment_flow_patterns=[],
        )
        assert result.scalar == Decimal("200.00")  # price-only rise, no contributions
    finally:
        conn.close()


# --- plan → run → render flow -------------------------------------------------------

def test_answer_renders_python_figure(tmp_path: Path) -> None:
    conn = connect(tmp_path / "d.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking")
        _txn(conn, _statement(conn, acct, "2026-01"), 1, "-42.50", None)
        conn.commit()
        planner = _FixedPlanner(
            SpendTotal(metric="spend_total", period=Period(start="2026-01", end="2026-01"))
        )
        out = analytics.answer(conn, "whatever", planner=planner, today=date(2026, 6, 1), fetch=None)
        assert planner.calls == 1
        assert "€42.50" in out and "2026-01" in out
    finally:
        conn.close()


def test_answer_renders_day_precise_span(tmp_path: Path) -> None:
    """A day-window question renders the resolved ISO day bounds (D5), not the month."""
    conn = connect(tmp_path / "dr.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking")
        _txn(conn, _statement(conn, acct, "2026-06"), 1, "-12.00", None)  # mid-month (15th)
        conn.commit()
        planner = _FixedPlanner(
            SpendTotal(metric="spend_total", period=Period(start="2026-06-10", end="2026-06-30"))
        )
        out = analytics.answer(conn, "vacation spend", planner=planner, today=date(2026, 7, 1), fetch=None)
        assert "from 2026-06-10 to 2026-06-30" in out and "€12.00" in out
    finally:
        conn.close()


def test_answer_unsupported_returns_capabilities(tmp_path: Path) -> None:
    conn = connect(tmp_path / "e.db")
    try:
        init_schema(conn)
        planner = _FixedPlanner(Unsupported(metric="unsupported", reason="not a finance question"))
        out = analytics.answer(conn, "what's the weather?", planner=planner, today=date(2026, 6, 1), fetch=None)
        assert out == analytics.CAPABILITIES
    finally:
        conn.close()


def test_net_worth_as_of_point_in_time(tmp_path: Path) -> None:
    conn = connect(tmp_path / "f.db")
    try:
        init_schema(conn)
        acct = _account(conn, "Checking")
        _statement(conn, acct, "2026-01", closing="500.00")
        conn.commit()
        result = analytics.run(
            conn, NetWorth(metric="net_worth", as_of="2026-01"), today=date(2026, 6, 1), fetch=None
        )
        assert result.scalar == Decimal("500.00") and result.as_of == "2026-01-31"
    finally:
        conn.close()
