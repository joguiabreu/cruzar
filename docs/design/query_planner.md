# The query planner — how `cruzar ask` works

> A learning-oriented walkthrough of the conversational-query feature (ADR-17): how a
> free-form question becomes an exact answer, why the LLM never touches the math, and
> how this is "an MCP without the protocol." Companion to `plan_018`.

## The problem

You want to ask, in plain language, *"how much did I spend on Dining in the last six
months?"* and get a correct number. The tempting shortcut — feed the reports to an LLM
and let it answer — is exactly what we **must not** do here, for two reasons baked into
this project:

1. **ADR-1: the LLM never computes.** An LLM is a next-token predictor with no
   calculator; it *approximates* arithmetic. Summing 200 transactions with cents, or
   converting a currency, it drifts — a transposed digit, a dropped row, a
   plausible-but-wrong total — and it fails **confidently** (no error, just a wrong
   number). For money that's unacceptable. Money is even stored as `Decimal` strings so
   aggregation happens in Python, never `SUM()` in SQL.
2. **Privacy.** Cruzar is local-first; the query model is the same local Ollama as
   categorization. Real balances never leave the machine.

## The idea: NL → query → Python answer

So we split the work along the model's actual strength. The LLM does **NLU** (Natural
Language Understanding — turning language into structure); Python does the math and the
**NLG** (Natural Language Generation — wording the answer) deterministically.

```
   "how much on Dining last 6 months?"            ← natural language (NL)
                  │
                  ▼   NLU: the model's only job
   QueryPlanner.plan(question, today)             ← driven port (ollama adapter, swappable)
                  │
                  ▼
   QuerySpec  =  SpendByCategory(                  ← the model's WHOLE output: a query, not a number
                    period=Period(last_n_months=6),
                    categories=["Dining"])
                  │
                  ▼   the model is now out of the loop
   analytics.run(conn, spec)                       ← THE PORT: Python, Decimal, reuses metrics
                  │
                  ▼
   QueryResult(scalar=Decimal("-242.50"), …)
                  │
                  ▼   deterministic templating (NLG)
   "You spent €242.50 on Dining from 2025-09 to 2026-02."
```

The model chooses *which* query and *with what parameters*. Every figure is computed by
the same `metrics`/`Decimal` code that builds the reports — so answers **reconcile**
with them and can't be hallucinated.

## Piece 1 — the catalog (the port, and a future MCP tool schema)

The set of answerable questions is a **bounded catalog** of typed queries. Each is a
Pydantic model; together they form a discriminated union keyed by a `metric` field:

```python
class SpendByCategory(BaseModel):
    """Cash spending grouped by category. `categories` → only those, summed; `top` → top N."""
    metric: Literal["spend_by_category"]
    period: Period
    categories: list[str] | None = None   # a *set* — "food" → ["Dining", "Groceries"]
    top: int | None = None

class Unsupported(BaseModel):
    """The question doesn't map to any known query."""
    metric: Literal["unsupported"]
    reason: str = ""

QuerySpec = Annotated[
    Union[SpendTotal, SpendByCategory, SpendByMerchant, IncomeTotal,
          IncomeBySource, NetWorth, NetWorthTrend, InvestmentPerformance, Unsupported],
    Field(discriminator="metric"),
]
```

This union **is the interface** (the hexagonal *port*). Its handlers in `analytics.run`
are pure Python. Note that **`Unsupported` is part of the union** — "I can't answer
that" is a first-class, in-grammar result, not an exception we parse out of prose.

## Piece 2 — how the LLM "chooses a query": constrained decoding

We hand the model the question plus the `QuerySpec` schema and ask for structured output
in JSON-schema mode:

```python
class _Plan(BaseModel):
    query: QuerySpec   # the discriminated union

spec = client.chat.completions.create(
    model=..., response_model=_Plan, mode=instructor.Mode.JSON_SCHEMA,
    messages=[{"role": "system", "content": SYSTEM},   # describes each query + the category vocab
              {"role": "user", "content": question}],
).query
```

