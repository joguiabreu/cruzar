"""Parser registry: institution -> parse(pdf_path) -> ParsedStatement(s) (ADR-11).

One module per institution. The core pipeline stays institution-agnostic by
looking parsers up here by the ``institution`` declared in sources.yaml. A parser
may return a single ``ParsedStatement`` or a ``list`` of them — a stacked
multi-period export (e.g. ActivoBank) yields one statement per section, in document
order; ``ingest_inbox`` normalizes both shapes (plan 023).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers import aforronet, activobank, degiro, interactivebrokers, moey, revolut

Parser = Callable[[str | Path], "ParsedStatement | list[ParsedStatement]"]

PARSERS: dict[str, Parser] = {
    "activobank": activobank.parse,
    "moey": moey.parse,
    "revolut": revolut.parse,
    "interactivebrokers": interactivebrokers.parse,
    "degiro": degiro.parse,
    "aforronet": aforronet.parse,
}


def get_parser(institution: str) -> Parser:
    try:
        return PARSERS[institution]
    except KeyError:
        raise ValueError(f"no parser registered for institution {institution!r}") from None
