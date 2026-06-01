# CLAUDE.md — Cruzar

Cruzar is a local, privacy-preserving personal-finance aggregator. The full
requirements live in `docs/SPEC.md`. **This file is HOW we work in this repo, not
WHAT we're building** — read `docs/SPEC.md` for behavior. On any conflict about
product behavior, `docs/SPEC.md` wins; ask before deviating from either.

## Stack

- Python 3.12, managed with `uv`.
- SQLite via stdlib `sqlite3`. `pdfplumber` for PDF text. `instructor`/`outlines`
  for schema-constrained LLM JSON. `keyring` for secrets. Ollama for the local LLM.
- Tests: `pytest`. Lint/format: `ruff`. Types: `pyright` (strict).

## Commands

- Install: `uv sync`
- Pipeline: `uv run cruzar process` (fetch: `cruzar fetch`, report: `cruzar report`)
- Tests: `uv run pytest`
- **Acceptance harness:** `uv run pytest tests/acceptance` — one test per AC in
  `SPEC.md` (AC1–AC22). This is the gate. Work isn't done until the relevant
  AC here is green.
- Must pass before any task is "done": `uv run ruff check . && uv run pyright && uv run pytest`

## Non-negotiable invariants (from SPEC.md ADRs — do not violate)

- **Money is `Decimal`, never `float`.** Parse statement strings straight to
  Decimal. No float arithmetic on monetary values anywhere.
- All financial math happens in Python/SQL. Never ask the LLM to compute, sum,
  or convert (ADR-1). The LLM extracts and categorizes only.
- SQLite is the source of truth; reports are derived and regenerable (ADR-3).
  Never hand-edit derived state to make a test pass.
- Amounts stored in **native currency, signed** (debits negative). Convert only
  at report time, at the period-end rate (ADR-5). Never mix currencies in an
  aggregate without going through that conversion.
- `holdings_snapshot` rows are immutable: INSERT only, never UPDATE/DELETE (ADR-6).
- LLM output is schema-constrained JSON, persisted, and **never recomputed**
  (ADR-2, ADR-12). Reprocessing an unchanged file makes zero LLM calls.
- Dedup key is `content_hash`; never insert a duplicate (ADR-7, AC3).
- Categorization authority: `manual > rule > llm` (ADR-13). A rule may overwrite
  an `llm` match; nothing overwrites `manual`.
- Secrets only in the macOS Keychain via `keyring`. Never write tokens to disk,
  never commit them (ADR-9).
- One parser module per institution in `/parsers/`, implementing
  `parse(pdf_path) -> ParsedStatement`, emitting lines in deterministic
  top-to-bottom order (ADR-11).

## Workflow

- **Plan first** for any task with 3+ steps. Produce `plan.md`, then STOP. Do not
  implement until I reply "address notes, implement."
- Work in **vertical slices**. Each slice ends with its acceptance test passing.
- "Done" means: the relevant AC test passes, `ruff` clean, `pyright` clean, full
  suite run. Reporting "done" with a failing/skipped AC is a failure.
- **Don't widen scope.** If you spot a needed change outside the task, write it
  in `plan.md` and ask — don't silently do it.
- If a change would touch an **ADR or an AC, stop and ask.** ADRs are decisions,
  not suggestions.
- Prefer editing existing files over adding new ones. No new top-level modules
  without a reason captured in the plan.

## Anti-patterns (these will be rejected)

- "Pre-existing issue" / "out of scope" used to dodge something the task requires.
  Fix it or flag it explicitly; don't bury it.
- Silencing the type checker (`# type: ignore`) or skipping a test instead of
  fixing the cause.
- Catching exceptions and continuing on partial data. Per SPEC.md, a parse
  failure marks the file failed and writes nothing — fail loud, write nothing partial.
- `float` for money. Stringly-typed currency codes outside ISO 4217.
- Reformatting or refactoring files unrelated to the current task.

## Testing conventions

- Every parser has a fixture: a redacted real PDF + expected `ParsedStatement`
  JSON (AC8).
- Acceptance tests are named for the AC they verify
  (`test_ac03_no_duplicate_content_hash`, `test_ac20_portfolio_delta_nets_contributions`, …).
  Keep that mapping 1:1 so a failure points straight at a spec clause.
- Each test uses a fresh temp SQLite file. Never touch a real DB.

## Notes

- `@docs/SPEC.md` is the full specification. `@docs/` holds any split-out design notes.
- Keep this file under ~150 lines. When the agent makes the same mistake twice,
  the fix is a new rule here — not re-explaining in chat. (That habit is the
  whole point: engineer the harness so the mistake can't recur.)
