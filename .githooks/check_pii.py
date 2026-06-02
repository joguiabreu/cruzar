#!/usr/bin/env python3
"""Deterministic pre-commit PII guard.

Aborts the commit if any literal term in the gitignored ``.pii-denylist``
appears in staged content. Unlike eyeballing, this does not get bored or
pattern-match selectively: a denylisted salary figure or account number either
appears in the staged blob or it does not. Numbers are matched
space-insensitively, so "1 234.56" and "1234.56" both trip the same term.

PDFs are deep-scanned via pdfplumber when available (the real risk is text files
like expected.json/source/docs, but staged PDFs are checked too). The denylist
holds only DISTINCTIVE real values — never round numbers a synthetic fixture
legitimately uses (e.g. 100.00) — to avoid false positives.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def repo_root() -> Path:
    return Path(_git("rev-parse", "--show-toplevel").decode().strip())


def staged_files() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACM").decode()
    return [line for line in out.splitlines() if line]


def staged_bytes(path: str) -> bytes:
    return _git("show", f":{path}")


def extract_pdf_text(data: bytes) -> str:
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except ImportError:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


def main() -> int:
    root = repo_root()
    denylist = root / ".pii-denylist"
    if not denylist.exists():
        print(
            "pii-guard: no .pii-denylist found; PII scanning disabled. "
            "Create one (it is gitignored) to enable.",
            file=sys.stderr,
        )
        return 0

    terms = [
        line.strip()
        for line in denylist.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not terms:
        return 0
    norm_terms = [(t, _norm(t)) for t in terms]

    violations: list[str] = []
    for path in staged_files():
        data = staged_bytes(path)
        text = data.decode("utf-8", errors="ignore")
        if path.lower().endswith(".pdf"):
            text = f"{text}\n{extract_pdf_text(data)}"
        ntext = _norm(text)
        for raw, nt in norm_terms:
            if raw in text or (nt and nt in ntext):
                # Never echo the secret itself — only where it was found.
                violations.append(path)
                break

    if violations:
        print("pii-guard: BLOCKED — a .pii-denylist value appears in staged files:", file=sys.stderr)
        for path in violations:
            print(f"  {path}", file=sys.stderr)
        print(
            "Remove the value or unstage the file, then commit again. "
            "(Override only if certain: git commit --no-verify)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
