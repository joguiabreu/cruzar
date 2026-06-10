# Plan 015 — `cruzar report` + complete the acceptance harness

## Goal

Two things, one coherent slice: (1) add the one missing read-only command,
`cruzar report` (standalone report regeneration — AC13 needs it and the CLI doesn't
have it yet), and (2) add the six remaining acceptance tests so the harness covers
**every AC, AC1–AC22**. After this, "the gate" in CLAUDE.md is genuinely complete.

The gap — ACs with no dedicated test today: **AC2** (reconciliation), **AC5** (no
secrets on disk), **AC6** (one snapshot row per holding), **AC7** (config-only
account add), **AC11** (debit/credit signs), **AC13** (`cruzar report` read-only).
AC5/6/7/11 assert behavior that already exists; AC13 needs the new command; AC2
needs a reconciliation check.

## Decisions (settled)

1. **D1 — `cruzar report` fetch policy.** Runs with **no fetcher** (`fetch=None`):
   converts using cached/manual `fx_rates` and renders `n/a` for any month-end rate
   not already present. A live FX fetch *persists* rates (a DB write) and would break
   "read-only"; fetching stays `cruzar process`'s job. Guarantees AC13
   unconditionally and matches "regeneration."
2. **D2 — AC7 test.** Seed a config with a **second** account reusing an existing
   parser (a second ActivoBank account under a different inbox folder), drop a
   statement in each folder, run, assert both resolve and persist — an account added
   by config alone, zero pipeline code touched. (The "new format ⇒ one parser + one
   fixture" half is already evidenced by the five AC8 parser fixtures.)
3. **D3 — AC2 reconciliation lives in the test, not production.** The test recomputes
   the oracle: *native (exact)* — `metrics.earned/spent` equals a raw
   `SUM(transactions.amount)` over the same account-class / period / `is_transfer` /
   `superseded` filter; *base (method-consistent)* — the converted Summary figure
   equals the test converting the same native sum via `fx.convert` at the period-end
   rate (same method, per the AC, not converted==native).
4. **D4 — scope.** `cruzar fetch` (Gmail) is **deferred** — a large separate slice
   (OAuth, keyring, network, allowlist); no remaining AC test depends on it (AC5 is a
   secrets smoke test that passes without it).

## Design — `cruzar report`

- New `pipeline.report_only(db_path, config_dir, reports_dir)`: `load_config` →
  `connect` → `init_schema` (idempotent; a no-op on an up-to-date DB) →
  `report.write_reports(..., investment_flow_patterns=…, fetch=None)`. No ingest,
  normalize, categorize, or LLM. Mirrors `process` minus the writes.
- `cli.py`: register the `report` subparser → call `report_only`; update the module
  docstring (drop "later slices" for report).
- `report.write_reports` is already SELECT-only over the DB; with `fetch=None` it
  cannot persist FX either, so the command writes only to `reports/`, never the DB.

## Tests — one per remaining AC (offline; reuse existing fixtures)

| AC | Asserts | How |
|----|---------|-----|
| AC2 | aggregates reconcile in storage currency (native exact; base method-consistent) | seed cash txns (+ a foreign-currency account & a cached rate); compare `metrics` vs raw `SUM` and vs a test-side `fx.convert` (D3) |
| AC5 | no secret material on disk outside the Keychain | run the SPEC's `grep` for `ya29`/`refresh_token` over tracked files + scan the DB file for token-shaped values; assert none (necessary-not-sufficient) |
| AC6 | one `holdings_snapshot` row per holding, dated `period_end`, linked via `statement_id` | persist an investment statement; group snapshots by `statement_id`, assert one per symbol, `snapshot_date == period_end`, FK resolves |
| AC7 | adding an account = one sources.yaml entry, no core changes | two accounts from config sharing one parser; both ingest & resolve (D2) |
| AC11 | debits negative, credits positive; no amount+type split | after ingest, `MIN(amount) < 0` and `MAX(amount) > 0`; schema has a single signed `amount` column (no type column) |
| AC13 | `cruzar report` is read-only w.r.t. the DB | run `process`, hash a sorted `iterdump` (per AC1); run `report_only`; hash again; assert equal and that a report file was (re)written |

## SPEC + README

- SPEC: no wording change needed (AC2/5/6/7/11/13 already specified). Optionally note
  `cruzar report` uses cached rates only (D1) where FX/Outputs are described.
- README: document the `cruzar report` command (read-only regeneration from the
  existing DB; cached FX, no network) under Commands.

## Out of scope

- `cruzar fetch` (Gmail) — its own slice (D4).
- `--reextract` — still a follow-up (plan 014).
- Any change to report content — this slice only adds the command + tests.

## Definition of done

- AC2/5/6/7/11/13 tests green → every AC AC1–AC22 has a passing acceptance test.
- `cruzar report` works and is read-only (AC13).
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- README updated. No real-run gate — fully offline (no network, no Ollama; the new
  command is read-only).
