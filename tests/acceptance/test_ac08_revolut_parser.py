"""AC8 (Revolut): the Revolut parser has a fixture (synthetic PDF + expected
JSON), and parsing the fixture reproduces it exactly. Also asserts the balance
identity and that the intervening Resumo do saldo summary and the Revertido /
Data de início reverted sub-table (no Saldo column) are ignored.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.revolut import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "revolut"


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


def test_ac08_revolut_parser_has_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert _serialize(statement) == expected


def test_ac08_revolut_balance_identity() -> None:
    statement = parse(FIXTURE_DIR / "statement.pdf")
    saldo_inicial = Decimal("1000.00")  # synthetic fixture (generate_fixture.py)
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    assert saldo_inicial + total == statement.closing_balance == Decimal("2535.00")


def test_ac08_revolut_ignores_summary_and_reverted_tables() -> None:
    # Five ledger transactions only: the Resumo do saldo summary rows and the
    # Revertido / Data de início reverted sub-table (€77.00, no Saldo column) must
    # not leak in, and no decoy summary figure should appear as a balance.
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert len(statement.transactions) == 5
    assert all("9999" not in t.description_raw for t in statement.transactions)
    assert all(t.amount != Decimal("77.00") for t in statement.transactions)
    assert [t.intra_statement_seq for t in statement.transactions] == [1, 2, 3, 4, 5]


def test_ac08_revolut_handles_both_layouts() -> None:
    # The old (single-date) and new (Data Lançamento + Data-Valor) layouts both
    # parse: a credit top-up, debit fees, and the thousands-separated transfer.
    statement = parse(FIXTURE_DIR / "statement.pdf")
    by_seq = {t.intra_statement_seq: t for t in statement.transactions}
    assert by_seq[1].amount == Decimal("100.00")  # old-layout credit
    assert by_seq[4].amount == Decimal("1500.00")  # new-layout credit, thousands sep
    assert by_seq[5].amount == Decimal("-10.00")  # new-layout debit
