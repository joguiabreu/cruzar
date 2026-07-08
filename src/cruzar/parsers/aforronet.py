"""AforroNet (IGCP) parser — Certificado de Aforro statements (ADR-11).

AforroNet exports a single-page "Extrato de Conta Aforro": a position SNAPSHOT of
Portuguese savings certificates at a date, with no cash ledger. It maps onto the
holdings model (ADR-6) like an investment account — one ``ParsedHolding`` per
certificate series, whose current value feeds Net Worth (ADR-16). The statement also
prints each series' acquisition unit value, so ``cost_basis`` (the subscribed amount)
is ``units × acquisition-unit-value`` — giving a Δ-vs-cost equal to the accrued
interest.

Numbers are PT-format (dot thousands, comma decimal: ``1.234,56`` = 1234.56). Amounts
stay native EUR; no FX or LLM math here (ADR-1/ADR-5). A parse failure raises —
nothing partial is ever returned (SPEC §Edge cases: fail loud, write nothing).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from cruzar.models import ParsedHolding, ParsedStatement
from cruzar.parsers._common import ParserError, cluster_rows, row_text

_CENTS = Decimal("0.01")

_DATE_RE = re.compile(r"Data do Extrato:\s*(\d{2})-(\d{2})-(\d{4})")
# A PT number token: dot-grouped thousands, optional comma decimals (or a bare integer).
_PT_NUM_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+(?:,\d+)?$|^-?\d+(?:,\d+)?$")
_ACQ_RE = re.compile(r"Valor Unit[áa]rio Aquisi[çc][ãa]o:\s*([\d.,]+)")
_SERIE_RE = re.compile(r"S[ée]rie\s+\w+")


class AforroNetParseError(ParserError):
    """Raised when the AforroNet statement layout cannot be parsed."""


def _pt_decimal(token: str) -> Decimal:
    """Normalize a PT-format number ('1.234,56' / '1,00000') to a plain Decimal."""
    norm = token.replace(".", "").replace(",", ".")
    try:
        return Decimal(norm)
    except InvalidOperation as exc:
        raise AforroNetParseError(f"unparseable amount: {token!r}") from exc


def _serie_key(text: str) -> str | None:
    """The 'Série X' token that ties a RESUMO product row to its DETALHE block."""
    match = _SERIE_RE.search(text)
    return match.group(0) if match else None


def _acquisition_by_serie(rows: list[list[dict[str, Any]]]) -> dict[str, Decimal]:
    """Each série's acquisition unit value, read from the DETALHE blocks. A block
    opens with a 'CAF / Série X' header and carries a 'Valor Unitário Aquisição' line."""
    acq: dict[str, Decimal] = {}
    current: str | None = None
    for row in rows:
        text = row_text(row)
        if text.startswith("CAF /"):
            current = _serie_key(text)
        match = _ACQ_RE.search(text)
        if match is not None and current is not None:
            acq[current] = _pt_decimal(match.group(1))
    return acq


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        rows: list[list[dict[str, Any]]] = []
        for page in pdf.pages:
            rows += cluster_rows(page.extract_words())

    date_match = _DATE_RE.search(all_text)
    if date_match is None:
        raise AforroNetParseError("could not locate 'Data do Extrato: DD-MM-YYYY'")
    day, month, year = (int(g) for g in date_match.groups())
    snapshot = date(year, month, day)

    acq_by_serie = _acquisition_by_serie(rows)

    holdings: list[ParsedHolding] = []
    in_resumo = False
    for row in rows:
        text = row_text(row)
        if "Produto/S" in text and "Unidades" in text and "Valor" in text:
            in_resumo = True
            continue
        if not in_resumo:
            continue
        if text.startswith("TOTAL"):  # end of the RESUMO product list
            break
        tokens = text.split()
        # The trailing two tokens are units and value; the rest is the product symbol.
        if len(tokens) < 3 or not (_PT_NUM_RE.match(tokens[-1]) and _PT_NUM_RE.match(tokens[-2])):
            continue
        quantity = _pt_decimal(tokens[-2])
        value = _pt_decimal(tokens[-1])
        symbol = " ".join(tokens[:-2]).strip()
        serie = _serie_key(symbol)
        acq = acq_by_serie.get(serie) if serie is not None else None
        cost_basis = (quantity * acq).quantize(_CENTS) if acq is not None else None
        holdings.append(
            ParsedHolding(
                symbol=symbol,
                quantity=quantity,
                cost_basis=cost_basis,
                value=value,
                currency="EUR",
            )
        )

    if not holdings:
        raise AforroNetParseError("no certificate holdings parsed from RESUMO table")

    return ParsedStatement(
        currency="EUR",
        period_start=snapshot,
        period_end=snapshot,
        closing_balance=Decimal("0.00"),  # no uninvested cash; value is in the holdings
        transactions=[],
        holdings=holdings,
    )