The key mechanism is **constrained decoding**: the model's token sampling is *masked* so
it can only emit tokens that keep the output valid against the schema. So "the LLM picks
a query" is literally: the `metric` field is forced to one of the allowed `Literal`s,
and that variant's required params must follow. It physically can't return prose, an
invalid query, or a malformed shape. (This is exactly how "function calling" / "tool
use" works under the hood — the union of tool schemas becomes the output constraint.)

Two craft details that make it good:
- The **system prompt** lists each query and *when* to use it, and pins category words
  to the controlled vocabulary (so "food" → `Dining`). `analytics.run` also matches
  categories case-insensitively as a safety net.
- **Dates stay in Python.** The model emits *intent* — `Period(last_n_months=6)` or
  `Period(year=2025)` — and `analytics.resolve_period` does the calendar arithmetic
  against `today`. The model never computes the month bounds.

## Piece 3 — run & render (where numbers are born)

`run` dispatches on the query kind to a Python handler that reuses `metrics`, iterating
months and summing in `Decimal`:

```python
if isinstance(spec, SpendByCategory):
    start, end = resolve_period(spec.period, today)
    merged = _merge([metrics.spending_by_category(conn, ym, fetch=fetch)
                     for ym in _months(start, end)])
    return _grouped_result(spec.metric, merged, start, end, spec.category, spec.top, ascending=True)
```

`render` is the **only** place a number becomes text — a per-result template filled with
the computed `Decimal`. The model never authors a digit, so even the wording can't drift
("€1,234.50" can't become "€1,234"). That's why we keep rendering deterministic rather
than letting the model phrase the answer.

### Don't trust the planner's structure — validate in Python

A small model is an inconsistent JSON author, so the Python side stays defensive (same
spirit as ADR-1: the model proposes, code disposes):

- **Periods are normalized.** A planner will sometimes emit a *reversed* range, or full
  `YYYY-MM-DD` dates instead of `YYYY-MM`. `resolve_period` truncates to year-month and
  swaps reversed bounds, so a backwards range can never silently resolve to zero months
  (which would render as a misleading "you spent nothing").
- **Categories are a set, matched case-insensitively, and echoed.** `categories` is a
  `list[str]` (an everyday word like "food" maps to *several* bookkeeping categories);
  `run` sums the matched ones and the answer **echoes which it counted**
  ("…on Dining + Groceries…") so the mapping is visible and correctable. A real category
  with no spending in range just contributes 0; if *nothing* matched, the answer says so
  honestly instead of implying zero.

These guards turn the common small-model failures (reversed period, multi-category,
fuzzy concept) into either a correct answer or a transparent one.

## Why this is "an MCP without the protocol"

[MCP](https://modelcontextprotocol.io) (the Model Context Protocol) is three things:

| MCP | Here |
|-----|------|
| (a) a catalog of tools with JSON schemas + descriptions | the `QuerySpec` Pydantic union (each variant's schema + docstring) |
| (b) a way for the LLM to *call* one | `instructor` constrained output against that union |
| (c) a transport (JSON-RPC over stdio/HTTP) | — skipped; we run in-process |

We build (a) and (b) and skip (c). The payoff is the **hexagonal** shape you can see in
the diagram: the catalog + `run` is the port; `cruzar ask` is the first *driving
adapter*. To expose Cruzar **as an MCP server** later, you wrap the same catalog +
handlers behind the MCP transport, and the external client's LLM does the planning —
replacing our `QueryPlanner`. The core (`analytics.run`) is reused verbatim; only the
front door changes. (Privacy caveat: an MCP *client* is often a cloud model, so that
path trades the local-only guarantee unless the client is also local.)

## If you want to go deeper

- **Query planning / federation:** Apache Calcite (relational algebra + pluggable
  adapters + cost-based optimizer), Trino/Presto, "polystore", logical-vs-physical plans.
- **Semantic / metrics layers** (closest to our catalog): dbt Semantic Layer / MetricFlow,
  Cube.dev, LookML — "define a metric once, answer many queries."
- **NL → query:** text-to-SQL / NL2SQL, semantic parsing, the Spider benchmark, NLIDB.
- **LLM mechanics:** function calling / tool use, constrained / grammar-guided decoding
  (`instructor`, `outlines`), ReAct, the MCP spec.

## Where the code lives

- `src/cruzar/analytics.py` — the catalog (`QuerySpec`), `resolve_period`, `run`,
  `render`, `answer`, and the `QueryPlanner` port.
- `src/cruzar/llm.py` — `ollama_query_planner` (the Ollama adapter; the only place that
  imports `instructor`/`openai`).
- `src/cruzar/pipeline.py` — `ask(...)` wires planner → run → render, read-only.
- `src/cruzar/cli.py` — the `cruzar ask "<question>"` command.
- `tests/test_analytics.py` — figures (Decimal, reconciling), period resolution, and the
  plan → render flow with a fake planner (offline).
