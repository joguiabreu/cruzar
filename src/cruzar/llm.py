"""Local LLM client for categorization (ADR-2) — the only module that imports
``instructor``/``openai``. Talks to Ollama's OpenAI-compatible endpoint and returns
schema-constrained JSON (a Pydantic model), never free text and never math (ADR-1).

The rest of the app depends only on the ``LlmCategorizer`` protocol in
``categorize``; tests inject a fake. Transport/validation failures are wrapped as
``LlmError`` so the pipeline degrades instead of crashing (the report still renders;
the affected lines show in Needs-Categorization and retry next run).
"""

from __future__ import annotations

from cruzar.analytics import QueryPlanner, QuerySpec
from cruzar.categorize import LlmCategorizer, LlmError, LlmTimeout, LlmUnavailable, Proposal
from cruzar.extract import LlmExtractor, to_parsed_statement
from cruzar.models import ParsedStatement

_EXTRACT_SYSTEM = (
    "You transcribe a bank statement that automated parsing could not read into "
    "structured JSON, for a personal-finance tool. Copy the PRINTED values exactly — "
    "do NOT calculate, sum, convert currencies, or infer anything not on the page "
    "(ADR-1). For each transaction line emit: date (as YYYY-MM-DD), description (the "
    "raw text), amount (the printed number as a plain decimal with a '.' decimal "
    "separator and NO thousands separators, e.g. 1234.56), and direction — 'debit' "
    "for money leaving the account, 'credit' for money arriving. Do not apply a sign; "
    "report the magnitude and the direction. Also emit the statement currency (ISO "
    "4217), period_start, period_end (YYYY-MM-DD), and the printed closing balance "
    "(signed if negative). Skip header/footer/summary rows that are not transactions."
)

_SYSTEM = (
    "You label a single bank-statement transaction for a personal-finance tool. "
    "Descriptions are often terse, abbreviated, or in Portuguese (e.g. 'COMPRA' = "
    "purchase, 'PAG' = payment, 'LEVANTAMENTO' = withdrawal) and may carry card/POS "
    "numbers, dates, and bank prefixes. Do two things:\n"
    "1. merchant — a clean, human-readable name in Title Case; strip the numbers, "
    "dates and bank noise (e.g. 'COMPRA 1234 ACME COFFEE LX' -> 'Acme Coffee'). If no "
    "merchant is identifiable, use the clearest available word.\n"
    "2. category — choose EXACTLY ONE from the list you are given; never invent one.\n"
    "confidence — how sure you are of both, in [0, 1]: use >= 0.8 when the merchant is "
    "clearly recognizable, <= 0.4 when the text is too cryptic to tell. Never do arithmetic."
)

# Obviously-synthetic few-shot examples (no real payees — privacy invariant) that
# teach the output shape and how to be decisive vs. hedge.
_EXAMPLES = (
    "Examples:\n"
    "- 'COMPRA 0421 ACME COFFEE' -> merchant 'Acme Coffee', category 'Dining', confidence 0.9\n"
    "- 'PAG GLOBEX MARKET 88' -> merchant 'Globex Market', category 'Groceries', confidence 0.85\n"
    "- 'TRX 99182 XZQ' -> merchant 'Xzq', category 'Other', confidence 0.3\n"
)


