"""Cruzar CLI. Implements `cruzar process` (manual ingest → persist → report) and
`cruzar report` (read-only re-render of reports from the existing DB). `cruzar fetch`
(Gmail) is a later slice.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cruzar.pipeline import ask, process, report_only

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
    ask_parser = sub.add_parser(
        "ask", help="ask a free-form question about your data (local LLM, read-only)"
    )
    ask_parser.add_argument("question", help="e.g. \"how much did I spend on Dining last 6 months?\"")
    anon_parser = sub.add_parser(
        "anonymize",
        help="produce a privacy-safe layout sample of a statement for parser development "
        "(local LLM; operator tool, not part of `process`)",
    )
    anon_parser.add_argument("pdf", help="path to the real statement PDF (kept local)")
    anon_parser.add_argument(
        "-o", "--out", default=None,
        help="output dir (default: data/parsergen/<pdf-stem>/)",
    )
    anon_parser.add_argument(
        "--model", default=None,
        help="Ollama model for classification (default: llm.anonymize_model, else llm.model). "
        "A bigger model over-scrubs fewer labels.",
    )
    anon_parser.add_argument(
        "--timeout", type=float, default=None,
        help="per-request seconds (default: llm.anonymize_timeout_seconds, else llm.timeout_seconds)",
    )

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
    elif args.command == "ask":
        print(ask(_ROOT / "data" / "cruzar.db", _ROOT / "config", args.question))
    elif args.command == "anonymize":
        return _anonymize(Path(args.pdf), args.out, args.model, args.timeout)
    return 0


def _anonymize(pdf_path: Path, out: str | None, model: str | None, timeout: float | None) -> int:
    """Operator command: anonymize one statement to a gitignored dev sample. Needs the local
    LLM (classification runs on it); fails loud and writes nothing if the safety gate finds a
    leak. The result is NOT sent anywhere — that is an explicit later step (plan 029)."""
    from cruzar.categorize import LlmError
    from cruzar.config import load_config
    from cruzar.parsergen.gates import FidelityGateError, SafetyGateError
    from cruzar.parsergen.run import anonymize_file

    config = load_config(_ROOT / "config")
    if not config.llm.enabled:
        logging.error(
            "anonymize needs the local LLM — set llm.enabled: true in config/cruzar.yaml "
            "and make sure Ollama is running."
        )
        return 1
    out_dir = Path(out) if out else _ROOT / "data" / "parsergen" / pdf_path.stem

    # anonymize prefers a stronger, slower model than categorization: CLI flag > llm.anonymize_*
    # > llm.* defaults.
    resolved_model = model or config.llm.anonymize_model or config.llm.model
    resolved_timeout = timeout or config.llm.anonymize_timeout or config.llm.timeout
    logging.info("anonymizing with model %s (timeout %.0fs)", resolved_model, resolved_timeout)

    from cruzar.llm import ollama_token_classifier

    classifier = ollama_token_classifier(resolved_model, config.llm.host, resolved_timeout)
    try:
        summary = anonymize_file(pdf_path, out_dir, classifier, repo_root=_ROOT)
    except SafetyGateError as exc:
        logging.error("SAFETY GATE FAILED — wrote nothing. %s", exc)
        return 1
    except FidelityGateError as exc:
        logging.error("fidelity gate failed after retries — wrote nothing. %s", exc)
        return 1
    except LlmError as exc:
        logging.error("local model error — wrote nothing. %s", exc)
        return 1

    print(
        f"Anonymized {summary.words} words ({summary.replaced} replaced) in "
        f"{summary.attempts} attempt(s).\n"
        f"  bundle: {summary.bundle_path}\n"
        f"  report: {summary.report_path}\n"
        "Gates passed (no value-shaped data survives). REVIEW the bundle for any personal NAME "
        "the model missed — names aren't value-shaped, so add yours to .pii-denylist to scrub "
        "them deterministically. Nothing has left your machine."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
