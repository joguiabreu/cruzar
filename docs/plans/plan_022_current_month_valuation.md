# Plan 022 — Value the in-progress month as-of today

## The bug

The report valued every month as-of `month_end(ym)` — the last calendar day. For the
**current, in-progress month** that date is in the *future* (today 2026-06-15;
`month_end("2026-06")` = 2026-06-30). No FX rate can exist for a future date, so any
non-EUR figure threw `FxError` and degraded to `n/a`. In the real data the only thing
needing conversion is a USD holding (ANET), so the IB account total, the Grand Total,
and Net Worth all rendered `n/a`; combined with no June cash transactions yet, the June
report looked empty/broken even though the current portfolio (snapshots dated
2026-06-10/11) was present. Past months were unaffected (their month-end has passed).

## Fix (decided): value the in-progress month as-of today

`metrics.as_of(ym, today) -> date = min(month_end(ym), today)`. For a completed month
this is exactly `month_end(ym)` (no change); for the in-progress month it is `today` —
a date with a fetchable rate and the real latest snapshot. The run's `today` is threaded
from the report/analytics entry points down to the metric conversions.

### Touch points (all switched from `month_end(ym)` to `as_of(ym, today)`)

| Path | Change |
| --- | --- |
| Net Worth (Summary) | report passes `as_of(ym, today)` to `net_worth` |
| Investment Detail | report passes `as_of(ym, today)` as the `on` date |
| Earned / Spent / Net | `_flow` takes `today`, converts at `as_of` |
| Spending / Income by group | `_convert_grouped` takes `today`, converts at `as_of` |
| Portfolio Δ | `end = as_of(ym, today)`; `prev` unchanged (past month-end); `net_contrib` + `_has_gross_account` use the capped `end` |
| analytics (`run`) | its existing `today` threaded into every metric call; `_resolve_as_of` caps every resolved date at `today` |

`iv`/`net_worth`/`investment_holdings` keep their `on: date` contract — only callers pass
a capped date. The `ym`-based metrics gained a `today: date | None = None` keyword
(defaulting to `date.today()`); `write_reports` gained `today: date | None = None`.

## Decisions

- **D1 — capping rule:** `as_of(ym, today) = min(month_end(ym), today)`. Past months
  unchanged; in-progress month valued at today.
- **D2 — `today` injection:** `write_reports(..., today=None)` defaults to `date.today()`
  and threads down; tests freeze `today` for determinism. `today` defaults rather than
  reading the clock deep in metrics, but the option is there so existing past-month tests
  need no edits (`min(month_end, today) == month_end` for any month already past).

## ADR / AC touched (signed off)

- **ADR-5 / ADR-16:** added a clause — the valuation date is `min(period-end, today)`;
  completed months unchanged, in-progress month valued as-of today.
- **AC10 (FX reproducibility):** scoped to **completed** months (still reproducible). The
  in-progress month is deliberately as-of-today, so not reproducible across days; a second
  fixture asserts its holdings convert at today, not the future month-end.

## Tests

- New `test_ac10_in_progress_month_valued_as_of_today`: a USD holding dated mid-June, a
  frozen `today` = 2026-06-15; asserts every conversion used today (not 2026-06-30), the
  account/Grand totals convert (200 USD @ 2.00 → 100.00 EUR, no `n/a`), and the in-progress
  Summary row shows Net Worth 100.00 with Δ `—`.
- Existing AC10 test made clock-independent with a frozen `today`, asserting completed-month
  valuation stays at the month-end.
- Existing direct-metric tests use past months, so default-today gives month-end — unchanged.
- `uv run ruff check . && uv run pyright && uv run pytest` clean (95 passed).

## Real-run gate

Re-run `uv run cruzar process`; the June report must value Net Worth and the IB/Grand
totals as-of 2026-06-15 (FX fetched for today), not `n/a`.

## Out of scope

- How statements/snapshots are dated (the June `period_end` statements are full-history
  exports — unrelated).
- Plan 019 multi-section; the held plan-020 prompt change.
- Suppressing the empty current-month report or its empty detail sections — the month is
  real (current portfolio); only its valuation date was wrong.
