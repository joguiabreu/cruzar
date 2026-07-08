# Plan 014 — LLM extraction fallback (AC4a, ADR-2/12)

## Goal

Close the last open clause of AC4: when a parser's structured (pdfplumber,
x-position) extraction recovers **<50% of the expected columns**, fall back to the
local LLM to extract the statement from raw page text as schema-constrained JSON
(ADR-2), then run it through the *same* persist path. The LLM extracts printed
values only — never computes (ADR-1). Ends with **AC4(a)** green and **AC16**'s
"trips the <50%-columns fallback" arm exercised.

## Acceptance

- **AC4**: LLM invoked only for (a) extraction when pdfplumber returns <50% of
  expected columns, and (b) unmatched-merchant categorization, only when no
  persisted prior result exists. Clause (b) done; this slice adds (a).
- **AC16**: first run over a fixture that trips the fallback invokes the LLM (count>0).
- **AC15**: a second run makes zero calls — already held by file-hash idempotency
  (a successfully-extracted file is `status=ok` and skipped).

## Decisions (settled)

1. **D1 — fallback signal.** A shared `ExtractionFallback(Exception)` in
   `parsers/_common.py` carrying the raw page text (`.text`). A parser raises it
   (not its generic parse error) when <50% of expected columns resolve; the
   pipeline catches this distinct type and runs the LLM extractor over `exc.text`.
   Keeps the `parse(pdf)->ParsedStatement` interface unchanged; pipeline stays
   institution-agnostic.
2. **D2 — what the LLM extracts + sign rule.** Full statement from raw text
   (`period_start/end`, `currency`, `closing_balance`, `transactions[]`),
   parser-agnostic. The model emits each line's printed `amount` magnitude + a
   `direction` (`debit`|`credit`); **Python applies the sign** (debit→negative) and
   parses strings to `Decimal` at the boundary — math stays out of the model
   (ADR-1). Amounts requested in plain international notation (period decimal, no
   thousands separators).
3. **D3 — the <50% metric (ActivoBank this slice).** `expected` = count of
   candidate transaction rows in the bracketed `SALDO INICIAL … FINAL` region (a
   row carrying a token in the date column, i.e. `x0 < _DATE_X0_MAX`); `resolved`
   = how many of those resolve an amount in a DEBITO/CREDITO column. Trip the
   fallback when `expected > 0 and resolved/expected < 0.5`. A clean statement
   resolves ~100%; a degenerate layout ~0%. **Guard:** `expected == 0` (a
   genuinely empty month, or so degraded that even dates don't cluster) does *not*
   trip the column metric — the existing "no transactions parsed" path still
   applies. The check runs **before** `closing_balance`/transaction extraction so a
   degraded statement falls back instead of raising a generic parse error.
   - *(Why a fraction over rows, per review:)* for a uniformly-rendered table it's
     effectively all-or-nothing, so the threshold is safe; the fraction is kept
     (not a one-row probe) to degrade gracefully on partial damage (multi-page,
     stray/footer rows). Measuring `expected` from the table region (not a single
     row) also avoids a divide-by-zero when the layout collapses entirely.
4. **D4 — failure policy.** instructor re-prompts once on schema drift; still
   unusable → `LlmError` → pipeline marks `extraction_failed`, writes **nothing**
   (fail loud; extraction is source parsing, not enrichment). LLM
   unavailable/timeout mid-extraction → same. **Not sticky:** an
   `extraction_failed` file isn't `status=ok`, so it retries next run (a transient
   outage self-heals); `--reextract` is only for re-running already-`ok` files.
5. **D5 — scope guards.** `cruzar process --reextract`: out of scope (follow-up;
   file-hash idempotency already satisfies AC15). The <50% trigger ships for
   **ActivoBank only** this slice (one degenerate fixture); the shared
   `ExtractionFallback` lets other parsers adopt it later with no core change (AC7).

## Design

- **No schema change.** The extracted statement flows through `persist_statement`
  into the normal `statements`/`transactions` tables; ADR-12's "extractions
  persisted, never recomputed" is satisfied by those rows + the file-hash skip.
