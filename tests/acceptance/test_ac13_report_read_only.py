"""AC13: `cruzar report` is read-only w.r.t. the DB. After a full `process`, running
`report_only` re-renders the Markdown but leaves the DB byte-identical (verified by a
sorted iterdump hash, per AC1) — it uses cached FX and never fetches/persists.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process, report_only


def _dump_hash(db_path: Path) -> str:
    conn = connect(db_path)
    try:
        lines = sorted(conn.iterdump())
    finally:
        conn.close()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_ac13_report_is_read_only(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path
) -> None:
    process(db_path, inbox_dir, config_dir, reports_dir)
    before = _dump_hash(db_path)

    # Wipe the rendered reports so we can prove `report` regenerates them (ADR-3).
    report_files = list(reports_dir.glob("cruzar-*.md"))
    assert report_files, "process should have written at least one report"
    for f in report_files:
        f.unlink()

    report_only(db_path, config_dir, reports_dir)

    after = _dump_hash(db_path)
    assert after == before  # DB untouched (read-only)
    assert list(reports_dir.glob("cruzar-*.md")), "report should have regenerated the files"
