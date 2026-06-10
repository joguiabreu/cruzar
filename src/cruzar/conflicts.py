"""Restated-transaction detection (ADR-8) — the normalize stage (ADR-4).

A corrected/restated line that reappears on a LATER statement hashes differently
(its amount changed), so it survives transaction dedup (ADR-7) as a second row.
ADR-8 is first-write-wins: keep the earliest leg, flag the later one, never merge,
never double-count.

Deterministic and fully recomputed each run (like ``transfers.detect``): every row
is reset to ``superseded = 0``, then the later legs are set to 1, so reprocessing is
idempotent (AC1). No LLM — ADR-12 untouched.

Conflict key is ``(account_id, date, description_raw)`` — ``intra_statement_seq`` is
deliberately excluded: it's a per-statement ordinal and drifts when a line reappears
amid different neighbours, so keying on it would miss real restatements. A group is a
conflict only when it spans **≥2 statements**; two same-key lines on the *same*
statement are independent transactions (never coalesced) and are left alone.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict

logger = logging.getLogger(__name__)


def detect(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT t.id AS id, s.account_id AS account_id, t.date AS date, "
        "t.statement_id AS statement_id, t.description_raw AS description_raw "
        "FROM transactions t JOIN statements s ON t.statement_id = s.id "
        "ORDER BY t.id"  # lowest id = first write (ADR-8)
    ).fetchall()

    groups: dict[tuple[int, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[(row["account_id"], row["date"], row["description_raw"])].append(row)

    superseded: list[int] = []
    for members in groups.values():
        statement_ids = {m["statement_id"] for m in members}
        if len(statement_ids) < 2:
            continue  # all on one statement → independent lines, not a restatement
        # The earliest statement (holding the lowest-id row) is the original;
        # every row on a later statement is a restatement.
        earliest = members[0]["statement_id"]  # rows are ordered by id
        superseded.extend(m["id"] for m in members if m["statement_id"] != earliest)

    # Full recompute: 0 for everyone, then 1 for the later legs.
    conn.execute("UPDATE transactions SET superseded = 0")
    if superseded:
        conn.executemany(
            "UPDATE transactions SET superseded = 1 WHERE id = ?",
            [(tid,) for tid in superseded],
        )
    conn.commit()
    logger.info("flagged %d restated transaction(s) (ADR-8)", len(superseded))
