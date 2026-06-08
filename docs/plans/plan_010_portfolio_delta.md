# Plan 010 — Portfolio Δ (Summary column · AC20 / ADR-14)

Plan of record (decisions settled). Adds the Summary's 4th column —
`Month | Earned | Spent | Portfolio Δ | Net Worth` — computing **total return net
of external contributions** per ADR-14, in EUR, per month-end. Closes AC20.

This slice was lost: never committed, work never landed (confirmed — no
`portfolio_delta`/`iv`/`net_contrib` in `metrics.py`, no `test_ac20_*`). plan_009
deferred this column here (its decision D1).

## The contract (ADR-14)

For month M, over investment accounts (`brokerage`, `retirement`):

```
Portfolio Δ(M) = (IV_end − IV_prev) − NetContrib(M)
```

- **IV_t** = Σ over investment accounts of [ Σ `holdings_snapshot.value` @ t +
  `statements.closing_balance` @ t (uninvested cash) ], each → EUR at t's month-end
  rate. Securities + cash means internal buys/sells net to zero → don't pollute Δ.
- **NetContrib(M)** = signed sum of *external* cash flows into/out of investment
  accounts in M (inbound +, outbound −). A txn on an investment account is external
  iff `is_transfer = 1` (step-2 pairing, e.g. checking→brokerage) OR
  `description_raw` matches an `investment_flow_pattern`. Internal trades excluded.
- Dividends (reinvested or held as cash) raise IV and are not external → counted as return.
- No prior snapshot → render `—`.
- **Degradation:** if a parser cannot emit cash-flow txns, contributions are
  undetectable; that month's Δ is computed **gross** (`IV_end − IV_prev`) and flagged
  `"(gross — contributions undetected)"`. Documented, never silent.

## Decisions (settled)

- **D1 — Gross-degradation trigger = per-account capability flag. AGREED (option a).**
  Add `accounts.emits_cash_flows INTEGER NOT NULL DEFAULT 1` (migration). Declared per
  account in `sources.yaml` (optional field, default `true`); persisted by the account
  upsert in `persist.py`. **Interactive Brokers → `false`** — its parser emits
  `transactions=[]` (the monthly Activity Statement has no per-deposit lines; granular
  flows need a Flex export, per its docstring), so absence of contributions is *unknown*,
  not zero → flag gross. **Degiro → `true`** (default) — its Account statement is a real
  cash ledger. Capability is a parser fact documented in `sources.yaml`, not data-inferred
  (rejected option b: a real zero-contribution month is indistinguishable from a blind parser).

