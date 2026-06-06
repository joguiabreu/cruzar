"""Generate the synthetic Revolut test fixture (statement.pdf + expected.json).

The data here is entirely MADE UP — no real payee names, card numbers, or
amounts. It mimics a real Revolut combined export closely enough that
``parsers.revolut.parse`` extracts it identically, so AC8 stays a faithful test
without committing private data. It deliberately exercises the hard parts:

  - **both layouts** — an "old" Revolut Ltd / E-Money section (single `Data`
    column, `Saldo`) followed by a "new" Revolut Bank UAB / Conta Corrente section
    (`Data Lançamento` + `Data-Valor`, `Saldo contabilístico`);
  - a debit fee and a credit top-up (sign comes from the retirado/recebido column);
  - a `Conversão cambial` with a foreign-currency detail line;
  - a wrapped payee + `Referência:`/`De:` continuation lines (one logical txn);
  - a thousands-separated amount (`€1,500.00`);
  - an intervening `Resumo do saldo` summary block AND a `Revertido` / `Data de
    início` reverted sub-table (no `Saldo` column) — both must be ignored.

Regenerate with:  uv run python tests/fixtures/revolut/generate_fixture.py
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
PERIOD_START = date(2020, 1, 5)
PERIOD_END = date(2025, 6, 25)


# Hand-authored oracle (CLAUDE.md testing conventions): this table is the source
# of truth. The PDF (test input) and expected.json (oracle) are BOTH rendered
# from it; the parser is never run to build the oracle, so a parser bug still
# fails AC8. Values MUST be obviously fake (round amounts, placeholder names,
# sequential card masks). `lines[0]` renders on the dated row; the rest render as
# continuation rows (no date) and merge into description_raw in order.
@dataclass(frozen=True)
class Txn:
    layout: str  # "old" | "new"
    day: date
    amount: Decimal  # signed; debit negative (retirado), credit positive (recebido)
    lines: tuple[str, ...]  # [dated-row desc, *continuation lines]

    @property
    def description(self) -> str:
        return " ".join(self.lines)


TRANSACTIONS: list[Txn] = [
    Txn("old", date(2020, 1, 5), Decimal("100.00"),
        ("Carregamento com cartão *1000", "De: *1000")),
    Txn("old", date(2020, 1, 10), Decimal("-5.00"),
        ("Comissão de entrega de cartão", "Cartão: 100000******1000")),
    Txn("old", date(2020, 1, 15), Decimal("-50.00"),
        ("Conversão cambial para PLN", "200.00 PLN")),
    Txn("new", date(2025, 6, 20), Decimal("1500.00"),
        ("Transferência de JOAO EXEMPLO", "DA SILVA",
         "Referência: From Joao E", "De: JOAO EXEMPLO DA SILVA")),
    Txn("new", date(2025, 6, 25), Decimal("-10.00"),
        ("Spotify", "Para: Spotify, Stockholm", "Cartão: 100000******2000")),
]

# Column x-positions, matched to the real export's geometry (token x0; Saldo is
# right-aligned). Amounts in retirado/recebido are left-aligned at the column's
# "Dinheiro" x0; the parser keys the sign off the right edge vs the recebido x0.
# ret_lbl/rec_lbl are spaced well clear of their "Dinheiro" anchor: at 8pt
# Helvetica an abutting label would merge into the "Dinheiro" token that the
# parser keys on. Label x-positions are cosmetic — the parser only uses the two
# "Dinheiro" x0 anchors and the presence of "Saldo".
X = {
    "old": {"date": 42.7, "desc": 124.8, "ret": 335.1, "ret_lbl": 385.0,
            "rec": 417.1, "rec_lbl": 467.0, "saldo_lbl": 534.9},
    "new": {"date": 42.7, "dateval": 119.1, "desc": 191.1, "ret": 375.0,
            "ret_lbl": 425.0, "rec": 449.0, "saldo_lbl": 530.8},
}
X_SALDO_R = 555.6
_WIDTH, _HEIGHT = A4


def _fmt_eur(value: Decimal) -> str:
    """Format a Decimal magnitude as '€1,234.56' (comma thousands, dot decimal)."""
    whole, frac = f"{abs(value):.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"€{','.join(groups)}.{frac}"


def _fmt_date(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def build() -> None:
    rl_config.invariant = 1  # reproducible bytes
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFont("Helvetica", 8)

    def at(top: float, x: float, s: str, *, right: bool = False) -> None:
        y = _HEIGHT - top
        if right:
            c.drawRightString(x, y, s)
        else:
            c.drawString(x, y, s)

    def render_txn(top: float, t: Txn, balance: Decimal) -> float:
        col = X[t.layout]
        at(top, col["date"], _fmt_date(t.day))
        if t.layout == "new":
            at(top, col["dateval"], _fmt_date(t.day))
        at(top, col["desc"], t.lines[0])
        amount_x = col["rec"] if t.amount > 0 else col["ret"]
        at(top, amount_x, _fmt_eur(t.amount))
        at(top, X_SALDO_R, _fmt_eur(balance), right=True)
        for cont in t.lines[1:]:
            top += 11.0
            at(top, col["desc"], cont)
        return top

    running = SALDO_INICIAL

    # ---- Page 1: section 1 (old layout, Revolut Ltd / E-Money) ----
    at(50, X["old"]["date"], "Extrato de EUR")
    at(70, X["old"]["date"], "Revolut Ltd")
    at(90, X["old"]["date"], "Resumo do saldo")
    at(104, X["old"]["date"], "Conta (E-Money) €0.00 €155.00 €1,155.00 €9,999.99")  # decoy
    at(126, X["old"]["date"],
       "Operações da conta de 5 de janeiro de 2020 a 15 de janeiro de 2020")
    # column header (old)
    htop = 150.0
    at(htop, X["old"]["date"], "Data")
    at(htop, X["old"]["desc"], "Descrição")
    at(htop, X["old"]["ret"], "Dinheiro")
    at(htop, X["old"]["ret_lbl"], "retirado")
    at(htop, X["old"]["rec"], "Dinheiro")
    at(htop, X["old"]["rec_lbl"], "recebido")
    at(htop, X["old"]["saldo_lbl"], "Saldo")
    top = htop
    for t in (x for x in TRANSACTIONS if x.layout == "old"):
        top += 16.0
        running += t.amount
        top = render_txn(top, t, running)

    # decoy reverted/pending sub-table (no Saldo column) — must be ignored
    top += 20.0
    at(top, 39.7, "Revertido de 5 de janeiro de 2020 para 15 de janeiro de 2020")
    top += 14.0
    at(top, X["old"]["date"], "Data")
    at(top, 63.5, "de início")
    at(top, X["old"]["desc"], "Descrição")
    at(top, X["old"]["ret"], "Dinheiro")
    at(top, X["old"]["ret_lbl"], "retirado")
    at(top, X["old"]["rec"], "Dinheiro")
    at(top, X["old"]["rec_lbl"], "recebido")
    top += 16.0
    at(top, X["old"]["date"], _fmt_date(date(2020, 1, 1)))
    at(top, X["old"]["desc"], "Carregamento com cartão *9999")
    at(top, X["old"]["rec"], "€77.00")
    top += 11.0
    at(top, X["old"]["desc"], "De: *9999")

    at(770, 85.4, "Comunicar perda ou roubo do cartão A Revolut Ltd é autorizada")
    at(782, 39.7, "© 2020 Revolut Ltd Página 1 de 2")
    c.showPage()
    c.setFont("Helvetica", 8)

    # ---- Page 2: section 2 (new layout, Revolut Bank UAB / Conta Corrente) ----
    at(50, X["new"]["date"], "Extrato de EUR")
    at(70, X["new"]["date"], "Revolut Bank UAB Sucursal em Portugal")
    at(90, X["new"]["date"], "Resumo do saldo")
    at(104, X["new"]["date"], "Conta (Conta Corrente) €10.00 €1,500.00 €8,888.88 €7,777.77")  # decoy
    at(126, X["new"]["date"],
       "Operações da conta de 20 de junho de 2025 a 25 de junho de 2025")
    # column header (new) — split across three sub-rows like the real export
    htop = 150.0
    at(htop, X["new"]["rec"], "Dinheiro")
    at(htop, X["new"]["saldo_lbl"], "Saldo")
    at(htop + 7, X["new"]["date"], "Data")
    at(htop + 7, 63.5, "Lançamento")
    at(htop + 7, X["new"]["dateval"], "Data-Valor")
    at(htop + 7, X["new"]["desc"], "Descrição")
    at(htop + 7, X["new"]["ret"], "Dinheiro")
    at(htop + 7, X["new"]["ret_lbl"], "retirado")
    at(htop + 14, X["new"]["rec"], "recebido")
    at(htop + 14, 501.8, "contabilístico")
    top = htop + 14
    for t in (x for x in TRANSACTIONS if x.layout == "new"):
        top += 16.0
        running += t.amount
        top = render_txn(top, t, running)

    at(770, 85.4, "Comunicar perda ou roubo do cartão A Revolut Bank UAB")
    at(782, 39.7, "© 2025 Revolut Bank UAB Página 2 de 2")
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
