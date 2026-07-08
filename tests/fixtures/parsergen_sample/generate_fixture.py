"""Generate a synthetic statement PDF for the anonymizer tests.

Entirely MADE UP — a fictional "Banco Ficticio" with obviously-fake payees, round amounts, and
sequential account/reference numbers. It exercises the token kinds the anonymizer must handle:
PT comma-decimal amounts (``1.000,00``), DD/MM/YYYY dates, letter payees, and a long numeric
account + an alnum reference. There is NO expected.json — the anonymizer tests assert structural
invariants (shape preserved, no source value survives), not parsed values.

Regenerate with:  uv run python tests/fixtures/parsergen_sample/generate_fixture.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
_WIDTH, _HEIGHT = A4

# (date, description, montante, saldo) — all obviously fake.
ROWS = [
    ("01/02/2020", "COMPRA ACME LDA", "-100,00", "900,00"),
    ("05/02/2020", "TRANSF GLOBEX SA", "250,00", "1.150,00"),
    ("10/02/2020", "COMPRA FANTASIA CAFE", "-12,50", "1.137,50"),
    ("20/02/2020", "PAG XPTO REF0000123456", "-1.000,00", "137,50"),
]

X = {"date": 42.7, "desc": 120.0, "montante": 400.0, "saldo": 500.0}


def build() -> None:
    rl_config.invariant = 1  # reproducible bytes
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFont("Helvetica", 9)

    def at(top: float, x: float, s: str) -> None:
        c.drawString(x, _HEIGHT - top, s)

    at(50, X["date"], "Banco Ficticio")
    at(70, X["date"], "Extrato de Conta EUR")
    at(90, X["date"], "Conta: 000000000001")
    at(105, X["date"], "Saldo inicial 1.000,00")

    htop = 135.0
    at(htop, X["date"], "Data")
    at(htop, X["desc"], "Descricao")
    at(htop, X["montante"], "Montante")
    at(htop, X["saldo"], "Saldo")

    top = htop
    for day, desc, montante, saldo in ROWS:
        top += 20.0
        at(top, X["date"], day)
        at(top, X["desc"], desc)
        at(top, X["montante"], montante)
        at(top, X["saldo"], saldo)

    at(770, X["date"], "Banco Ficticio SA Pagina 1 de 1")
    c.showPage()
    c.save()
    print(f"wrote {pdf_path.name}")


if __name__ == "__main__":
    build()
