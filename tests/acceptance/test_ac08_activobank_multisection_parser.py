"""AC8 (ActivoBank multi-section): a single PDF of several stacked monthly sections
parses into ONE combined ParsedStatement (plan 019). Parsing the synthetic two-section
fixture reproduces its oracle exactly, and the structural claims hold: both sections
captured with continuous seq, both VENCIMENTO credits signed +, the period spans the
first section's start to the last's end, the closing balance is the last section's
SALDO FINAL, and each section's dates resolve against its own month (across a year).
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
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert _serialize(statement) == expected


def test_ac08_activobank_multisection_combines_sections() -> None:
    statement = parse(FIXTURE_DIR / "statement.pdf")

    # Both sections captured into one statement, continuous seq across them (ADR-11).
    assert [t.intra_statement_seq for t in statement.transactions] == [1, 2, 3, 4]
    # Period spans the first section's start to the LAST section's end (D1).
    assert statement.period_start.isoformat() == "2025-12-02"
    assert statement.period_end.isoformat() == "2026-01-30"
    # Closing balance is the last section's SALDO FINAL, not the first's (D1).
    assert statement.closing_balance == Decimal("5700.00")

    # BOTH monthly salaries present and signed positive (the bug dropped section 2's).
    vencimentos = [t for t in statement.transactions if "VENCIMENTO" in t.description_raw]
    assert [t.amount for t in vencimentos] == [Decimal("2000.00"), Decimal("3000.00")]

    # Each section's M.DD dates resolve against ITS OWN period — section 1 is 2025,
    # section 2 is 2026, exact across the year boundary (D4).
    by_seq = {t.intra_statement_seq: t for t in statement.transactions}
    assert by_seq[1].date.year == 2025 and by_seq[3].date.year == 2026

    # Balance identity over the combined statement (saldo_inicial 1000.00).
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    assert Decimal("1000.00") + total == statement.closing_balance
