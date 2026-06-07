"""Schema-upgrade guard: init_schema() must bring an OLD database fully in line
with the current schema.sql via db._migrate().

This is the guardrail that the slice-7 bug needed: the test suite otherwise only
ever builds fresh DBs, so a new column in schema.sql with no matching migration
passes ruff/pyright/pytest yet breaks a pre-existing real DB on `cruzar process`.

Here a DB is created from a FROZEN old baseline (tests/schema_baseline.sql), then
init_schema() is run; its tables must end up identical to a fresh DB. Add a column
to schema.sql without a db._migrate() step and this fails.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cruzar.db import connect, init_schema

_BASELINE_SQL = (Path(__file__).parent / "schema_baseline.sql").read_text(encoding="utf-8")


def _schema(conn: sqlite3.Connection) -> dict[str, dict[str, tuple[str, int, int]]]:
    """Map each user table to {column: (type, notnull, pk)}.

    Includes the NOT NULL flag so a migration that must drop/add NOT NULL (not just
    add a column) is also verified. The default value is intentionally excluded —
    an additive ALTER ... DEFAULT legitimately differs from the fresh DDL.
    """
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        t: {
            row[1]: (row[2].upper(), row[3], row[5])  # name: (type, notnull, pk)
            for row in conn.execute(f"PRAGMA table_info({t})")
        }
        for t in tables
    }


def test_baseline_db_upgrades_to_current_schema(tmp_path: Path) -> None:
    fresh = connect(tmp_path / "fresh.db")
    legacy = connect(tmp_path / "legacy.db")
    try:
        init_schema(fresh)  # current schema.sql + migrations

        legacy.executescript(_BASELINE_SQL)  # an old DB
        legacy.commit()
        init_schema(legacy)  # must migrate it up to current

        assert _schema(legacy) == _schema(fresh)
    finally:
        fresh.close()
        legacy.close()
