"""AC8 (Moey): the Moey parser has a fixture (synthetic PDF + expected JSON), and
parsing the fixture reproduces it exactly. Also asserts the balance identity and
that the decoy APLICAÇÕES summary after the first SALDO FINAL is ignored.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.moey import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "moey"


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


def test_ac08_moey_parser_has_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert _serialize(statement) == expected


def test_ac08_moey_balance_identity() -> None:
    statement = parse(FIXTURE_DIR / "statement.pdf")
    saldo_inicial = Decimal("5000.00")  # synthetic fixture (generate_fixture.py)
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    assert saldo_inicial + total == statement.closing_balance == Decimal("5840.00")


def test_ac08_moey_ignores_decoy_aplicacoes_summary() -> None:
    # The decoy APLICAÇÕES block after the first SALDO FINAL (a fake date-led row
    # + a second SALDO FINAL of 9999.99) must not leak into the parsed output.
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert len(statement.transactions) == 6
    assert statement.closing_balance == Decimal("5840.00")
    assert all("DECOY" not in t.description_raw for t in statement.transactions)