def ollama_categorizer(model: str, host: str, timeout: float = 60.0) -> LlmCategorizer:
    """Build an Ollama-backed categorizer. Imports are local so the heavy client is
    loaded only when the LLM tier is actually enabled.

    Retries are deliberate: the OpenAI transport does NOT retry (``max_retries=0``)
    and instructor re-prompts only on schema/validation drift, never on a refused
    connection — a down service fails fast as ``LlmUnavailable`` so the pass can stop
    instead of backing off against every line."""
    from functools import lru_cache
    from typing import Any, Literal

    import instructor
    from openai import APIConnectionError, APITimeoutError, OpenAI
    from pydantic import BaseModel, create_model
    from tenacity import Retrying, retry_if_not_exception_type, stop_after_attempt

    @lru_cache(maxsize=8)
    def _response_model(categories: tuple[str, ...]) -> type[BaseModel]:
        # `category` is constrained to the controlled vocabulary, so under JSON_SCHEMA
        # the grammar cannot emit an off-vocab label — that path is unreachable by
        # construction (a Literal of arbitrary strings; an Enum can't hold names like
        # "Fees & Charges"). Built once per vocabulary, not per call.
        category_type: Any = Literal[categories] if categories else str
        return create_model(
            "ConstrainedProposal",
            merchant=(str, ...),
            category=(category_type, ...),
            confidence=(float, ...),
        )

    # JSON_SCHEMA mode makes Ollama grammar-CONSTRAIN the output to the schema, so a
    # small model can't echo the schema, wrap the result, or mis-case keys (the failure
    # modes a looser JSON mode produces). The shape AND the category set are guaranteed;
    # only the labeling judgment is left to the model.
    client = instructor.from_openai(
        OpenAI(base_url=f"{host.rstrip('/')}/v1", api_key="ollama", max_retries=0, timeout=timeout),
        mode=instructor.Mode.JSON_SCHEMA,
    )
    # Retry the model only when it returns unusable JSON; reraise a refused connection
    # immediately (no point retrying a service that's down).
    reprompt = Retrying(
        stop=stop_after_attempt(2),
        retry=retry_if_not_exception_type(APIConnectionError),
        reraise=True,
    )

    class _OllamaCategorizer:
        def propose(self, description: str, categories: list[str]) -> Proposal:
            try:
                result = client.chat.completions.create(
                    model=model,
                    temperature=0,  # deterministic structured output (extraction/classification)
                    response_model=_response_model(tuple(categories)),
                    max_retries=reprompt,
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {
                            "role": "user",
                            "content": (
                                f"Allowed categories: {', '.join(categories)}\n\n"
                                f"{_EXAMPLES}\n"
                                f"Now label this description:\n{description}"
                            ),
                        },
                    ],
                )
            except APITimeoutError as exc:
                # A slow generation, NOT a dead server (APITimeoutError subclasses
                # APIConnectionError, so this must come first). Per-item, but a run of
                # them aborts the pass (see _llm_pass).
                raise LlmTimeout(
                    f"LLM timed out after {timeout:.0f}s — model too slow for this "
                    f"description (try a smaller model or raise llm.timeout_seconds)"
                ) from exc
            except APIConnectionError as exc:
                raise LlmUnavailable(f"cannot reach Ollama at {host}") from exc
            except Exception as exc:  # schema-validation or other failure
                raise LlmError(f"categorization failed: {exc}") from exc
            # Read via model_dump so the dynamically-built model stays statically opaque.
            data = result.model_dump()
            return Proposal(str(data["merchant"]), str(data["category"]), float(data["confidence"]))

    return _OllamaCategorizer()


def ollama_extractor(model: str, host: str, timeout: float = 60.0) -> LlmExtractor:
    """Build an Ollama-backed statement extractor (ADR-2, AC4a). Same constrained-JSON
    client as the categorizer; the model transcribes printed values and Python owns the
    math (sign, Decimal) via ``extract.to_parsed_statement``. Transport/validation
    failures wrap as ``LlmError`` so the pipeline marks the file ``extraction_failed``
    and writes nothing (fail loud — extraction is source parsing)."""
    from typing import Literal

    import instructor
    from openai import APIConnectionError, APITimeoutError, OpenAI
    from pydantic import BaseModel
    from tenacity import Retrying, retry_if_not_exception_type, stop_after_attempt

    class _Line(BaseModel):
        date: str
        description: str
        amount: str
        direction: Literal["debit", "credit"]

    class _Statement(BaseModel):
        currency: str
        period_start: str
        period_end: str
        closing_balance: str
        transactions: list[_Line]

    client = instructor.from_openai(
        OpenAI(base_url=f"{host.rstrip('/')}/v1", api_key="ollama", max_retries=0, timeout=timeout),
        mode=instructor.Mode.JSON_SCHEMA,
    )
    reprompt = Retrying(
        stop=stop_after_attempt(2),  # malformed twice => extraction_failed (AC4a / SPEC)
        retry=retry_if_not_exception_type(APIConnectionError),
        reraise=True,
    )

    class _OllamaExtractor:
        def extract(self, text: str) -> ParsedStatement:
            try:
                result = client.chat.completions.create(
                    model=model,
                    temperature=0,  # deterministic structured output (extraction/classification)
                    response_model=_Statement,
                    max_retries=reprompt,
                    messages=[
                        {"role": "system", "content": _EXTRACT_SYSTEM},
                        {"role": "user", "content": f"Statement text:\n{text}"},
                    ],
                )
            except APITimeoutError as exc:
                raise LlmTimeout(f"LLM extraction timed out after {timeout:.0f}s") from exc
            except APIConnectionError as exc:
                raise LlmUnavailable(f"cannot reach Ollama at {host}") from exc
            except Exception as exc:  # schema-validation or other failure
                raise LlmError(f"extraction failed: {exc}") from exc
            data = result.model_dump()
            return to_parsed_statement(
                currency=str(data["currency"]),
                period_start=str(data["period_start"]),
                period_end=str(data["period_end"]),
                closing_balance=str(data["closing_balance"]),
                lines=result.transactions,
            )

    return _OllamaExtractor()


