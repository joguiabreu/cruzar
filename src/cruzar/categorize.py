"""Rule-only categorization (ADR-13, rule tier).

For each transaction not frozen as ``manual``, match ``merchant_patterns`` (lower
priority wins, ties broken by id). A match sets merchant_id + merchant_source =
'rule' (overriding a prior 'llm'); no match clears to 'none'. LLM proposals are
a later slice. Re-evaluated every run, so it is idempotent.
"""

from __future__ import annotations

import re
import sqlite3


def categorize(conn: sqlite3.Connection) -> None:
    patterns = conn.execute(
        "SELECT merchant_id, pattern FROM merchant_patterns ORDER BY priority ASC, id ASC"
    ).fetchall()
    compiled = [(row["merchant_id"], re.compile(row["pattern"], re.IGNORECASE)) for row in patterns]

    rows = conn.execute(
        "SELECT id, description_raw FROM transactions WHERE merchant_source != 'manual'"
    ).fetchall()
    for row in rows:
        matched_merchant: int | None = None
        for merchant_id, rx in compiled:
            if rx.search(row["description_raw"]):
                matched_merchant = merchant_id
                break
        if matched_merchant is not None:
            conn.execute(
                "UPDATE transactions SET merchant_id = ?, merchant_source = 'rule' WHERE id = ?",
                (matched_merchant, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE transactions SET merchant_id = NULL, merchant_source = 'none' WHERE id = ?",
                (row["id"],),
            )
    conn.commit()
