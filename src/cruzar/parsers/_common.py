"""Shared PDF row helpers for parsers (ADR-11).

Cluster pdfplumber words into deterministic top-to-bottom rows. Every parser
needs this before it can read a statement, so it lives here once — a single fix
applies to all institutions. No institution-specific logic belongs in this file.
"""

from __future__ import annotations

from typing import Any

_ROW_TOLERANCE = 3.0  # vertical clustering tolerance (points)


def cluster_rows(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group words into rows by their top coordinate, sorted top-to-bottom."""
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(word["top"] - rows[-1][0]["top"]) <= _ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return rows


def row_text(row: list[dict[str, Any]]) -> str:
    return " ".join(w["text"] for w in row)
