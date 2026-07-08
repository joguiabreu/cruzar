"""AC8: every parser module has >=1 fixture (redacted PDF + expected JSON), and
parsing the fixture reproduces it exactly. Also asserts the balance identity.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.activobank import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "activobank"


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


def test_ac08_parser_has_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    # A single-month statement is the degenerate one-section case → a 1-element list.
    statements = parse(FIXTURE_DIR / "statement.pdf")
    assert len(statements) == 1
    assert _serialize(statements[0]) == expected


def test_ac08_balance_identity() -> None:
    (statement,) = parse(FIXTURE_DIR / "statement.pdf")
    saldo_inicial = Decimal("1000.00")  # synthetic fixture (generate_fixture.py)
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    assert saldo_inicial + total == statement.closing_balance == Decimal("2450.00")
