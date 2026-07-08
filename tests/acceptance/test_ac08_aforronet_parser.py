"""AC8 (AforroNet): the Certificado de Aforro parser reproduces its synthetic fixture
exactly — a single-page position snapshot parsed into holdings (one per série, with a
cost_basis derived from units × acquisition unit value), no transactions, and a zero
cash balance (the value lives entirely in the holdings).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.aforronet import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "aforronet"


def _serialize(statement: ParsedStatement) -> dict[str, object]:
    return {
        "currency": statement.currency,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "closing_balance": str(statement.closing_balance),
        "transactions": [
            {
                "intra_statement_seq": t.intra_statement_seq,
                "date": t.date.isoformat(),
                "amount": str(t.amount),
                "description_raw": t.description_raw,
            }
            for t in statement.transactions
        ],
        "holdings": [
            {
                "symbol": h.symbol,
                "quantity": str(h.quantity),
                "cost_basis": str(h.cost_basis) if h.cost_basis is not None else None,
                "value": str(h.value),
                "currency": h.currency,
            }
            for h in statement.holdings
        ],
    }


def test_ac08_aforronet_matches_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    assert _serialize(parse(FIXTURE_DIR / "statement.pdf")) == expected


def test_ac08_aforronet_holdings_shape() -> None:
    statement = parse(FIXTURE_DIR / "statement.pdf")

    # A position snapshot: no cash ledger, value held entirely in the certificates.
    assert statement.transactions == []
    assert statement.closing_balance == Decimal("0.00")
    assert statement.period_start == statement.period_end  # single snapshot date
    assert statement.currency == "EUR"

    # One holding per série, EUR, with cost_basis = units × acquisition unit value
    # (acquisition 1.00000 → cost == units in euros), so Δ-vs-cost is the accrued interest.
    by_symbol = {h.symbol: h for h in statement.holdings}
    e = by_symbol["Certificados de Aforro Série E"]
    assert (e.quantity, e.cost_basis, e.value, e.currency) == (
        Decimal("1000"), Decimal("1000.00"), Decimal("1200.00"), "EUR"
    )
    f = by_symbol["Certificados de Aforro Série F"]
    assert (f.quantity, f.cost_basis, f.value, f.currency) == (
        Decimal("2000"), Decimal("2000.00"), Decimal("2500.00"), "EUR"
    )
