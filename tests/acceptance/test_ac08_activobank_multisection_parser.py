"""AC8 (ActivoBank multi-section): a single PDF of several stacked monthly sections
parses into ONE ParsedStatement PER section (plan 023). Parsing the synthetic
two-section fixture reproduces its oracle exactly, and the structural claims hold: each
section is its own statement with its own period, its own SALDO FINAL as closing
balance, seq reset to 1..n, both VENCIMENTO credits signed +, and dates resolved against
that section's own month (across a year boundary).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.activobank import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "activobank_multisection"


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
    }


def test_ac08_activobank_multisection_has_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    statements = parse(FIXTURE_DIR / "statement.pdf")
    assert [_serialize(s) for s in statements] == expected


def test_ac08_activobank_multisection_one_statement_per_section() -> None:
    statements = parse(FIXTURE_DIR / "statement.pdf")

    # One statement per monthly section (plan 023), in document order.
    assert len(statements) == 2
    dec, jan = statements

    # Each section keeps its OWN period, SALDO FINAL, and seq reset to 1..n.
    assert dec.period_start.isoformat() == "2025-12-02"
    assert dec.period_end.isoformat() == "2025-12-30"
    assert dec.closing_balance == Decimal("2900.00")
    assert [t.intra_statement_seq for t in dec.transactions] == [1, 2]
    assert jan.period_start.isoformat() == "2026-01-02"
    assert jan.period_end.isoformat() == "2026-01-30"
    assert jan.closing_balance == Decimal("5700.00")
    assert [t.intra_statement_seq for t in jan.transactions] == [1, 2]

    # BOTH monthly salaries present and signed positive (the 019 bug dropped section 2's).
    assert dec.transactions[0].amount == Decimal("2000.00")
    assert jan.transactions[0].amount == Decimal("3000.00")
    assert "VENCIMENTO" in dec.transactions[0].description_raw
    assert "VENCIMENTO" in jan.transactions[0].description_raw

    # Each section's M.DD dates resolve against ITS OWN period across the year boundary.
    assert dec.transactions[0].date.year == 2025
    assert jan.transactions[0].date.year == 2026

    # Per-section balance identity: section 1 opens at 1000.00, section 2 at its close.
    assert Decimal("1000.00") + sum((t.amount for t in dec.transactions), Decimal("0")) == dec.closing_balance
    assert dec.closing_balance + sum((t.amount for t in jan.transactions), Decimal("0")) == jan.closing_balance
