# Plan 025 — Day-level periods for `cruzar ask`

## Goal

Make `cruzar ask` understand sub-month time windows — "how much did I spend at Acme
Coffee between the 4th and the 10th of April?", "in the last 10 days", "this month",
"last month". The motivating use is **spending over a vacation**: e.g. "how much did I
spend from the 10th to the 30th of June?" answers "what did a 20-day trip cost".

Today the whole feature is month-grained: the `Period` schema only carries `YYYY-MM`
plus month-relative descriptors, and `resolve_period` truncates any day component with
`[:7]`, so a day range is silently widened to the whole month and "last N days" is
inexpressible. This slice adds **day resolution to flow (spend / income) queries** while
preserving every existing invariant. Read-only, no schema change, local Ollama only.

## Two layers that currently stop at the month

1. **Schema** — `Period` (`analytics.py:34`) has only `start`/`end` as `YYYY-MM` plus
   `last_n_months`, `last_n_years`, `year`, `this_year`. No days; no
   `this_month`/`last_month`.
2. **Executor** — every flow query loops `_months(start,end)` and calls a metric that
   filters with `substr(t.date,1,7) = ?` (e.g. `metrics.py:326`). The data is there
   (`transactions.date` is full `YYYY-MM-DD`, `schema.sql:64`) but nothing filters by it.

## Architecture (unchanged ports & adapters — ADR-17)

```
question → QueryPlanner.plan(q, today) → QuerySpec | Unsupported   (driven port)
         → analytics.run(conn, spec)   ← THE PORT / tool contract
         → QueryResult → render() → deterministic answer string
```

The LLM only maps NL → `QuerySpec`; Python/`Decimal` computes every figure (ADR-1). This
slice widens the `Period` part of that contract and the resolution math behind it.

## Decisions (settled)

1. **D1 — periods resolve to inclusive day bounds.** `resolve_period(period, today) ->
   (date, date)` (inclusive) instead of `(start_ym, end_ym)` strings. A whole-month
   request resolves to the month's first…last day, so it is *exactly* today's behavior;
   explicit `YYYY-MM-DD` bounds now survive instead of being truncated. The month
   iteration is derived from the day bounds (`_months_spanning(start_date, end_date)`),
   and each per-month metric call is passed the global `(start_date, end_date)` as an
   optional day clip.
2. **D2 — new relative descriptors + fix "last month".** Add `last_n_days: int`,
   `this_month: bool`, `last_month: bool` to `Period`. These remove the month-arithmetic
   the model gets wrong today: "last month" currently tempts the planner toward
   `last_n_months=1`, which resolves to the **current** month (`_shift_ym(today_ym, 0)`,
   `analytics.py:189-191`). The explicit descriptors make the calendar math Python's, not
   the model's. Existing `last_n_months` semantics ("trailing N months including current")
   are left untouched; the prompt steers single-month intents to the explicit descriptors.
3. **D3 — FX stays per-month (ADR-5 preserved).** Day bounds only clip *which*
   transactions are summed within each month; currency conversion stays per-month at that
   month's as-of rate, exactly as the reports do. SQL becomes
   `substr(t.date,1,7)=ym AND t.date BETWEEN ? AND ?`. A full-month clip == no clip, so a
   day slice of a month converts identically to the month it sits in — no new FX semantics.
4. **D4 — day granularity applies to FLOW queries only.** `spend_total`,
   `spend_by_category`, `spend_by_merchant`, `income_total`, `income_by_source` honor day
   bounds (spending is the use case). Point-in-time / series metrics stay month/snapshot-
   grained, because net worth comes from monthly `holdings_snapshot` rows (no daily
   snapshots): `net_worth` already accepts `YYYY-MM-DD` `as_of` (unchanged);
   `net_worth_trend` and `investment_performance` snap a day range to the covering months.
5. **D5 — render the resolved day span, in ISO `YYYY-MM-DD`.** `QueryResult.period`
   carries the resolved `(start_date, end_date)`; `render` prints e.g. "from 2026-06-10 to
   2026-06-30". ISO (not `DD-MM-YYYY`) to stay consistent with the `net_worth` `as_of`
   line and every stored date, and to stay unambiguous. Pure render-layer choice, cheap to
   revisit later — if the local format reads better after living with it, flip it in one
   place and switch `as_of` too so the answer layer stays uniform.
6. **D6 — planner prompt teaches the new shapes.** Update `ollama_query_planner`'s system
   prompt: explicit bounds may now be `YYYY-MM-DD`; prefer `last_n_days` / `this_month` /
   `last_month` for those intents; `today` anchors all relatives; still "never compute the
   bounds yourself". No code-path change beyond the prompt + the widened `Period` schema
   instructor constrains to.
