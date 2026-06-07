"""Generate the synthetic Interactive Brokers fixture (statement.pdf + expected.json).

Entirely MADE UP — no real tickers, quantities, or amounts. It mimics an IBKR
Activity Statement closely enough that ``parsers.interactivebroker.parse`` extracts
it identically: a period line, ``Base Currency EUR``, a Cash Report with a
``Base Currency Summary → Ending Cash`` row, an **Open Positions** table with both
an EUR and a USD holding (exercising the per-holding currency column), per-currency
``Total`` subtotals + ``Total in EUR`` (which must be skipped), and a
``Financial Instrument Information`` section that bounds the positions region.

Holdings carry their OWN currency; the EUR one and USD one prove the parser tracks
the currency sub-header. One value is thousands-separated to exercise number parsing.

Regenerate with:  uv run python tests/fixtures/interactivebroker/generate_fixture.py
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent

PERIOD_START = date(2026, 5, 1)
PERIOD_END = date(2026, 5, 31)
BASE_CURRENCY = "EUR"
ENDING_CASH = Decimal("250.00")  # Base Currency Summary → Ending Cash (Total)

# Hand-authored oracle: (symbol, quantity, cost_price, cost_basis, close_price,
# value, currency). cost_price/close_price are rendered for realism but not parsed.
# value/cost_basis are in the holding's OWN currency. Obviously-fake values.
_Holding = tuple[str, Decimal, Decimal, Decimal, Decimal, Decimal, str]
HOLDINGS: list[_Holding] = [
    ("AAAA", Decimal("10"), Decimal("50.00"), Decimal("500.00"),
     Decimal("55.00"), Decimal("550.00"), "EUR"),
    ("BBBB", Decimal("4"), Decimal("300.00"), Decimal("1200.00"),
     Decimal("375.00"), Decimal("1500.00"), "USD"),  # thousands-separated
]

_WIDTH, _HEIGHT = landscape(A4)

# Column right-edges (drawRightString) matched to the real statement geometry so
# the parser's x1 bands pick the right numbers.
X_SYM = 38.0
X_QTY_R = 260.0
X_COSTPRICE_R = 383.0
X_COSTBASIS_R = 459.0
X_CLOSEPRICE_R = 536.0
X_VALUE_R = 620.0


def _fmt(v: Decimal) -> str:
    """Format as '1,234.56' (comma thousands, dot decimal)."""
    whole, frac = f"{v:.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{','.join(groups)}.{frac}"


def build() -> None:
    rl_config.invariant = 1
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=landscape(A4))
    c.setFont("Helvetica", 8)

    def at(top: float, x: float, s: str, *, right: bool = False) -> None:
        y = _HEIGHT - top
        c.drawRightString(x, y, s) if right else c.drawString(x, y, s)

    at(40, X_SYM, "Activity Statement")
    at(55, X_SYM, f"{PERIOD_START:%B %-d, %Y} - {PERIOD_END:%B %-d, %Y}")
    at(75, X_SYM, "Account Information")
    at(90, X_SYM, f"Base Currency {BASE_CURRENCY}")

    # --- Cash Report (must precede Open Positions) ---
    at(120, X_SYM, "Cash Report")
    at(135, X_SYM, "Total Securities Futures Month to Date Year to Date")
    at(150, X_SYM, "Base Currency Summary")
    at(165, X_SYM, "Ending Cash")
    at(165, 420, _fmt(ENDING_CASH), right=True)       # Total (leftmost numeric)
    at(165, 500, _fmt(ENDING_CASH), right=True)       # Securities
    at(165, 560, "0.00", right=True)                  # Futures
    at(180, X_SYM, "Ending Settled Cash")
    at(180, 420, _fmt(ENDING_CASH), right=True)

    # --- Open Positions ---
    at(210, X_SYM, "Open Positions")
    at(225, X_SYM, "Symbol")
    at(225, 230, "Quantity")
    at(225, 294, "Mult")
    at(225, 420, "Cost Basis")
    at(225, 500, "Close Price")
    at(225, 600, "Value")
    at(225, 660, "Unrealized P/L")
    at(225, 735, "Code")

    def position(top: float, h: _Holding) -> None:
        symbol, qty, cost_price, cost_basis, close_price, value, _ccy = h
        at(top, X_SYM, symbol)
        at(top, X_QTY_R, str(qty), right=True)
        at(top, X_COSTPRICE_R, _fmt(cost_price), right=True)
        at(top, X_COSTBASIS_R, _fmt(cost_basis), right=True)
        at(top, X_CLOSEPRICE_R, _fmt(close_price), right=True)
        at(top, X_VALUE_R, _fmt(value), right=True)

    top = 240.0
    at(top, X_SYM, "Stocks")
    for ccy in ("EUR", "USD"):
        top += 15.0
        at(top, X_SYM, ccy)  # currency sub-header
        for h in (x for x in HOLDINGS if x[6] == ccy):
            top += 15.0
            position(top, h)
        top += 15.0  # per-currency subtotal — must be skipped
        at(top, 44, "Total")
        at(top, X_COSTBASIS_R, "0.00", right=True)
        at(top, X_VALUE_R, "0.00", right=True)
    top += 15.0
    at(top, 44, "Total in EUR")  # grand total — must be skipped
    at(top, X_VALUE_R, "0.00", right=True)

    top += 25.0
    at(top, X_SYM, "Financial Instrument Information")  # bounds the positions region
    top += 15.0
    at(top, X_SYM, "Symbol Description Conid Security ID Listing Exch Type")

    c.showPage()
    c.save()

    expected = {
        "currency": BASE_CURRENCY,
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "closing_balance": str(ENDING_CASH),
        "transactions": [],
        "holdings": [
            {
                "symbol": sym,
                "quantity": str(qty),
                "cost_basis": str(cost_basis),
                "value": str(value),
                "currency": ccy,
            }
            for sym, qty, _cp, cost_basis, _clp, value, ccy in HOLDINGS
        ],
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pdf_path.name} and expected.json; ending cash {ENDING_CASH}")


if __name__ == "__main__":
    build()
