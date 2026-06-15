"""Conversational-query core (ADR-17) — the bounded query catalog + executor.

This is the hexagonal *port*: a small, typed catalog of read-only analytics queries
(`QuerySpec`) plus `run`/`render`. The local LLM only maps a natural-language question
to a `QuerySpec` (the `QueryPlanner` driven port); **every number is computed here in
Python/Decimal** by reusing `metrics` (ADR-1 — the model never sums or converts). A
future MCP server is just another driving adapter over this same catalog.

The `QuerySpec` variants are pydantic models so they double as the constrained-output
schema for the planner (and, later, MCP tool schemas). Money stays `Decimal`; period
math is deterministic and done here, never by the model.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Protocol, Union

from pydantic import BaseModel, Field

from cruzar import metrics
from cruzar.fx import Fetcher

logger = logging.getLogger(__name__)
_CENTS = Decimal("0.01")


# --- the catalog (the port / tool contract) ----------------------------------------

class Period(BaseModel):
    """A time window. Either an explicit ``start``/``end`` (YYYY-MM) or a relative
    descriptor; Python resolves relatives against ``today``. Default (all unset) is the
    trailing 12 months."""

    start: str | None = None  # YYYY-MM
    end: str | None = None  # YYYY-MM
    last_n_months: int | None = None
    last_n_years: int | None = None
    year: int | None = None
    this_year: bool = False


class SpendTotal(BaseModel):
    """Total cash spending over a period."""

    metric: Literal["spend_total"]
    period: Period = Field(default_factory=Period)


class SpendByCategory(BaseModel):
    """Cash spending grouped by category. ``categories`` → only those, summed (map an
    everyday word to one or more configured categories, e.g. 'food' → Dining + Groceries);
    ``top`` → the top N; neither → the full breakdown."""

    metric: Literal["spend_by_category"]
    period: Period = Field(default_factory=Period)
    categories: list[str] | None = None
    top: int | None = None


class SpendByMerchant(BaseModel):
    """Cash spending grouped by merchant. ``merchant`` → just that one; ``top`` → top N."""

    metric: Literal["spend_by_merchant"]
    period: Period = Field(default_factory=Period)
    merchant: str | None = None
    top: int | None = None


class IncomeTotal(BaseModel):
    """Total cash income over a period."""

    metric: Literal["income_total"]
    period: Period = Field(default_factory=Period)


class IncomeBySource(BaseModel):
    """Cash income grouped by source (merchant or raw description); ``top`` → top N."""

    metric: Literal["income_by_source"]
    period: Period = Field(default_factory=Period)
    top: int | None = None


class NetWorth(BaseModel):
    """Net worth at a point in time (``as_of`` YYYY-MM or YYYY-MM-DD; default latest)."""

    metric: Literal["net_worth"]
    as_of: str | None = None


class NetWorthTrend(BaseModel):
    """Net worth at each month-end over a period (a monthly series)."""

    metric: Literal["net_worth_trend"]
    period: Period = Field(default_factory=Period)


class InvestmentPerformance(BaseModel):
    """Total portfolio return (net of contributions, ADR-14) over a period."""

    metric: Literal["investment_performance"]
    period: Period = Field(default_factory=Period)


class Unsupported(BaseModel):
    """The question doesn't map to any known query."""

    metric: Literal["unsupported"]
    reason: str = ""


QuerySpec = Annotated[
    Union[
        SpendTotal, SpendByCategory, SpendByMerchant, IncomeTotal, IncomeBySource,
        NetWorth, NetWorthTrend, InvestmentPerformance, Unsupported,
    ],
    Field(discriminator="metric"),
]


class QueryPlanner(Protocol):
    """Driven port: map a natural-language question to a QuerySpec. Raises ``LlmError``
    on a transport failure; returns ``Unsupported`` when the question doesn't fit."""

    def plan(self, question: str, today: date) -> QuerySpec: ...


# --- result + capability message ----------------------------------------------------

@dataclass(frozen=True)
class QueryResult:
    metric: str
    period: tuple[str, str] | None = None  # (start_ym, end_ym)
    as_of: str | None = None
    subject: str | None = None  # e.g. the filtered category/merchant name
    scalar: Decimal | None = None
    rows: list[tuple[str, Decimal]] | None = None
    series: list[tuple[str, Decimal]] | None = None
    flagged: bool = False  # gross portfolio (contributions undetected)
    note: str | None = None


CAPABILITIES = (
    "I can answer questions about your spending, income, net worth, and investment "
    "performance over a time range — e.g. total spending, spending by category or "
    "merchant, income sources, net worth (now or as a trend), and how your investments "
    "have done. Try: \"how much did I spend on Dining in the last 6 months?\""
)


# --- period resolution (deterministic; the model never does this math) --------------

