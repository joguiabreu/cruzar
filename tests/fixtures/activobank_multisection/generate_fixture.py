"""Generate the synthetic ActivoBank MULTI-SECTION test fixture (statement.pdf +
expected.json).

A real ActivoBank export is a single PDF of several stacked monthly sections —
each a complete mini-statement (its own ``EXTRATO DE … A …``, ``SALDO INICIAL``,
``SALDO FINAL``, and a ``VENCIMENTO`` salary). This fixture mimics that with TWO
sections crossing a year boundary (Dec 2025 → Jan 2026) so the parser must: capture
every section, run ``intra_statement_seq`` continuously across them, span the period
from the first section's start to the last's end, take the LAST section's SALDO FINAL
as the closing balance, and resolve each section's ``M.DD`` dates against ITS OWN
period (plan 019 D1–D4). The single-section fixture lives in ``../activobank``.

The data here is entirely MADE UP — no real payees, account numbers, or amounts.
Both the PDF (test input) and expected.json (oracle) are rendered from the SECTIONS
table below; the parser under test is never run to build the oracle, so a parser bug
still fails AC8 (CLAUDE.md testing conventions).

Regenerate with:
    uv run python tests/fixtures/activobank_multisection/generate_fixture.py
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


@dataclass(frozen=True)
class Section:
    period_start: date
    period_end: date
    saldo_inicial: Decimal
    # (posting_date, description, amount) — debits negative, credits positive.
    transactions: list[tuple[date, str, Decimal]]
    # Reprint the column header before this 0-based transaction index, mimicking a
    # real export's page break MID-section (the header row the parser must skip, not
    # read 'DEBITO' as an amount). -1 = no reprint. Does NOT add a transaction.
    reprint_header_before: int = -1


# Hand-authored oracle. Obviously-fake values (round/sequential amounts, placeholder
# names and refs). Section 2's SALDO INICIAL continues section 1's SALDO FINAL, and
# the two sections sit a month apart across the 2025→2026 year boundary so per-section
# date resolution is exercised.
SECTIONS: list[Section] = [
    Section(
        period_start=date(2025, 12, 2),
        period_end=date(2025, 12, 30),
        saldo_inicial=Decimal("1000.00"),
        transactions=[
            (date(2025, 12, 5), "TRANSFERENCIA - VENCIMENTO", Decimal("2000.00")),
            (date(2025, 12, 10), "TRF P/ EXEMPLO UM", Decimal("-100.00")),
        ],
    ),
    Section(
        period_start=date(2026, 1, 2),
        period_end=date(2026, 1, 30),
        saldo_inicial=Decimal("2900.00"),
        transactions=[
            (date(2026, 1, 6), "TRANSFERENCIA - VENCIMENTO", Decimal("3000.00")),
            (date(2026, 1, 12), "COMPRA 0002 Spotify TESTREF0002 Stockholm SE", Decimal("-200.00")),
        ],
        reprint_header_before=1,  # page break between the two transactions
    ),
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

    top = 58.0
    for section in SECTIONS:
        # Each section reprints its own EXTRATO period line + column header, exactly
        # as a stacked monthly section does in a real export.
        text_at(top, X_LANC, f"EXTRATO DE {section.period_start:%Y/%m/%d} A {section.period_end:%Y/%m/%d}")
        top += 28.0
        text_at(top, X_LANC, "DATA DATA")
        top += 10.0
        text_at(top, X_LANC, "LANC.VALOR DESCRITIVO")
        text_at(top, X_DEBIT_R, "DEBITO", right=True)
        text_at(top, X_CREDIT_R, "CREDITO", right=True)
        text_at(top, X_SALDO_R, "SALDO", right=True)

        top += 14.0
        text_at(top, X_DESC, "SALDO INICIAL")
        text_at(top, X_SALDO_R, _fmt(section.saldo_inicial), right=True)

        def _header_row(top: float) -> None:
            text_at(top, X_LANC, "LANC.VALOR DESCRITIVO")
            text_at(top, X_DEBIT_R, "DEBITO", right=True)
            text_at(top, X_CREDIT_R, "CREDITO", right=True)
            text_at(top, X_SALDO_R, "SALDO", right=True)

        running = section.saldo_inicial
        for idx, (posting_date, description, amount) in enumerate(section.transactions):
            if idx == section.reprint_header_before:
                top += 12.0
                _header_row(top)  # reprinted header mid-section; must be skipped
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
        top += 24.0  # gap before the next section

    c.showPage()
    c.save()

    # Oracle: continuous seq across sections; closing = last section's SALDO FINAL.
    transactions: list[dict[str, object]] = []
    seq = 0
    closing = SECTIONS[-1].saldo_inicial + sum(
        (a for _, _, a in SECTIONS[-1].transactions), Decimal("0")
    )
    for section in SECTIONS:
        for posting_date, description, amount in section.transactions:
            seq += 1
            transactions.append(
                {
                    "intra_statement_seq": seq,
                    "date": posting_date.isoformat(),
                    "amount": str(amount),
                    "description_raw": description,
                }
            )
    expected = {
        "currency": "EUR",
        "period_start": SECTIONS[0].period_start.isoformat(),
        "period_end": SECTIONS[-1].period_end.isoformat(),
        "closing_balance": str(closing),
        "transactions": transactions,
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pdf_path.name} and expected.json; {seq} txns, closing {closing}")


if __name__ == "__main__":
    build()
