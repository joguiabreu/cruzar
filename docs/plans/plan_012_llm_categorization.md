# Plan 012 — LLM categorization (Section 5 · AC4b/15/16/17/18)

Plan of record (decisions settled). Adds the `llm` tier to categorization (ADR-13):
for transactions no rule matched, ask the local LLM to propose a merchant + category,
persist the proposal (ADR-12, never recomputed), auto-apply confident in-vocabulary
proposals, and surface the rest in the report's conditional **Section 5 — Needs
Categorization**.

Today `categorize.py` is rule-only; `none` rows never get a proposal; Section 5
doesn't exist; no LLM dependency is installed.

## The contract (SPEC + ADRs)

- **ADR-13 authority:** `manual > rule > llm`. A rule may overwrite an `llm` row;
  nothing overwrites `manual`; `none` is eligible for an LLM proposal.
- **ADR-2:** LLM returns only schema-constrained JSON (no free text, no math — ADR-1).
- **ADR-12:** proposals persisted and **never recomputed** — a re-run over unchanged
  data makes zero LLM calls (AC15).
- **Section 5 (conditional):** shown iff un-categorized merchants exist. Columns:
  `Raw Description | LLM-Proposed Merchant | LLM-Proposed Category`.
- **Edge cases:** low-confidence → Needs Categorization, no auto-assign; off-vocabulary
  category → treat as un-categorized, never silently create a category.

## Decisions (settled)

- **D1 — Categorization only; defer the extraction fallback. AGREED.** AC4 has two LLM
  uses: (a) extraction when pdfplumber returns <50% of expected columns, (b)
  categorization of unmatched merchants. (a) is a separate machine (LLM reading PDFs) —
  a nice feature but not core; deferred to its own slice. AC16 explicitly accepts "trips
  the <50%-columns fallback OR an unmatched transaction", so the categorization path
  satisfies AC15/16/17/18 and the (b) half of AC4. The AC4 test added here covers clause
  (b); clause (a) stays unimplemented and noted in SPEC.