7. **D7 — new AC23: `ask` reconciles with the ledger.** Adds the first acceptance
   criterion for the catalog (not just day-periods): *for a seeded ledger, `analytics.run`
   over a `QuerySpec` returns figures equal to the independently-summed ledger total for
   the resolved period; `last_month` resolves to the prior calendar month; a day-range sums
   only in-window lines; a full-month day-range equals the month total.* Deterministic and
   offline (constructed `QuerySpec` / fake planner, no Ollama), so it belongs in the
   harness. The LLM's NL→spec understanding stays **out** of the gate (real-run gate below).
   The AC23 clause is authored/blessed by the user in SPEC.md (the oracle); I propose the
   wording at implementation time.

## Design / modules

- **`analytics.py`** — widen `Period` (D2); rewrite `resolve_period` to return inclusive
  `(date, date)` (D1); add `_months_spanning(start_date, end_date)`; thread the day bounds
  through the flow branches of `run` into the metric calls; for trend/performance derive
  the month list from the day bounds (D4); `QueryResult.period` + `render` become
  day-precise ISO (D5). All numbers still Decimal, still computed here.
- **`metrics.py`** — give `spent`, `earned`, `spending_by_category`,
  `spending_by_merchant`, `income_by_source` (and the shared `_group_spend`) an optional
  `day_range: tuple[date, date] | None = None` that appends `AND t.date BETWEEN ? AND ?`.
  Default `None` ⇒ byte-for-byte the current behavior, so the report paths are untouched.
- **`llm.py`** — prompt update only (D6).
- **`cli.py`** — no change (already passes `today`, cached FX, read-only).

## Tests (offline; fake planner injected — no Ollama)

**AC23 acceptance test** — `tests/acceptance/test_ac23_ask_reconciles_with_ledger.py`
(the gate, D7), over an obviously-fake seeded ledger:

- `run(QuerySpec)` figures equal the independently-summed ledger total for the resolved
  period, across `spend_total`, `spend_by_category`, `spend_by_merchant`, `income_*`.
- `last_month` resolves to the *previous* calendar month (the bug this fixes); a 10th–30th
  June day-range sums only the in-window lines; a full-month day-range == the month total
  (clip is a no-op at month edges → reports still reconcile).

**Supporting unit tests** (cover the seams, not the AC):

- Period resolution against a fixed `today`: explicit `YYYY-MM-DD` survives;
  `last_n_days=10`, `this_month`, `last_month` resolve to the right inclusive day bounds;
  whole-month and existing descriptors resolve identically to before.
- Merchant-in-a-day-window: "Acme Coffee, 10th–30th June" returns the right Decimal.
- Render: the answer string shows the ISO day-precise span and the Python figure.
- `conftest` keeps the suite offline; `ask` never builds the real planner.

## SPEC + README + docs

- **SPEC**: one-line ADR-17 amendment (periods resolve to inclusive day bounds; flow
  metrics honor day granularity, point-in-time metrics stay month/snapshot-grained) **and**
  a new **AC23** clause the user authors (D7) — the canonical AC1–AC22 list grows to AC23.
- **README**: update the `cruzar ask` supported-question shapes to list day ranges,
  "last N days", "this/last month".
- **Learning doc** `docs/design/query_planner.md`: updated only if the user asks (per
  CLAUDE.md, not proactively).

## Out of scope (flagged, not silently done)

- **Fuzzy / substring merchant matching.** Merchant filtering is exact (case-insensitive)
  — `_grouped_result` (`analytics.py:295`) — so "Acme" won't match a stored "Acme Coffee".
  A real limitation but a separate concern from periods; possible follow-up.
- Daily net-worth snapshots; weeks, quarters, fiscal years; multi-turn memory; LLM
  narration; switching displayed dates to a localized format (D5 revisit).

## Definition of done

- Day-range, last-N-days, this/last-month questions resolve and compute correctly
  (Decimal, reconcile with month totals at the edges); "last month" ≠ "this month".
- Existing month-grained queries and all current tests unchanged and green.
- **AC23 green** — `uv run pytest tests/acceptance` includes
  `test_ac23_ask_reconciles_with_ledger` and it passes.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- SPEC (ADR-17 amendment + AC23 clause) + README updated.
- **Real-run gate (user):** with Ollama up, `cruzar ask` answers the motivating questions
  correctly against the real DB — including the vacation day-range.
