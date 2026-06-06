"""Manual ingestion pipeline (ADR-4/7/10).

process(): scan /data/inbox, resolve each PDF to one account by folder
convention, dedup by file hash, parse, persist, categorize, then write reports.
Each file is processed atomically: it either fully lands or rolls back, marking
processed_files with a terminal status (SPEC §Account resolution & failure modes).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cruzar import categorize, report
from cruzar.config import load_config
from cruzar.db import connect, init_schema
from cruzar.parsers import get_parser
from cruzar.parsers.activobank import ActivoBankParseError
from cruzar.persist import persist_statement, seed_config

# Logs carry filenames and counts only — never transaction descriptions or
# amounts (real values must stay out of anything quotable, including logs).
logger = logging.getLogger(__name__)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _record_file(
    conn: sqlite3.Connection,
    file_hash: str,
    filename: str,
    status: str,
    statement_id: int | None,
) -> None:
    conn.execute(
        "INSERT INTO processed_files(file_hash, original_filename, processed_at, "
        "statement_id, status) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(file_hash) DO UPDATE SET status = excluded.status, "
        "statement_id = excluded.statement_id, processed_at = excluded.processed_at",
        (file_hash, filename, _now(), statement_id, status),
    )


def _resolve_account(conn: sqlite3.Connection, folder: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM accounts WHERE account_match = ?", (folder,)
    ).fetchone()


def process(
    db_path: str | Path,
    inbox_dir: str | Path,
    config_dir: str | Path,
    reports_dir: str | Path,
) -> None:
    config = load_config(config_dir)
    conn = connect(db_path)
    try:
        init_schema(conn)
        seed_config(conn, config)
        _ingest_inbox(conn, Path(inbox_dir))
        categorize.categorize(conn)
        report.write_reports(conn, Path(reports_dir))
    finally:
        conn.close()


def _ingest_inbox(conn: sqlite3.Connection, inbox_dir: Path) -> None:
    pdfs = sorted(inbox_dir.rglob("*.pdf"))
    if not pdfs:
        logger.info("no PDFs found in %s", inbox_dir)
        return

    ingested = skipped = failed = 0
    for pdf_path in pdfs:
        file_hash = _file_hash(pdf_path)
        existing = conn.execute(
            "SELECT status FROM processed_files WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if existing is not None and existing["status"] == "ok":
            logger.debug("already processed, skipping %s", pdf_path.name)
            skipped += 1
            continue  # file-hash idempotency (ADR-7); zero LLM/DB work on re-run

        # Manual path: account resolved by the folder under data/inbox/.
        folder = pdf_path.parent.name
        account = _resolve_account(conn, folder)
        if account is None:
            logger.warning(
                "no account configured for folder %r; skipping %s", folder, pdf_path.name
            )
            _record_file(conn, file_hash, pdf_path.name, "unresolved_account", None)
            conn.commit()
            skipped += 1
            continue

        try:
            parser = get_parser(account["institution"])
            statement = parser(pdf_path)
        except (ActivoBankParseError, ValueError):
            logger.error("parse failed, skipping %s", pdf_path.name)
            conn.rollback()
            _record_file(conn, file_hash, pdf_path.name, "parse_failed", None)
            conn.commit()
            failed += 1
            continue

        # statement period+account dedup (ADR-7): skip if already present.
        dupe = conn.execute(
            "SELECT id FROM statements WHERE account_id = ? AND period_start = ? "
            "AND period_end = ?",
            (account["id"], statement.period_start.isoformat(),
             statement.period_end.isoformat()),
        ).fetchone()
        if dupe is not None:
            logger.debug("statement period already ingested, skipping %s", pdf_path.name)
            _record_file(conn, file_hash, pdf_path.name, "ok", dupe["id"])
            conn.commit()
            skipped += 1
            continue

        try:
            # Statement first, then the processed_files row points at it
            # (processed_files.statement_id -> statements). One-directional FK,
            # no cycle, no backfill.
            statement_id = persist_statement(conn, account["id"], statement)
            _record_file(conn, file_hash, pdf_path.name, "ok", statement_id)
            conn.commit()
            logger.info(
                "ingested %s (%d transactions)", pdf_path.name, len(statement.transactions)
            )
            ingested += 1
        except Exception:
            conn.rollback()
            _record_file(conn, file_hash, pdf_path.name, "parse_failed", None)
            conn.commit()
            failed += 1
            raise

    logger.info(
        "processed %d file(s): %d ingested, %d skipped, %d failed",
        len(pdfs), ingested, skipped, failed,
    )
