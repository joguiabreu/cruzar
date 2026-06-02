"""AC1: two consecutive runs on the same input produce identical DB state.
Verified by sha256 of a sorted iterdump, and by the second run inserting no rows.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process


def _dump_hash(db_path: Path) -> str:
    conn = connect(db_path)
    try:
        # Sort lines so the hash is insensitive to dump ordering, but sensitive
        # to any row/value change. Exclude the volatile processed_at/created_at
        # columns is unnecessary here: identical reprocessing rewrites nothing.
        lines = sorted(conn.iterdump())
    finally:
        conn.close()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _row_count(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        conn.close()


def test_ac01_idempotent_reprocessing(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path
) -> None:
    process(db_path, inbox_dir, config_dir, reports_dir)
    first_hash = _dump_hash(db_path)
    first_count = _row_count(db_path)

    process(db_path, inbox_dir, config_dir, reports_dir)
    second_hash = _dump_hash(db_path)
    second_count = _row_count(db_path)

    assert first_count == 11
    assert second_count == first_count  # file-hash skip: zero new rows
    assert first_hash == second_hash
