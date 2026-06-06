"""Revolut statement parser (ADR-11).

Reads a Revolut EUR account export and returns a single ``ParsedStatement`` with
transaction lines in deterministic top-to-bottom order across the whole file.

A real export is harder than a monthly statement (see plan_004):

- It is a multi-year *combined* file with several stacked statement sections,
  each preceded by a ``Resumo do saldo`` summary block (which we skip).
- It mixes **two layouts** as Revolut's format drifted: an "old" Revolut Ltd
  E-Money layout (single ``Data`` column, ``Saldo``) and a "new" Revolut Bank UAB
  Conta Corrente layout (``Data Lançamento`` + ``Data-Valor`` columns, ``Saldo
  contabilístico``). Column x-positions differ between them, so each page's
  columns are derived from its own header row rather than hardcoded.

The whole file is parsed as ONE statement (period = first section start … last
section end; ``closing_balance`` = the Saldo of the last transaction row).
Amounts are ``€1,234.56`` (dot decimal); the retirado/recebido column gives the
sign (retirado = debit/negative, recebido = credit/positive). Amounts stay native
EUR — the foreign leg of a currency conversion is kept only as description text,
never converted here (ADR-1, ADR-5).

A parse failure raises ``RevolutParseError`` — nothing partial (SPEC §Edge cases).
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
from cruzar.parsers._common import PT_DATE_RE, cluster_rows, parse_pt_month_date, row_text

# A transaction date token: DD/MM/YYYY (pt-pt).
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
# A money amount: € prefix, comma thousands, dot decimal (e.g. "€1,234.56").
# Matched as a *substring* (not anchored): a long description can abut the amount
# with no space, so pdfplumber emits a glued token like "48€10.00" — the digits
# before "€" belong to the description and the amount still right-aligns to its
# column, so we anchor on the right edge (x1).
_AMOUNT_SUB = re.compile(r"€[\d,]+\.\d{2}")
_DATE_X0_MAX = 100.0  # transaction date columns sit left of this
_CURRENCY_RE = re.compile(r"Extrato de ([A-Z]{3})")

# Row text that marks the end of a transaction table body: a page footer or the
# between-section summary/section markers. Seeing any of these exits the body so
# their amount-shaped tokens are never mistaken for transactions or merged into a
# description.
_EXIT_MARKERS = (
    "Comunicar perda",
    "Resumo do saldo",
    "Operações da conta",
    "Onde as suas",
    "Página",
    "Revertido",  # "Revertido de … para …" precedes a reverted/pending sub-table
)


class RevolutParseError(Exception):
    """Raised when the Revolut statement layout cannot be parsed."""


@dataclass(frozen=True)
class _Columns:
    """Per-page column anchors derived from the transaction header row."""

    desc_x0: float
    retirado_x0: float  # left edge of the amount columns / debit anchor
    recebido_x0: float  # credit anchor
    header_bottom: float  # max top of header words; body starts below this


def _eur_decimal(token: str) -> Decimal:
    """Convert a '€1,234.56' token to Decimal('1234.56')."""
    try:
        return Decimal(token.removeprefix("€").replace(",", ""))
    except InvalidOperation as exc:
        raise RevolutParseError(f"unparseable amount: {token!r}") from exc


def _period(all_text: str) -> tuple[date, date]:
    matches = PT_DATE_RE.findall(all_text)
    if not matches:
        raise RevolutParseError("could not locate any '<d> de <mês> de <ano>' date")
    try:
        start = parse_pt_month_date(*matches[0])
        end = parse_pt_month_date(*matches[-1])
    except ValueError as exc:
        raise RevolutParseError(str(exc)) from exc
    return start, end


def _header_columns(band: list[dict[str, Any]], desc_word: dict[str, Any]) -> _Columns:
    """Derive column anchors from the header band around a 'Descrição' token."""
    dinheiro_xs = sorted(w["x0"] for w in band if w["text"] == "Dinheiro")
    if len(dinheiro_xs) < 2:
        raise RevolutParseError("header missing the two 'Dinheiro' amount columns")
    return _Columns(
        desc_x0=desc_word["x0"],
        retirado_x0=dinheiro_xs[0],
        recebido_x0=dinheiro_xs[1],
        header_bottom=max(w["top"] for w in band),
    )


def parse(pdf_path: str | Path) -> ParsedStatement:
    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        pages_words = [page.extract_words() for page in pdf.pages]

    period_start, period_end = _period(all_text)
    currency_match = _CURRENCY_RE.search(all_text)
    if currency_match is None:
        raise RevolutParseError("could not locate 'Extrato de <CCY>' currency")
    currency = currency_match.group(1)

    transactions: list[ParsedTransaction] = []
    last_balance: Decimal | None = None
    seq = 0

    for words in pages_words:
        columns: _Columns | None = None  # set when we enter a table body
        cont_idx: int | None = None  # transaction open for continuation merges
        for row in cluster_rows(words):
            text = row_text(row)
            first = row[0]

            if "Descrição" in text:  # a table header → maybe (re)enter body
                desc_word = next(w for w in row if w["text"] == "Descrição")
                # The "new" layout splits its header across ~±7pt of sub-rows;
                # keep the band tight so the first transaction (≥16pt below) is
                # not pulled in and mistaken for header.
                band = [w for w in words if abs(w["top"] - desc_word["top"]) <= 11.0]
                # Only the account-operations table carries a Saldo (running
                # balance) column. Auxiliary tables ("Revertido …" / "Data de
                # início" — reverted/pending entries with no balance) lack it; they
                # are not ledger transactions, so do not enter their body.
                cont_idx = None
                if any(w["text"] == "Saldo" for w in band):
                    columns = _header_columns(band, desc_word)
                else:
                    columns = None
                continue
            if any(marker in text for marker in _EXIT_MARKERS):
                columns = None  # leave the body; following summary/footer ignored
                cont_idx = None
                continue
            if columns is None or first["top"] <= columns.header_bottom:
                continue  # outside any table body (page chrome, summary, footer)

            if _DATE_RE.match(first["text"]) and first["x0"] < _DATE_X0_MAX:
                seq += 1
                txn, last_balance = _transaction(row, columns, seq)
                transactions.append(txn)
                cont_idx = len(transactions) - 1
            elif cont_idx is not None:
                # Continuation of a wrapped/detail line (no date): one logical
                # line (ADR-11) — seq does not advance. Covers De:/Para:/Cartão:/
                # Referência:, wrapped names, and FX notes.
                transactions[cont_idx] = _append_continuation(transactions[cont_idx], row)

    if not transactions:
        raise RevolutParseError("no transactions parsed")
    assert last_balance is not None

    return ParsedStatement(
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        closing_balance=last_balance,
        transactions=transactions,
    )


def _transaction(
    row: list[dict[str, Any]], columns: _Columns, seq: int
) -> tuple[ParsedTransaction, Decimal]:
    """Parse a date-led row; return the transaction and its running balance."""
    date_words = [w for w in row if _DATE_RE.match(w["text"])]
    posting_date = _parse_date(min(date_words, key=lambda w: w["x0"])["text"])

    # Amount-bearing words (a token may carry glued description text on its left).
    amounts = [(w, m) for w in row if (m := _AMOUNT_SUB.search(w["text"]))]
    if len(amounts) < 2:
        raise RevolutParseError(
            f"expected movement + balance amounts, got {len(amounts)}: {row_text(row)!r}"
        )
    amounts.sort(key=lambda pair: pair[0]["x1"])  # right edge: balance is rightmost
    movement_word, movement_match = amounts[-2]
    balance_word, balance_match = amounts[-1]

    magnitude = _eur_decimal(movement_match.group())
    # Credit sits in the recebido column (right of its left edge); debit in retirado.
    is_credit = movement_word["x1"] > columns.recebido_x0
    amount = magnitude if is_credit else -magnitude
    balance = _eur_decimal(balance_match.group())

    # Description: plain tokens in the description band, plus any text glued around
    # the movement amount (e.g. the "48" in "48€10.00"). Earlier €-tokens, if any,
    # stay in the band and are kept verbatim.
    parts: list[str] = []
    for w in row:
        if w is balance_word:
            continue
        if w is movement_word:
            text = w["text"]
            glued = text[: movement_match.start()] + " " + text[movement_match.end():]
            glued = glued.strip()
            if glued:
                parts.append(glued)
            continue
        if columns.desc_x0 - 1.0 <= w["x0"] < columns.retirado_x0:
            parts.append(w["text"])
    description = " ".join(parts)
    return (
        ParsedTransaction(
            intra_statement_seq=seq,
            date=posting_date,
            amount=amount,
            description_raw=description,
        ),
        balance,
    )


def _parse_date(token: str) -> date:
    day, month, year = token.split("/")
    return date(int(year), int(month), int(day))


def _append_continuation(
    transaction: ParsedTransaction, row: list[dict[str, Any]]
) -> ParsedTransaction:
    merged = f"{transaction.description_raw} {row_text(row)}".strip()
    return ParsedTransaction(
        intra_statement_seq=transaction.intra_statement_seq,
        date=transaction.date,
        amount=transaction.amount,
        description_raw=merged,
    )
