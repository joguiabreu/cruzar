"""ActivoBank statement parser (ADR-11).

Reads a text-extractable ActivoBank PDF and returns a ``ParsedStatement`` with
transaction lines in deterministic top-to-bottom order. Amounts are bucketed to
the DEBITO / CREDITO / SALDO columns by x-position; debits are stored negative,
credits positive. A parse failure raises — nothing partial is ever returned
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

# Column boundaries by token-center x (derived from the header positions
# DEBITO~370, CREDITO~445, SALDO~542). Description/amount split at x0 >= 340.
_AMOUNT_X0_MIN = 340.0
_DEBIT_CREDIT_BOUND = 407.0  # center < this and amount => DEBITO
_CREDIT_SALDO_BOUND = 493.0  # center < this => CREDITO, else SALDO
_DATE_X0_MAX = 110.0  # the two date columns sit left of this
_ROW_TOLERANCE = 3.0  # vertical clustering tolerance (points)

_PERIOD_RE = re.compile(r"EXTRATO DE (\d{4}/\d{2}/\d{2}) A (\d{4}/\d{2}/\d{2})")


class ActivoBankParseError(Exception):
    """Raised when the ActivoBank statement layout cannot be parsed."""


def _to_decimal(tokens: list[str]) -> Decimal:
    """Join PT-formatted numeric tokens ('1', '234.56') into Decimal(1234.56)."""
    joined = "".join(tokens).replace(" ", "")
    try:
        return Decimal(joined)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ActivoBankParseError(f"unparseable amount: {tokens!r}") from exc


def _cluster_rows(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
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


def _row_text(row: list[dict[str, Any]]) -> str:
    return " ".join(w["text"] for w in row)


def _infer_year(month: int, period_start: date, period_end: date) -> int:
    """Resolve the year for a M.DD transaction date across a period boundary."""
    if month == period_start.month:
        return period_start.year
    if month == period_end.month:
        return period_end.year
    # Fall back to whichever endpoint's year contains the month.
    return period_start.year if month >= period_start.month else period_end.year


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        period_match = _PERIOD_RE.search(all_text)
        if period_match is None:
            raise ActivoBankParseError("could not locate 'EXTRATO DE ... A ...' period")
        period_start = date.fromisoformat(period_match.group(1).replace("/", "-"))
        period_end = date.fromisoformat(period_match.group(2).replace("/", "-"))

        # The transaction table lives on the page carrying SALDO INICIAL/FINAL.
        rows: list[list[dict[str, Any]]] | None = None
        for page in pdf.pages:
            words = page.extract_words()
            text = " ".join(w["text"] for w in words)
            if "INICIAL" in text and "FINAL" in text:
                rows = _cluster_rows(words)
                break
        if rows is None:
            raise ActivoBankParseError("could not find transaction table page")

    start_idx = end_idx = None
    for i, row in enumerate(rows):
        text = _row_text(row)
        if start_idx is None and "SALDO INICIAL" in text:
            start_idx = i
        elif "SALDO FINAL" in text:
            end_idx = i
            break
    if start_idx is None or end_idx is None or end_idx <= start_idx:
        raise ActivoBankParseError("could not bracket SALDO INICIAL .. SALDO FINAL")

    closing_balance = _column_amount(rows[end_idx], "saldo")
    if closing_balance is None:
        raise ActivoBankParseError("missing SALDO FINAL balance")

    transactions: list[ParsedTransaction] = []
    seq = 0
    for row in rows[start_idx + 1 : end_idx]:
        date_tokens = [w["text"] for w in row if w["x0"] < _DATE_X0_MAX]
        if not date_tokens:
            continue  # not a transaction line (e.g. wrapped text)
        debit = _column_amount(row, "debit")
        credit = _column_amount(row, "credit")
        if debit is None and credit is None:
            continue
        seq += 1
        month_str, day_str = date_tokens[0].split(".")
        month, day = int(month_str), int(day_str)
        year = _infer_year(month, period_start, period_end)
        posting_date = date(year, month, day)
        if credit is not None:
            amount = credit
        else:
            assert debit is not None
            amount = -debit
        description = " ".join(
            w["text"]
            for w in row
            if _DATE_X0_MAX <= w["x0"] < _AMOUNT_X0_MIN
        )
        transactions.append(
            ParsedTransaction(
                intra_statement_seq=seq,
                date=posting_date,
                amount=amount,
                description_raw=description,
            )
        )

    if not transactions:
        raise ActivoBankParseError("no transactions parsed")

    return ParsedStatement(
        currency="EUR",
        period_start=period_start,
        period_end=period_end,
        closing_balance=closing_balance,
        transactions=transactions,
    )


def _column_amount(row: list[dict[str, Any]], column: str) -> Decimal | None:
    """Extract the amount in the given numeric column from a row, or None."""
    tokens: list[str] = []
    for w in row:
        if w["x0"] < _AMOUNT_X0_MIN:
            continue
        center = (w["x0"] + w["x1"]) / 2
        if center < _DEBIT_CREDIT_BOUND:
            bucket = "debit"
        elif center < _CREDIT_SALDO_BOUND:
            bucket = "credit"
        else:
            bucket = "saldo"
        if bucket == column:
            tokens.append(w["text"])
    if not tokens:
        return None
    return _to_decimal(tokens)
