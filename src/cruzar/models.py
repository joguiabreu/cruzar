"""Account-agnostic parser output types (ADR-11).

A parser reads a PDF and returns a ``ParsedStatement``; account resolution is
external (by ingestion path, per SPEC §Account resolution), so these carry no
account identity. Money is always ``Decimal`` (never ``float``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ParsedTransaction:
    intra_statement_seq: int  # line ordinal within statement; feeds content_hash
    date: date  # posting date (DATA LANC.)
    amount: Decimal  # signed, native currency (debits negative)
    description_raw: str


@dataclass(frozen=True)
class ParsedStatement:
    currency: str  # ISO 4217
    period_start: date
    period_end: date
    closing_balance: Decimal  # native currency, signed
    transactions: list[ParsedTransaction]
