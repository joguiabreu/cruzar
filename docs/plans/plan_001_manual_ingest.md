# Cruzar — Slice 1 Plan (Manual ingest → persist → Spending Detail)

## Context

Greenfield repo: only docs/SPEC.md (Rev 7), CLAUDE.md, .gitignore exist. This plan covers slice 1 only — the first vertical slice ends with its acceptance tests green. We stand up the project skeleton, the full SQLite schema, the manual ingestion path, one real parser (ActivoBank), persistence, and a Spending-Detail-only report. Gmail/OAuth, FX, LLM extraction/categorization, investments, transfer detection, Summary/Investment/Needs-Categorization report sections are all out of this slice (deferred to later slices, not cut from the product).

Per your answers: PDF-first (parser logic baked from the real fixture below), fixture account_type = checking, cruzar process writes per-month report files, and we seed merchants.yaml + simple regex rules now (no LLM).

The fixture is data/inbox/activobank/EXTRATO COMBINADO 2026005.pdf — ActivoBank, single account CONTA SIMPLES &lt;redacted&gt;, EUR, period 2026-05-04→2026-05-29, closing balance &lt;redacted&gt;, 11 transactions on page 2. "EXTRATO COMBINADO" is one account (RESUMO DAS CONTAS lists only one) → no multi-account split issue (ADR/known break).

## Slice scope (in) vs deferred (out)

### In slice 1

- uv project scaffold, src layout, cruzar CLI with process subcommand.
- Full SQLite DDL for all data-model tables (used or not this slice).
- Manual ingestion only: scan /data/inbox/, resolve account by folder convention.
- parsers/activobank.py implementing parse(pdf_path) -> ParsedStatement.
- Persist statement + transactions; file-hash + content-hash + period dedup.
- Seed categories.yaml, merchants.yaml, merchant_patterns; run rule-only categorization (regex), authority rule/none (no manual/llm yet).
- Report writer: Spending Detail section only, one file per calendar month.
- Acceptance tests: AC1, AC3, AC8, AC12, named per AC.

### Deferred (later slices — explicitly not now)

- Gmail fetch / OAuth / keyring (ADR-9, AC5), cruzar fetch.
- LLM extraction + categorization (ADR-2/12, AC4/15/16); Needs-Categorization section.
- Transfer detection (ADR-15, AC21) → is_transfer stays false this slice.
- FX (ADR-5, AC10), Summary section, Net Worth (ADR-16, AC22).
- Investments / holdings_snapshot population, Portfolio Δ (ADR-14, AC6/20), Investment Detail section.
- AC2/AC9/AC11/AC13/AC14/AC17–22 — not targeted by this slice (AC9 full-report ordering will be red until later sections land; that is expected, not a failure).

## Design decisions confirmed

1. **Parsers location**: `src/cruzar/parsers/activobank.py` (importable subpackage). Substance of ADR-11 (one module per institution, common parse() interface, deterministic order) is preserved.

2. **Money storage type**: SQLite columns as TEXT holding canonical Decimal strings. All aggregation in Python with Decimal — never SUM() in SQL.

3. **Account relocation**: Fixture now at `data/inbox/activobank/EXTRATO COMBINADO 2026005.pdf`. Will always follow `data/inbox/<account_match>/` pattern.

4. **sources.yaml**: `config/sources.yaml` is gitignored. `config/sources.yaml.example` is committed. Tests build their own temp config.

5. **account_type**: Set to **checking** (DEPÓSITO À ORDEM, demand deposit, not savings).

