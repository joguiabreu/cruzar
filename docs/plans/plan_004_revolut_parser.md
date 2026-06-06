# Cruzar — Slice 4 Plan (Revolut parser — ADR-11, AC7/AC8)

Plan of record (decisions settled). **Not implemented yet — awaiting "address
notes, implement."**

## Context

Slices 1–3 (manual ingest, transfer detection, Moey parser) are green. A real
Revolut export sits at
`data/inbox/revolut/account-statement_…_pt-pt.pdf` (a multi-year combined export) but
can't ingest: there is no `revolut` account in `sources.yaml` and no
`parsers/revolut.py`. This slice adds the parser + a synthetic fixture + the
`sources.yaml` entry — the same AC7 shape as Moey — but the Revolut file is
materially harder than ActivoBank/Moey, which drives the decisions below.

## Findings from the real export

Studied with digit/PII masking; no real values recorded here. The file contains
real counterparty names — they live only in gitignored `data/` and never enter
the fixture or this plan.

- **It is a 7-year combined export, not a monthly statement.** 38 pages, **4
  stacked statement sections** each headed `Operações da conta de <d> de <mês> de
  <ano> a …`, each preceded by its own `Resumo do saldo` balance summary.
- **Two layouts in one file (format drift):**
  - **"old"** — issuer *Revolut Ltd*, product `Conta (E-Money)`. Columns:
    `Data | Descrição | Dinheiro retirado | Dinheiro recebido | Saldo`. Single
    date column. This is the **majority** of the file (≈pages 0–30).
  - **"new"** — issuer *Revolut Bank UAB Sucursal em Portugal*, product
    `Conta (Conta Corrente)`. Columns: `Data Lançamento | Data-Valor | Descrição |
    Dinheiro retirado | Dinheiro recebido | Saldo contabilístico`. **Two** date
    columns (≈pages 31–37).
- **No holdings / investment sections.** The `Stock` matches are "Stockholm" in
  card descriptions; `Investiment` is footer legalese. Pure EUR cash account —
  **no ADR-6 holdings concern.**
- **Column geometry** (token x0, masked):
  - old: `Data 42.7 | Descrição 124.8 | retirado ~335 | recebido ~417 | Saldo (x1≈555)`
  - new: `DataLanç 42.7 | Data-Valor 119.1 | Descrição 191.1 | retirado ~375 | recebido ~449 | Saldo (x1≈555)`
  - Bands differ between layouts → column detection must adapt per layout (D2).
