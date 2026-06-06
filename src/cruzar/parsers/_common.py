"""Shared PDF row helpers for parsers (ADR-11).

Cluster pdfplumber words into deterministic top-to-bottom rows, plus the
Portuguese-month date parsing several PT statements need. Lives here once so a
single fix applies to all institutions. No institution-specific logic belongs in
this file.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_ROW_TOLERANCE = 3.0  # vertical clustering tolerance (points)

_PT_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

# A "<d> de <mês> de <ano>" Portuguese date, e.g. "4 de Maio de 2026".
PT_DATE_RE = re.compile(r"(\d{1,2}) de ([A-Za-zçÇãÃéÉ]+) de (\d{4})")


def parse_pt_month_date(day: str, month_name: str, year: str) -> date:
    """Build a date from PT day/month-name/year parts (case-insensitive month).

    Raises ``ValueError`` on an unknown month name; callers wrap it in their own
    parser error so failures stay loud and institution-scoped.
    """
    month = _PT_MONTHS.get(month_name.lower())
    if month is None:
        raise ValueError(f"unknown Portuguese month: {month_name!r}")
    return date(int(year), month, int(day))


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
