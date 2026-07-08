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
  `SPEC.md` (AC1–AC23). This is the gate. Work isn't done until the relevant
  AC here is green.
- Must pass before any task is "done": `uv run ruff check . && uv run pyright && uv run pytest`

## Non-negotiable invariants (from SPEC.md ADRs — do not violate)

- **Money is `Decimal`, never `float`.** Parse statement strings straight to
  Decimal. No float arithmetic on monetary values anywhere.
- **International number notation everywhere we control.** Decimal point, no
  comma decimals — in `Decimal` values, fixtures, `expected.json`, reports, logs,
  code, and docs (`1234.56`, never `1.234,56`). Locale comma-decimal (e.g. a PT
  statement printing `1.234,56`) exists ONLY as parser *input* we convert on the
  way in; never emit it. A parser that reads a localized format owns a tiny
  helper to normalize it to a plain `Decimal` at the boundary.
- **Transactions are independent; never merge or coalesce distinct lines.** Each
  statement line is one transaction, even if identical to another — no
  de-duplicating, summing, or collapsing rows. (Reassembling a description that
  the PDF *wrapped* across physical lines is not merging: a continuation line has
  no date/amount and is part of the one transaction above it.)
- All financial math happens in Python/SQL. Never ask the LLM to compute, sum,
  or convert (ADR-1). The LLM extracts and categorizes only.
- SQLite is the source of truth; reports are derived and regenerable (ADR-3).
  Never hand-edit derived state to make a test pass.
- Amounts stored in **native currency, signed** (debits negative). Convert only
  at report time, at the period-end rate (ADR-5). Never mix currencies in an
  aggregate without going through that conversion.
- `holdings_snapshot` rows are immutable: INSERT only, never UPDATE/DELETE (ADR-6).
- **Schema changes are migrations, never CREATE-only.** `db.init_schema` is the
  sole schema entry point and must bring ANY prior DB up to `schema.sql`. Adding or
  altering a column on an existing table requires a guarded step in `db._migrate`
  (`CREATE TABLE IF NOT EXISTS` never alters an existing table). The schema-parity
  test enforces this — a fresh DB and an upgraded old DB must match.
- LLM output is schema-constrained JSON, persisted, and **never recomputed**
  (ADR-2, ADR-12). Reprocessing an unchanged file makes zero LLM calls.
- Dedup key is `content_hash`; never insert a duplicate (ADR-7, AC3).
- Categorization authority: `manual > rule > llm` (ADR-13). A rule may overwrite
  an `llm` match; nothing overwrites `manual`.
- Secrets only in the macOS Keychain via `keyring`. Never write tokens to disk,
  never commit them (ADR-9).
- **Real values NEVER touch committable parts of the repo.** No real payees,
  balances, amounts, account numbers, or names in code, configs, fixtures, docs,
  README, comments, plans, commit messages, or logs — anything git can track.
  Real data lives ONLY in gitignored `/data/` and `/reports/`. Use obviously-fake
  placeholders everywhere else (the testing-conventions rules below are the
  specific case of this general invariant). When in doubt, redact.
- One parser module per institution in `/parsers/`, implementing
  `parse(pdf_path) -> ParsedStatement`, emitting lines in deterministic
  top-to-bottom order (ADR-11).

## Workflow

- **Run the grilling loop** (`/grill` skill) for any task with 3+ steps or that
  touches an AC/ADR: I grill you for context, self-red-team a thin draft, you
  grill the survivor live (with a walkthrough of risky code paths), I finalize
  the plan, then after implementation I close by grilling the result against it.
  The plan file `docs/plans/plan_NNN_<slug>.md` (never repo root) is created at
  the *start* and is the running checkpoint + final record. STOP at finalize — do
  not implement until I reply "address notes, implement." Why: `docs/DEVELOPMENT.md`.
- **Never list a fixture/oracle sign-off as a plan decision.** It is already
  mandated (see Testing conventions) — not a choice. Don't put it in a plan's
  decisions; just propose the obviously-fake table inline at implementation time
  for verification. Anything CLAUDE.md already mandates is not a "decision."
- Work in **vertical slices**. Each slice ends with its acceptance test passing.
- "Done" means: the relevant AC test passes, `ruff` clean, `pyright` clean, full
  suite run. Reporting "done" with a failing/skipped AC is a failure.
- **Update `README.md` as part of every plan implementation.** A slice isn't done
  until `README.md` reflects any new/changed command, config file, or
  user-visible behavior it introduced. Treat a stale README like a failing test.
- **Explainer/design docs (under `docs/design/`) are written or updated ONLY when I
  explicitly ask for them — never proactively, never as an unrequested part of a
  slice.** (This is separate from `README.md` and `SPEC.md` updates, which stay
  mandatory parts of a slice as above.) When I do ask, dark-mode is not required —
  these are committed Markdown. Keep them learning-oriented: diagrams and snippets
  welcome.
