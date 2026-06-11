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
from datetime import UTC, date, datetime
from pathlib import Path

from cruzar import categorize, conflicts, fx, report, transfers
from cruzar.categorize import LlmError
from cruzar.config import load_config
from cruzar.db import connect, init_schema
from cruzar.extract import LlmExtractor
from cruzar.models import ParsedStatement
from cruzar.parsers import get_parser
from cruzar.parsers._common import ExtractionFallback, ParserError
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


def _ingest_summary(statement: ParsedStatement) -> str:
    """A human phrase for what a statement contributed — cash transactions and/or
    holdings. Investment statements carry holdings (snapshots), not transactions, so
    plain '0 transactions' hides that something was ingested."""

    def _n(count: int, noun: str) -> str:
        return f"{count} {noun}{'' if count == 1 else 's'}"

    n_txn, n_hold = len(statement.transactions), len(statement.holdings)
    parts: list[str] = []
    if n_txn or not n_hold:  # show txns unless it's a pure holdings snapshot
        parts.append(_n(n_txn, "transaction"))
    if n_hold:
        parts.append(_n(n_hold, "holding"))
    return ", ".join(parts)


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
        # LLM tier (ADR-2/13): build the Ollama clients only when enabled; otherwise
        # rule-only with zero calls and no extraction fallback. Imports are local so
        # offline runs never load instructor/openai.
        propose = None
        extractor: LlmExtractor | None = None
        if config.llm.enabled:
            from cruzar.llm import ollama_categorizer, ollama_extractor

            propose = ollama_categorizer(config.llm.model, config.llm.host, config.llm.timeout)
            extractor = ollama_extractor(config.llm.model, config.llm.host, config.llm.timeout)
        ingest_inbox(conn, Path(inbox_dir), extractor=extractor)
        transfers.detect(conn, config.transfer_patterns)  # normalize (ADR-15)
        conflicts.detect(conn)  # flag restated transactions (normalize, ADR-8)
        categorize.categorize(
            conn,
            propose=propose,
            model=config.llm.model,
            min_confidence=config.llm.min_confidence,
        )
        # FX for Net Worth: offline → no fetch (cache/manual rates only).
        fetch = (
            None
            if config.fx_offline
            else fx.live_fetcher(access_key=config.fx_access_key, timeout=config.fx_timeout)
        )
        report.write_reports(
            conn,
            Path(reports_dir),
            investment_flow_patterns=config.investment_flow_patterns,
            fetch=fetch,
        )
    finally:
        conn.close()


def report_only(
    db_path: str | Path, config_dir: str | Path, reports_dir: str | Path
) -> None:
    """Re-render the monthly reports from the existing DB — the `report` pipeline stage
    on its own (ADR-3/4). Read-only w.r.t. the DB (AC13): no ingest, normalize,
    categorize, or LLM, and no FX fetch (`fetch=None`) — a fetch would persist a rate.
    Converts using cached/manual `fx_rates`, rendering `n/a` for any absent month-end
    rate; fetching is `process`'s job. Writes only to `reports/`."""
    config = load_config(config_dir)
    conn = connect(db_path)
    try:
        init_schema(conn)  # idempotent; a no-op (no byte change) on an up-to-date DB
        report.write_reports(
            conn,
            Path(reports_dir),
            investment_flow_patterns=config.investment_flow_patterns,
            fetch=None,
        )
    finally:
        conn.close()


def ask(db_path: str | Path, config_dir: str | Path, question: str, *, today: date | None = None) -> str:
    """Answer a free-form question about the data (ADR-17). The local LLM maps the
    question to a bounded analytics query; Python computes the figure (ADR-1). Read-only,
    cached FX only (no fetch), like `report`. Returns a plain-text answer."""
    from datetime import date as _date

    from cruzar import analytics

    config = load_config(config_dir)
    if not config.llm.enabled:
        return (
            "The assistant needs the local LLM enabled — set `llm.enabled: true` in "
            "config/cruzar.yaml and make sure Ollama is running."
        )
    conn = connect(db_path)
    try:
        init_schema(conn)
        categories = [r["name"] for r in conn.execute("SELECT name FROM categories ORDER BY name")]
        from cruzar.llm import ollama_query_planner

        planner = ollama_query_planner(
            config.llm.model, config.llm.host, config.llm.timeout, categories
        )
        try:
            return analytics.answer(
                conn, question,
                planner=planner,
                today=today or _date.today(),
                fetch=None,  # read-only: cached/manual rates only
                investment_flow_patterns=config.investment_flow_patterns,
            )
        except LlmError as exc:
            return f"The assistant couldn't reach the local LLM ({exc}). Is Ollama running?"
    finally:
        conn.close()


def ingest_inbox(
    conn: sqlite3.Connection, inbox_dir: Path, *, extractor: LlmExtractor | None = None
) -> None:
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
        except ExtractionFallback as fallback:
            # AC4a: structured parse recovered <50% of columns. Hand the raw text to
            # the LLM extractor (ADR-2). No extractor (LLM disabled) or an unusable
            # result → extraction_failed, write nothing (fail loud), retried next run.
            if extractor is None:
                logger.error("layout too degraded and LLM disabled, skipping %s", pdf_path.name)
                _record_file(conn, file_hash, pdf_path.name, "extraction_failed", None)
                conn.commit()
                failed += 1
                continue
            try:
                statement = extractor.extract(fallback.text)
                logger.info("LLM extraction fallback for %s", pdf_path.name)
            except LlmError:
                logger.error("LLM extraction failed, skipping %s", pdf_path.name)
                conn.rollback()
                _record_file(conn, file_hash, pdf_path.name, "extraction_failed", None)
                conn.commit()
                failed += 1
                continue
        except (ParserError, ValueError) as exc:
            # Any parser's failure (ADR-11 ParserError base) or a stray date/Decimal
            # ValueError → mark this file parse_failed and continue; one bad statement
            # never crashes the whole run (SPEC §Edge cases: fail loud, write nothing).
            logger.error("parse failed, skipping %s (%s)", pdf_path.name, exc)
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
            logger.info("ingested %s (%s)", pdf_path.name, _ingest_summary(statement))
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
