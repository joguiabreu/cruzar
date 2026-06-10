"""Cruzar CLI. Implements `cruzar process` (manual ingest → persist → report) and
`cruzar report` (read-only re-render of reports from the existing DB). `cruzar fetch`
(Gmail) is a later slice.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cruzar.pipeline import process, report_only

_ROOT = Path.cwd()


class _CleanFormatter(logging.Formatter):
    """Plain text for INFO (progress the user wants), level-prefixed above it so
    warnings/errors stand out without prefixing every line."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno > logging.INFO:
            return f"{record.levelname}: {record.getMessage()}"
        return record.getMessage()


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_CleanFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # The OpenAI/httpx clients log every request + retry at INFO; keep our output to
    # Cruzar's own progress. Their warnings/errors still surface.
    for noisy in ("httpx", "httpcore", "openai", "instructor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="cruzar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("process", help="ingest /data/inbox, persist, write reports")
    sub.add_parser("report", help="re-render reports from the existing DB (read-only)")

    args = parser.parse_args(argv)
    if args.command == "process":
        process(
            db_path=_ROOT / "data" / "cruzar.db",
            inbox_dir=_ROOT / "data" / "inbox",
            config_dir=_ROOT / "config",
            reports_dir=_ROOT / "reports",
        )
    elif args.command == "report":
        report_only(
            db_path=_ROOT / "data" / "cruzar.db",
            config_dir=_ROOT / "config",
            reports_dir=_ROOT / "reports",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