def ollama_query_planner(
    model: str, host: str, timeout: float, categories: list[str]
) -> QueryPlanner:
    """Build an Ollama-backed query planner (ADR-17, AC-free feature). Maps a free-form
    question to a schema-constrained ``QuerySpec`` (the analytics catalog) — it selects a
    query and its parameters; it never computes (ADR-1). ``categories`` (the controlled
    vocabulary) is given to the model so question words map to real category names. A
    transport failure wraps as ``LlmError``; an unmappable question returns ``Unsupported``."""
    from datetime import date

    import instructor
    from openai import APIConnectionError, APITimeoutError, OpenAI
    from pydantic import BaseModel
    from tenacity import Retrying, retry_if_not_exception_type, stop_after_attempt

    class _Plan(BaseModel):
        query: QuerySpec  # the discriminated union — instructor constrains to it

    client = instructor.from_openai(
        OpenAI(base_url=f"{host.rstrip('/')}/v1", api_key="ollama", max_retries=0, timeout=timeout),
        mode=instructor.Mode.JSON_SCHEMA,
    )
    reprompt = Retrying(
        stop=stop_after_attempt(2),
        retry=retry_if_not_exception_type(APIConnectionError),
        reraise=True,
    )
    vocab = ", ".join(categories) if categories else "(none configured)"

    class _OllamaQueryPlanner:
        def plan(self, question: str, today: date) -> QuerySpec:
            system = (
                "You translate a personal-finance question into ONE structured query for a "
                "local tool that does all the math itself — you only choose the query and its "
                "parameters, you NEVER compute or guess a number. Today is "
                f"{today.isoformat()}.\n"
                "PERIOD: prefer a RELATIVE descriptor — set the period's last_n_months (e.g. 6), "
                "last_n_years (e.g. 1 for 'last year'), year (e.g. 2025), or this_year. Only use "
                "explicit start/end as 'YYYY-MM' (year-month, no day), start <= end. Never compute "
                "the bounds yourself.\n"
                "CATEGORIES: for a spend_by_category query, set `categories` to a LIST of the "
                f"relevant categories chosen ONLY from this exact list: {vocab}. Map everyday "
                "words to one or MORE of them — e.g. 'food' -> ['Dining', 'Groceries'], 'eating "
                "out' -> ['Dining']. Use the exact spellings above; never invent a category.\n"
                "If the question doesn't fit any available query, return the 'unsupported' query "
                "with a brief reason."
            )
            try:
                result = client.chat.completions.create(
                    model=model,
                    temperature=0,  # deterministic structured output (extraction/classification)
                    response_model=_Plan,
                    max_retries=reprompt,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": question},
                    ],
                )
            except APITimeoutError as exc:
                raise LlmTimeout(f"query planning timed out after {timeout:.0f}s") from exc
            except APIConnectionError as exc:
                raise LlmUnavailable(f"cannot reach Ollama at {host}") from exc
            except Exception as exc:  # schema-validation or other failure
                raise LlmError(f"query planning failed: {exc}") from exc
            return result.query

    return _OllamaQueryPlanner()
