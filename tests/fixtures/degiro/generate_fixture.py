"""Generate the synthetic Degiro fixtures: portfolio.pdf + account.pdf (+ oracles).

Entirely MADE UP — fake ISIN, amounts, dates. Degiro exports two document shapes
and the parser dispatches between them, so there are two fixtures:

- ``portfolio.pdf`` (Portfolio Overview) → holdings + cash. Exercises: ISIN as
  symbol, native value with its currency, **cost_basis = null** (Degiro reports
  none), a thousands-separated value, and the CASH line as closing_balance.
- ``account.pdf`` (Account statement, newest-first) → transactions + closing
  balance. Exercises: a credit (+), a thousands-separated deposit, a **Conta Caixa
  mirror row with no Change** (skipped, and its ``AG: …`` continuation must not
  merge into the prior txn), and a buy (−) with a wrapped continuation.

PT numbers: space thousands, comma decimal.

Regenerate with:  uv run python tests/fixtures/degiro/generate_fixture.py
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
_WIDTH, _HEIGHT = landscape(A4)
FAKE_ISIN = "IE00TESTFAKE"


def _fmt_pt(v: Decimal) -> str:
    """Decimal -> '1 500,00' (space thousands, comma decimal, signed)."""
    neg = v < 0
    whole, frac = f"{abs(v):.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    s = f"{' '.join(groups)},{frac}"
    return f"-{s}" if neg else s


def _canvas(name: str) -> canvas.Canvas:
    c = canvas.Canvas(str(HERE / name), pagesize=landscape(A4))
    c.setFont("Helvetica", 8)
    return c


def _at(c: canvas.Canvas, top: float, x: float, s: str, *, right: bool = False) -> None:
    y = _HEIGHT - top
    c.drawRightString(x, y, s) if right else c.drawString(x, y, s)


# --- Portfolio Overview ------------------------------------------------------

PORTFOLIO_DATE = date(2026, 5, 31)
PORTFOLIO_CASH = Decimal("100.00")
# (isin, quantity, value_native, currency)
PORTFOLIO_HOLDINGS = [(FAKE_ISIN, Decimal("30"), Decimal("1500.00"), "EUR")]


def build_portfolio() -> None:
    c = _canvas("portfolio.pdf")
    _at(c, 50, 38, f"Portfolio Overview per {PORTFOLIO_DATE:%d-%m-%Y}")
    # header (not used by the parser, included for realism)
    _at(c, 80, 135, "Product")
    _at(c, 80, 285, "Symbol/ISIN")
    _at(c, 80, 430, "Amount", right=True)
    _at(c, 80, 496, "Closing", right=True)
    _at(c, 80, 558, "Local value", right=True)
    _at(c, 80, 670, "Value in EUR", right=True)

    top = 100.0
    # CASH line -> closing_balance
    _at(c, top, 135, "CASH & CASH FUND & FTX CASH (EUR)")
    _at(c, top, 515, "EUR")
    _at(c, top, 571, _fmt_pt(PORTFOLIO_CASH), right=True)
    _at(c, top, 670, _fmt_pt(PORTFOLIO_CASH), right=True)
    for isin, qty, value, ccy in PORTFOLIO_HOLDINGS:
        top += 15.0
        _at(c, top, 135, "AAAA FUND")
        _at(c, top, 285, isin)
        _at(c, top, 430, str(qty), right=True)
        _at(c, top, 496, "50,00", right=True)        # closing price (unused)
        _at(c, top, 515, ccy)                         # closing currency
        _at(c, top, 571, _fmt_pt(value), right=True)  # local value (native)
        _at(c, top, 670, _fmt_pt(value), right=True)  # value in EUR (unused)
    top += 20.0
    _at(c, top, 135, "Total portfolio value")
    _at(c, top, 639, "EUR")
    _at(c, top, 670, _fmt_pt(PORTFOLIO_CASH + sum(h[2] for h in PORTFOLIO_HOLDINGS)), right=True)
    c.showPage()
    c.save()

    expected = {
        "currency": "EUR",
        "period_start": PORTFOLIO_DATE.isoformat(),
        "period_end": PORTFOLIO_DATE.isoformat(),
        "closing_balance": str(PORTFOLIO_CASH),
        "transactions": [],
        "holdings": [
            {
                "symbol": isin,
                "quantity": str(qty),
                "cost_basis": None,
                "value": str(value),
                "currency": ccy,
            }
            for isin, qty, value, ccy in PORTFOLIO_HOLDINGS
        ],
    }
    (HERE / "expected_portfolio.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )


# --- Account statement (newest first) ---------------------------------------

def _acct_row(c: canvas.Canvas, top: float, posting: date, desc: str,
              change: Decimal | None, balance: Decimal, *, product: str = "",
              isin: str = "") -> None:
    _at(c, top, 35, f"{posting:%d-%m-%Y}")
    if product:
        _at(c, top, 163, product)
    if isin:
        _at(c, top, 329, isin)
    _at(c, top, 403, desc)
    if change is not None:
        _at(c, top, 636, "EUR")
        _at(c, top, 696, _fmt_pt(change), right=True)
    _at(c, top, 705, "EUR")
    _at(c, top, 775, _fmt_pt(balance), right=True)


def build_account() -> None:
    c = _canvas("account.pdf")
    _at(c, 50, 35, "Account statement")
    _at(c, 80, 35, "Date Time Value date Product ISIN Description FX Change Balance")
    top = 100.0
    # newest first
    _acct_row(c, top, date(2026, 5, 5), "Flatex Interest Income", Decimal("0.50"), Decimal("100.50"))
    top += 15
    _acct_row(c, top, date(2026, 5, 4), "flatex Deposit", Decimal("1000.00"), Decimal("100.00"))
    top += 15
    # Conta Caixa mirror: dated, NO Change -> skipped; its AG continuation must not leak
    _acct_row(c, top, date(2026, 5, 3),
              "Levantamentos da sua Conta Caixa na flatexDEGIRO Bank", None, Decimal("50.00"))
    top += 11
    _at(c, top, 403, "AG: 1 000,00 EUR")
    top += 15
    _acct_row(c, top, date(2026, 5, 2), "Compra", Decimal("-500.00"), Decimal("50.00"),
              product="VANGUARD FTSE ALL-WORLD UCITS ETF", isin=FAKE_ISIN)
    top += 11
    _at(c, top, 403, f"Acc 50,00 EUR ({FAKE_ISIN})")  # wrapped continuation -> merges into the buy
    top += 40
    _at(c, top, 35,
        "flatexDEGIRO Bank Dutch Branch, trading under the name DEGIRO, is the Dutch branch.")
    c.showPage()
    c.save()

    expected = {
        "currency": "EUR",
        "period_start": "2026-05-02",
        "period_end": "2026-05-05",
        "closing_balance": "100.50",
        "transactions": [
            {"intra_statement_seq": 1, "date": "2026-05-05", "amount": "0.50",
             "description_raw": "Flatex Interest Income"},
            {"intra_statement_seq": 2, "date": "2026-05-04", "amount": "1000.00",
             "description_raw": "flatex Deposit"},
            {"intra_statement_seq": 3, "date": "2026-05-02", "amount": "-500.00",
             "description_raw":
                 f"VANGUARD FTSE ALL-WORLD UCITS ETF {FAKE_ISIN} Compra "
                 f"Acc 50,00 EUR ({FAKE_ISIN})"},
        ],
        "holdings": [],
    }
    (HERE / "expected_account.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )


def build() -> None:
    rl_config.invariant = 1
    build_portfolio()
    build_account()
    print("wrote portfolio.pdf + account.pdf and their expected_*.json")


if __name__ == "__main__":
    build()
