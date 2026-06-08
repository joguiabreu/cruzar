# Cruzar — Slice 9 Plan (Summary report, Section 1 — ADR-16, AC19/AC22/AC10)

Plan of record (decisions settled). Adds the report's **Summary** section
(Net Worth + Earned + Spent), making the ingested holdings + FX visible. Portfolio Δ
is split to plan_010 (D1).

## Section 1 (per SPEC)

Each monthly report `cruzar-YYYY-MM.md` opens with a Summary table — one row per
month, **descending, up to last 12 months** of available data, all in **EUR**, each
row computed as of that month-end (reproducible; FX at the persisted period-end rate).

- **Earned(M):** Σ amount over cash-account txns in M, amount > 0, `is_transfer=0`.
- **Spent(M):** same, amount < 0 (negative).
- **Net Worth(M)** (ADR-16): over non-closed accounts, Σ [latest `closing_balance`
  ≤ M-end] + Σ [latest `holdings_snapshot.value` ≤ M-end], each converted to EUR at
  the M-end rate. Closed accounts drop from the latest row, remain in earlier rows.
- **Portfolio Δ:** column **deferred to plan_010** (D1) — omitted for now.

## Decisions (settled)

- **D1 — Split Portfolio Δ to plan_010. AGREED.** This slice ships
  `Month | Earned | Spent | Net Worth`. The Δ column is omitted (honest incremental
  build) rather than showing a misleading "—"; plan_010 adds it with the ADR-14
  machinery (IV over time, NetContrib, gross-degradation, AC20's 4 fixtures).
- **D2 — FX wiring + offline. AGREED.** `pipeline.process` builds the fetcher from
  `cruzar.yaml` fx settings (`offline:true` → `fetch=None`) and passes it to the
  report; Net Worth converts each account's cash/holdings at the row's month-end
  rate. Flows are EUR today but the code converts per-account currency for correctness.
- **D3 — New `metrics.py`. AGREED.** Pure computations (`earned`/`spent`/`net_worth`,
  later `iv`/`net_contrib`/`portfolio_delta`) live there; `report.py` renders only.
- **D4 — Months shown. AGREED.** Months with any activity (cash txn / statement
  period-end / holdings snapshot) up to M, newest first, capped at 12. No padding.
- **Cleanup — AGREED.** Rename the mis-mapped `test_ac19_income_not_flagged.py`
  (really transfer/income safety) → `test_transfer_income_safety.py`; add the real
  AC19 (Earned/Spent exclude investment accounts).

## Out of scope (separate slices)

- Portfolio Δ (AC20) → plan_010.
- Section 3 Investment Detail (per-position) → its own slice.
- Section 4 Needs Categorization (LLM) → with the LLM slice.
- Standalone `cruzar report` command → optional later.

## Design

```text
src/cruzar/metrics.py  (NEW)
  months_available(conn) -> list[str]            # YYYY-MM, newest first (all activity)
  earned(conn, ym, *, fetch) -> Decimal          # cash, +, is_transfer=0, EUR
  spent(conn, ym, *, fetch)  -> Decimal          # cash, −, is_transfer=0, EUR
  net_worth(conn, month_end, *, fetch) -> Decimal# ADR-16, FX at month_end
src/cruzar/report.py    # render Section 1 (≤12 rows desc) above Section 2; take fetch
src/cruzar/pipeline.py  # build fetch from Config.fx_* ; pass conn+fetch to write_reports
```

Report files are now written for **every activity month** (so an investment-only
month still surfaces), each with Section 1 + Section 2 (Section 2 may be empty).
Money stays `Decimal`, quantized to 2dp at render. Net Worth uses each account's
currency (EUR cash; USD IBKR holdings) via `fx.convert` at the row's month-end.

## Files touched

```text
src/cruzar/metrics.py                                 # NEW — earned/spent/net_worth + months
src/cruzar/report.py                                  # Section 1 renderer; per-activity-month; fetch
src/cruzar/pipeline.py                                # build fetch from config, pass through
README.md                                             # report now shows a Summary (Net Worth/Earned/Spent)
tests/acceptance/test_ac22_net_worth.py               # NEW — Net Worth incl. FX + closed-account fixture
tests/acceptance/test_ac19_earned_spent_cash_only.py  # NEW — real AC19 (investment excluded)
tests/acceptance/test_ac10_fx_reproducible.py         # NEW — regenerate twice → identical (cached rate)
tests/acceptance/test_transfer_income_safety.py       # RENAMED from test_ac19_income_not_flagged.py
```

## Test plan (slice gate)

- **AC22 — Net Worth:** fixture with a EUR cash account, a USD holding (+ seeded
  fx_rate), and brokerage cash; assert Net Worth = Σ cash + Σ holdings·rate at
  month-end; a closed account is excluded from the latest row but present earlier.
- **AC19 — Earned/Spent:** a brokerage buy + in-account dividend never appear in
  Earned/Spent; cash income/spend do.
- **AC10 — reproducibility:** one foreign-currency account; regenerate the same
  month twice → identical Summary, fetch called ≤ once (rate persisted; offline
  second run matches).
- Existing suite green; pipeline smoke exercises Section 1; `ruff`/`pyright`/`pytest` clean.

## Verification / done

- AC22 + AC19 + AC10 green; full gate clean; README shows the Summary.
- **Manual smoke:** `uv run cruzar process` → monthly report opens with a Net Worth /
  Earned / Spent table; IBKR USD holdings converted to EUR at the month-end rate.
- **"Done"** = Section 1 (Net Worth + Earned/Spent) renders and reconciles; gate
  clean; docs updated. Portfolio Δ + Investment Detail deferred.
```