- **D2 — LLM client library = `instructor`. AGREED.** Deps: `instructor` + `openai` +
  `pydantic`, behind an injectable `Protocol` so the offline suite never imports or calls
  it. instructor validates the reply against a Pydantic model and re-prompts on malformed
  output, talking to Ollama's OpenAI-compatible endpoint (`:11434/v1`). Least code;
  automates the fiddly validate/retry of coercing a local 8B model into schema-conforming
  JSON; ADR-2 already names it. (outlines' constrained-decoding edge doesn't land through
  Ollama's HTTP API; raw-ollama would need an ADR-2 tweak + hand-rolled validation.)

- **D3 — Persistence = new `llm_categorizations` cache table, keyed by exact
  `description_raw`. AGREED.** One LLM call per distinct raw description; repeats reuse it;
  re-runs read it → zero calls (ADR-12). Exact key (not normalized) for v1 — simple and
  safe; near-duplicate descriptions each cost one call (known cost). Columns:

  ```sql
  llm_categorizations(
    description_raw    TEXT PRIMARY KEY,
    proposed_merchant  TEXT NOT NULL,
    proposed_category  TEXT NOT NULL,
    confidence         REAL NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('applied','needs_review')),
    model              TEXT NOT NULL,     -- which model produced it (future --reextract)
    created_at         TEXT NOT NULL
  )
  ```

  New table via `CREATE TABLE IF NOT EXISTS` in `schema.sql` — that is the migration path
  for a new table (init runs it on old DBs too); schema-parity holds with no `_migrate`
  step (that rule is for altering existing tables).

  **Low-confidence / off-vocabulary handling (done in this slice):** such a proposal IS
  persisted with `status='needs_review'` but NOT applied — the txn stays `none` and
  appears in Section 5 with the LLM's guess shown. Cached, so a re-run makes no call. A
  real answer we chose not to auto-apply — distinct from an outage (D6), which caches
  nothing so it can retry.

- **D4 — Auto-apply = upsert a merchant row, link the txn as `llm`. AGREED.** (*Upsert* =
  `INSERT … ON CONFLICT … DO UPDATE`: create the merchant if its name is new, reuse if it
  exists — the pattern `persist.py` already uses.) When a proposal is confident AND its
  category is in-vocabulary (`status='applied'`): upsert a `merchants` row
  (name=proposed_merchant, category=proposed_category) and set
  `transactions.merchant_id` + `merchant_source='llm'`. Keeps Spending/Earning Detail
  uniform (one `merchant_id` join) and matches the data model. A later rule still
  overrides it (ADR-13).

- **D5 — Confidence threshold + config (disable supported). AGREED.** Proposal carries
  `confidence ∈ [0,1]`; `< min_confidence` → `needs_review` (Section 5, not applied). New
  `llm:` block in `cruzar.yaml`:

  ```yaml
  llm:
    enabled: true                 # false → rule-only, no calls (offline users)
    model: qwen3:8b               # was top-level llm_model; kept back-compatible
    host: http://localhost:11434
    min_confidence: 0.7
  ```

- **D6 — Degrade, never crash, with automatic recovery. AGREED.** Categorization is
  enrichment, not source parsing — wrap transport/parse failures in `LlmError`, log a
  warning, leave those rows `none`, finish the run (numbers are all still there; we lose
  only the merchant/category enrichment; rows show in Section 5 as raw descriptions).
  **Recovery:** on an outage we write nothing to `llm_categorizations`, so when Ollama is
  back the next `cruzar process` finds no cache entry → re-invokes → fills them in.
  Automatic, no manual step. Not an ADR-12 violation: a transport failure isn't a result
  to preserve.

- **D7 — Section 5 scope = this report-month's `none` cash transactions. AGREED.**
  Consistent with Sections 2/3. Each distinct raw description shows once with its persisted
  proposal (the `needs_review` one if present, else blank).

- **D8 — Defer `recategorize` + `--reextract` CLI. AGREED.** Separable; this slice ships
  automatic llm proposals + caching + Section 5. AC17 (manual frozen) is testable by
  seeding a `manual` row directly.

## Design

### categorize.py (the injectable client, offline-safe)

```python
class Proposal(NamedTuple):
    merchant: str
    category: str
    confidence: float

class LlmCategorizer(Protocol):
    def propose(self, description: str, categories: list[str]) -> Proposal | None: ...
        # None on a clean "I don't know"; raises LlmError on transport failure

def categorize(conn, *, propose: LlmCategorizer | None = None,
               model: str = "", min_confidence: float = 0.7) -> None:
    # 1. rule pass (existing): manual frozen; rule sets/overrides; else -> none
    # 2. llm pass over remaining 'none' rows, grouped by description_raw:
    #    - cache hit       -> apply or skip per stored status; NO call
    #    - miss & propose None (disabled/rule-only) -> leave 'none'
    #    - miss & propose set -> call; on LlmError: log, leave 'none', cache nothing
    #      else classify (applied iff confidence>=min_confidence AND category in vocab),
    #      persist to llm_categorizations; if applied, upsert merchant + link txn
```

Tests inject a fake `propose` (counting spy / raise-on-call). The real Ollama client lives
in a new `llm.py` and is only built by `pipeline.process` when `llm.enabled`.

### llm.py (new — the only module importing instructor/openai)

- `ollama_categorizer(model, host) -> LlmCategorizer`: builds the instructor client;
  `propose()` sends the description + the controlled category list, parses a Pydantic
  `Proposal`, wraps any transport/validation failure as `LlmError`.
- Prompt: "Given this bank-statement description, name the merchant and pick exactly one
  category from this list; give a 0–1 confidence." No amounts, no math (ADR-1).

### report.py — Section 5

- `_needs_categorization_section`: the report-month's cash transactions with
  `merchant_source='none'`; one row per distinct description with its `needs_review`
  proposal (merchant/category) if persisted, else blank. Rendered only if non-empty (AC9).
  Appended after Investment Detail.

### config.py / cruzar.yaml / pipeline.py

- `config`: parse the `llm:` block (back-compat: fall back to top-level `llm_model`).
  Add `LlmConfig` (enabled, model, host, min_confidence) to `Config`.
- `pipeline.process`: build the categorizer when `enabled` (else `None`); pass `propose`
  + `min_confidence` to `categorize.categorize`.

## Acceptance tests (the gate)

| AC | Asserts | How |
|----|---------|-----|
| AC4(b) | LLM invoked only for pattern-unmatched txns, and only when no persisted prior | counting spy + a pre-seeded cache row that must NOT trigger a call |
| AC15 | second run → zero LLM calls | raise-on-call stub; run completes |
| AC16 | first run with an unmatched txn → calls > 0 | counting spy |
| AC17 | `manual` never modified | seed a manual row a rule AND the llm would match; assert unchanged |
| AC18 | a rule matching an `llm` row → becomes `rule`, no call | raise-on-call stub |

Plus a Section-5 render test (proposal shown; section absent when all categorized) and a
degradation test (D6: `propose` raises `LlmError` → rows stay `none`, run completes, nothing
cached, Section 5 lists them). All offline — no fixture ever reaches Ollama. Fixture/oracle
values proposed inline (obviously-fake descriptions/merchants) for sign-off.

## Out of scope

- AC4(a) LLM extraction fallback (<50% columns) — its own slice.
- `cruzar recategorize` (manual tier CLI) and `--reextract` — follow-up.
- Description normalization / fuzzy merchant grouping — v2.

## README + SPEC

- README: document the `llm:` config block, the Needs-Categorization section, and the
  "LLM optional — degrades gracefully if Ollama isn't running, recovers on next run".
- SPEC: no ADR/AC weakening. Add a one-line note that AC4(a) extraction is a later slice.

## Definition of done

- AC4(b)/15/16/17/18 tests green; Section-5 + degradation tests green.
- schema-parity + smoke green (new table appears on upgraded DBs).
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- README updated.
- **Real-run gate (user):** `uv run cruzar process` with Ollama running proposes + persists
  on the real inbox — and a second run makes zero calls.
