"""Generate a DEGRADED-layout ActivoBank-style fixture (AC4a) — statement.pdf only.

Same synthetic, obviously-fake content as the clean ActivoBank fixture, but the
transaction amounts are rendered INSIDE the description band instead of the
DEBITO/CREDITO columns. So ``parsers.activobank.parse`` brackets the
SALDO INICIAL..FINAL region (the text is intact) yet resolves 0% of amount columns
and raises ``ExtractionFallback`` — the trigger for the LLM extraction fallback.

There is NO ``expected.json`` here: the oracle for a fallback is the LLM's output,
which is non-deterministic. The offline AC4a test injects a *fake* extractor that
returns a canned statement; the real Ollama path is the run-time gate, not a fixture.

Regenerate with:  uv run python tests/fixtures/activobank_degraded/generate_fixture.py
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent

PERIOD_START = date(2025, 3, 1)
PERIOD_END = date(2025, 3, 31)

# (posting_date, description, printed_amount) — obviously fake. Amounts here are
# rendered as plain text in the description band, NOT in a numeric column.
TRANSACTIONS: list[tuple[date, str, Decimal]] = [
    (date(2025, 3, 5), "EXAMPLE SUBSCRIPTION", Decimal("10.00")),
    (date(2025, 3, 9), "EXAMPLE SALARY", Decimal("2000.00")),
    (date(2025, 3, 18), "EXAMPLE GROCER", Decimal("42.50")),
]

X_LANC = 56.7   # date column (x0 < parser's _DATE_X0_MAX = 110 → row is a candidate)
X_DESC = 114.5  # description band (< _AMOUNT_X0_MIN = 340 → never a resolved amount)

_WIDTH, _HEIGHT = A4


def build() -> None:
    rl_config.invariant = 1  # reproducible bytes
    pdf_path = HERE / "statement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFont("Helvetica", 8)

    def text_at(top: float, x: float, s: str) -> None:
        c.drawString(x, _HEIGHT - top, s)

    text_at(100, X_LANC, f"EXTRATO DE {PERIOD_START:%Y/%m/%d} A {PERIOD_END:%Y/%m/%d}")
    text_at(128, X_LANC, "DATA DATA")
    text_at(138, X_LANC, "LANC.VALOR DESCRITIVO DEBITO CREDITO SALDO")

    top = 152.0
    text_at(top, X_DESC, "SALDO INICIAL")
    for posting_date, description, amount in TRANSACTIONS:
        top += 12.0
        text_at(top, X_LANC, f"{posting_date.month}.{posting_date.day:02d}")
        # Amount glued into the description band — the layout degradation we model.
        text_at(top, X_DESC, f"{description} {amount:.2f}")
    top += 12.0
    text_at(top, X_DESC, "SALDO FINAL")

    c.showPage()
    c.save()
    print(f"wrote {pdf_path.name} (degraded layout — trips ExtractionFallback)")


if __name__ == "__main__":
    build()
