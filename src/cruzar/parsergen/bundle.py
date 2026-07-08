"""The ``pdfplumber`` view a parser consumes, captured as a serializable bundle.

A parser reads a statement via ``pdfplumber`` words (text + x0/x1/top/bottom) clustered into
rows (see ``parsers._common``). The anonymizer works on exactly that layer: it captures a
``LayoutBundle`` from the source PDF, rewrites value tokens (same geometry, fake text), and
hands the bundle to parser development. Geometry is the format a parser keys on; keeping it
verbatim is what makes the anonymized sample a faithful stand-in.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber

from cruzar.parsers._common import cluster_rows, row_text


@dataclass(frozen=True)
class Word:
    """One ``pdfplumber`` word: its text and bounding box (points). Anonymization rewrites
    only ``text``, and only to an equal-length fake, so x0/x1 stay valid — geometry is never
    moved."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    words: tuple[Word, ...]


@dataclass(frozen=True)
class LayoutBundle:
    pages: tuple[Page, ...]

    def iter_words(self) -> Iterator[Word]:
        for page in self.pages:
            yield from page.words


def _word(raw: dict[str, Any]) -> Word:
    return Word(
        text=str(raw["text"]),
        x0=float(raw["x0"]),
        x1=float(raw["x1"]),
        top=float(raw["top"]),
        bottom=float(raw["bottom"]),
    )


def extract(pdf_path: str | Path) -> LayoutBundle:
    """Capture the word/geometry layer of ``pdf_path`` as a ``LayoutBundle``."""
    pages: list[Page] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = tuple(_word(w) for w in page.extract_words())
            pages.append(
                Page(width=float(page.width), height=float(page.height), words=words)
            )
    return LayoutBundle(pages=tuple(pages))


def distinct_tokens(bundle: LayoutBundle) -> list[str]:
    """Every distinct word text, in first-seen order (the unit the classifier labels)."""
    seen: dict[str, None] = {}
    for word in bundle.iter_words():
        seen.setdefault(word.text, None)
    return list(seen)


def rows(bundle: LayoutBundle) -> list[str]:
    """The bundle rendered as clustered rows of text — the same row view parsers see, used
    for classifier context and the fidelity row-count check (single source: ``_common``)."""
    lines: list[str] = []
    for page in bundle.pages:
        words: list[dict[str, Any]] = [
            {"text": w.text, "x0": w.x0, "top": w.top} for w in page.words
        ]
        lines.extend(row_text(r) for r in cluster_rows(words))
    return lines


def to_dict(bundle: LayoutBundle) -> dict[str, Any]:
    return {
        "pages": [
            {
                "width": p.width,
                "height": p.height,
                "words": [
                    {"text": w.text, "x0": w.x0, "x1": w.x1, "top": w.top, "bottom": w.bottom}
                    for w in p.words
                ],
            }
            for p in bundle.pages
        ]
    }


def from_dict(data: dict[str, Any]) -> LayoutBundle:
    pages = tuple(
        Page(
            width=float(p["width"]),
            height=float(p["height"]),
            words=tuple(
                Word(
                    text=str(w["text"]),
                    x0=float(w["x0"]),
                    x1=float(w["x1"]),
                    top=float(w["top"]),
                    bottom=float(w["bottom"]),
                )
                for w in p["words"]
            ),
        )
        for p in data["pages"]
    )
    return LayoutBundle(pages=pages)