- **D2 — IV_prev = previous *calendar* month-end, always. AGREED (option a).** Month-to-month
  deltas every month. IV at end/prev is the latest snapshot+balance ≤ that month-end
  (consistent with `net_worth`'s "latest ≤ on"). No `holdings_snapshot` ≤ prev for any
  investment account → render `—`. (Rejected b: skipping gaps to the previous *snapshot*
  month would misattribute multi-month drift to one month.)

- **D3 — Aggregate, flag whole cell. AGREED.** The Summary Δ is one number over all
  investment accounts. A gross account (D1=false) contributes its IV delta but its
  NetContrib is omitted (undetectable); if any such account is present in the month, the
  whole row's Δ carries the `(gross — contributions undetected)` flag.

- **D4 — Seed `investment_flow_pattern` from the real Degiro string. AGREED.** Degiro
  external deposits read **`flatex Deposit`** (evidenced in `tests/fixtures/degiro/`).
  Seed `flows.yaml` → `investment_flow_patterns: ["flatex Deposit"]`. Kept deposit-specific
  (same specificity discipline as `transfer_patterns`): `Flatex Interest Income` is a
  *return* not a contribution, and `Compra` is an internal buy — both must stay out of
  NetContrib, and a `flatex Deposit` pattern excludes them cleanly. A `flatex Withdrawal`
  mirror is added only when a real withdrawal string is seen (we seed only what's evidenced).

## Design

### metrics.py (pure, read-only — `report.py` only renders)

```python
@dataclass(frozen=True)
class Delta:
    value: Decimal
    flagged: bool            # gross — contributions undetected (D3)

def iv(conn, on: date, *, fetch) -> Decimal:
    # Σ over investment accounts: latest closing_balance ≤ on (uninvested cash)
    # + Σ latest holdings_snapshot.value ≤ on, each → EUR at on's rate.
    # (net_worth narrowed to _INVESTMENT_TYPES.)

def net_contrib(conn, ym, patterns, *, fetch) -> Decimal:
    # Σ signed amount of txns on investment accounts WITH emits_cash_flows=1 in ym,
    # where is_transfer=1 OR description_raw matches a pattern (case-insensitive,
    # mirroring transfers.detect); → EUR at ym's month-end rate.

def portfolio_delta(conn, ym, *, patterns, fetch) -> Delta | None:
    # end = month_end(ym); prev = month_end(previous calendar month)  [D2]
    # if no holdings_snapshot ≤ prev for any investment account: return None  → "—"
    # gross = iv(end) − iv(prev)
    # flagged = any investment account with emits_cash_flows=0 active in the window  [D1/D3]
    # return Delta(gross − net_contrib(...), flagged)
```

- `is_transfer=1 OR pattern` is an OR over one transaction → a paired deposit matched by
  both is still counted once (no double-count).
- A gross (D1=false) account is excluded from `net_contrib`; its IV delta stays in `gross`.

### report.py

- `_summary_section`: header → `| Month | Earned | Spent | Portfolio Δ | Net Worth |`
  (Portfolio Δ before Net Worth, matching SPEC §Section 1 column order).
- New cell: `None → "—"`; `flagged → "€X.XX (gross — contributions undetected)"`; else
  `_eur(value)`. Reuse the `_cell` degrade-on-FX-failure wrapper so an FX `n/a` still renders.

### config.py + flows.yaml

- `Config`: add `investment_flow_patterns: list[str]`, loaded from
  `flows_doc.get("investment_flow_patterns", [])`.
- `flows.yaml`: replace the commented placeholder with
  `investment_flow_patterns: ["flatex Deposit"]`.
- `Config` account entries: add optional `emits_cash_flows: bool = True`; IB's entry sets
  `false`. Thread `investment_flow_patterns` from `pipeline.process` →
  `report.write_reports` → `portfolio_delta`, same as `transfer_patterns` → `transfers.detect`.

### db.py + persist.py

- `schema.sql`: add `emits_cash_flows INTEGER NOT NULL DEFAULT 1` to `accounts`.
- `db._migrate`: add `emits_cash_flows` via the existing guarded add-column helper
  (PRAGMA table_info check). Update `tests/schema_baseline.sql`; `test_schema_parity` stays green.
- `persist.py` account upsert: include `emits_cash_flows` from the account config.

## AC20 — the four fixtures (the gate)

One test `tests/acceptance/test_ac20_portfolio_delta_nets_contributions.py`, fresh temp DB,
`fetch=None` + seeded EUR rates (offline), built on the `test_ac22` helper shape.

| # | Scenario (SPEC AC20) | Asserts |
|---|----------------------|---------|
| i | Contribution checking→brokerage (a transfer pair) | Δ unaffected by the transfer itself — the inbound leg is subtracted out |
| ii | Internal buy funded by existing in-account cash | Δ unchanged (cash↓, securities↑ net zero; no external flow) |
| iii | Price-only rise, no flows | Δ rises by exactly the price increase |
| iv | No prior snapshot | Δ renders `—` (function returns `None`) |

### Proposed obviously-fake fixture values — for sign-off (oracle)

Per CLAUDE.md the oracle is authored/verified by the user, not generated by the code under
test. Round/sequential placeholders below — **confirm or correct before they become truth.**

| Fixture | Setup (EUR, month-end snapshots) | Expected Δ |
|---------|----------------------------------|------------|
| i | Brokerage IV_prev = 10000 (securities). M: checking→brokerage transfer +2000 (paired, `is_transfer`), buys securities. IV_end = 12000 securities + 0 cash. | (12000 − 10000) − 2000 = **0.00** |
| ii | IV_prev = securities 8000 + cash 1000 = 9000. M: internal buy 1000 (cash→securities, no transfer). IV_end = securities 9000 + cash 0 = 9000. | (9000 − 9000) − 0 = **0.00** |
| iii | IV_prev = 5000 (securities). M: no txns; securities revalue to 5300. IV_end = 5300. | (5300 − 5000) − 0 = **+300.00** |
| iv | Brokerage has a single snapshot in M, none earlier. | **—** |

A fifth assertion (not a SPEC fixture, but covers D1) is worth adding: an `emits_cash_flows=0`
account with IV movement renders the `(gross — contributions undetected)` flag.

## Out of scope

- LLM categorization (Section 4 Needs-Categorization, AC4/15/16/17/18) — separate slice.
- Standalone `cruzar report` command (optional later).
- Time-/money-weighted % return — Δ is absolute EUR (ADR-14, explicitly excluded).
- A `flatex Withdrawal` pattern — add when a real withdrawal string is observed.

## README

Update the Summary-section description: list the Portfolio Δ column, its `—` case, the
gross-degradation flag, the new `investment_flow_patterns` in `flows.yaml`, and the optional
per-account `emits_cash_flows` field in `sources.yaml`.

## Definition of done

- `test_ac20_portfolio_delta_nets_contributions.py` green (4 fixtures + gross-flag assertion).
- `test_schema_parity.py` + smoke green (baseline updated for the new column).
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- README reflects the new column.
- **Real-run gate:** `uv run cruzar process` against the real inbox completes (the live
  investment path is not exercised by the offline suite).
