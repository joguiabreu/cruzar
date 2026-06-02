"""Generate the synthetic ActivoBank test fixture (statement.pdf + expected.json).

The data here is entirely MADE UP — no real payee names, account numbers, or
amounts. It mimics the ActivoBank statement layout (word x-positions, PT number
formatting, SALDO INICIAL/FINAL bracketing, the EXTRATO DE … A … period line)
closely enough that ``parsers.activobank.parse`` extracts it identically to a
real statement, so AC8 stays a faithful test without committing private data.

Regenerate with:  uv run python tests/fixtures/activobank/generate_fixture.py
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent

PERIOD_START = date(2026, 5, 4)
PERIOD_END = date(2026, 5, 29)
SALDO_INICIAL = Decimal("1000.00")

# Hand-authored oracle (CLAUDE.md testing conventions): this table is the
# source of truth. The PDF (test input) and expected.json (oracle) are BOTH
# rendered from it; the parser under test is never run to build the oracle, so a
# parser bug still fails AC8. Edit values here, then regenerate.
#
# Values MUST be obviously fake — round/sequential amounts, placeholder names,
# placeholder references — so any realistic-looking figure stands out as a
# leaked real value instead of blending in. The structure still exercises the
# parser: one credit, a thousands-separated amount, same-day repeated dates
# (seq 7/8 and 10/11) for intra_statement_seq, and a Spotify line for the
# rule-categorization path.
# (posting_date, description, amount) — debits negative, credits positive.
TRANSACTIONS: list[tuple[date, str, Decimal]] = [
    (date(2026, 5, 7), "TRF MB WAY P/ ALICE EXEMPLO", Decimal("-10.00")),
    (date(2026, 5, 11), "TRF P/ Moey", Decimal("-20.00")),
    (date(2026, 5, 15), "TRF MB WAY P/ BRUNO AMOSTRA", Decimal("-30.00")),
    (date(2026, 5, 20), "TRF P/ Moey", Decimal("-40.00")),
    (date(2026, 5, 21), "TRF P/ Moey", Decimal("-50.00")),
    (date(2026, 5, 22), "TRANSFERENCIA - VENCIMENTO", Decimal("2000.00")),
    (date(2026, 5, 25), "TRF P/ Moey", Decimal("-60.00")),
    (date(2026, 5, 25), "TRF P/ Moey", Decimal("-70.00")),
    (date(2026, 5, 27), "COMPRA 0001 Spotify TESTREF0001 Stockholm SE", Decimal("-80.00")),
    (date(2026, 5, 28), "TRF P/ CARLA FICTICIA", Decimal("-90.00")),
    (date(2026, 5, 28), "TRF P/ Moey", Decimal("-100.00")),
]

# x positions chosen to land inside the parser's column bands (DEBITO center
# <407, CREDITO 407–493, SALDO >493; date columns x0<110; description 110–340).
X_LANC = 56.7
X_VALOR = 87.3
X_DESC = 114.5
X_DEBIT_R = 385.3   # right edge (drawRightString)
X_CREDIT_R = 463.2
X_SALDO_R = 557.0

_WIDTH, _HEIGHT = A4


def _fmt(value: Decimal) -> str:
    """Format a positive Decimal in PT style: space thousands, dot decimal."""
    whole, frac = f"{value:.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{' '.join(groups)}.{frac}"


def _fmt_date(d: date) -> str:
    return f"{d.month}.{d.day:02d}"


def build() -> None:
    rl_config.invariant = 1  # reproducible bytes: no embedded timestamps
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFont("Helvetica", 8)

    def text_at(top: float, x: float, s: str, *, right: bool = False) -> None:
        y = _HEIGHT - top
        if right:
            c.drawRightString(x, y, s)
        else:
            c.drawString(x, y, s)

    text_at(58, X_LANC, "26/05/29 EXT. N. 2026/005 DEPOSITO A ORDEM: 00000000000 PAG: 00002")
    text_at(100, X_LANC, f"EXTRATO DE {PERIOD_START:%Y/%m/%d} A {PERIOD_END:%Y/%m/%d}")

    text_at(128, X_LANC, "DATA DATA")
    text_at(138, X_LANC, "LANC.VALOR DESCRITIVO")
    text_at(138, X_DEBIT_R, "DEBITO", right=True)
    text_at(138, X_CREDIT_R, "CREDITO", right=True)
    text_at(138, X_SALDO_R, "SALDO", right=True)

    top = 152.0
    text_at(top, X_DESC, "SALDO INICIAL")
    text_at(top, X_SALDO_R, _fmt(SALDO_INICIAL), right=True)

    running = SALDO_INICIAL
    for posting_date, description, amount in TRANSACTIONS:
        top += 12.0
        running += amount
        text_at(top, X_LANC, _fmt_date(posting_date))
        text_at(top, X_VALOR, _fmt_date(posting_date))
        text_at(top, X_DESC, description)
        if amount < 0:
            text_at(top, X_DEBIT_R, _fmt(-amount), right=True)
        else:
            text_at(top, X_CREDIT_R, _fmt(amount), right=True)
        text_at(top, X_SALDO_R, _fmt(running), right=True)

    top += 12.0
    text_at(top, X_DESC, "SALDO FINAL")
    text_at(top, X_SALDO_R, _fmt(running), right=True)
    top += 10.0
    text_at(top, X_DESC, "SALDO DISPONIVEL")
    text_at(top, X_SALDO_R, _fmt(running), right=True)

    c.showPage()
    c.save()

    closing = SALDO_INICIAL + sum((a for _, _, a in TRANSACTIONS), Decimal("0"))
    expected = {
        "currency": "EUR",
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "closing_balance": str(closing),
        "transactions": [
            {
                "intra_statement_seq": i,
                "date": d.isoformat(),
                "amount": str(a),
                "description_raw": desc,
            }
            for i, (d, desc, a) in enumerate(TRANSACTIONS, start=1)
        ],
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pdf_path.name} and expected.json; closing balance {closing}")


if __name__ == "__main__":
    build()
