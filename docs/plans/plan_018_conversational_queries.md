# Plan 018 — `cruzar ask` (conversational queries)

## Goal

Ask free-form questions — "how much on dining the last 6 months?", "what was my
main source of spending last year?", "how have my investments been going?" — and get
exact answers. The local LLM only **chooses and parameterizes a query** from a bounded
catalog; **Python computes every number** (ADR-1). Built hexagonal: the query catalog
is the stable port, `cruzar ask` is the first adapter, an MCP server can be a second
adapter later over the same catalog. Local Ollama only (privacy). No new deps, no
schema change, read-only.

## Architecture (ports & adapters)

```
question → QueryPlanner.plan(q, today) → QuerySpec | Unsupported
                 (driven port: ollama_query_planner, swappable)
           → analytics.run(conn, spec)  ← THE PORT / tool contract (analytics.py)
           → QueryResult → render() → deterministic answer string
driving adapters:  cruzar ask (now)  │  MCP server (future — same catalog)
```

Boundary: the LLM never sums/converts/invents a figure — it maps NL → `QuerySpec`;
`metrics`/`Decimal` executes; answers render from the computed result.

## Decisions (settled)

1. **D1 — catalog is the port.** `QuerySpec` discriminated union (each variant a typed
   params model) + `analytics.run(conn, spec, *, today, fetch) -> QueryResult` +
   `render`. `QueryPlanner` Protocol (`plan(question, today) -> QuerySpec`) is the
   driven port; `llm.ollama_query_planner` is its Ollama adapter. `cruzar ask` wires
   planner → run → render.
2. **D2 — v1 vocabulary:** `spend_total`, `spend_by_category` (category?/top?),
   `spend_by_merchant` (top?), `income_total`, `income_by_source` (top?), `net_worth`
   (as_of?), `net_worth_trend`, `investment_performance`. All reuse the existing
   metric filters, generalized to a month range by iterating months and summing in
   Decimal, so answers reconcile with the reports.
3. **D3 — periods:** planner emits a structured `Period` (explicit `{start,end}` or
   relative `{last_n_months}`/`{year}`/`{this_year}`/`{last_n_years}`); Python resolves
   relatives against `today`. The LLM extracts intent; Python does the calendar math.
4. **D4 — Python-rendered answers:** per-tool deterministic templates filled with the
   computed figures; the LLM never authors a number. (LLM narration deferred.)
5. **D5 — honest refusal:** `Unsupported` is a first-class union variant; an
   unmappable / low-confidence question → a capability message, never a fabricated
   answer. Reader, not advisor.
6. **D6 — new ADR-17** ("NL → bounded query, Python computes, local model"); reuse the
   existing `llm:` config; `ask` needs Ollama and errors clearly if disabled/unreachable
   (no partial answer to degrade to).

## Design / modules

- `analytics.py` (port + core, no LLM imports): `Period`, the `QuerySpec` union (pydantic
  — doubles as the future MCP tool schema), `QueryResult`, `QueryPlanner` Protocol,
  `resolve_period`, `run`, `render`. Reuses `metrics`; adds month-range composition.
- `metrics.py`: add per-month `spending_by_merchant` and `income_by_source` (parallel to
  `spending_by_category`), so range queries merge tested per-month primitives.
- `llm.ollama_query_planner(model, host, timeout, categories)`: instructor/JSON_SCHEMA
  over the `QuerySpec` union (wrapped in a `_Plan` model); failures wrap as `LlmError`.
  Built with the controlled category vocabulary so NL category words map to real ones;
  `run` also matches categories case-insensitively as a safety net.
- `cli.py`: `cruzar ask "<question>"` → build planner (from DB categories) → plan → run
  → render → print. Read-only, cached FX (no fetch), like `report`.

## Tests (offline; fake planner injected — no Ollama)

- Catalog correctness per tool: obviously-fake data, assert `run` returns the right
  Decimal and reconciles (e.g. `spend_by_category` range == Σ monthly; `investment_
  performance` range == IV_end − IV_start − contributions).
- Period resolution against a fixed `today`.
- Planner→answer with a fake planner: rendered answer contains the Python figure;
  `Unsupported` → capability message.
- `conftest` keeps the suite offline; `ask` never builds the real planner in tests.

## SPEC + README + docs

- SPEC: add **ADR-17** and a short "Conversational queries (`cruzar ask`)" subsection.
  No AC change (harness complete; feature, not gate).
- README: document `cruzar ask`, the supported question shapes, local/read-only, answers
  from computed figures.
- Learning doc `docs/design/query_planner.md` (requested): the NL→query→Python flow,
  ports/adapters, constrained decoding, and the MCP relationship, with a diagram + snippets.

## Out of scope

- MCP server adapter (B) — future; catalog shaped to be drop-in.
- LLM-authored narration / multi-turn memory.
- Forecasts, advice, budgets. Out-of-vocabulary → honest refusal, not new query kinds.

## Definition of done

- v1 catalog computes correctly (Decimal, reconciles); planner→render works with a fake;
  honest refusal on `Unsupported`.
- `uv run ruff check . && uv run pyright && uv run pytest` clean.
- SPEC (ADR-17) + README + learning doc updated.
- **Real-run gate (user):** with Ollama running, `cruzar ask` answers the example
  questions correctly against the real DB.
