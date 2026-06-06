"""is_transfer detection (ADR-15 steps 1+2) — the normalize stage (ADR-4).

Deterministic and fully recomputed each run: a transaction is marked
``is_transfer = 1`` if its description matches a ``transfer_pattern`` (step 1) OR
it pairs with an opposite-signed, equal-magnitude transaction on another tracked
account within ±3 days (step 2); everything else is set to 0. There is no
``manual`` override for is_transfer (unlike merchant_source), so a full recompute
is correct and keeps reprocessing idempotent (AC1). No LLM — ADR-12 untouched.

The salary carve-out (``TRANSFERENCIA - VENCIMENTO`` is income, not a transfer)
is enforced by pattern specificity in flows.yaml, not by code here.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date
from decimal import Decimal

logger = logging.getLogger(__name__)

_PAIR_WINDOW_DAYS = 3  # ADR-15 step 2: opposite legs within ±3 calendar days


def detect(conn: sqlite3.Connection, transfer_patterns: list[str]) -> None:
    rows = conn.execute(
        "SELECT t.id AS id, s.account_id AS account_id, a.currency AS currency, "
        "t.date AS date, t.amount AS amount, t.description_raw AS description_raw "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id"
    ).fetchall()

    transfer_ids: set[int] = set()

    # Step 1 — description rules (case-insensitive, like merchant patterns).
    compiled = [re.compile(p, re.IGNORECASE) for p in transfer_patterns]
    for row in rows:
        if any(rx.search(row["description_raw"]) for rx in compiled):
            transfer_ids.add(row["id"])
    by_rule = len(transfer_ids)

    # Step 2 — account-pair matching (adds the paired legs to the set).
    by_pair = _pair_match(rows, transfer_ids)

    # Full recompute: 1 for members, 0 for everyone else.
    conn.executemany(
        "UPDATE transactions SET is_transfer = ? WHERE id = ?",
        [(1 if row["id"] in transfer_ids else 0, row["id"]) for row in rows],
    )
    conn.commit()
    logger.info(
        "marked %d transfer(s) (%d by rule, %d by pairing)",
        len(transfer_ids), by_rule, by_pair,
    )


def _pair_match(rows: list[sqlite3.Row], transfer_ids: set[int]) -> int:
    """Deterministic greedy one-to-one pairing. Returns the count of ids newly
    added to ``transfer_ids`` (legs not already flagged by a rule)."""
    items = sorted(
        (
            (
                row["id"],
                row["account_id"],
                row["currency"],
                date.fromisoformat(row["date"]),
                Decimal(row["amount"]),
            )
            for row in rows
        ),
        key=lambda it: (it[3], it[0]),  # (date, id) — stable, deterministic order
    )
    paired: set[int] = set()
    added = 0
    for tid, acct, cur, dt, amt in items:
        if tid in paired or amt == 0:
            continue
        best: tuple[int, int] | None = None  # (day_gap, candidate_id)
        for cid, cacct, ccur, cdt, camt in items:
            if cid == tid or cid in paired:
                continue
            if cacct == acct or ccur != cur:  # different account, same currency (D3)
                continue
            if amt + camt != 0:  # opposite sign AND equal magnitude in one test
                continue
            gap = abs((dt - cdt).days)
            if gap > _PAIR_WINDOW_DAYS:
                continue
            key = (gap, cid)
            if best is None or key < best:  # smallest gap, tie-break lowest id
                best = key
        if best is not None:
            mate = best[1]
            paired.update((tid, mate))
            for leg in (tid, mate):
                if leg not in transfer_ids:
                    transfer_ids.add(leg)
                    added += 1
    return added
