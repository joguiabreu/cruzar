"""AC11: debits are negative, credits positive — one signed amount column, no
amount+type split. Verified by MIN/MAX(amount) after a real ingest, and by the
schema carrying a single signed `amount` and no separate type/debit/credit column.
"""

from __future__ import annotations

from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process


def test_ac11_debits_negative_credits_positive(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path
) -> None:
    process(db_path, inbox_dir, config_dir, reports_dir)
    conn = connect(db_path)
    try:
        # The ActivoBank fixture has both debits and one credit (a salary).
        # MIN/MAX over the signed amount strings won't order numerically, so compare
        # as Decimals in Python.
        from decimal import Decimal

        amounts = [
            Decimal(r["amount"])
            for r in conn.execute("SELECT amount FROM transactions").fetchall()
        ]
        assert min(amounts) < 0  # at least one debit, stored negative
        assert max(amounts) > 0  # at least one credit, stored positive

        # Single signed column: no amount+type split (no type/debit/credit columns).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
        assert "amount" in cols
        assert not (cols & {"amount_type", "type", "debit", "credit"})
    finally:
        conn.close()
