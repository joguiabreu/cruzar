"""AC5: no secret material on disk outside the Keychain (necessary-not-sufficient).

Scans every git-tracked file AND a freshly-built DB for token-SHAPED values — a
Google access token (``ya29.<token>``) or refresh token (``1//<token>``). We match
value shapes, not the bare words ``ya29``/``refresh_token`` (which appear as examples
in SPEC.md and plans), so a real leaked credential is caught without false-positiving
on documentation. ADR-9: secrets live only in the macOS Keychain via ``keyring``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cruzar.db import connect, init_schema

_REPO = Path(__file__).resolve().parents[2]
_THIS = Path(__file__).name

# Token-shaped values (a prefix followed by a long run of token characters), not the
# bare config words — those are legitimately present in docs as examples.
_TOKEN_PATTERNS = [
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),  # Google OAuth access token
    re.compile(r"1//[A-Za-z0-9_\-]{30,}"),  # Google OAuth refresh token
]


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [_REPO / name for name in out.split("\0") if name and name != f"tests/acceptance/{_THIS}"]


def test_ac05_no_token_shaped_values_in_tracked_files() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary (e.g. fixture PDFs) or removed; nothing text to leak
        if any(p.search(content) for p in _TOKEN_PATTERNS):
            offenders.append(str(path.relative_to(_REPO)))
    assert not offenders, f"token-shaped values found in tracked files: {offenders}"


def test_ac05_no_token_shaped_values_in_db(tmp_path: Path) -> None:
    db_path = tmp_path / "scan.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
    finally:
        conn.close()
    blob = db_path.read_bytes().decode("latin-1")  # byte-faithful scan
    assert not any(p.search(blob) for p in _TOKEN_PATTERNS)
