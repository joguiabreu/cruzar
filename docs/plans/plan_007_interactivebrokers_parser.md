# Cruzar — Slice 7 Plan (Interactive Brokers — first holdings; ADR-6/11/16, AC8/AC1)

Plan of record (decisions settled). **Supersedes the parked Degiro cash-only plan
(006) as the first broker.** This is the first slice to write `holdings_snapshot`
— the data foundation for Net Worth (ADR-16) / Portfolio Δ (ADR-14). The Net
Worth/Portfolio *report* is a later Summary slice; this slice only ingests the data.

## What the statement contains (real files, digit/PII/ticker masked)

Two real IBKR Activity Statements, same format: `U…_20251203.pdf` (Dec 2025) and
`ActivityStatement.202605.pdf` (May 2026). Base currency **EUR**, positions in
**USD**. Which securities are held is private — redacted to `<SYM>`.

- **Open Positions** (the holdings oracle): `Symbol | Quantity | Mult | Cost Price
  | Cost Basis | Close Price | Value | Unrealized P/L | Code`, under a
  `Stocks → USD` sub-header, then `Total` (USD) and `Total in EUR`. → maps to
  `holdings_snapshot`: `symbol, quantity, cost_basis, value` (value = market value
  at the close date). Currency = USD (from the sub-header).
- **Net Asset Value** summary: Cash + Stock = Total, with the statement's prior &
  current dates.
- **Cash Report** — per currency (Base Summary, then EUR, then USD): Starting Cash,
  Commissions, Dividends, Trades (Sales/Purchase), Withholding, FX Translation,
  **Ending Cash**. → cash `closing_balance`.
- **Financial Instrument Information**: symbol → ISIN (Security ID) map.
- **Base Currency Exchange Rate** table: period-end FX (USD→EUR, …).
- **Numbers:** `1,234.56` (comma thousands, dot decimal); negatives `-1.23`.

**Both statements are summary-level** — the Cash Report carries category *totals*,
not per-trade/per-deposit lines, and there is no Trades/Deposits detail section.
So granular cash-flow transactions are not available from IBKR PDFs (see D3).

## Decisions (settled)

- **D1 — Add a `currency` column to `holdings_snapshot` (SPEC deviation). APPROVED.**
  Positions are USD in an EUR-base account; SPEC stores holdings value/cost_basis
  in *native* currency, converted to base at report time (ADR-5/16). The table has
  no per-holding currency, so a USD value can't be converted later. Add
  `currency TEXT NOT NULL`; store native value + currency. **Also update
  `docs/SPEC.md` §holdings_snapshot** to add the field (keep SPEC and schema in
  sync). Alternatives (pre-convert to EUR) need FX math in the parser (violates
  ADR-1/ADR-5) and lose native — rejected.
- **D2 — Parser contract + persistence (ADR-11, ADR-6). AGREED.** Add
  `ParsedHolding(symbol, quantity, cost_basis, value, currency)` and
  `holdings: list[ParsedHolding]` to `ParsedStatement` (default empty — cash
  parsers unaffected). `persist` writes `holdings_snapshot` INSERT-only/immutable
  (ADR-6), deduped on PK `(account_id, symbol, snapshot_date)` so reprocessing adds
  nothing (idempotent, AC1).
- **D3 — Granular transactions deferred. AGREED (option a).** Ingest holdings +
  cash closing balance now; granular trades/dividends/deposits need a detailed/Flex
  IBKR export (a future follow-up). Re-confirmed against the added monthly sample
  (also summary-level). `transactions` is empty for IBKR in this slice.
- **D4 — `closing_balance` = Cash Report "Base Currency Summary → Ending Cash"
  (EUR). AGREED.** Consolidates EUR+USD cash incl. FX translation.

## Flags — deferred (delegated to me; all kept out of scope)

- Net Worth / Portfolio Δ **report** (Summary slice; AC22/AC20) — not built here.
- **fx_rates** capture from the statement's exchange-rate table — Summary/FX slice.
- **ISIN** capture — `holdings_snapshot` keys on `symbol`; no schema field for ISIN.
- `account_type: brokerage` → any future transactions excluded from Spending Detail
  by the existing `_CASH_TYPES` filter (AC19).

## Parsing strategy

1. Cluster rows; locate the **Open Positions** table (header `Symbol Quantity …
   Value …`); read each `Stocks/<CCY>` row up to `Total`; emit a `ParsedHolding`
   per row (symbol, quantity, cost_basis = Cost Basis col, value = Value col,
   currency = the sub-header CCY). Skip `Total` / `Total in EUR`.
2. `closing_balance` = Cash Report Base Summary "Ending Cash" (D4).
3. `snapshot_date`/`period_end` = the statement's current date; `period_start` =
   the NAV "prior" date; statement `currency` = EUR.
4. `transactions` = empty (D3a).
5. Any failure raises `InteractiveBrokersParseError` — nothing partial.

## Files touched

```text
src/cruzar/models.py                                   # +ParsedHolding, +holdings field (D2)
src/cruzar/schema.sql                                  # +currency on holdings_snapshot (D1)
src/cruzar/persist.py                                  # persist holdings_snapshot (INSERT-only, ADR-6)
src/cruzar/parsers/interactivebrokers.py                # NEW — parse() (ADR-11): holdings + cash
src/cruzar/parsers/__init__.py                         # register "interactivebrokers"
config/sources.yaml + .example                         # add interactivebrokers account (brokerage, EUR)
tests/fixtures/interactivebrokers/generate_fixture.py   # NEW — synthetic IBKR-shaped PDF + expected.json
tests/fixtures/interactivebrokers/statement.pdf         # NEW
tests/fixtures/interactivebrokers/expected.json         # NEW (verified at impl time)
tests/acceptance/test_ac08_interactivebrokers_parser.py # NEW — AC8 parser fixture (incl. holdings)
tests/acceptance/test_ac01_holdings_idempotent.py      # NEW — reprocess adds no duplicate snapshot (AC1/ADR-6)
docs/SPEC.md                                           # add currency to holdings_snapshot model (D1)
README.md                                              # five parsers; first with holdings; Net Worth report pending
```

### sources.yaml entry

```yaml
  - institution: interactivebrokers
    name: Conta Interactive Brokers
    account_match: interactivebrokers   # files in data/inbox/interactivebrokers/
    source_type: manual
    account_type: brokerage
    currency: EUR                      # base currency; holdings carry their own currency (D1)
```

## Test plan (slice gate)

- `test_ac08_interactivebrokers_parser.py` — parse(synthetic fixture) ==
  `expected.json`, including the `holdings` list (obviously-fake tickers/values, a
  USD sub-header, a skipped Total line) and cash `closing_balance`.
- `test_ac01_holdings_idempotent.py` — persist twice; exactly one
  `holdings_snapshot` row per (symbol, snapshot_date), no mutation (ADR-6 / AC1).
- Existing suite green; `ruff` / `pyright` / `pytest` clean.
- **Manual smoke (not committed):** `uv run cruzar process` ingests the real
  statements; holdings_snapshot populated, no brokerage rows in Spending Detail.

## Verification / done

- AC8 (IBKR) + holdings-idempotency tests green; full gate clean.
- SPEC + README updated.
- **"Done"** = parser emits holdings + cash closing balance, persisted immutably;
  gate clean; docs updated. Net Worth/Portfolio reporting and granular transactions
  explicitly deferred.
```
