# Cruzar — Slice 3 Plan (Moey parser — ADR-11, AC7/AC8)

## Context

Slices 1 (manual ingest → Spending Detail) and 2 (transfer detection, ADR-15)
are green. A real Moey statement is waiting at `data/inbox/moey/May.pdf` but
can't ingest: there is no `moey` account in `sources.yaml` and no
`parsers/moey.py`. This slice adds the Moey parser + a synthetic fixture + the
`sources.yaml` entry so `May.pdf` ingests. It also **activates ADR-15 step-2
pairing on real data** — ActivoBank `TRF P/ Moey` (outbound) and Moey
`TRANSF SEPA -<self>` (inbound) are the two legs of genuine own-account
transfers, which today report `0 by pairing` because only one account exists.

This slice is the canonical demonstration of **AC7** (adding an institution =
one `sources.yaml` entry + one parser + one fixture + one registry line; no core
pipeline change).

## Findings from the real statement (data/inbox/moey/May.pdf)

Studied with digit/PII masking; no real values recorded here.

- **Single transactional account.** `CONTA MOEY` spans pages 0–2 (≈94
  date-prefixed rows). Page 2 also carries an `APLICAÇÕES / RESPONSABILIDADES /
  RESUMO` **summary** block (savings applications + liabilities — not transaction
  tables); page 3 is footer. **No multi-account split problem** (the v1 known
  break does not apply).
- **Column layout** (token x-positions, left→right):
  - `DATA LANÇAMENTO` date `~x54`, a `/` separator `~x105`, `DATA VALOR` date
    `~x114` (both `DD-MM-YYYY`).
  - `DESCRIÇÃO` from `~x189`, including a trailing reference/auth number
    (e.g. `1234567`) before the amount.
  - `MOVIMENTOS` amount `~x435` (comma-decimal), then a **separate sign token**
    `+`/`-` `~x463`, then `SALDO CONTABILÍSTICO` balance `~x537` (comma-decimal).
- **Sign is its own token:** `-` = debit (outflow), `+` = credit (inflow).
  Example credit: `TRANSF SEPA -<self> … + …`.
- **PT number format:** dot thousands, comma decimal (`1.234,56`). Distinct from
  reference numbers, which carry no comma — the comma is how we tell the amount
  from the reference.
- **Multi-line wrapped descriptions:** a long payee wraps onto the next line
  with no date/amount (e.g. `Trf imediata <NAME…> E` then `SOUSA`). These
  continuation lines must be merged into the preceding transaction.
- **Period line** is Portuguese month names: `… <d> de Maio de YYYY … <d> de
  Maio de YYYY`. Needs a PT-month map (no numeric form present).
- **Currency:** `Extracto em EUR`.
- **Closing balance:** `SALDO FINAL` appears in the CONTA MOEY section *before*
  `APLICAÇÕES`; a second `SALDO FINAL` belongs to the summary and must be
  ignored.

## Scope (in) vs deferred (out)

### In slice 3

- `src/cruzar/parsers/moey.py` — `parse(pdf_path) -> ParsedStatement` (ADR-11).
- Register `"moey"` in `parsers/__init__.py` `PARSERS` (one line).
- `config/sources.yaml` + `sources.yaml.example` — a `moey` account entry.
- `tests/fixtures/moey/` — `generate_fixture.py` (committed generator) →
  synthetic `statement.pdf` + `expected.json` (oracle authored/verified by you).
- AC8 coverage for Moey (parse fixture == expected.json + balance identity).

### Deferred (out)

- Moey **APLICAÇÕES / investment** section as its own account (it's a summary
  here, no transactions) — future, when/if a holdings statement appears.
- Any change to Summary/Net Worth/FX/LLM — unrelated slices.
- Refactoring the ActivoBank parser beyond the optional shared-helper extraction
  in D1.

## Parsing strategy (right-anchored, geometry-tolerant)

Rather than hard-coded x-band buckets (as in `activobank.py`), Moey rows are
parsed by **anchoring from the right**, which is robust to small x drift and
makes the synthetic fixture less brittle:

1. Cluster words into rows by `top` (reuse the row-clustering helper — see D1).
2. Locate the CONTA MOEY transaction region: from the first row beginning with a
   `DD-MM-YYYY` date after the column header, up to the first `SALDO FINAL`
   (which precedes `APLICAÇÕES`). Repeated per-page headers inside the region are
   skipped (they begin with `DATA`, not a date).
3. **Transaction row** (starts with a date): tokens sorted by `x0`.
   - posting date = first `DD-MM-YYYY` token (DATA LANÇAMENTO).
   - From the right: `balance` = last comma-decimal token; `sign` = the `+`/`-`
     token left of it; `amount` = the comma-decimal token left of the sign.
   - `amount` signed: `+` → positive, `-` → negative.
   - `description_raw` = tokens between the second date and the amount token,
     joined by a single space (includes the reference number, kept verbatim — D2).
