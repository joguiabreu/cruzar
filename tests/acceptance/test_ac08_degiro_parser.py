"""AC8 (Degiro): the parser dispatches on document type and reproduces both
fixtures exactly — the Portfolio Overview (holdings + cash; cost_basis null) and
the Account statement (transactions + closing balance; Conta Caixa mirror skipped).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cruzar.models import ParsedStatement
from cruzar.parsers.degiro import parse

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "degiro"


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


def test_ac08_degiro_portfolio_matches_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected_portfolio.json").read_text(encoding="utf-8"))
    assert _serialize(parse(FIXTURE_DIR / "portfolio.pdf")) == expected


def test_ac08_degiro_account_matches_fixture() -> None:
    expected = json.loads((FIXTURE_DIR / "expected_account.json").read_text(encoding="utf-8"))
    assert _serialize(parse(FIXTURE_DIR / "account.pdf")) == expected


def test_ac08_degiro_portfolio_holdings_have_no_cost_basis() -> None:
    statement = parse(FIXTURE_DIR / "portfolio.pdf")
    assert statement.holdings, "expected at least one holding"
    assert all(h.cost_basis is None for h in statement.holdings)  # Degiro reports none (D1)
    assert all(h.symbol.startswith("IE") for h in statement.holdings)  # symbol = ISIN


def test_ac08_degiro_account_skips_conta_caixa_mirror() -> None:
    # The no-Change Conta Caixa row is excluded, and its "AG: …" continuation does
    # not leak into the previous transaction; closing balance is the most recent.
    statement = parse(FIXTURE_DIR / "account.pdf")
    assert len(statement.transactions) == 3
    assert all("AG:" not in t.description_raw for t in statement.transactions)
    assert all("Conta Caixa" not in t.description_raw for t in statement.transactions)
    assert statement.closing_balance == Decimal("100.50")