- **`extract.py` (new).** Holds the `LlmExtractor` Protocol
  (`extract(text) -> ParsedStatement`) and `to_parsed_statement(...)` — the
  raw-values→`ParsedStatement` boundary (str→`Decimal`, direction→sign, ISO dates).
  Justified: extraction is a distinct concern from categorization, and the boundary
  is unit-testable without Ollama. Reuses `categorize`'s `LlmError` family.
- **`llm.py`.** Add `ollama_extractor(model, host, timeout)` — same instructor /
  OpenAI client + `JSON_SCHEMA` constraint as the categorizer; builds the Pydantic
  statement model, then calls `extract.to_parsed_statement`; transport/validation
  failures wrapped as `LlmError`.
- **Pipeline.** `_ingest_inbox(conn, inbox, *, extractor=None)`; `process` builds
  the extractor when `llm.enabled` (alongside `propose`). In the parse block, catch
  `ExtractionFallback` separately: if an extractor exists, call it over `exc.text`
  (log "LLM extraction fallback for <file>" — AC4 log inspection) and continue down
  the normal dedup/persist path; else / on `LlmError` → `extraction_failed`. Genuine
  layout errors still raise `parse_failed` unchanged.

## Fixture (obviously-fake) — sign-off

A committed generator (`tests/fixtures/activobank_degraded/generate_fixture.py`)
builds a degenerate-layout ActivoBank-style PDF: period line + `SALDO INICIAL` /
`SALDO FINAL` present (so the table still brackets), but transaction amounts
rendered in the description band (not the DEBITO/CREDITO columns), so the parser
resolves 0% of amount columns and raises `ExtractionFallback`. No `expected.json`
oracle — the offline test injects a **fake** extractor returning the canned
statement below (the real LLM output is non-deterministic, covered by the real-run
gate, not committed).

| # | Date | Description | Direction / Amount |
|---|------|-------------|--------------------|
| 1 | 2025-03-05 | `EXAMPLE SUBSCRIPTION` | debit 10.00 |
| 2 | 2025-03-09 | `EXAMPLE SALARY` | credit 2000.00 |
| 3 | 2025-03-18 | `EXAMPLE GROCER` | debit 42.50 |

Period 2025-03-01…2025-03-31, EUR, closing 1947.50.

## Tests (offline; fake extractor injected — no Ollama)

- `test_ac04_extraction_fallback_invokes_llm` — degraded PDF + counting fake
  extractor via `_ingest_inbox(..., extractor=spy)`: extractor called (AC16
  count>0), statement persisted, transactions match the canned return.
- `test_ac04_clean_statement_skips_extraction` — the normal ActivoBank fixture +
  raise-on-call fake: extractor never called (clean layout resolves ≥50%).
- Failure paths: extractor raises `LlmError` → `extraction_failed`, zero rows;
  `ExtractionFallback` with no extractor (LLM disabled) → `extraction_failed`.
- A `to_parsed_statement` unit test for the sign/`Decimal`/date conversion.
- Suite stays offline (`conftest` keeps `llm.enabled:false`); AC8 fixtures + smoke
  + parity stay green.

## SPEC + README

- SPEC: flip AC4's implementation note to "clause (a) implemented"; no ADR/AC
  wording change.
- README: document the fallback (defeated layout → local LLM reads it; needs Ollama,
  else `extraction_failed` and retried next run).

## Out of scope

- `--reextract` CLI (D5).
- Rolling the <50% trigger to the other four parsers.
- Holdings/investment extraction fallback (cash transactions only for v1).

## Definition of done

- AC4(a) + AC16 tests green; AC8/AC15/parity/smoke green.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- README + SPEC updated.
- **Real-run gate (user):** adds a live-network + real-PDF path, so it isn't done
  until `uv run cruzar process` with Ollama running has extracted a real degraded
  statement end-to-end. Green offline tests are necessary, not sufficient.
