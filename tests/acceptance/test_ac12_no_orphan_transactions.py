"""AC12: every transaction resolves to a non-null account_id via the FK chain;
no orphans. A PDF in a folder with no sources.yaml entry is recorded
unresolved_account with zero statements/transactions.
"""

from __future__ import annotations

from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process


def test_ac12_no_orphan_transactions(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path
) -> None:
    process(db_path, inbox_dir, config_dir, reports_dir)
    conn = connect(db_path)
    try:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM transactions t "
            "LEFT JOIN statements s ON t.statement_id = s.id "
            "LEFT JOIN accounts a ON s.account_id = a.id "
            "WHERE a.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0
    finally:
        conn.close()


def test_ac12_unresolved_account(
    db_path: Path, inbox_dir: Path, config_dir: Path, reports_dir: Path,
) -> None:
    # Drop a PDF into a folder with no matching sources.yaml entry. Resolution
    # happens by folder before parsing, so the bytes only need to be distinct
    # from the activobank fixture (else file-hash dedup would skip it).
    unknown = inbox_dir / "unknown_bank"
    unknown.mkdir()
    (unknown / "mystery.pdf").write_bytes(b"%PDF-unresolved-content")

    process(db_path, inbox_dir, config_dir, reports_dir)
    conn = connect(db_path)
    try:
        status = conn.execute(
            "SELECT status FROM processed_files WHERE original_filename = 'mystery.pdf'"
        ).fetchone()["status"]
        assert status == "unresolved_account"

        # The unresolved file contributed no statements/transactions. Only the
        # resolvable activobank statement did.
        statements = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
        assert statements == 1
    finally:
        conn.close()
