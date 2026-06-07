"""Account-agnostic parser output types (ADR-11).

A parser reads a PDF and returns a ``ParsedStatement``; account resolution is
external (by ingestion path, per SPEC §Account resolution), so these carry no
account identity. Money is always ``Decimal`` (never ``float``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ParsedTransaction:
    intra_statement_seq: int  # line ordinal within statement; feeds content_hash
    date: date  # posting date (DATA LANC.)
    amount: Decimal  # signed, native currency (debits negative)
    description_raw: str


@dataclass(frozen=True)
class ParsedHolding:
    """A broker-reported position at the statement's period_end (ADR-6).

    cost_basis/value are broker-reported aggregates in the holding's OWN native
    currency (e.g. a USD stock in an EUR account) — never computed or converted
    here; conversion to base happens at report time (ADR-5).
    """

    symbol: str
    quantity: Decimal
    cost_basis: Decimal  # broker-reported aggregate, native currency
    value: Decimal  # market value at snapshot_date, native currency
    currency: str  # holding's native currency (ISO 4217)


def _no_holdings() -> list[ParsedHolding]:
    return []


@dataclass(frozen=True)
class ParsedStatement:
    currency: str  # ISO 4217 (the account/cash currency)
    period_start: date
    period_end: date
    closing_balance: Decimal  # native currency, signed
    transactions: list[ParsedTransaction]
    holdings: list[ParsedHolding] = field(default_factory=_no_holdings)  # empty for cash parsers
