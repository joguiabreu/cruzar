# Cruzar — Slice 6 Plan (Degiro parser — holdings + cash; ADR-6/11/16, AC8)

Plan of record (decisions settled — all agreed). Un-parked & rescoped: the earlier
cash-only version is superseded because `Portfolio.pdf` carries positions **with
market value**, so Degiro is now a real holdings broker. Reuses the slice-7
machinery (`ParsedHolding`, `holdings_snapshot`, `currency` column, immutable
persistence). **Not implemented until this is built per "implement".**

## Two documents in the folder (parser dispatches on type)

- `Portfolio.pdf` — `Portfolio Overview per DD-MM-YYYY` → **holdings + cash**.
- `Account.pdf` — `Account statement` cash ledger → **transactions + cash balance**.

## Findings — Portfolio.pdf (masked)

```
Portfolio Overview per DD-MM-YYYY
Product | Symbol/ISIN | Amount | Closing | Local value | Value in EUR
CASH & CASH FUND & FTX CASH (EUR) | EUR | … | …            (cash line)
VANGUARD FTSE ALL-WORLD UCITS … | IE00BK… | <qty> | <price> EUR | <local> | <eur>
Total portfolio value EUR …
```
- Per position: quantity (Amount), native market value (Local value) + a pre-converted
  Value in EUR, closing price + currency, ISIN. **No cost-basis column** (D1).
- `CASH` line = uninvested cash → `closing_balance`.
- PT numbers (space thousands, comma decimal, e.g. `20 123,45`).
- `snapshot_date` from `per DD-MM-YYYY`.

`Account.pdf` is the wide cash ledger from the original plan (Date · Value date ·
Product · ISIN · Description · FX · Change · Balance; PT numbers w/ currency-code
prefix; the `Levantamentos/Depósitos da Conta Caixa` rows carry no Change value —
flatex-side mirror of a Cash Sweep — and are skipped).

## Decisions (settled)

- **D1 — `cost_basis` nullable. AGREED.** Degiro's Portfolio doesn't report cost
  basis (IBKR did); SPEC says it's broker-reported, never computed by us, so we
  store NULL for Degiro. Make `holdings_snapshot.cost_basis` nullable +
  `ParsedHolding.cost_basis: Decimal | None`; cost_basis is auxiliary (Net Worth /
  Portfolio Δ use `value`). SQLite can't drop NOT NULL in place → a **table-rebuild
  migration** (create-nullable → copy → drop → rename); the table is empty so it's
  cheap, and the schema-parity test (extended to compare NOT NULL flags) covers it.
- **D2 — One `degiro.parse()` dispatching on document type. AGREED.** Sniff the
  text: `Portfolio Overview` → positions parser; `Account statement` → ledger parser.
- **D3 — Holdings mapping (Portfolio). AGREED.** `symbol` = ISIN; `quantity` =
  Amount; `value` = native **Local value** with `currency` = the closing-price
  currency (not Degiro's pre-converted "Value in EUR"); `cost_basis` = NULL. `CASH`
  line → `closing_balance`; `period_start = period_end =` snapshot date.
- **D4 — Include the Account ledger now. AGREED.** transactions + closing_balance
  (last Balance); PT-number helper (space thousands, comma decimal, currency
  prefix); skip the no-Change `Conta Caixa` mirror rows (verified vs the balance
  column at build).

## Reused from slice 7 (no rework)

- `ParsedHolding` + `holdings` on `ParsedStatement` (cost_basis → `Decimal | None`).
- `holdings_snapshot` INSERT-only/immutable persistence (ADR-6) with `currency`.
- Brokerage transactions excluded from Spending Detail by the `_CASH_TYPES` filter (AC19).

## Flags — deferred

- Net Worth / Portfolio Δ **report** (Summary slice; AC22/AC20).
- Contribution/transfer detection for Degiro deposits & sweeps (flows.yaml / Δ slice).
- FX: Degiro holdings here are EUR; a non-EUR holding stores its native currency
  and relies on report-time conversion later.

## Files touched

```text
src/cruzar/models.py                         # cost_basis -> Decimal | None (D1)
src/cruzar/schema.sql                         # holdings_snapshot.cost_basis nullable (D1)
src/cruzar/db.py                              # rebuild migration for nullable cost_basis
src/cruzar/persist.py                         # cost_basis NULL-safe canonicalization
src/cruzar/parsers/degiro.py                  # NEW — parse() dispatch: Portfolio + Account
src/cruzar/parsers/__init__.py                # register "degiro"
config/sources.yaml + .example                # add degiro account (brokerage, EUR)
tests/fixtures/degiro/generate_fixture.py     # NEW — synthetic Portfolio + Account PDFs + expected.json
tests/fixtures/degiro/*.pdf, expected.json    # NEW
tests/acceptance/test_ac08_degiro_parser.py   # NEW — AC8 (holdings + cash; + ledger)
tests/schema_baseline.sql / test_schema_parity.py  # parity guard extended (NOT NULL) covers the migration
docs/SPEC.md                                  # cost_basis nullable (D1)
README.md                                     # five parsers
```

### sources.yaml entry

```yaml
  - institution: degiro
    name: Conta Degiro
    account_match: degiro            # files in data/inbox/degiro/
    source_type: manual
    account_type: brokerage
    currency: EUR
```

## Test plan (slice gate)

- `test_ac08_degiro_parser.py` — parse(Portfolio fixture) == expected.json (holdings:
  ISIN symbol, qty, native value, currency, cost_basis null; cash closing_balance);
  parse(Account fixture) == its oracle (transactions + closing_balance; Conta Caixa
  mirror skipped).
- Schema-parity test (now comparing NOT NULL flags) proves the nullable-cost_basis
  migration upgrades an old DB; holdings immutability already covered.
- `ruff` / `pyright` / `pytest` clean; pipeline smoke still runs.
- **Manual smoke:** `uv run cruzar process` ingests both Degiro PDFs; holdings_snapshot
  gets the Degiro position; no brokerage rows in Spending Detail.

## Verification / done

- Degiro AC8 green; nullable-cost_basis migration green via schema-parity; full gate
  clean; SPEC + README updated.
- **"Done"** = Degiro holdings (+ cash + ledger) parsed and persisted immutably;
  gate clean; docs updated. Net Worth reporting deferred.
```
