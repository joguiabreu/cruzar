# Plan 024 — AforroNet (Certificados de Aforro) parser

**Goal.** Add a parser for the **Certificado de Aforro** statement exported from **AforroNet**
(IGCP), so the value of your Portuguese savings certificates shows up in Net Worth and its growth
in the investment view. New module `src/cruzar/parsers/aforronet.py` + a new account in
`config/sources.yaml`, following the one-parser-per-institution pattern (ADR-11).

## What it is

A Certificado de Aforro is a state savings instrument: you subscribe cash and hold **units**
(*unidades*) whose value accrues interest (capitalising), redeemable later. Economically a
fixed-income/bond-like holding — a *current value* that grows, not a spending ledger. It maps onto
our **holdings snapshot** model (ADR-6), like the Degiro Portfolio Overview.

## Confirmed against the real samples (gitignored `data/inbox/aforronet/`)

Six monthly exports inspected (no values reproduced here — real PII stays out of git):

- **Single page, text-extractable** (no OCR needed).
- **PT number format**: dot thousands, comma decimal (e.g. `1.234,56` → `1234.56`; `1,00000` → `1.00000`).
  A tiny boundary helper normalises to plain `Decimal` (never emit comma-decimals — money invariant).
- **Snapshot date**: `Data do Extrato: DD-MM-YYYY` → `period_start = period_end`.
- **`RESUMO DE SALDOS POR PRODUTOS`** table — one row per série: `Produto/Série | Unidades | Valor`
  (the holding's symbol, units, and current EUR value).
- **`DETALHE DE SALDOS POR PRODUTOS`** table — per série: a `Valor Unitário Aquisição: <u> EUR`
  line (the subscription unit value) plus subscription rows and a `TOTAL CAF / Série X` line.
- **No movements ledger** (subscriptions/redemptions/interest are not itemised as transactions).

## Decisions (signed off)

- **D1 — investment account, `account_type: brokerage`.** Confirmed: track it as an investment, so
  its current value lands in Net Worth and its growth in the investment view. (Not `savings`, not
  `retirement`.)
- **D2 — one `ParsedHolding` per série.** `symbol` = the RESUMO product label (e.g.
  `"Certificados de Aforro Série E"`); `quantity` = units; `value` = current EUR value;
  `currency = EUR`; `cost_basis` = `units × acquisition-unit-value` (derived in Python from the two
  printed values — the statement reports the acquisition unit value, so the subscribed amount is
  exact; gives a meaningful Δ-vs-cost = accrued interest). `closing_balance = 0.00` (no uninvested
  cash; the whole value is in the holding → Net Worth = 0 + holdings value).
- **D3 — `emits_cash_flows: false`.** Confirmed: a pure position snapshot with no ledger, so external
  contributions are undetectable → Portfolio Δ renders **gross + flagged** "(contributions
  undetected)", honest, same as the IBKR monthly summary (ADR-14).

## Design (mirrors the Degiro Portfolio path)

- `parse(pdf_path) → ParsedStatement` (single snapshot statement). `pdfplumber` words →
  `cluster_rows`; `row_text` for line matching.
- Snapshot date from the `Data do Extrato:` line.
- Holdings from the RESUMO table (rows between the `Produto/Série … Unidades Valor` header and the
  `TOTAL` line): the trailing two PT-number tokens are units and value; the leading tokens are the
  symbol.
- `cost_basis`: collect each série's `Valor Unitário Aquisição` from the DETALHE table, keyed by the
  `Série X` token, and multiply by that série's units.
- PT-decimal helper at the boundary; fail loud on an unrecognised layout
  (`AforroNetParseError(ParserError)`); nothing partial (SPEC §Edge cases). Registered in
  `parsers/__init__.py`; returns a single `ParsedStatement` (no change to the plan-023
  list/normalize path).

## SPEC / AC / config / tests

- **No ADR change** — adding an institution parser is what ADR-11 anticipates; investment treatment
  is ADR-6/14/16.
- **AC8 fixture**: a committed generator builds a synthetic AforroNet-style PDF (from a hand-authored,
  obviously-fake certificate table) + `expected.json`; `test_ac08_aforronet_parser.py` asserts the
  parse reproduces the oracle and the holding fields (incl. derived `cost_basis`). The synthetic
  values are proposed inline for sign-off before becoming the oracle (standing procedure).
- `config/sources.yaml` (+ `.example`): add the `aforronet` account (`account_type: brokerage`,
  `emits_cash_flows: false`).
- **README**: bump the parser list (five → six) and note Certificados de Aforro support.
- Gate: `uv run ruff check . && uv run pyright && uv run pytest` clean.

## Real-run gate (mine, before "done")

`uv run cruzar process` against the real `data/inbox/aforronet/` export must parse, land the
holding(s), and show the certificate's value in Net Worth and the Investment Detail section without
error. (The parser is also validated directly against the real samples during implementation.)

## Out of scope

- Projecting or computing future interest, redemption value, or tax — record the statement's reported
  value only (no LLM math, ADR-1).
- OCR of an image-only PDF (SPEC non-goal); realized-gain / tax-lot accounting.
- Any change to other parsers or the ingest contract.

## Definition of done

- `aforronet.py` parses the real export; synthetic fixture + AC8 test green; full suite green.
- `sources.yaml` + README updated; real-run gate met (value in Net Worth / Investment Detail).
