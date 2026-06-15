# Plan 019 — ActivoBank multi-section (multi-month) statements

## The bug (silent data loss)

A real ActivoBank export is a single PDF of **several stacked monthly sections** — each a
complete mini-statement with its own `EXTRATO DE … A …`, `SALDO INICIAL`, `SALDO FINAL`,
and salary (`VENCIMENTO`). The parser (built in plan 001 for a single-month statement)
finds the *first* page with both markers, brackets the *first* `SALDO INICIAL → FINAL`,
parses it, and stops. On the user's 15-page / 5-section file that means **4 of 5 months
are silently dropped** — 4 salaries and all their spending — and the reported period (Jan
only) and closing balance are wrong too. No error: it just under-reads.

## Goal

Parse *every* `SALDO INICIAL … SALDO FINAL` section across *all* pages into one combined
`ParsedStatement` — as the Revolut parser already does for its stacked sections. A
single-month statement is the degenerate 1-section case, so it keeps working and the
existing single-section fixture + all conftest-based tests stay untouched.

## Decisions (settled)

> **SUPERSEDED by [plan 023](plan_023_activobank_per_section_statements.md):** D1 below
> (one combined statement) broke per-month Net Worth — a single statement exposes only
> the last month's closing balance. Plan 023 emits **one statement per section** instead.
> 019's section discovery, per-section date resolution, and header/noise-row robustness
> all stand; only the final assembly changed.

1. **D1 — combined-statement semantics.** The whole file is ONE `ParsedStatement`
   (ADR-11, top-to-bottom): `period_start` = first section's start, `period_end` = last
   section's end; `closing_balance` = the *last* section's `SALDO FINAL`;
   `intra_statement_seq` runs continuously 1..N across all sections in page order.
   **Both single-month and combined statements are supported** — "find all sections"
   finds one section in the single-month case (the existing fixture proves it).
2. **D2 — section discovery.** Cluster rows *per page* (tops are page-relative) and
   concatenate in page order into one row sequence; walk it pairing each `SALDO INICIAL`
   with the next `SALDO FINAL` (a section may span a page break), parsing the date-bearing
   rows between each pair. The column bands are hardcoded and consistent across the whole
   statement, so only the bracketing loop changes from "find first" to "find all".
3. **D3 — AC4a degradation gate across sections.** The `<50%`-columns LLM-fallback check
   is computed over the candidate rows of *all* sections combined — one decision, not per
   section.
4. **D4 — per-section dates (revised).** Every section carries full dates in its own
   `EXTRATO DE 2026/01/02 A 2026/01/30` line, so we **don't infer** the year — each
   section's `M.DD` transaction dates resolve against **that section's own period**
   (the `EXTRATO DE` line most-recently seen before its `SALDO INICIAL`). This is exact
   and correct even across a year boundary, so the old "accepted cross-year edge" is gone.

## Design (localized to `activobank.parse`)

- **Period (overall):** `_PERIOD_RE.findall(all_text)` → first match's start, last match's
  end (today it's `.search` = first only).
- **Rows:** `all_rows = []; for page: all_rows += cluster_rows(page.extract_words())`
  (drop the "first page with both markers" shortcut).
- **Sections:** walk `all_rows` collecting `(inicial_idx, final_idx)` pairs and, for each,
  the section's `EXTRATO DE` period (the most recent period row before its `SALDO INICIAL`).
  Run the existing transaction-row loop per section, resolving each row's year against that
  section's period (D4), appending to a shared list with a running `seq`. `closing_balance`
  = the last pair's `SALDO FINAL` saldo.
- **AC4a:** accumulate candidate/resolved counts across sections; trip `ExtractionFallback`
  once if `resolved/candidates < 0.5`.

No data-model, pipeline, or other-parser changes — contained to one parser function plus
its fixture/test.

## Fixture & tests

- **New** `tests/fixtures/activobank_multisection/` (generator + statement.pdf +
  expected.json): a synthetic **two-section** ActivoBank statement, each section with its
  own `EXTRATO DE`, `SALDO INICIAL/FINAL`, a `VENCIMENTO` credit, and a debit. The
  obviously-fake transaction table is proposed inline for sign-off at implementation
  (standing procedure). The **existing** single-section `activobank` fixture is left
  untouched, so conftest-based tests (AC1/3/11/13, smoke) stay stable.
- AC8 test asserts: both sections' transactions captured with continuous seq; **both**
  vencimento credits present and signed `+`; period spans section 1 start … section 2 end;
  `closing_balance` = section 2's `SALDO FINAL`; a transaction's year comes from its own
  section (covered by giving the two sections different months/years).
- **Real-run gate (user):** `cruzar process` on the real 15-page export → all **5
  vencimentos** and every month's transactions, period 2026-01-02…2026-05-29, closing =
  May's `SALDO FINAL`. Green offline tests prove the logic; the real multi-section PDF is
  the gate.

## Out of scope

- Multi-section handling for other parsers — only ActivoBank exhibits this; the others are
  single-statement or already section-aware (Revolut).

## Definition of done

- New AC8 multi-section test green; existing ActivoBank fixture + full suite green.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- Real-run gate: the real statement yields all 5 months (≈5× the transactions, all 5
  vencimentos), correct period and closing balance.
- README: a line noting ActivoBank multi-month combined exports are supported.
