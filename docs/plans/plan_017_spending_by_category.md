# Plan 017 — Spending by Category (report section)

## Goal

Add a per-category spending rollup for the month to each report — "where did my
money go this month." A new `## Spending by Category` section: each category and how
much was spent on it, in EUR, summing to that month's Summary **Spent**. Pure report
addition: no schema change, no new deps, fully offline.

## Decisions (settled)

1. **D1 — currency = EUR (base), at the month-end rate.** Same ADR-5 period-end
   conversion as the Summary, so the category rows **sum exactly to the Summary's
   Spent** (self-reconciling; the test asserts it). Chosen over native-per-currency
   (which needs a category×currency grid once two currencies appear). An absent
   month-end rate degrades like every other EUR figure (`n/a`).
2. **D2 — placement + AC9.** New section placed **right after Spending Detail**:
   Summary → Spending Detail → **Spending by Category** → Earning Detail → Investment
   Detail → [Needs Categorization] → [Conflicts]. Always shown. Extends **AC9**'s
   enumeration, so AC9's text + section-order test are updated.
3. **D3 — uncategorized bucket + sort.** Spending with no matched merchant/category
   is bucketed as **"Uncategorized"** (nothing dropped, so rows still sum to Spent).
   Rows sorted **most-spent-first** (largest magnitude on top). Same filter as
   `metrics.spent`: cash accounts, `amount < 0`, `is_transfer = 0`, `superseded = 0`,
   this month.

## Design

- `metrics.spending_by_category(conn, ym, *, fetch) -> list[tuple[str, Decimal]]`:
  the spent rows joined to `merchants.category` (`COALESCE(..., 'Uncategorized')`),
  summed per (category, currency), each converted to EUR at the month-end rate,
  ordered by amount ascending (most negative first). Mirrors `_flow`'s filter so the
  totals reconcile with `spent()`.
- `report._spending_by_category_section(conn, ym, *, fetch)`: renders
  `| Category | Spent (EUR) |`, wired into `write_reports` right after
  `_spending_section`. Read-only (AC13 unaffected). Catches `FxError` and degrades to
  an `n/a` note, like the Summary cells.

```
## Spending by Category

| Category | Spent (EUR) |
| --- | --- |
| Groceries | -242.50 |
| Subscriptions | -10.00 |
| Uncategorized | -42.50 |
```

## Tests

- `test_ac09_*`: add the new section to the asserted section order (both functions).
- New `test_spending_by_category.py`: a fixture with two categories + one
  uncategorized debit; assert per-category EUR totals, the "Uncategorized" bucket,
  sort order, and that **Σ rows == `metrics.spent(ym)`** (self-reconciling). A
  foreign-currency spend with a cached rate checks the EUR conversion (D1).
- Existing suite stays green (offline).

## SPEC + README

- SPEC §Outputs: add the "Spending by Category" section (EUR, sums to Spent); extend
  **AC9**'s enumeration. No ADR change.
- README: add the section to the sample report and the prose description.

## Out of scope

- Per-category Earned (income breakdown) — easy follow-up if wanted.
- Month-over-month category trends / charts / budgets.

## Definition of done

- New section renders and reconciles with Spent; AC9 (updated) + new test green.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- SPEC + README updated. No real-run gate (offline, derived report only).
