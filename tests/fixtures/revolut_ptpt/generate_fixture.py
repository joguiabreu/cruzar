"""Generate the synthetic Revolut pt-pt test fixture (statement.pdf + expected.json).

Same "new" Revolut Bank UAB / Conta Corrente layout as the main Revolut fixture, but
with amounts in the **Portuguese locale** Revolut exports: ``1.234,56€`` (dot thousands,
comma decimal, € suffix) rather than ``€1,234.56``. This covers the pt-pt amount path
the parser learned in this slice — the only thing that differed from the existing layout.

The data is entirely MADE UP — round amounts, placeholder merchants. The table below is
the hand-authored oracle (CLAUDE.md testing conventions): the PDF and expected.json are
BOTH rendered from it; the parser is never run to build the oracle. Row 2 renders its
long description across a dated row + a continuation line (no date), which merge into one
``description_raw`` (ADR-11).

Regenerate with:  uv run python tests/fixtures/revolut_ptpt/generate_fixture.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
SALDO_INICIAL = Decimal("1000.00")


@dataclass(frozen=True)
class Txn:
    day: date
    amount: Decimal  # signed; debit negative (retirado), credit positive (recebido)
    lines: tuple[str, ...]  # [dated-row desc, *continuation lines]

    @property
    def description(self) -> str:
        return " ".join(self.lines)


TRANSACTIONS: list[Txn] = [
    Txn(date(2026, 1, 5), Decimal("-10.00"), ("EXAMPLE GROCER",)),
    Txn(date(2026, 1, 10), Decimal("2000.00"),
        ("Carregamento com Google Pay", "através de Example")),
    Txn(date(2026, 1, 15), Decimal("-5.50"), ("EXAMPLE SUBSCRIPTION",)),
    Txn(date(2026, 1, 20), Decimal("-1234.56"), ("EXAMPLE SHOP",)),
]
PERIOD_START = TRANSACTIONS[0].day
PERIOD_END = TRANSACTIONS[-1].day

# "new"-layout column x-positions (token x0), matched to the real export geometry.
X = {"date": 42.7, "dateval": 119.1, "desc": 191.1, "ret": 375.0,
     "ret_lbl": 425.0, "rec": 449.0, "saldo_lbl": 530.8}
X_SALDO_R = 555.6
_WIDTH, _HEIGHT = A4


def _fmt_eur_pt(value: Decimal) -> str:
    """Format a Decimal magnitude as '1.234,56€' (dot thousands, comma decimal, € suffix)."""
    whole, frac = f"{abs(value):.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{'.'.join(groups)},{frac}€"


def _fmt_date(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def build() -> None:
    rl_config.invariant = 1  # reproducible bytes
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFont("Helvetica", 8)

    def at(top: float, x: float, s: str, *, right: bool = False) -> None:
        y = _HEIGHT - top
        (c.drawRightString if right else c.drawString)(x, y, s)

    at(50, X["date"], "Extrato de EUR")
    at(70, X["date"], "Revolut Bank UAB Sucursal em Portugal")
    at(90, X["date"], "Resumo do saldo")
    at(104, X["date"], "Conta (Conta Corrente) 10,00€ 1.500,00€ 8.888,88€ 7.777,77€")  # decoy
    at(126, X["date"],
       "Operações da conta de 5 de janeiro de 2026 a 20 de janeiro de 2026")

    # column header (new layout), split across three sub-rows like the real export
    htop = 150.0
    at(htop, X["rec"], "Dinheiro")
    at(htop, X["saldo_lbl"], "Saldo")
    at(htop + 7, X["date"], "Data")
    at(htop + 7, 63.5, "Lançamento")
    at(htop + 7, X["dateval"], "Data-Valor")
    at(htop + 7, X["desc"], "Descrição")
    at(htop + 7, X["ret"], "Dinheiro")
    at(htop + 7, X["ret_lbl"], "retirado")
    at(htop + 14, X["rec"], "recebido")
    at(htop + 14, 501.8, "contabilístico")

    top = htop + 14
    running = SALDO_INICIAL
    for t in TRANSACTIONS:
        top += 16.0
        running += t.amount
        at(top, X["date"], _fmt_date(t.day))
        at(top, X["dateval"], _fmt_date(t.day))
        at(top, X["desc"], t.lines[0])
        at(top, X["rec"] if t.amount > 0 else X["ret"], _fmt_eur_pt(t.amount))
        at(top, X_SALDO_R, _fmt_eur_pt(running), right=True)
        for cont in t.lines[1:]:
            top += 11.0
            at(top, X["desc"], cont)

    at(770, 85.4, "Comunicar perda ou roubo do cartão A Revolut Bank UAB")
    at(782, 39.7, "© 2026 Revolut Bank UAB Página 1 de 1")
    c.showPage()
    c.save()

    closing = SALDO_INICIAL + sum((t.amount for t in TRANSACTIONS), Decimal("0"))
    expected = {
        "currency": "EUR",
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "closing_balance": str(closing),
        "transactions": [
            {
                "intra_statement_seq": i,
                "date": t.day.isoformat(),
                "amount": str(t.amount),
                "description_raw": t.description,
            }
            for i, t in enumerate(TRANSACTIONS, start=1)
        ],
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pdf_path.name} and expected.json; closing balance {closing}")


if __name__ == "__main__":
    build()