def _shift_ym(ym: str, delta_months: int) -> str:
    year, month = (int(p) for p in ym.split("-"))
    idx = year * 12 + (month - 1) + delta_months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _months(start_ym: str, end_ym: str) -> list[str]:
    if end_ym < start_ym:
        return []
    months: list[str] = []
    cur = start_ym
    while cur <= end_ym:
        months.append(cur)
        cur = _shift_ym(cur, 1)
    return months


def resolve_period(period: Period, today: date) -> tuple[str, str]:
    """Resolve a Period to inclusive (start_ym, end_ym) against ``today``.

    Defensive against the planner: explicit bounds are normalized to YYYY-MM (a model
    sometimes emits full YYYY-MM-DD) and swapped if reversed, so a backwards range never
    silently resolves to zero months (and a misleading 'you spent nothing')."""
    today_ym = f"{today.year:04d}-{today.month:02d}"
    if period.start and period.end:
        a, b = period.start[:7], period.end[:7]
        return (a, b) if a <= b else (b, a)
    if period.year is not None:
        return (f"{period.year:04d}-01", f"{period.year:04d}-12")
    if period.this_year:
        return (f"{today.year:04d}-01", today_ym)
    n_months = period.last_n_months or (period.last_n_years * 12 if period.last_n_years else None)
    n_months = n_months or 12  # default: trailing 12 months
    return (_shift_ym(today_ym, -(n_months - 1)), today_ym)


# --- execution (Python/Decimal; reuses metrics) -------------------------------------

def _merge(per_month: list[list[tuple[str, Decimal]]]) -> dict[str, Decimal]:
    merged: dict[str, Decimal] = {}
    for rows in per_month:
        for label, amount in rows:
            merged[label] = merged.get(label, Decimal(0)) + amount
    return merged


def _resolve_as_of(conn: sqlite3.Connection, as_of: str | None, today: date) -> date:
    # Never value past `today`: the in-progress month's month-end is in the future,
    # where no FX rate exists (ADR-5/16). Cap every resolved date at `today`.
    if as_of:
        if len(as_of) == 10:  # YYYY-MM-DD
            return min(date.fromisoformat(as_of), today)
        return min(metrics.month_end(as_of), today)  # YYYY-MM
    available = metrics.months_available(conn)
    if available:
        return min(metrics.month_end(available[0]), today)  # latest month with data
    return min(metrics.month_end(f"{today.year:04d}-{today.month:02d}"), today)


def run(
    conn: sqlite3.Connection,
    spec: QuerySpec,
    *,
    today: date,
    fetch: Fetcher | None,
    investment_flow_patterns: list[str] | None = None,
) -> QueryResult:
    """Execute a QuerySpec, computing every figure in Decimal (reusing metrics)."""
    patterns = investment_flow_patterns or []

    if isinstance(spec, (SpendTotal, IncomeTotal)):
        start, end = resolve_period(spec.period, today)
        fn = metrics.spent if isinstance(spec, SpendTotal) else metrics.earned
        total = sum((fn(conn, ym, fetch=fetch, today=today) for ym in _months(start, end)), Decimal(0))
        return QueryResult(spec.metric, period=(start, end), scalar=total)

    if isinstance(spec, SpendByCategory):
        start, end = resolve_period(spec.period, today)
        merged = _merge([metrics.spending_by_category(conn, ym, fetch=fetch, today=today) for ym in _months(start, end)])
        return _grouped_result(spec.metric, merged, start, end, spec.categories, spec.top, ascending=True)

    if isinstance(spec, SpendByMerchant):
        start, end = resolve_period(spec.period, today)
        merged = _merge([metrics.spending_by_merchant(conn, ym, fetch=fetch, today=today) for ym in _months(start, end)])
        subjects = [spec.merchant] if spec.merchant else None
        return _grouped_result(spec.metric, merged, start, end, subjects, spec.top, ascending=True)

    if isinstance(spec, IncomeBySource):
        start, end = resolve_period(spec.period, today)
        merged = _merge([metrics.income_by_source(conn, ym, fetch=fetch, today=today) for ym in _months(start, end)])
        return _grouped_result(spec.metric, merged, start, end, None, spec.top, ascending=False)

    if isinstance(spec, NetWorth):
        on = _resolve_as_of(conn, spec.as_of, today)
        value = metrics.net_worth(conn, on, fetch=fetch)
        return QueryResult(spec.metric, as_of=on.isoformat(), scalar=value)

    if isinstance(spec, NetWorthTrend):
        start, end = resolve_period(spec.period, today)
        series = [
            (ym, metrics.net_worth(conn, metrics.as_of(ym, today), fetch=fetch))
            for ym in _months(start, end)
        ]
        return QueryResult(spec.metric, period=(start, end), series=series)

    if isinstance(spec, InvestmentPerformance):
        start, end = resolve_period(spec.period, today)
        total, flagged, any_value = Decimal(0), False, False
        for ym in _months(start, end):
            delta = metrics.portfolio_delta(conn, ym, patterns=patterns, fetch=fetch, today=today)
            if delta is not None:
                total += delta.value
                flagged = flagged or delta.flagged
                any_value = True
        note = None if any_value else "No prior snapshot to compare against."
        return QueryResult(
            spec.metric, period=(start, end),
            scalar=total if any_value else None, flagged=flagged, note=note,
        )

    raise ValueError(f"run() called on unsupported spec: {spec.metric}")  # pragma: no cover


