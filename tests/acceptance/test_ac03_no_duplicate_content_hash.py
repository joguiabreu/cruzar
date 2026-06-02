"""AC3: no content_hash appears twice. After ingest COUNT == COUNT(DISTINCT),
and a direct duplicate insert raises (UNIQUE enforced).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cruzar.db import connect
from cruzar.pipeline import process


def test_ac03_no_duplicate_content_hash(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path
) -> None:
    process(db_path, inbox_dir, config_dir, reports_dir)
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT content_hash) FROM transactions"
        ).fetchone()[0]
        assert total == distinct == 11

        existing = conn.execute(
            "SELECT statement_id, date, amount, description_raw, intra_statement_seq, "
            "content_hash FROM transactions LIMIT 1"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO transactions(statement_id, date, amount, description_raw, "
                "intra_statement_seq, content_hash) VALUES (?, ?, ?, ?, ?, ?)",
                tuple(existing),
            )
    finally:
        conn.close()
