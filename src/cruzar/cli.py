"""Cruzar CLI. This slice implements `cruzar process` (manual ingest → persist →
report). `cruzar fetch` / `cruzar report` are later slices.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cruzar.pipeline import process

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


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="cruzar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("process", help="ingest /data/inbox, persist, write reports")

    args = parser.parse_args(argv)
    if args.command == "process":
        process(
            db_path=_ROOT / "data" / "cruzar.db",
            inbox_dir=_ROOT / "data" / "inbox",
            config_dir=_ROOT / "config",
            reports_dir=_ROOT / "reports",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
