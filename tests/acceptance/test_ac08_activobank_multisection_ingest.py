"""Pipeline ingest of a multi-section ActivoBank file (plan 023): one file yields one
statement PER section, persisted independently, and a re-run is idempotent (ADR-7).
This covers the multi-statement ingest path that the single-section fixture can't.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process

_MULTI_PDF = Path(__file__).parent.parent / "fixtures" / "activobank_multisection" / "statement.pdf"


def _inbox_with_multisection(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    (inbox / "activobank").mkdir(parents=True)
    shutil.copy(_MULTI_PDF, inbox / "activobank" / "statement.pdf")
    return inbox


def test_multisection_ingest_persists_one_statement_per_section(
    tmp_path: Path, db_path: Path, config_dir: Path, reports_dir: Path
) -> None:
    inbox = _inbox_with_multisection(tmp_path)
    process(db_path, inbox, config_dir, reports_dir)

    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT period_start, period_end, closing_balance FROM statements "
            "ORDER BY period_start"
        ).fetchall()
        # One statement per section, each with its own period and SALDO FINAL.
        assert [(r["period_start"], r["period_end"]) for r in rows] == [
            ("2025-12-02", "2025-12-30"),
            ("2026-01-02", "2026-01-30"),
        ]
        assert [Decimal(r["closing_balance"]) for r in rows] == [
            Decimal("2900.00"), Decimal("5700.00")
        ]
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 4
    finally:
        conn.close()

    # Re-run: file-hash skip → zero new statements or transactions (ADR-7, AC1).
    process(db_path, inbox, config_dir, reports_dir)
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 4
    finally:
        conn.close()