4. **Continuation row** (no leading date, within the region): append its joined
   tokens to the previous transaction's `description_raw` with a single space.
   `intra_statement_seq` does **not** advance (ADR-11 — one logical line).
5. `closing_balance` = the first `SALDO FINAL` value (comma-decimal → Decimal).
6. `period_start`/`period_end` via PT-month regex; `currency = "EUR"` from
   `Extracto em EUR`.
7. Any failure raises `MoeyParseError` — nothing partial (SPEC §Edge cases).

A `_pt_decimal(str) -> Decimal` helper converts `1.234,56` → `Decimal("1234.56")`
(strip dots, comma→dot). This comma-decimal form is **input only** — the Moey PDF
prints it, so the parser normalizes at the boundary; everything we emit
(`expected.json`, reports, logs, Decimal values) is international notation,
decimal point (CLAUDE invariant). Amounts stay native, signed (AC11). The
synthetic fixture PDF still renders comma-decimal (it must exercise `_pt_decimal`),
but its `expected.json` oracle uses the decimal point.

## DECISIONS

- **D1 — Shared row helpers. RESOLVED: extract to `parsers/_common.py` (option a).**
  Move `_cluster_rows` / `_row_text` into `parsers/_common.py`; both
  `activobank.py` and `moey.py` import them. The ActivoBank change is import-only
  (no logic edits).

- **D2 — Keep the reference number in `description_raw`. RESOLVED: keep verbatim.**
  `description_raw` is the raw line; deterministic → stable `content_hash`, and
  merchant rules match case-insensitively on substrings.

- **D3 — Synthetic fixture oracle sign-off. RESOLVED (now a repo standard).**
  Per CLAUDE testing conventions this is the standing flow for every new parser:
  I propose an obviously-fake Moey table (round amounts, placeholder payees, fake
  references, one wrapped-description line, one `+` credit, one `TRANSF SEPA`
  line, a thousands-separated amount), you sign off the values, *then* it's the
  oracle. Not re-raised as a per-plan decision.

- **D4 — Continuation-line join. RESOLVED: join with a single space; transactions
  stay independent.** This is NOT merging distinct transactions — it only
  reassembles a description the PDF *wrapped* onto a second physical line (no
  date/amount). `"… E" + "SOUSA"` → `"… E SOUSA"`, one logical transaction.
  Distinct transaction lines are never merged/coalesced (CLAUDE invariant).

## Files touched

```text
src/cruzar/parsers/moey.py                 # NEW — parse() (ADR-11)
src/cruzar/parsers/_common.py              # NEW (D1 option a) — row clustering helpers
src/cruzar/parsers/activobank.py           # import shared helpers (D1 option a; import-only)
src/cruzar/parsers/__init__.py             # register "moey": moey.parse
config/sources.yaml                        # add moey account (gitignored)
config/sources.yaml.example                # add moey account (committed template)
tests/fixtures/moey/generate_fixture.py    # NEW — synthetic PDF + expected.json
tests/fixtures/moey/statement.pdf          # NEW — generated synthetic fixture
tests/fixtures/moey/expected.json          # NEW — oracle (your sign-off, D3)
tests/acceptance/test_ac08_moey_parser.py  # NEW — AC8 for Moey
```

No change to `pipeline.py`, `persist.py`, `report.py`, `transfers.py`,
`schema.sql` — the pipeline is institution-agnostic (AC7).

### `sources.yaml` entry (proposed)

```yaml
  - institution: moey
    name: Conta Moey
    account_match: moey          # files in data/inbox/moey/
    source_type: manual
    account_type: checking       # CONTA MOEY = à ordem (demand deposit)
    currency: EUR
```

## Test plan (slice gate)

- **`tests/acceptance/test_ac08_moey_parser.py`** — `parse(fixture/statement.pdf)`
  serialized == `expected.json`; plus a balance identity
  (`saldo_inicial + Σ amounts == closing_balance`). Mirrors the ActivoBank AC8.
- Existing suite (AC1/3/8/12/21 + units) stays green.
- **Manual smoke (not a committed test):** after adding the `moey` account,
  `uv run cruzar process` ingests `May.pdf`; the run logs Moey transactions and
  transfer detection now reports **> 0 by pairing** where ActivoBank↔Moey legs
  match. Confirms the cross-slice payoff. (Pairing is already unit-tested
  synthetically in slice 2; no real-data assertion is committed.)

## Verification / done

- `uv run pytest tests/acceptance` → Moey AC8 green alongside the rest.
- `uv run ruff check . && uv run pyright && uv run pytest` all clean.
- `README.md` updated (per CLAUDE workflow): note Moey support + that adding an
  institution is a parser+fixture+yaml change (AC7).
- **"Done"** = Moey AC8 passes, gate clean, README updated. The investment
  (APLICAÇÕES) section remains explicitly deferred.