- **Amounts:** `€1,234.56` — € prefix, comma thousands, **dot decimal**
  (international notation, unlike Moey's PT comma-decimal). Parse = strip `€` and
  `,` → `Decimal`. The retirado/recebido split gives the sign (retirado = debit,
  negative; recebido = credit, positive).
- **Multi-line detail rows** hang under a dated row with no date: `De: *1234`,
  `Para: …`, `Cartão: …`, `Referência: …`, wrapped payee names, and FX notes like
  `123.45 PLN` / `12,345.00 HUF` (the foreign-currency leg of a `Conversão
  cambial`).
- **Per-page noise to exclude:** a repeated column header on every page; a legal
  footer beginning `Comunicar perda ou roubo…` and ending `© <year> Revolut …
  Página n de m`; and the `Resumo do saldo` summary tables (amount-shaped tokens
  but no leading date).
- **Date format:** `DD/MM/YYYY` (pt-pt). Section period lines use PT month names
  (`de janeiro de …`) — same map Moey needs (D6).
- **Currency:** `Extrato de EUR`.

## Scope

### In slice 4

- `src/cruzar/parsers/revolut.py` — `parse(pdf_path) -> ParsedStatement` handling
  **both** layouts across all 4 sections (see Flag B).
- Register `"revolut"` in `parsers/__init__.py`.
- `config/sources.yaml` + `.example` — a `revolut` account entry.
- `tests/fixtures/revolut/` — committed generator → synthetic multi-section,
  two-layout PDF + `expected.json`.
- AC8 coverage for Revolut (parse == expected.json + balance identity +
  footer/summary-ignored assertions).
- Extract a shared PT-month date helper into `_common.py` (D6).

### Deferred (out)

- `flows.yaml` / transfer-pattern or categorization changes — ADR-15 /
  categorization slices, not this one (same boundary as plan_003; and per the
  LEVANT/IPS guidance, incoming-transfer patterns risk dropping income, so they're
  a deliberate separate decision).
- Any Summary/Net-Worth/FX/LLM change.
- Splitting the export into one `ParsedStatement` per section (D1 — treated as one).

## Parsing strategy

1. Concatenate `cluster_rows()` output across all pages in document order (reuse
   `parsers/_common.py`).
2. Walk pages; for each page locate the **table body**: from the column-header row
   (the row containing `Descrição`) down to the **footer** (first row matching a
   footer marker — `Comunicar perda` / `©` / `Página`). Rows outside that band
   (header, footer, `Resumo do saldo` summary) are skipped.
3. Determine the page's **layout** from its header row
   (`Data-Valor`/`contabilístico` present ⇒ "new", else "old") and **derive that
   page's column x-boundaries from the header label positions** (D2).
4. **Transaction row** = leading `DD/MM/YYYY` at `x0 < ~100`:
   - `date` = first date token (Data / Data Lançamento — posting).
   - amount = the single `€`-token in the retirado *or* recebido band; retirado ⇒
     negative, recebido ⇒ positive.
   - `description_raw` = tokens between the last date column and the amount bands,
     joined by single spaces (kept verbatim, incl. card masks / refs — Moey D2).
5. **Continuation row** (no leading date, inside the table body): append its joined
   tokens to the previous transaction's `description_raw` with one space;
   `intra_statement_seq` does **not** advance (one logical line, ADR-11). Covers
   `De:/Para:/Cartão:/Referência:`, wrapped names, and FX notes.
6. `closing_balance` = the Saldo of the last transaction row in document order (D3).
7. `period_start` = start of the first section; `period_end` = end of the last
   section (PT-month parse, D6). `currency = "EUR"` from `Extrato de EUR`.
8. Any failure raises `RevolutParseError` — nothing partial (SPEC §Edge cases).

A `_eur_decimal(str)` helper converts `€1,234.56` → `Decimal("1234.56")` (strip
`€` and commas). Amounts stay native EUR, signed (AC11). No FX math in the parser —
the foreign leg is kept only as description text (ADR-1, ADR-5).

## Decisions (settled)

- **D1 — One `ParsedStatement` for the whole export. RESOLVED.** Concatenate all
  sections' transactions in document order (global `intra_statement_seq`),
  `period_start` = first section start, `period_end` = last section end.
  Intermediate `Resumo do saldo` blocks skipped.
- **D2 — Detect layout per page from its header row; derive column x-boundaries
  from the header label positions. RESOLVED.** No hardcoded per-layout magic
  x-numbers; robust to drift and jitter.
- **D3 — `closing_balance` = the Saldo of the last transaction row. RESOLVED.**
- **D4 — Continuation/detail lines merged verbatim into `description_raw` with
  single spaces, one logical transaction. RESOLVED.** Distinct transactions are
  never merged (CLAUDE invariant) — this only reassembles wrapped detail under one
  dated row.
- **D6 — Extract `parse_pt_month_date()` (and the PT-month map) into
  `parsers/_common.py`. RESOLVED.** Both Moey and Revolut import it; Moey edit is
  import-only-ish (swap its local `_PT_MONTHS`/date parse for the shared one).

> Fixture-oracle sign-off is **not** a decision — it's mandated by CLAUDE.md
> testing conventions. During implementation I'll propose an obviously-fake table
> (covering both layouts, a debit fee, a credit top-up, a `Conversão cambial` with
> a foreign detail line, a wrapped payee + `Referência:`/`De:` continuation, a
> thousands-separated amount, and an intervening `Resumo do saldo` block to prove
> it's skipped) for verification before it becomes the AC8 oracle.

## Resolved flags

- **Flag A — Balance identity across the account-type switch. RESOLVED (your
  deferral accepted).** The running Saldo may not be continuous across the
  E-Money → Conta Corrente change in the *real* file. AC8 asserts the identity only
  on the *synthetic* fixture (built continuous), consistent with the Moey/ActivoBank
  AC8s. No whole-file identity assertion on real data.
- **Flag B — Keep BOTH layouts. RESOLVED.** With the header-driven dynamic
  detection (D2), supporting both is only modestly harder than one; and since the
  **majority of the real file is the "old" layout** (≈pages 0–30), parsing only the
  newest layout would silently drop years of transactions — incompatible with D1
  (whole file). So both layouts are supported. (If maintenance ever proves painful,
  revisit; today it does not.)

## Files touched

```text
src/cruzar/parsers/revolut.py                # NEW — parse() (ADR-11), both layouts
src/cruzar/parsers/_common.py                # ADD parse_pt_month_date() + PT-month map (D6)
src/cruzar/parsers/moey.py                   # use shared PT-month helper (D6; import-only-ish)
src/cruzar/parsers/__init__.py               # register "revolut": revolut.parse
config/sources.yaml                          # add revolut account (gitignored)
config/sources.yaml.example                  # add revolut account (committed template)
tests/fixtures/revolut/generate_fixture.py   # NEW — synthetic 2-layout, multi-section PDF + expected.json
tests/fixtures/revolut/statement.pdf         # NEW — generated synthetic fixture
tests/fixtures/revolut/expected.json         # NEW — oracle (verified at impl time)
tests/acceptance/test_ac08_revolut_parser.py # NEW — AC8 for Revolut
README.md                                    # three parsers; AC7 note
```

No change to `pipeline.py`, `persist.py`, `report.py`, `transfers.py`,
`schema.sql` — the pipeline stays institution-agnostic (AC7).

### `sources.yaml` entry (proposed)

```yaml
  - institution: revolut
    name: Conta Revolut
    account_match: revolut           # files in data/inbox/revolut/
    source_type: manual
    account_type: checking           # E-Money / Conta Corrente = demand deposit
    currency: EUR
```

## Test plan (slice gate)

- `tests/acceptance/test_ac08_revolut_parser.py` — `parse(fixture/statement.pdf)`
  serialized == `expected.json`; balance identity on the synthetic fixture; and
  assertions that the repeated header, the legal footer, and the intervening
  `Resumo do saldo` summary are all excluded.
- Existing suite stays green.
- **Manual smoke (not committed):** `uv run cruzar process` ingests the real
  export; run logs show transactions across both layouts. (Touches real data/DB —
  run on request.)

## Verification / done

- `uv run pytest tests/acceptance` → Revolut AC8 green alongside the rest.
- `uv run ruff check . && uv run pyright && uv run pytest` all clean.
- `README.md` updated (three parsers; AC7 still a parser+fixture+yaml change).
- **"Done"** = Revolut AC8 passes, gate clean, README updated.
```
