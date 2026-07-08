# Plan 023 — ActivoBank: one statement per monthly section

## The bug (regression from plan 019 D1)

Plan 019 combined a stacked 5-month ActivoBank export into **one** `ParsedStatement`
(D1) spanning month 1 → month 5, with `closing_balance` = the *last* month's. Transactions and
the flow metrics (Earned/Spent/Net) are correct — all salaries recovered. But Net Worth is a
*stock* that reads each account's latest `closing_balance ≤ month-end` (ADR-16), and a single
combined statement (period ending in month 5) exposes only the last month's balance. Illustrated
with placeholder figures:

| Month-end | combined-statement contributes | correct (that month's SALDO FINAL) |
| --- | --- | --- |
| month 1 | **nothing** (combined stmt ends in month 5) | A |
| month 2 | nothing | B |
| month 3 | nothing | C |
| month 4 | nothing | D |
| month 5 | E ✓ | E |

A stacked multi-month export is really *N monthly statements*; squashing them into one loses
every intermediate month's balance.

## Second bug it also fixes: overlapping-statement idempotency

If a combined statement spanning months 1–3 is ingested and the user later uploads a standalone
month-2 statement, today's combined behavior **double-counts month 2**: the combined statement's
period (`Jan–Mar`) ≠ the standalone's (`Feb`), so the statement-period dedup
(`pipeline.py:250`) misses; and the combined statement numbers `intra_statement_seq`
*continuously*, so month-2 rows there get a different `content_hash` than the standalone's
`seq 1..n` → rows inserted again. Per-section statements fix both dedup layers:

1. **Statement-period dedup** matches `(account, period_start, period_end)` — the per-section
   month-2 statement and the standalone share the same `EXTRATO` period → the file is skipped.
2. **`content_hash`** (`sha256(account, date, amount, description, intra_statement_seq)`,
   `ON CONFLICT DO NOTHING`) — per-section `seq` resets to `1..n`, matching the standalone, so
   any rows that reach insert are deduped. (Edge: a standalone export with *different* period
   bounds AND a different row order would weaken both layers — accepted.)

## Goal

ActivoBank emits **one `ParsedStatement` per monthly section** — each with its own
`period_start/period_end` (that section's `EXTRATO DE` line), its own `closing_balance` (that
section's `SALDO FINAL`), its own transactions, and `intra_statement_seq` running `1..n` WITHIN
the section. Per-month Net Worth and flows are both correct. Plan 019's section discovery,
per-section date resolution, and header/noise-row robustness all stay; only the final assembly
changes from "combine into one" to "yield one each".

## Decisions

- **D1 — parser contract (minimal blast radius):** a parser may return
  `ParsedStatement | list[ParsedStatement]`; `ingest_inbox` normalizes
  (`stmts = result if isinstance(result, list) else [result]`) and persists each. **Only
  ActivoBank returns a list**; the other four parsers and their tests are untouched. Chosen over
  a uniform `list[ParsedStatement]` for all five parsers (wider churn, no benefit — only
  ActivoBank stacks months).
- **D2 — per-section statement:** each section → `ParsedStatement(period = its EXTRATO period,
  closing_balance = its SALDO FINAL, transactions = its lines, seq 1..n reset per section)`. A
  single-month statement yields a one-element list (existing fixture values unchanged).
- **D3 — ingest idempotency:** the file is still processed once by `file_hash`; it now produces
  N statements, each deduped independently by `(account, period_start, period_end)`.
  `processed_files` is recorded once (status `ok`); its single `statement_id` points at the last
  section's statement. A file whose sections are all already present counts as skipped; any new
  section makes it ingested.
- **D4 — AC4a degradation stays whole-file:** the `<50%`-columns gate is computed over all
  sections combined (019 D3); on trip the whole file's raw text goes to the LLM extractor, which
  returns one statement. Accepted edge: a degraded multi-section file loses per-month granularity
  until it parses structurally.

## ADR / supersede (signed off)

- **ADR-11** gains a clause: a parser MAY yield multiple statements for a stacked multi-period
  export, in document order.
- **Supersedes plan 019 D1** (one combined statement). 019's discovery/date/robustness logic
  stays; only assembly changes. Noted in both plan files.

## Tests

- **AC8 multisection** now asserts the parser returns **two** statements (Dec 2025, Jan 2026),
  each with its own period, closing balance, and `seq 1..n`. The approved transaction values are
  unchanged — just grouped per section (no new oracle sign-off; same figures).
- **Single-section ActivoBank fixture**: parser returns a one-element list; its test unwraps
  `[0]`. Values unchanged.
- **Other parsers' AC8 tests**: untouched (ingest normalizes; their `parse` returns a single
  statement).
- **Pipeline**: a multi-statement ingest persists N statements and dedups each.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.

## Real-run / re-ingest gate

Re-ingest the real ActivoBank file (delete the combined statement + its transactions + its
`processed_files` row, then `cruzar process`). Expect **5 monthly statements**, each carrying its
own month-end SALDO FINAL, so per-month Net Worth reflects that month's actual balance (not the
last month's for all), with the flow metrics unchanged from the current (correct) values.

## Out of scope

- Multi-section handling for other parsers (only ActivoBank stacks months).
- A uniform `list[ParsedStatement]` contract for all parsers (rejected in D1).
- The held plan-020 prompt change.

## Definition of done

- ActivoBank yields one statement per section; per-month Net Worth correct on real data.
- AC8 multisection updated (two statements); single-section + other parsers green; full suite green.
- ADR-11 clause + 019-supersede note added; README updated (combined → per-section).