6. **is_transfer = false this slice**: Without ADR-15 detection, TRF P/ Moey / TRF MB WAY debits appear in Spending Detail (they're real outflows until transfer-pairing lands). Consistent with the spec's accepted "uncaught transfers leak" note.

7. **Canonical amount serialization (pinned now)**: A single helper `canonical_amount(value: Decimal, currency: str) -> str` in `persist.py` quantizes every amount to the currency's ISO 4217 minor-unit scale (EUR → 2) and returns one canonical string used for **both** the stored `amount` column **and** the `content_hash` input. So `Decimal("-100.0")` and `Decimal("-100.00")` both serialize to `"-100.00"` and hash identically — cross-statement dedup cannot silently miss a duplicate on scale drift. Not exercised by slice 1 (single statement), but a dedicated unit test pins the invariant.

## Expected ParsedStatement JSON (AC8 fixture)

Posting date = DATA LANC. (left date column). Debits negative, credits positive. Spaces stripped from amounts. Year (2026) inferred from statement period.

```json
{
  "currency": "EUR",
  "period_start": "2026-05-04",
  "period_end": "2026-05-29",
  "closing_balance": "<redacted>",
  "transactions": [
    {
      "intra_statement_seq": 1,
      "date": "2026-05-07",
      "amount": "<redacted>",
      "description_raw": "TRF MB WAY P/ <redacted>"
    },
    {
      "intra_statement_seq": 2,
      "date": "2026-05-11",
      "amount": "<redacted>",
      "description_raw": "TRF P/ Moey"
    },
    {
      "intra_statement_seq": 3,
      "date": "2026-05-15",
      "amount": "<redacted>",
      "description_raw": "TRF MB WAY P/ <redacted>"
    },
    {
      "intra_statement_seq": 4,
      "date": "2026-05-20",
      "amount": "<redacted>",
      "description_raw": "TRF P/ Moey"
    },
    {
      "intra_statement_seq": 5,
      "date": "2026-05-21",
      "amount": "<redacted>",
      "description_raw": "TRF P/ Moey"
    },
    {
      "intra_statement_seq": 6,
      "date": "2026-05-22",
      "amount": "<redacted>",
      "description_raw": "TRANSFERENCIA - VENCIMENTO"
    },
    {
      "intra_statement_seq": 7,
      "date": "2026-05-25",
      "amount": "<redacted>",
      "description_raw": "TRF P/ Moey"
    },
    {
      "intra_statement_seq": 8,
      "date": "2026-05-25",
      "amount": "<redacted>",
      "description_raw": "TRF P/ Moey"
    },
    {
      "intra_statement_seq": 9,
      "date": "2026-05-27",
      "amount": "<redacted>",
      "description_raw": "COMPRA <redacted> Spotify <redacted> Stockholm SE"
    },
    {
      "intra_statement_seq": 10,
      "date": "2026-05-28",
      "amount": "<redacted>",
      "description_raw": "TRF P/ <redacted>"
    },
    {
      "intra_statement_seq": 11,
      "date": "2026-05-28",
      "amount": "<redacted>",
      "description_raw": "TRF P/ Moey"
    }
  ]
}
```

**Sanity check**: &lt;redacted&lt; (SALDO INICIAL) + Σ amounts = &lt;redacted&lt; (SALDO FINAL). ✓ (verified against the real fixture during AC8; actual values never committed.)

## Proposed layout

```
pyproject.toml                                    # uv, deps, [project.scripts] cruzar=...

src/cruzar/
├── __init__.py
├── cli.py                                       # argparse: `cruzar process`
├── config.py                                    # load cruzar.yaml / sources.yaml / categories.yaml / merchants.yaml
├── db.py                                        # connect, init schema (idempotent), FK pragma
├── schema.sql                                   # DDL for ALL data-model tables (spec §Data model)
├── models.py                                    # ParsedStatement / ParsedTransaction dataclasses (Decimal)
├── pipeline.py                                  # process(): scan→resolve→dedup→parse→persist→report (atomic per file)
├── persist.py                                   # seed configs; insert statement+transactions; content_hash; dedup
├── categorize.py                                # rule-only merchant_patterns matching (ADR-13 rule tier)
├── report.py                                    # Spending Detail writer → reports/cruzar-YYYY-MM.md
└── parsers/
    ├── __init__.py                              # registry: institution -> parse()
    └── activobank.py                            # parse(pdf_path) -> ParsedStatement (pdfplumber, x-column bucketing)

config/
├── cruzar.yaml                                  # base_currency: EUR (committed)
├── categories.yaml                              # starter vocabulary (committed)
├── merchants.yaml                               # seed merchants + regex patterns (committed)
└── sources.yaml.example                         # one ActivoBank account entry (committed; real one gitignored)

data/
└── inbox/
    └── activobank/
        └── EXTRATO COMBINADO 2026005.pdf       # relocated fixture

reports/                                         # gitignored output

tests/
├── conftest.py                                  # temp DB + temp inbox + temp config fixtures
├── fixtures/
│   └── activobank/
│       ├── statement.pdf                        # redacted fixture (copy of the provided PDF)
│       └── expected.json                        # the JSON above
└── acceptance/
    ├── test_ac01_idempotent_reprocessing.py
    ├── test_ac03_no_duplicate_content_hash.py
    ├── test_ac08_parser_has_fixture.py
    └── test_ac12_no_orphan_transactions.py
```

## Implementation checklist

1. **Scaffold uv init** (src layout), pyproject.toml deps: pdfplumber, pyyaml; dev: pytest, ruff, pyright. [project.scripts] cruzar. → CLAUDE Stack/Commands
   - [ ] Done

2. **schema.sql** — all tables accounts, statements, transactions, holdings_snapshot, merchants, merchant_patterns, categories, processed_files, fx_rates, with PKs/FKs/immutables/UNIQUE(content_hash) exactly per spec §Data model. Money columns TEXT. PRAGMA foreign_keys=ON. → SPEC §Data model, ADR-7, AC3
   - [ ] Done

3. **models.py** ParsedStatement(currency, period_start, period_end, closing_balance: Decimal, transactions) + ParsedTransaction(intra_statement_seq, date, amount: Decimal, description_raw). Account-agnostic (resolution is external). → ADR-11
   - [ ] Done

4. **parsers/activobank.py** parse(pdf_path) — pdfplumber word extraction on page 2; locate transaction block between SALDO INICIAL and SALDO FINAL; bucket numeric tokens to DEBITO/CREDITO/SALDO by header x-position; sign by column; posting date = DATA LANC, year from period; emit lines top-to-bottom with intra_statement_seq 1..N; parse EXTRATO DE … A … for period and SALDO FINAL for closing_balance; amounts Decimal (strip spaces). Parse failure → raise (no partial). → ADR-11, AC8, anti-pattern "fail loud / write nothing partial"
   - [ ] Done

5. **config.py** load cruzar.yaml (base EUR), sources.yaml (accounts), categories.yaml, merchants.yaml. → SPEC §Inputs, ADR-3
   - [ ] Done

6. **persist.py** — config seeding idempotent upsert of categories, merchants, merchant_patterns, accounts into SQLite (SQLite = source of truth). Add `canonical_amount(value: Decimal, currency: str) -> str` (quantize to ISO 4217 minor-unit scale, EUR→2) returning the canonical string used for **both** the stored `amount` and the `content_hash` input (decision 7). → ADR-3, AC7
   - [ ] Done

7. **pipeline.py** — process(): walk /data/inbox/ for PDFs; per file: compute file_hash; skip if processed_files.status=ok (idempotency); resolve account by <account_match> folder → none ⇒ record unresolved_account, write nothing; else parse() (fail ⇒ parse_failed, nothing); statement period+account dedup; insert statement + transactions with content_hash = sha256(account_id, posting_date, **canonical_amount**, description_raw, intra_statement_seq), INSERT-guarded on UNIQUE(content_hash); one transaction per file = atomic (commit/rollback). → ADR-4/7/10, AC1/3/12, SPEC §Account resolution & failure modes
   - [ ] Done

8. **categorize.py** rule-only: for each transaction with no manual/llm, match merchant_patterns (lower priority wins, tie by id) → set merchant_id + merchant_source='rule'; else 'none'. (Spotify → Subscriptions in this fixture.) → ADR-13 (rule tier only this slice)
   - [ ] Done

9. **report.py** for each month present (from statement period_end), write reports/cruzar-YYYY-MM.md with Spending Detail only: cash account, amount<0, is_transfer=false, that month, sorted date desc; columns Date | Amount | Currency | Merchant | Category (Merchant = matched name else raw; Category from merchant else blank). → SPEC §Outputs Section 2
   - [ ] Done

10. **cli.py** argparse cruzar process → pipeline.process() (writes report at the end). → CLAUDE Commands
    - [ ] Done

## Acceptance tests (the slice gate)

- [ ] **test_ac01_idempotent_reprocessing.py** — run process twice over a temp inbox containing the fixture; sha256 of a sorted conn.iterdump() equal across runs; second run inserts zero rows (file-hash skip). → AC1

- [ ] **test_ac03_no_duplicate_content_hash.py** — after ingest, COUNT(\*)==COUNT(DISTINCT content_hash); and a direct duplicate insert raises IntegrityError (UNIQUE enforced). → AC3

- [ ] **test_ac08_parser_has_fixture.py** — parse(tests/fixtures/activobank/statement.pdf) serialized == expected.json; assert balance identity (synthetic fixture: SALDO INICIAL + Σ == closing). → AC8

- [ ] **test_ac12_no_orphan_transactions.py** — (a) after ingest, LEFT JOIN transactions→statements→accounts yields zero NULL account_id; (b) a PDF dropped in a folder with no sources.yaml entry ⇒ processed_files.status='unresolved_account' with zero statements/transactions. → AC12

- [ ] **test_canonical_amount_dedup.py** (unit, not an AC) — pins decision 7: `canonical_amount(Decimal("-100.0"),"EUR") == canonical_amount(Decimal("-100.00"),"EUR")` and the resulting content_hash is identical, so cross-statement scale drift cannot defeat dedup. Guards the latent bug now, before any multi-statement slice exists.

## Verification / done

- `uv run pytest tests/acceptance` → AC1, AC3, AC8, AC12 green.
- `uv run ruff check . && uv run pyright && uv run pytest` all clean (CLAUDE gate).
- Manual smoke: `uv run cruzar process` ingests the relocated fixture and writes reports/cruzar-2026-05.md with the 10 debit rows (Spotify categorized Subscriptions; transfers present per flag 6).
- **"Done"** = the four ACs pass + ruff/pyright/full-suite clean (no skips). Sections and ACs outside this slice remain intentionally unimplemented and are listed as deferred.
