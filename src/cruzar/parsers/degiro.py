"""Degiro parser (ADR-11) — dispatches on document type.

Degiro exports two shapes, both landing in data/inbox/degiro/:

- **Portfolio Overview** — a positions snapshot → ``holdings`` (symbol = ISIN,
  quantity, native market value + its currency; Degiro reports no cost basis, so
  ``cost_basis`` is None) plus the uninvested ``CASH`` line → ``closing_balance``.
- **Account statement** — a chronological cash ledger (newest first) →
  ``transactions`` (signed Change) + ``closing_balance`` (most recent Balance).
  The ``Levantamentos/Depósitos da Conta Caixa`` rows are the flatex-side mirror of
  a Cash Sweep and carry no Change value — they are not ledger movements, so skipped.

Numbers are PT-format: space thousands, comma decimal (``20 123,45``). Amounts stay
native; no FX math here (ADR-1/ADR-5). A parse failure raises — nothing partial.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from cruzar.models import ParsedHolding, ParsedStatement, ParsedTransaction
from cruzar.parsers._common import cluster_rows, row_text

_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9,10}$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")
# A PT number piece: digits/commas, optional leading '-' (space-split thousands
# arrive as separate tokens and are joined).
_NUMPIECE_RE = re.compile(r"^-?[\d,]+$")
_PORTFOLIO_DATE_RE = re.compile(r"Portfolio Overview per (\d{2}-\d{2}-\d{4})")


class DegiroParseError(Exception):
    """Raised when the Degiro statement layout cannot be parsed."""


def _pt_decimal(tokens: list[str]) -> Decimal:
    joined = "".join(tokens).replace(" ", "").replace(",", ".")
    try:
        return Decimal(joined)
    except InvalidOperation as exc:
        raise DegiroParseError(f"unparseable amount: {tokens!r}") from exc


def _pt_date(token: str) -> date:
    day, month, year = token.split("-")
    return date(int(year), int(month), int(day))


def _band(row: list[dict[str, Any]], lo: float, hi: float) -> list[str]:
    """Number-piece tokens whose x0 falls in [lo, hi), left-to-right."""
    return [
        w["text"] for w in row if lo <= w["x0"] < hi and _NUMPIECE_RE.match(w["text"])
    ]


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        rows: list[list[dict[str, Any]]] = []
        for page in pdf.pages:
            rows.extend(cluster_rows(page.extract_words()))

    if "Portfolio Overview" in all_text:
        return _parse_portfolio(all_text, rows)
    if "Account statement" in all_text:
        return _parse_account(rows)
    raise DegiroParseError("unrecognized Degiro document (no Portfolio/Account marker)")


# --- Portfolio Overview: holdings + cash ------------------------------------

# Column x0 bands (from the Portfolio Overview geometry).
_PF_AMOUNT = (400.0, 440.0)
_PF_LOCAL_VALUE = (538.0, 600.0)
_PF_VALUE_EUR = (620.0, 700.0)


def _parse_portfolio(all_text: str, rows: list[list[dict[str, Any]]]) -> ParsedStatement:
    date_match = _PORTFOLIO_DATE_RE.search(all_text)
    if date_match is None:
        raise DegiroParseError("could not locate 'Portfolio Overview per <date>'")
    snapshot = _pt_date(date_match.group(1))

    holdings: list[ParsedHolding] = []
    closing_balance: Decimal | None = None
    for row in rows:
        text = row_text(row)
        if "Total portfolio value" in text:
            break
        if text.startswith("CASH"):  # uninvested cash line
            value = _band(row, *_PF_VALUE_EUR)
            if value:
                closing_balance = _pt_decimal(value)
            continue
        isin = next((w["text"] for w in row if _ISIN_RE.match(w["text"])), None)
        if isin is None:
            continue  # header / chrome / wrapped product name
        amount = _band(row, *_PF_AMOUNT)
        local_value = _band(row, *_PF_LOCAL_VALUE)
        currency = next(
            (w["text"] for w in row if _CCY_RE.match(w["text"]) and 500.0 <= w["x0"] < 545.0),
            None,
        )
        if not amount or not local_value or currency is None:
            raise DegiroParseError(f"incomplete holding row: {text!r}")
        holdings.append(
            ParsedHolding(
                symbol=isin,
                quantity=_pt_decimal(amount),
                cost_basis=None,  # Degiro Portfolio does not report cost basis (D1)
                value=_pt_decimal(local_value),
                currency=currency,
            )
        )

    if closing_balance is None:
        raise DegiroParseError("Portfolio Overview missing the CASH line")
    if not holdings:
        raise DegiroParseError("no holdings parsed")

    return ParsedStatement(
        currency="EUR",
        period_start=snapshot,
        period_end=snapshot,
        closing_balance=closing_balance,
        transactions=[],
        holdings=holdings,
    )


# --- Account statement: cash ledger -----------------------------------------

# Change column (the signed movement) and Balance column, by token x0.
_ACC_CHANGE = (652.0, 705.0)
_ACC_BALANCE = (720.0, 900.0)
_ACC_DESC = (163.0, 636.0)  # Product + ISIN + Description + FX


def _parse_account(rows: list[list[dict[str, Any]]]) -> ParsedStatement:
    transactions: list[ParsedTransaction] = []
    dated_balances: list[tuple[date, Decimal]] = []
    in_region = False
    cont_ok = False  # may the next continuation row merge into the last transaction?
    seq = 0
    for row in rows:
        text = row_text(row)
        if "Change" in text and "Balance" in text:  # column header (per page)
            in_region = True
            cont_ok = False
            continue
        # Footer/legal block. Must be specific: transaction descriptions contain
        # "flatexDEGIRO Bank AG" (the Conta Caixa rows), so key on "Dutch Branch".
        if "flatexDEGIRO Bank Dutch Branch" in text:
            in_region = False
            cont_ok = False
            continue
        if not in_region:
            continue

        first = row[0]
        balance = _band(row, *_ACC_BALANCE)
        if _DATE_RE.match(first["text"]):
            posting = _pt_date(first["text"])
            if balance:
                dated_balances.append((posting, _pt_decimal(balance)))
            change = _band(row, *_ACC_CHANGE)
            if not change:
                # Conta Caixa mirror row (no Change) — not a ledger movement; its own
                # wrapped "AG: <amt> EUR" line must NOT merge into the prior txn.
                cont_ok = False
                continue
            seq += 1
            description = " ".join(
                w["text"] for w in row if _ACC_DESC[0] <= w["x0"] < _ACC_DESC[1]
            )
            transactions.append(
                ParsedTransaction(
                    intra_statement_seq=seq,
                    date=posting,
                    amount=_pt_decimal(change),
                    description_raw=description,
                )
            )
            cont_ok = True
        elif cont_ok and transactions:  # wrapped description continuation (no date)
            extra = " ".join(
                w["text"] for w in row if _ACC_DESC[0] <= w["x0"] < _ACC_DESC[1]
            )
            if extra:
                last = transactions[-1]
                transactions[-1] = ParsedTransaction(
                    last.intra_statement_seq, last.date, last.amount,
                    f"{last.description_raw} {extra}".strip(),
                )

    if not transactions:
        raise DegiroParseError("no transactions parsed")
    if not dated_balances:
        raise DegiroParseError("no running balance found")

    # The ledger is newest-first; closing balance is the most recent date's balance.
    period_end = max(d for d, _ in dated_balances)
    period_start = min(d for d, _ in dated_balances)
    closing_balance = next(bal for d, bal in dated_balances if d == period_end)

    return ParsedStatement(
        currency="EUR",
        period_start=period_start,
        period_end=period_end,
        closing_balance=closing_balance,
        transactions=transactions,
    )