- **Don't widen scope.** If you spot a needed change outside the task, write it
  in the plan and ask — don't silently do it.
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
- An **external integration** (FX provider, future fetchers) letting a raw
  transport/parse exception escape into the pipeline. These wrap failures into the
  domain error and **degrade** (cached/flagged value), never crash the run — the
  deliberate exception to "fail loud", only for valuation/enrichment, never for
  source parsing. (FX: `_http_json` → `FxError`; report renders `n/a`.)
- Conflating an integration's failure modes: **unreachable** → abort; **timeout/too-slow**
  → skip item, abort a *run* of them; **bad output** → skip. Catching a timeout as
  "unreachable" (e.g. SDK subclasses `APITimeoutError` from `APIConnectionError`) turns
  "model is slow" into a phantom "server is down". Read the integration's own logs first.
- `float` for money. Stringly-typed currency codes outside ISO 4217.
- Reformatting or refactoring files unrelated to the current task.
- No circular foreign keys between tables. If the data model implies two tables referencing each other, stop and flag it — model the relationship with a single FK and query the inverse direction.

## Testing conventions

- Every parser has a fixture: a **synthetic** PDF (no real data — built by a
  committed generator from a hand-authored transaction table) + expected
  `ParsedStatement` JSON (AC8). Real statements live ONLY in gitignored
  `/data/`; never copy one into `tests/fixtures/` (it's tracked → real payees,
  balances, account numbers would land in git history).
- Synthetic fixture values MUST be obviously fake (round/sequential amounts,
  placeholder names/refs) so a leaked real figure stands out instead of blending
  in. Names AND figures AND reference strings — redaction that only catches
  names is the judgment gap that lets a salary slip through.
- Deterministic guards (terms in gitignored `.pii-denylist`) block any real value
  from being committed, scanning figures + account numbers space-insensitively, not
  just names: `pre-commit` → `.githooks/check_pii.py` scans staged file content
  (incl. PDF text); `commit-msg` → `.githooks/check_commit_msg.py` scans the commit
  MESSAGE (don't put a real balance in a message either). **A fresh clone must opt
  in:** `git config core.hooksPath .githooks`. These guards, not eyeballing, are the
  durable defense.
- Acceptance tests are named for the AC they verify
  (`test_ac03_no_duplicate_content_hash`, `test_ac20_portfolio_delta_nets_contributions`, …).
  Keep that mapping 1:1 so a failure points straight at a spec clause.
- Each test uses a fresh temp SQLite file. Never touch a real DB. **But fresh DBs
  hide upgrade bugs:** an end-to-end pipeline smoke (`test_pipeline_smoke.py`) and a
  schema-parity guard (`test_schema_parity.py` against frozen
  `tests/schema_baseline.sql`) run in the suite so "green" means the basic process
  actually runs AND an existing DB still upgrades — not just that fresh installs pass.
- **Green offline tests ≠ the program runs.** The suite is offline + synthetic, so
  it can't exercise live-network or real-statement paths. A slice that adds such a
  path (FX fetch, a new parser, anything hitting the network or real PDFs) is NOT
  done until **`uv run cruzar process` has actually been run against the real inbox
  and completed**. I run it before reporting done — passing pytest is necessary, not
  sufficient. (This is the lesson from the FX-fetch crash: tests were green; the
  first real fetch wasn't.)
- **The offline suite must STAY offline.** A new external integration wired into
  `pipeline.process` MUST be disabled by default in the test config (`conftest`, e.g.
  `llm.enabled: false`), or pipeline tests silently make live calls and the suite hangs
  on the network. A pipeline test that *can* reach the network is broken, not slow.
- Expected-output fixtures (e.g. parser expected.json) are authored or verified by me from the source, never generated by running the code under test. If you need values to write a fixture, show them to me for sign-off; don't self-generate the oracle and assert against it. This is the **standing procedure for every new parser/fixture** — propose an obviously-fake transaction table, get my sign-off on the values, *then* it's the oracle. It is the default flow, not a per-plan decision to re-raise.

## Notes

- `@docs/SPEC.md` is the full specification. `@docs/` holds any split-out design notes.
- Plans: **always** live in `docs/plans/` — never at the repo root. The grilling
  loop creates `plan_NNN_<slug>.md` at the start and finalizes it on sign-off (see
  Workflow / `docs/DEVELOPMENT.md`); it is the committed plan of record. Read
  existing `.md` plans there for prior-slice decisions before planning a new slice.
  (Pre-existing `.html` plans predate the loop and get converted as we revisit them.)
- Keep this file under ~150 lines. When the agent makes the same mistake twice,
  the fix is a new rule here — not re-explaining in chat. (That habit is the
  whole point: engineer the harness so the mistake can't recur.)
