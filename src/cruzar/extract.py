"""LLM extraction fallback (ADR-2, AC4a) — the protocol + the raw-values boundary.

When a parser can't recover a statement's columns (`ExtractionFallback`), the
pipeline hands the raw page text to an ``LlmExtractor``, which returns a
``ParsedStatement``. The model emits printed values only — date, description, the
amount *magnitude*, and a debit/credit *direction*; ``to_parsed_statement`` applies
the sign and parses strings to ``Decimal`` here in Python, so no arithmetic is ever
asked of the model (ADR-1). The concrete Ollama client lives in ``llm.py`` (the only
module importing instructor/openai); this boundary stays import-light and unit-
testable without a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from cruzar.categorize import LlmError
from cruzar.models import ParsedStatement, ParsedTransaction


class ExtractedLine(Protocol):
    """The per-transaction values the model emits (printed, unsigned). Read-only
    (properties) so a concrete line whose ``direction`` is a narrower ``Literal`` still
    satisfies it — a mutable attribute would be invariant and reject the subtype."""

    @property
    def date(self) -> str: ...  # ISO YYYY-MM-DD
    @property
    def description(self) -> str: ...
    @property
    def amount(self) -> str: ...  # printed magnitude, plain decimal (no thousands sep)
    @property
    def direction(self) -> str: ...  # 'debit' | 'credit'


class LlmExtractor(Protocol):
    def extract(self, text: str) -> ParsedStatement:
        """Extract a full statement from raw page ``text``. Raises ``LlmError`` on a
        transport failure or unusable output."""
        ...


def to_parsed_statement(
    *,
    currency: str,
    period_start: str,
    period_end: str,
    closing_balance: str,
    lines: Sequence[ExtractedLine],
) -> ParsedStatement:
    """Convert the model's printed values into a ``ParsedStatement``: debit→negative,
    strings→``Decimal``, ISO strings→``date``, ``intra_statement_seq`` from order.

    Raises ``LlmError`` if any value is malformed — the caller treats that as an
    unusable extraction (no partial data, fail loud per AC4a / SPEC).
    """
    try:
        transactions = [
            ParsedTransaction(
                intra_statement_seq=seq,
                date=date.fromisoformat(line.date),
                amount=_signed(line.amount, line.direction),
                description_raw=line.description,
            )
            for seq, line in enumerate(lines, start=1)
        ]
        return ParsedStatement(
            currency=currency,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            closing_balance=Decimal(closing_balance),
            transactions=transactions,
        )
    except (InvalidOperation, ValueError) as exc:
        raise LlmError(f"LLM extraction returned malformed values: {exc}") from exc


def _signed(amount: str, direction: str) -> Decimal:
    magnitude = Decimal(amount)
    if direction == "debit":
        return -magnitude
    if direction == "credit":
        return magnitude
    raise ValueError(f"unknown direction {direction!r}")
