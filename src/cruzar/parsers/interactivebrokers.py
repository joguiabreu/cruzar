"""Interactive Brokers Activity Statement parser (ADR-11) — first broker w/ holdings.

Reads an IBKR Activity Statement and returns a ``ParsedStatement`` carrying:
- ``holdings`` from the **Open Positions** table (symbol, quantity, cost_basis,
  value, currency) — the broker-reported snapshot at period_end (ADR-6);
- ``closing_balance`` = the Cash Report "Base Currency Summary → Ending Cash"
  (base currency, consolidates all cash incl. FX translation);
- ``transactions`` = empty: this monthly summary statement has no per-trade /
  per-deposit lines (only Cash Report category totals). Granular cash flows need a
  detailed/Flex export (plan_007 D3).

Positions may be denominated in a currency other than the account base (e.g. a USD
stock in an EUR account), so each holding carries its own ``currency``; no FX math
is done here (ADR-1/ADR-5). A parse failure raises — nothing partial.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from cruzar.models import ParsedHolding, ParsedStatement
from cruzar.parsers._common import ParserError, cluster_rows, row_text

# A money/quantity number: comma thousands, dot decimal (e.g. "1,234.56", "-12.34").
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
# "May 1, 2026 - May 31, 2026" (statement period).
_PERIOD_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*-\s*([A-Z][a-z]+ \d{1,2}, \d{4})"
)
_SINGLE_DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")
_BASE_CCY_RE = re.compile(r"Base Currency\s+([A-Z]{3})")
_CCY_RE = re.compile(r"^[A-Z]{3}$")

# Open Positions column bands by token right-edge (x1), from the header geometry.
# Narrow + separated so neighbouring numeric columns (Mult, Cost/Close Price,
# Unrealized P/L) are never picked.
_QTY_BAND = (225.0, 292.0)
_COST_BASIS_BAND = (438.0, 478.0)
_VALUE_BAND = (585.0, 635.0)

_ASSET_HEADERS = {"Stocks", "Forex", "Bonds", "Funds", "Options", "Total"}


class InteractiveBrokersParseError(ParserError):
    """Raised when the IBKR statement layout cannot be parsed."""


def _to_decimal(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise InteractiveBrokersParseError(f"unparseable number: {text!r}") from exc


def _named_date(s: str) -> date:
    return datetime.strptime(s, "%B %d, %Y").date()


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        rows: list[list[dict[str, Any]]] = []
        for page in pdf.pages:
            rows.extend(cluster_rows(page.extract_words()))

    period_start, period_end = _period(all_text)
    base_match = _BASE_CCY_RE.search(all_text)
    if base_match is None:
        raise InteractiveBrokersParseError("could not locate 'Base Currency <CCY>'")
    currency = base_match.group(1)

    holdings = _open_positions(rows)
    closing_balance = _ending_cash(rows)

    return ParsedStatement(
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        closing_balance=closing_balance,
        transactions=[],
        holdings=holdings,
    )


def _period(all_text: str) -> tuple[date, date]:
    match = _PERIOD_RE.search(all_text)
    try:
        if match is not None:
            return _named_date(match.group(1)), _named_date(match.group(2))
        single = _SINGLE_DATE_RE.search(all_text)
        if single is not None:
            d = _named_date(single.group(1))
            return d, d
    except ValueError as exc:
        raise InteractiveBrokersParseError(str(exc)) from exc
    raise InteractiveBrokersParseError("could not locate the statement period")


def _band_number(row: list[dict[str, Any]], band: tuple[float, float]) -> Decimal | None:
    lo, hi = band
    for w in row:
        if lo <= w["x1"] <= hi and _NUM_RE.match(w["text"]):
            return _to_decimal(w["text"])
    return None


def _open_positions(rows: list[list[dict[str, Any]]]) -> list[ParsedHolding]:
    start = next((i for i, r in enumerate(rows) if "Open Positions" in row_text(r)), None)
    if start is None:
        raise InteractiveBrokersParseError("could not find the Open Positions section")
    # The header row carries the Symbol/Quantity/Value columns.
    header = next(
        (i for i in range(start + 1, len(rows))
         if "Quantity" in row_text(rows[i]) and "Value" in row_text(rows[i])),
        None,
    )
    if header is None:
        raise InteractiveBrokersParseError("Open Positions header row not found")

    holdings: list[ParsedHolding] = []
    current_currency: str | None = None
    for row in rows[header + 1:]:
        text = row_text(row)
        if "Financial Instrument Information" in text:
            break
        first = row[0]["text"]
        if len(row) == 1 and _CCY_RE.match(first):  # currency sub-header (EUR / USD)
            current_currency = first
            continue
        if first in _ASSET_HEADERS:  # asset-class header or a subtotal ("Total …")
            continue
        quantity = _band_number(row, _QTY_BAND)
        cost_basis = _band_number(row, _COST_BASIS_BAND)
        value = _band_number(row, _VALUE_BAND)
        if quantity is None or cost_basis is None or value is None:
            continue  # not a position row
        if current_currency is None:
            raise InteractiveBrokersParseError(
                f"position {first!r} before any currency sub-header"
            )
        holdings.append(
            ParsedHolding(
                symbol=first,
                quantity=quantity,
                cost_basis=cost_basis,
                value=value,
                currency=current_currency,
            )
        )

    if not holdings:
        raise InteractiveBrokersParseError("no open positions parsed")
    return holdings


def _ending_cash(rows: list[list[dict[str, Any]]]) -> Decimal:
    summary = next(
        (i for i, r in enumerate(rows) if "Base Currency Summary" in row_text(r)), None
    )
    if summary is None:
        raise InteractiveBrokersParseError("Cash Report 'Base Currency Summary' not found")
    for row in rows[summary + 1:]:
        text = row_text(row)
        if text.startswith("Ending Cash"):  # not "Ending Settled Cash"
            for w in row:
                if _NUM_RE.match(w["text"]):
                    return _to_decimal(w["text"])  # first numeric = Total column
    raise InteractiveBrokersParseError("could not locate 'Ending Cash' (Base Summary)")
