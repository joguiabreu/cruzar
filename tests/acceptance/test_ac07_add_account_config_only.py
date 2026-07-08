"""AC7: adding an account requires only one sources.yaml entry (and, for a new
format, one parser module + one fixture) — no core pipeline changes. Demonstrated by
configuring TWO accounts that reuse already-registered parsers (ActivoBank + Moey):
dropping each statement under its folder, both resolve and persist with nothing in
`src/cruzar` touched. (The "new format ⇒ one parser + one fixture" half is evidenced
by the five AC8 parser fixtures.)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cruzar.db import connect
from cruzar.pipeline import process

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

_SOURCES = """\
accounts:
  - institution: activobank
    name: Checking One
    account_match: acct_a
    source_type: manual
    account_type: checking
    currency: EUR
  - institution: moey
    name: Checking Two
    account_match: acct_b
    source_type: manual
    account_type: checking
    currency: EUR
"""


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "sources.yaml").write_text(_SOURCES, encoding="utf-8")
    (cfg / "cruzar.yaml").write_text("base_currency: EUR\nllm:\n  enabled: false\n", encoding="utf-8")
    (cfg / "categories.yaml").write_text("categories:\n  - Other\n", encoding="utf-8")
    (cfg / "merchants.yaml").write_text("merchants: []\n", encoding="utf-8")
    return cfg


def _inbox(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    (inbox / "acct_a").mkdir(parents=True)
    (inbox / "acct_b").mkdir(parents=True)
    shutil.copy(_FIXTURES / "activobank" / "statement.pdf", inbox / "acct_a" / "s.pdf")
    shutil.copy(_FIXTURES / "moey" / "statement.pdf", inbox / "acct_b" / "s.pdf")
    return inbox


def test_ac07_two_accounts_from_config_only(tmp_path: Path) -> None:
    process(tmp_path / "c.db", _inbox(tmp_path), _config(tmp_path), tmp_path / "reports")
    conn = connect(tmp_path / "c.db")
    try:
        # Both accounts exist and each resolved its statement from config alone.
        accounts = {
            r["account_match"]: r["id"]
            for r in conn.execute("SELECT id, account_match FROM accounts").fetchall()
        }
        assert set(accounts) == {"acct_a", "acct_b"}

        for match in ("acct_a", "acct_b"):
            stmts = conn.execute(
                "SELECT COUNT(*) FROM statements WHERE account_id = ?", (accounts[match],)
            ).fetchone()[0]
            assert stmts == 1, f"{match} should have ingested one statement"

        # Both files processed ok; transactions landed under the right accounts.
        statuses = [r["status"] for r in conn.execute("SELECT status FROM processed_files")]
        assert statuses == ["ok", "ok"]
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 11 + 6
    finally:
        conn.close()
