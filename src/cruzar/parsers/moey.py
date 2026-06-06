"""Moey statement parser (ADR-11).

Reads a text-extractable Moey ``CONTA MOEY`` PDF and returns a
``ParsedStatement`` with transaction lines in deterministic top-to-bottom order.

Moey rows are parsed by **anchoring from the right** rather than by hard-coded
x-bands: each transaction row ends with ``amount  sign  balance`` where the sign
(``+`` credit / ``-`` debit) is its own token. Amounts are stored signed and
native (debits negative). The Moey PDF prints PT comma-decimals (``1.234,56``);
``_pt_decimal`` normalizes that to ``Decimal`` at the boundary — everything we
emit is plain decimal-point notation.

A parse failure raises ``MoeyParseError`` — nothing partial is ever returned
(SPEC §Edge cases: fail loud, write nothing).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.parsers._common import cluster_rows, parse_pt_month_date, row_text

# A transaction date token: DD-MM-YYYY (DATA LANÇAMENTO / DATA VALOR).
_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
# A PT comma-decimal amount/balance: optional dot thousands, comma + 2 decimals.
# The comma is what distinguishes an amount from a reference number (which has
# none), so reference digits are never mistaken for the amount.
_AMOUNT_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$")
_SIGN_TOKENS = {"+", "-"}

# "... 4 de Maio de 2026 ... 29 de Maio de 2026" — first/last day-month-year.
_PERIOD_RE = re.compile(
    r"(\d{1,2})\s+de\s+([A-Za-zçÇãÃéÉ]+)\s+de\s+(\d{4})"
    r".*?(\d{1,2})\s+de\s+([A-Za-zçÇãÃéÉ]+)\s+de\s+(\d{4})",
    re.DOTALL,
)
_CURRENCY_RE = re.compile(r"Extracto em ([A-Z]{3})")


class MoeyParseError(Exception):
    """Raised when the Moey statement layout cannot be parsed."""


def _pt_decimal(raw: str) -> Decimal:
    """Convert a PT comma-decimal string ('1.234,56') to Decimal('1234.56')."""
    normalized = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise MoeyParseError(f"unparseable amount: {raw!r}") from exc


def _pt_date(token: str) -> date:
    day, month, year = token.split("-")
    return date(int(year), int(month), int(day))


def _period(all_text: str) -> tuple[date, date]:
    match = _PERIOD_RE.search(all_text)
    if match is None:
        raise MoeyParseError("could not locate '<d> de <Month> de <year>' period")
    try:
        start = parse_pt_month_date(match.group(1), match.group(2), match.group(3))
        end = parse_pt_month_date(match.group(4), match.group(5), match.group(6))
    except ValueError as exc:
        raise MoeyParseError(str(exc)) from exc
    return start, end


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        rows: list[list[dict[str, Any]]] = []
        for page in pdf.pages:
            rows.extend(cluster_rows(page.extract_words()))

    period_start, period_end = _period(all_text)
    currency_match = _CURRENCY_RE.search(all_text)
    if currency_match is None:
        raise MoeyParseError("could not locate 'Extracto em <CCY>' currency")
    currency = currency_match.group(1)

    # The CONTA MOEY transaction region runs from the first column header (the
    # row carrying MOVIMENTOS) to the first SALDO FINAL — which precedes the
    # APLICAÇÕES summary and its own decoy SALDO FINAL.
    header_idx = next(
        (i for i, row in enumerate(rows) if "MOVIMENTOS" in row_text(row)),
        None,
    )
    if header_idx is None:
        raise MoeyParseError("could not find the MOVIMENTOS column header")
    end_idx = next(
        (i for i in range(header_idx + 1, len(rows)) if "SALDO FINAL" in row_text(rows[i])),
        None,
    )
    if end_idx is None:
        raise MoeyParseError("could not locate SALDO FINAL closing the CONTA MOEY section")

    closing_tokens = _row_amount_tokens(rows[end_idx])
    if not closing_tokens:
        raise MoeyParseError("missing SALDO FINAL balance")
    closing = _pt_decimal(closing_tokens[-1]["text"])

    transactions: list[ParsedTransaction] = []
    seq = 0
    for row in rows[header_idx + 1 : end_idx]:
        first = row[0]["text"]
        if _DATE_RE.match(first):
            seq += 1
            transactions.append(_transaction(row, seq))
        elif first == "DATA":
            continue  # repeated per-page column header
        elif transactions:
            # Continuation of a wrapped description (no date/amount): one logical
            # line (ADR-11) — seq does not advance.
            transactions[-1] = _append_continuation(transactions[-1], row)

    if not transactions:
        raise MoeyParseError("no transactions parsed")

    return ParsedStatement(
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        closing_balance=closing,
        transactions=transactions,
    )


def _row_amount_tokens(row: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [w for w in row if _AMOUNT_RE.match(w["text"])]


def _transaction(row: list[dict[str, Any]], seq: int) -> ParsedTransaction:
    """Parse a date-led row, anchoring amount/sign/balance from the right."""
    dates = [w for w in row if _DATE_RE.match(w["text"])]
    if len(dates) < 2:
        raise MoeyParseError(f"row missing the two date columns: {row_text(row)!r}")
    posting_date = _pt_date(dates[0]["text"])
    second_date = dates[1]

    signs = [w for w in row if w["text"] in _SIGN_TOKENS]
    if not signs:
        raise MoeyParseError(f"row missing +/- sign token: {row_text(row)!r}")
    sign = signs[-1]
    amounts = _row_amount_tokens(row)
    balance_tokens = [w for w in amounts if w["x0"] > sign["x0"]]
    amount_tokens = [w for w in amounts if w["x0"] < sign["x0"]]
    if not balance_tokens or not amount_tokens:
        raise MoeyParseError(f"row missing amount/balance around sign: {row_text(row)!r}")
    amount_word = max(amount_tokens, key=lambda w: w["x0"])

    magnitude = _pt_decimal(amount_word["text"])
    amount = magnitude if sign["text"] == "+" else -magnitude

    description = " ".join(
        w["text"]
        for w in row
        if w["x0"] > second_date["x0"] and w["x0"] < amount_word["x0"]
    )
    return ParsedTransaction(
        intra_statement_seq=seq,
        date=posting_date,
        amount=amount,
        description_raw=description,
    )


def _append_continuation(
    transaction: ParsedTransaction, row: list[dict[str, Any]]
) -> ParsedTransaction:
    extra = row_text(row)
    merged = f"{transaction.description_raw} {extra}".strip()
    return ParsedTransaction(
        intra_statement_seq=transaction.intra_statement_seq,
        date=transaction.date,
        amount=transaction.amount,
        description_raw=merged,
    )
