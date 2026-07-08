#!/usr/bin/env python3
"""Deterministic commit-message PII guard.

Aborts the commit if any literal term in the gitignored ``.pii-denylist`` appears
in the commit MESSAGE. This complements ``check_pii.py`` (the pre-commit hook),
which scans staged file *content* but not the message — the gap that once let a
real balance slip into a commit message. Same denylist, same space-insensitive
matching; only the input differs (the message file Git passes as ``$1``).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Reuse the denylist loading + matching from the sibling staged-file guard.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_pii import load_denylist, repo_root, text_hits_denylist  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:  # pragma: no cover - Git always passes the message path
        print("commit-msg pii-guard: no message file argument", file=sys.stderr)
        return 0
    message = Path(argv[1]).read_text(encoding="utf-8", errors="ignore")

    norm_terms = load_denylist(repo_root())
    if not norm_terms:  # no denylist, or it's empty → nothing to enforce
        return 0

    if text_hits_denylist(message, norm_terms):
        # Never echo the secret itself — only that the message carries one.
        print(
            "pii-guard: BLOCKED — a .pii-denylist value appears in the commit message.\n"
            "Redact it from the message, then commit again. "
            "(Override only if certain: git commit --no-verify)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
