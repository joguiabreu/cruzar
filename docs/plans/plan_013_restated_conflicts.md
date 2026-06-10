# Plan 013 — Restated-transaction conflicts (AC14, ADR-8)

## Goal

A corrected/restated transaction that reappears on a later statement hashes
differently (its amount changed), so it survives dedup (ADR-7) as a **second**
row. ADR-8: *first write wins; the restatement is flagged, never merged, never
double-counted.* Nothing detects this today. This slice adds the detection,
excludes the superseded leg from flow aggregates so it isn't double-counted, and
surfaces both legs in a new Conflicts report section. Ends with **AC14** green.

Offline + synthetic throughout — no network, no Ollama, no real-run gate.

## Acceptance — AC14

> A restated transaction (same identity inputs, differing amount) on a later
> statement is surfaced in a conflicts section, never merged or double-counted.
> Verified by a fixture pair.

## Decisions (settled)

1. **Conflict key = `(account_id, date, description_raw)`** — `intra_statement_seq`
   is intentionally dropped. Seq is a per-statement ordinal and is exactly what
   drifts when a line reappears amid different neighbours on a later statement;
   keying on it would miss real restatements and let the double-count through.
   This is a deliberately narrow reading of AC14's "identity inputs."
   - **Cross-statement requirement:** a group is a conflict only when it spans
     **≥2 statements**. Two same-`(account,date,description)` lines on the *same*
     statement are independent transactions (invariant: never coalesce), so
     they are never flagged. The original is the row on the earliest statement
     (lowest id); every row on a *later* statement in the group is superseded.

2. **New conditional Conflicts section (Section 6)** — rendered iff the month has
   a conflict. Placed last, after Needs-Categorization. Columns:
   `Date | Account | Description | Amount (kept) | Amount (restated)` (native).
   SPEC Outputs gains Section 6 and **AC9** is amended to name the optional
   Conflicts section (no ADR change — ADR-8 already mandates the behavior).

## Design

- **Persisted flag, recomputed each run** (mirrors `is_transfer`). Add
  `transactions.superseded INTEGER NOT NULL DEFAULT 0`, set by a normalize-stage
  pass: reset all to 0, then mark the later legs 1. Idempotent (AC1); no
  hand-edited derived state. The report stays read-only (AC13) — it only reads
  the flag. Aggregate queries gain one predicate `AND t.superseded = 0`.

- **Schema + migration.** `schema.sql` declares the column;
  `db._migrate` adds a guarded `_add_column_if_missing(... "superseded",
  "INTEGER NOT NULL DEFAULT 0")` step. `tests/schema_baseline.sql` stays frozen;
  the parity test proves fresh == upgraded.

- **New module `conflicts.py`** (normalize stage). Justified as the ADR-8
  counterpart to `transfers.py` (ADR-15): distinct concern, same shape (whole-DB
  pass writing a flag). `conflicts.detect(conn)` groups by the key, and for any
  group spanning ≥2 statements marks every row not on the earliest statement
  (by lowest id) `superseded = 1`.

- **Pipeline wiring.** `pipeline.process` calls `conflicts.detect(conn)` right
  after `transfers.detect(...)`.

- **Exclude the superseded leg from flows** (the "never double-counted" half):
  add `AND t.superseded = 0` to `metrics._flow` (Earned/Spent),
  `metrics.net_contrib`, and the report's `_spending_section`, `_earning_section`,
  `_needs_categorization_section`. `net_worth`/`iv` read `closing_balance` +
  snapshots, not transaction sums — unaffected.

- **Report.** New `_conflicts_section(conn, year_month)`; appended after
  Needs-Categorization. One row per superseded leg whose date is in the month,
  paired with its group's kept (earliest, lowest-id non-superseded) amount.
  Returns `[]` → section omitted.

## Fixture (obviously-fake) — for the AC14 test

Two statements on one cash account, persisted via `persist_statement` (so the
real dedup/hash path runs); statement B re-lists a corrected Jan charge.

| Statement | Period | Lines: date / description / amount |
|---|---|---|
| A | 2025-01-01…2025-01-31 | 2025-01-15 / `ACME SUBSCRIPTION` / `-10.00`; 2025-01-20 / `WIDGET STORE` / `-25.00` |
| B | 2025-02-01…2025-02-28 | 2025-01-15 / `ACME SUBSCRIPTION` / `-12.00` (restatement); 2025-02-03 / `COFFEE BAR` / `-4.00` |

Oracle:
- Both `ACME SUBSCRIPTION` rows persist (not merged) → 4 transaction rows.
- Exactly one `superseded = 1` — statement B's `ACME` leg (later, higher id).
- 2025-01 **Spent** = `-35.00` (kept `-10.00` + `-25.00`), not `-47.00`.
- 2025-01 report Conflicts section row:
  `2025-01-15 | <account> | ACME SUBSCRIPTION | -10.00 | -12.00`.
- Re-running `conflicts.detect` leaves the flags unchanged (idempotent, AC1).

## Tests

- `tests/acceptance/test_ac14_restated_conflict.py` — the fixture pair asserting
  the oracle (persist-not-merge, one superseded leg, no double-count in Spent,
  Conflicts section present).
- A `conflicts.detect` unit case: two independent same-key lines on one statement
  flag nothing; a re-run is idempotent.
- Existing suite stays green. AC9's fixture has no conflict, so its asserted
  section order is unchanged.

## SPEC + README

- SPEC: add *Section 6 (conditional): Conflicts* to Outputs; amend AC9 to mention
  the optional Conflicts section.
- README: document the Conflicts section and first-write-wins (a corrected charge
  on a later statement is flagged, not merged; the original stays in totals).

## Out of scope

- Tooling to *resolve* a conflict (accept restatement / supersede original) —
  a manual-tier CLI follow-up.
- AC4(a) LLM extraction fallback — its own slice.
- Fuzzy/normalized description matching — restatements match on exact raw text.

## Definition of done

- AC14 green; AC9/AC1/schema-parity/smoke green.
- `superseded` column appears on upgraded DBs (parity test).
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- README + SPEC updated.
