"""Parser registry: institution -> parse(pdf_path) -> ParsedStatement (ADR-11).

One module per institution. The core pipeline stays institution-agnostic by
looking parsers up here by the ``institution`` declared in sources.yaml.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers import activobank, moey, revolut

PARSERS: dict[str, Callable[[str | Path], ParsedStatement]] = {
    "activobank": activobank.parse,
    "moey": moey.parse,
    "revolut": revolut.parse,
}


def get_parser(institution: str) -> Callable[[str | Path], ParsedStatement]:
    try:
        return PARSERS[institution]
    except KeyError:
        raise ValueError(f"no parser registered for institution {institution!r}") from None
