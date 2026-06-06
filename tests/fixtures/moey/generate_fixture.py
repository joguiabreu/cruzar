"""Generate the synthetic Moey test fixture (statement.pdf + expected.json).

The data here is entirely MADE UP — no real payee names, account numbers, or
amounts. It mimics the Moey ``CONTA MOEY`` layout (DD-MM-YYYY date columns, a
``/`` separator, a right-anchored ``amount  sign  balance`` trio with the +/-
sign as its own token, PT comma-decimal numbers, the PT-month period line, the
``Extracto em EUR`` currency line, and a ``SALDO FINAL`` that precedes a decoy
APLICAÇÕES summary) closely enough that ``parsers.moey.parse`` extracts it
identically to a real statement, so AC8 stays a faithful test without committing
private data.

Regenerate with:  uv run python tests/fixtures/moey/generate_fixture.py
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
SALDO_INICIAL = Decimal("5000.00")

# Hand-authored oracle (CLAUDE.md testing conventions): this table is the
# source of truth. The PDF (test input) and expected.json (oracle) are BOTH
# rendered from it; the parser under test is never run to build the oracle, so a
# parser bug still fails AC8. Edit values here, then regenerate.
#
# Values MUST be obviously fake — round/sequential amounts, placeholder names,
# sequential references — so any realistic-looking figure stands out as a leaked
# real value instead of blending in. The structure still exercises the parser:
#   - a wrapped description (line1 "...JOAO E" + continuation "SOUSA") → one line;
#   - a "+" credit with a thousands-separated amount (TRANSF SEPA inbound leg);
#   - a "TRANSF SEPA -SELF" line (the "-" is glued to SELF, not the sign token);
#   - a Spotify line for the rule-categorization path.
# (posting_date, line1_description, continuation_or_None, amount) — debits
# negative, credits positive. description_raw = line1 + " " + continuation.
TRANSACTIONS: list[tuple[date, str, str | None, Decimal]] = [
    (date(2026, 5, 5), "Trf imediata JOAO E", "SOUSA", Decimal("-100.00")),
    (date(2026, 5, 7), "COMPRA CONTINENTE 2000002", None, Decimal("-200.00")),
    (date(2026, 5, 10), "TRANSF SEPA -SELF EXEMPLO 3000003", None, Decimal("1500.00")),
    (date(2026, 5, 15), "COMPRA Spotify 4000004 Stockholm SE", None, Decimal("-10.00")),
    (date(2026, 5, 20), "TRF P/ ALICE EXEMPLO 5000005", None, Decimal("-50.00")),
    (date(2026, 5, 25), "COMPRA RESTAURANTE 6000006", None, Decimal("-300.00")),
]

# Decoy APLICAÇÕES summary after the real SALDO FINAL: a date-led row and a
# second SALDO FINAL that the parser MUST ignore (region ends at the first
# SALDO FINAL). If either leaks into the output, AC8 fails.
DECOY_DATE = date(2026, 5, 31)
DECOY_DESC = "APLICACAO DECOY 9999999"
DECOY_AMOUNT = Decimal("999.00")
DECOY_SALDO_FINAL = Decimal("9999.99")

_PT_MONTHS = {5: "Maio"}

X_LANC = 54.0
X_SEP = 105.0
X_VALOR = 114.0
X_DESC = 189.0
X_AMOUNT_R = 450.0  # right edge (drawRightString)
X_SIGN = 465.0
X_BALANCE_R = 560.0

_WIDTH, _HEIGHT = A4


def _fmt_pt(value: Decimal) -> str:
    """Format a Decimal magnitude in PT style: dot thousands, comma decimal."""
    whole, frac = f"{abs(value):.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{'.'.join(groups)},{frac}"


def _fmt_date(d: date) -> str:
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


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

    text_at(50, X_LANC, "Extracto em EUR")
    text_at(70, X_LANC, "CONTA MOEY")
    text_at(
        90,
        X_LANC,
        f"Periodo de {PERIOD_START.day} de {_PT_MONTHS[PERIOD_START.month]} de "
        f"{PERIOD_START.year} a {PERIOD_END.day} de {_PT_MONTHS[PERIOD_END.month]} de "
        f"{PERIOD_END.year}",
    )

    text_at(130, X_LANC, "DATA LANÇAMENTO / DATA VALOR DESCRIÇÃO")
    text_at(130, X_AMOUNT_R, "MOVIMENTOS", right=True)
    text_at(130, X_BALANCE_R, "SALDO CONTABILÍSTICO", right=True)

    running = SALDO_INICIAL
    top = 130.0

    def render_row(
        posting: date, desc: str, amount: Decimal, balance: Decimal
    ) -> None:
        text_at(top, X_LANC, _fmt_date(posting))
        text_at(top, X_SEP, "/")
        text_at(top, X_VALOR, _fmt_date(posting))
        text_at(top, X_DESC, desc)
        text_at(top, X_AMOUNT_R, _fmt_pt(amount), right=True)
        text_at(top, X_SIGN, "+" if amount > 0 else "-")
        text_at(top, X_BALANCE_R, _fmt_pt(balance), right=True)

    for posting_date, line1, continuation, amount in TRANSACTIONS:
        top += 14.0
        running += amount
        render_row(posting_date, line1, amount, running)
        if continuation is not None:
            top += 12.0
            text_at(top, X_DESC, continuation)  # wrapped tail: no date/amount

    top += 14.0
    text_at(top, X_DESC, "SALDO FINAL")
    text_at(top, X_BALANCE_R, _fmt_pt(running), right=True)

    # --- decoy APLICAÇÕES summary (must be ignored by the parser) ---
    top += 24.0
    text_at(top, X_LANC, "APLICAÇÕES / RESPONSABILIDADES / RESUMO")
    top += 14.0
    render_row(DECOY_DATE, DECOY_DESC, DECOY_AMOUNT, DECOY_SALDO_FINAL)
    top += 14.0
    text_at(top, X_DESC, "SALDO FINAL")
    text_at(top, X_BALANCE_R, _fmt_pt(DECOY_SALDO_FINAL), right=True)

    c.showPage()
    c.save()

    closing = SALDO_INICIAL + sum((a for _, _, _, a in TRANSACTIONS), Decimal("0"))
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
                "description_raw": line1 if cont is None else f"{line1} {cont}",
            }
            for i, (d, line1, cont, a) in enumerate(TRANSACTIONS, start=1)
        ],
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pdf_path.name} and expected.json; closing balance {closing}")


if __name__ == "__main__":
    build()
