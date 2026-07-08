# Plan 021 — Summary "Net" column (Earned + Spent)

**Goal.** Add a per-month **Net** to the Summary: `Earned + Spent` (Spent is stored
negative, so this is what you actually kept that month). One new column, in EUR, that
reconciles for free (it's the sum of two columns already shown). No new metric, no
schema change, fully offline.

## Before / after

```
now:    | Month | Earned | Spent | Portfolio Δ | Net Worth |
after:  | Month | Earned | Spent | Net | Portfolio Δ | Net Worth |
                                  └ Earned + Spent (e.g. 2000.00 + -610.00 = 1390.00)
```

## Decisions (signed off)

- **D1 — column name: `Net`.** "Delta" was the original ask, but the Summary already
  has a **Portfolio Δ** column, so a second "Δ" would be confusing. `Net` =
  Earned + Spent = net cash flow for the month.
- **D2 — placement: right after `Spent`,** so the three cash figures sit together
  (`Earned | Spent | Net`) before the investment columns.

## Design

- `report._summary_section` already computes `earned` and `spent` per month; add a
  **Net** cell rendered via the same `_cell` degradation path, so if a month-end FX
  rate is missing and Earned/Spent show `n/a`, Net does too. Net is EUR like the rest
  of the Summary.
- Added `metrics.net(conn, ym, *, fetch)` = `earned(ym) + spent(ym)` — pure
  composition, no new query, for clarity/testability.

## SPEC / AC / tests

- **Touches AC9 + SPEC §Outputs Section 1** (a schema extension, not an ADR): added a
  **Net** metric definition, added "Net" to the Section-1 column list, and to AC9's
  wording.
- AC9 section test now asserts the new header row.
- New test (`test_ac09_summary_net_equals_earned_plus_spent`): asserts each month's
  Net cell == Earned + Spent, including a month where Spent > Earned so Net is negative.
- README: Net column added to the sample report + a line in the prose.

## Out of scope

- Any change to Earned/Spent themselves, or to Portfolio Δ / Net Worth.
- A cumulative/running total or savings-rate % — easy follow-ups if wanted.

## Definition of done

- Net column renders and equals Earned + Spent; AC9 (updated) + the new test green.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- SPEC + README updated. No real-run gate (derived, offline report change).