def _grouped_result(
    metric: str,
    merged: dict[str, Decimal],
    start: str,
    end: str,
    subjects: list[str] | None,
    top: int | None,
    *,
    ascending: bool,
) -> QueryResult:
    if subjects:
        # Sum the requested labels, matched case-insensitively. A requested label that
        # is a real category with no spending in range contributes 0 (a true answer);
        # if NONE matched, flag it so render gives an honest "didn't find" message.
        by_lower = {k.lower(): (k, v) for k, v in merged.items()}
        total = Decimal(0)
        labels: list[str] = []
        matched_any = False
        for s in subjects:
            hit = by_lower.get(s.lower())
            labels.append(hit[0] if hit else s)  # canonical name when known
            if hit is not None:
                total += hit[1]
                matched_any = True
        note = None if matched_any else "no spending found under those labels"
        return QueryResult(metric, period=(start, end), subject=" + ".join(labels),
                           scalar=total, note=note)
    ranked = sorted(merged.items(), key=lambda kv: (kv[1] if ascending else -kv[1], kv[0]))
    rows = ranked[:top] if top else ranked
    return QueryResult(metric, period=(start, end), rows=rows)


# --- rendering (the only place a number becomes text) -------------------------------

def _eur(value: Decimal) -> str:
    return f"€{value.quantize(_CENTS)}"


def _mag(value: Decimal) -> str:
    return f"€{abs(value).quantize(_CENTS)}"


def render(result: QueryResult) -> str:
    r = result
    span = f"{r.period[0]} to {r.period[1]}" if r.period else ""

    if r.metric == "spend_total":
        assert r.scalar is not None
        return f"You spent {_mag(r.scalar)} from {span}."
    if r.metric == "income_total":
        assert r.scalar is not None
        return f"You earned {_eur(r.scalar)} from {span}."

    if r.metric in ("spend_by_category", "spend_by_merchant"):
        noun = "category" if r.metric == "spend_by_category" else "merchant"
        if r.scalar is not None:  # filtered to specific subject(s)
            if r.note:  # nothing matched the requested labels
                return f"I didn't find any spending under {r.subject} from {span}."
            return f"You spent {_mag(r.scalar)} on {r.subject} from {span}."
        assert r.rows is not None
        if not r.rows:
            return f"No spending recorded from {span}."
        head = f"Your spending by {noun} from {span}:"
        return head + "\n" + "\n".join(f"  - {label}: {_mag(amt)}" for label, amt in r.rows)

    if r.metric == "income_by_source":
        assert r.rows is not None
        if not r.rows:
            return f"No income recorded from {span}."
        return f"Your income by source from {span}:\n" + "\n".join(
            f"  - {label}: {_eur(amt)}" for label, amt in r.rows
        )

    if r.metric == "net_worth":
        assert r.scalar is not None
        return f"Your net worth as of {r.as_of} was {_eur(r.scalar)}."

    if r.metric == "net_worth_trend":
        assert r.series is not None
        if not r.series:
            return f"No net-worth data from {span}."
        return f"Net worth at each month-end, {span}:\n" + "\n".join(
            f"  - {ym}: {_eur(val)}" for ym, val in r.series
        )

    if r.metric == "investment_performance":
        if r.scalar is None:
            return f"I can't compute investment performance from {span}: {r.note}"
        flag = " (gross — some contributions could not be detected)" if r.flagged else ""
        verb = "returned" if r.scalar >= 0 else "lost"
        return f"Your investments {verb} {_mag(r.scalar)} from {span} (net of contributions){flag}."

    raise ValueError(f"render() got unknown metric: {r.metric}")  # pragma: no cover


def answer(
    conn: sqlite3.Connection,
    question: str,
    *,
    planner: QueryPlanner,
    today: date,
    fetch: Fetcher | None,
    investment_flow_patterns: list[str] | None = None,
) -> str:
    """Full flow: plan (NLU) → run (Decimal) → render. Honest refusal on Unsupported;
    a graceful message if a required FX rate is missing."""
    from cruzar.fx import FxError

    spec = planner.plan(question, today)
    if isinstance(spec, Unsupported):
        logger.info("ask: unsupported question (%s)", spec.reason or "no reason")
        return CAPABILITIES
    try:
        result = run(conn, spec, today=today, fetch=fetch, investment_flow_patterns=investment_flow_patterns)
    except FxError:
        return (
            "I couldn't convert a foreign-currency amount for that period — no exchange "
            "rate is cached. Run `cruzar process` to fetch rates, then ask again."
        )
    return render(result)
