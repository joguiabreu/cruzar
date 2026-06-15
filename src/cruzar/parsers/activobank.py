"""ActivoBank statement parser (ADR-11).

Reads a text-extractable ActivoBank PDF and returns a ``ParsedStatement`` with
transaction lines in deterministic top-to-bottom order. Amounts are bucketed to
the DEBITO / CREDITO / SALDO columns by x-position; debits are stored negative,
credits positive. A parse failure raises — nothing partial is ever returned
(SPEC §Edge cases: fail loud, write nothing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.parsers._common import ExtractionFallback, ParserError, cluster_rows, row_text

# AC4a: if fewer than this fraction of candidate transaction rows resolve an amount
# column, the layout is too degraded to trust the structured parse → LLM fallback.
_MIN_COLUMN_RESOLUTION = 0.5

# Column boundaries by token-center x (derived from the header positions
# DEBITO~370, CREDITO~445, SALDO~542). Description/amount split at x0 >= 340.
_AMOUNT_X0_MIN = 340.0
_DEBIT_CREDIT_BOUND = 407.0  # center < this and amount => DEBITO
_CREDIT_SALDO_BOUND = 493.0  # center < this => CREDITO, else SALDO
_DATE_X0_MAX = 110.0  # the two date columns sit left of this

_PERIOD_RE = re.compile(r"EXTRATO DE (\d{4}/\d{2}/\d{2}) A (\d{4}/\d{2}/\d{2})")
# A transaction's posting date in the left columns, e.g. '5.07' / '12.30'. Used to
# tell a real transaction row from a reprinted column header ('LANC.VALOR … DEBITO')
# or OCR-noise row that a multi-page section interleaves (it has no M.DD date token).
_DATE_TOKEN_RE = re.compile(r"^\d{1,2}\.\d{2}$")


class ActivoBankParseError(ParserError):
    """Raised when the ActivoBank statement layout cannot be parsed."""


def _row_date_token(row: list[dict[str, Any]]) -> str | None:
    """The row's posting-date token (M.DD) from the left date columns, or None if the
    row carries none — i.e. it is not a transaction line (a header, SALDO marker, or
    wrapped continuation). Scans for the M.DD shape so a leading OCR-noise token
    (e.g. 'soruE') doesn't shadow the real date."""
    for w in row:
        if w["x0"] < _DATE_X0_MAX and _DATE_TOKEN_RE.match(w["text"]):
            return w["text"]
    return None


def _to_decimal(tokens: list[str]) -> Decimal:
    """Join PT-formatted numeric tokens ('1', '234.56') into Decimal(1234.56)."""
    joined = "".join(tokens).replace(" ", "")
    try:
        return Decimal(joined)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ActivoBankParseError(f"unparseable amount: {tokens!r}") from exc


def _infer_year(month: int, period_start: date, period_end: date) -> int:
    """Resolve the year for a M.DD transaction date against ITS section's period."""
    if month == period_start.month:
        return period_start.year
    if month == period_end.month:
        return period_end.year
    # Fall back to whichever endpoint's year contains the month.
    return period_start.year if month >= period_start.month else period_end.year


@dataclass(frozen=True)
class _Section:
    """One ``SALDO INICIAL … SALDO FINAL`` block within a (possibly multi-month)
    statement, with the period from the ``EXTRATO DE … A …`` line that heads it. A
    single-month statement is the degenerate one-section case (ADR-11)."""

    start_idx: int  # index of the SALDO INICIAL row in the combined row list
    end_idx: int  # index of the SALDO FINAL row
    period_start: date
    period_end: date


def _find_sections(rows: list[list[dict[str, Any]]]) -> list[_Section]:
    """Walk the combined rows pairing each ``SALDO INICIAL`` with the next
    ``SALDO FINAL`` (a section may span a page break), tagging each with the period
    from the most-recent ``EXTRATO DE`` line — so each section's dates resolve
    against its OWN month, exact even across a year boundary (plan 019 D4)."""
    sections: list[_Section] = []
    current_period: tuple[date, date] | None = None
    start_idx: int | None = None
    section_period: tuple[date, date] | None = None
    for i, row in enumerate(rows):
        text = row_text(row)
        match = _PERIOD_RE.search(text)
        if match is not None:
            current_period = (
                date.fromisoformat(match.group(1).replace("/", "-")),
                date.fromisoformat(match.group(2).replace("/", "-")),
            )
        if "SALDO INICIAL" in text:
            start_idx, section_period = i, current_period
        elif "SALDO FINAL" in text and start_idx is not None:
            if section_period is None:
                raise ActivoBankParseError("section has no 'EXTRATO DE ... A ...' period")
            sections.append(_Section(start_idx, i, section_period[0], section_period[1]))
            start_idx, section_period = None, None
    return sections


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        # Cluster rows PER page (tops are page-relative) and concatenate in page order
        # into one sequence — a section can then span a page break (plan 019 D2).
        rows: list[list[dict[str, Any]]] = []
        for page in pdf.pages:
            rows += cluster_rows(page.extract_words())

    sections = _find_sections(rows)
    if not sections:
        raise ActivoBankParseError("could not bracket SALDO INICIAL .. SALDO FINAL")

    # AC4a degradation gate: of the candidate transaction rows (those carrying a
    # date-column token) ACROSS ALL SECTIONS, how many resolve an amount in a
    # DEBITO/CREDITO column? A clean statement resolves ~all; a degenerate layout
    # ~none. <50% (with at least one candidate) means pdfplumber lost the columns →
    # hand the raw text to the LLM extractor instead of raising. One decision over the
    # whole statement, not per section (plan 019 D3). Checked BEFORE closing_balance so
    # a degraded statement falls back rather than tripping a generic "missing SALDO".
    candidates = [
        row
        for sec in sections
        for row in rows[sec.start_idx + 1 : sec.end_idx]
        if _row_date_token(row) is not None
    ]
    if candidates:
        resolved = sum(
            1
            for row in candidates
            if _column_amount(row, "debit") is not None
            or _column_amount(row, "credit") is not None
        )
        if resolved / len(candidates) < _MIN_COLUMN_RESOLUTION:
            raise ExtractionFallback(all_text)

    transactions: list[ParsedTransaction] = []
    seq = 0
    for sec in sections:
        for row in rows[sec.start_idx + 1 : sec.end_idx]:
            date_token = _row_date_token(row)
            if date_token is None:
                continue  # not a transaction line (header, SALDO marker, wrapped text)
            debit = _column_amount(row, "debit")
            credit = _column_amount(row, "credit")
            if debit is None and credit is None:
                continue
            seq += 1
            month_str, day_str = date_token.split(".")
            month, day = int(month_str), int(day_str)
            year = _infer_year(month, sec.period_start, sec.period_end)
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

    # Combined statement (plan 019 D1): period spans the first section's start to the
    # last section's end; the closing balance is the LAST section's SALDO FINAL.
    closing_balance = _column_amount(rows[sections[-1].end_idx], "saldo")
    if closing_balance is None:
        raise ActivoBankParseError("missing SALDO FINAL balance")

    return ParsedStatement(
        currency="EUR",
        period_start=sections[0].period_start,
        period_end=sections[-1].period_end,
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
