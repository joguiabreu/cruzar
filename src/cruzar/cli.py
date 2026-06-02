"""Cruzar CLI. This slice implements `cruzar process` (manual ingest → persist →
report). `cruzar fetch` / `cruzar report` are later slices.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cruzar.pipeline import process

_ROOT = Path.cwd()


def main(argv: list[str] | None = None) -> int:
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
