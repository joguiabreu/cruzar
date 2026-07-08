"""Generate the synthetic AforroNet test fixture (statement.pdf + expected.json).

Mimics an AforroNet "Extrato de Conta Aforro" — a single-page position snapshot of
Certificados de Aforro — closely enough that ``parsers.aforronet.parse`` extracts it
identically to a real export (AC8), without committing any private data.

The data here is entirely MADE UP — no real account holder, number, or amounts. Both
the PDF (test input) and expected.json (oracle) are rendered from the SERIES table
below; the parser under test is never run to build the oracle, so a parser bug still
fails AC8 (CLAUDE.md testing conventions). Values are obviously fake (round units and
values, placeholder subscription refs). Two series exercise the multi-holding path and
the per-series cost-basis derivation (units × acquisition unit value).

Regenerate with:
    uv run python tests/fixtures/aforronet/generate_fixture.py
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

STATEMENT_DATE = date(2026, 3, 15)


@dataclass(frozen=True)
class Serie:
    label: str           # e.g. "Série E"
    units: int           # number of units (each subscribed at acq_unit EUR)
    acq_unit: Decimal    # "Valor Unitário Aquisição"
    unit_value: Decimal  # current unit value
    subscr_no: str       # placeholder subscription number

    @property
    def value(self) -> Decimal:
        return (self.units * self.unit_value).quantize(Decimal("0.01"))

    @property
    def cost_basis(self) -> Decimal:
        return (self.units * self.acq_unit).quantize(Decimal("0.01"))

    @property
    def symbol(self) -> str:
        return f"Certificados de Aforro {self.label}"


SERIES: list[Serie] = [
    Serie("Série E", 1000, Decimal("1.00000"), Decimal("1.20000"), "100000001"),
    Serie("Série F", 2000, Decimal("1.00000"), Decimal("1.25000"), "100000002"),
]

_WIDTH, _HEIGHT = A4


def _fmt_pt(value: Decimal, decimals: int) -> str:
    """Format a Decimal in PT style: dot thousands, comma decimal."""
    q = value.quantize(Decimal(1).scaleb(-decimals)) if decimals else value.quantize(Decimal(1))
    digits = f"{abs(q):.{decimals}f}"
    whole, _, frac = digits.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    out = ".".join(groups)
    return f"{out},{frac}" if decimals else out


def build() -> None:
    rl_config.invariant = 1  # reproducible bytes: no embedded timestamps
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFont("Helvetica", 9)

    lines: list[str] = [
        "Extrato de Conta Aforro",
        "NOME EXEMPLO DO TITULAR",
        "CONTA AFORRO N.º 00000000000",
        f"Data do Extrato: {STATEMENT_DATE:%d-%m-%Y} Valores em Euros",
        "",
        "RESUMO DE SALDOS POR PRODUTOS NA DATA DO EXTRATO",
        "Produto/Série Unidades Valor",
    ]
    total = Decimal("0.00")
    for s in SERIES:
        lines.append(f"{s.symbol} {_fmt_pt(Decimal(s.units), 0)} {_fmt_pt(s.value, 2)}")
        total += s.value
    lines.append(f"TOTAL {_fmt_pt(total, 2)}")
    lines += [
        "",
        "DETALHE DE SALDOS POR PRODUTOS NA DATA DO EXTRATO",
        "Produto Data Subscr. Subscrição nº Valor Unitário Unidades Valor",
    ]
    for s in SERIES:
        lines.append(f"CAF / {s.label}")
        lines.append(f"Valor Unitário Aquisição: {_fmt_pt(s.acq_unit, 5)} EUR")
        lines.append(
            f"01-01-2024 {s.subscr_no} {_fmt_pt(s.unit_value, 5)} "
            f"{_fmt_pt(Decimal(s.units), 0)} {_fmt_pt(s.value, 2)}"
        )
        lines.append(f"TOTAL CAF / {s.label} {_fmt_pt(Decimal(s.units), 0)} {_fmt_pt(s.value, 2)}")

    top = 60.0
    for line in lines:
        if line:
            c.drawString(56.7, _HEIGHT - top, line)
        top += 16.0
    c.showPage()
    c.save()

    expected = {
        "currency": "EUR",
        "period_start": STATEMENT_DATE.isoformat(),
        "period_end": STATEMENT_DATE.isoformat(),
        "closing_balance": "0.00",
        "transactions": [],
        "holdings": [
            {
                "symbol": s.symbol,
                "quantity": str(Decimal(s.units)),
                "cost_basis": str(s.cost_basis),
                "value": str(s.value),
                "currency": "EUR",
            }
            for s in SERIES
        ],
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pdf_path.name} and expected.json; {len(SERIES)} holdings, total {total}")


if __name__ == "__main__":
    build()
