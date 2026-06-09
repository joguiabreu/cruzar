"""Local LLM client for categorization (ADR-2) — the only module that imports
``instructor``/``openai``. Talks to Ollama's OpenAI-compatible endpoint and returns
schema-constrained JSON (a Pydantic model), never free text and never math (ADR-1).

The rest of the app depends only on the ``LlmCategorizer`` protocol in
``categorize``; tests inject a fake. Transport/validation failures are wrapped as
``LlmError`` so the pipeline degrades instead of crashing (the report still renders;
the affected lines show in Needs-Categorization and retry next run).
"""

from __future__ import annotations

from cruzar.categorize import LlmCategorizer, LlmError, LlmTimeout, LlmUnavailable, Proposal

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
