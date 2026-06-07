"""Smoke: the basic `process` pipeline runs end-to-end on a fresh install and
produces output. A blunt "does it actually run?" guard — complements the unit/AC
tests by exercising the real wiring (config seed → ingest → parse → persist →
normalize → report) over the committed synthetic fixture.
"""

from __future__ import annotations

from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process


def test_process_fresh_install_runs_and_writes_report(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path
) -> None:
    process(db_path, inbox_dir, config_dir, reports_dir)

    conn = connect(db_path)
    try:
        n_txns = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        conn.close()
    assert n_txns > 0, "pipeline persisted no transactions"

    reports = list(reports_dir.glob("cruzar-*.md"))
    assert reports, "pipeline wrote no report"
    assert any(r.read_text(encoding="utf-8").strip() for r in reports), "report is empty"
