"""AC8 (Interactive Brokers): the IBKR parser has a fixture (synthetic PDF +
expected JSON), and parsing it reproduces the result exactly — including the
``holdings`` list (EUR + USD positions, each with its own currency) and the cash
``closing_balance``. The per-currency ``Total`` subtotals and ``Total in EUR`` row
must be excluded.
"""

from __future__ import annotations

import json
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.interactivebroker import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "interactivebroker"


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
                "cost_basis": str(h.cost_basis),
                "value": str(h.value),
                "currency": h.currency,
            }
            for h in statement.holdings
        ],
    }


def test_ac08_interactivebroker_parser_has_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    statement = parse(FIXTURE_DIR / "statement.pdf")
    assert _serialize(statement) == expected


def test_ac08_interactivebroker_holdings_currencies() -> None:
    # Two positions, distinct native currencies; subtotal/"Total in EUR" rows excluded.
    statement = parse(FIXTURE_DIR / "statement.pdf")
    by_symbol = {h.symbol: h for h in statement.holdings}
    assert set(by_symbol) == {"AAAA", "BBBB"}
    assert by_symbol["AAAA"].currency == "EUR"
    assert by_symbol["BBBB"].currency == "USD"
    assert all("Total" not in h.symbol for h in statement.holdings)
