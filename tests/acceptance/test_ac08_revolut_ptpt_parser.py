"""AC8 (Revolut pt-pt): the Revolut parser also handles the Portuguese-locale amount
format — '1.234,56€' (dot thousands, comma decimal, € suffix), as opposed to the
'€1,234.56' the main fixture uses. Parsing the synthetic fixture reproduces its oracle
exactly, including the comma-decimal → Decimal normalization and a wrapped description.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.revolut import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "revolut_ptpt"


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


def test_ac08_revolut_ptpt_parser_has_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert _serialize(statement) == expected


def test_ac08_revolut_ptpt_amounts_and_signs() -> None:
    statement = parse(FIXTURE_DIR / "statement.pdf")
    by_seq = {t.intra_statement_seq: t for t in statement.transactions}
    # comma-decimal magnitudes parsed; retirado/recebido column gives the sign
    assert by_seq[1].amount == Decimal("-10.00")  # '10,00€' debit
    assert by_seq[2].amount == Decimal("2000.00")  # '2.000,00€' credit (thousands sep)
    assert by_seq[4].amount == Decimal("-1234.56")  # '1.234,56€' debit (thousands sep)
    # wrapped description merged into one logical transaction (ADR-11)
    assert by_seq[2].description_raw == "Carregamento com Google Pay através de Example"
    # balance identity (saldo_inicial 1000.00 from generate_fixture.py)
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    assert Decimal("1000.00") + total == statement.closing_balance == Decimal("1749.94")
