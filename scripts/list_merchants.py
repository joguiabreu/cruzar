"""List merchants grouped by category, plus the still-uncategorized tail, from the DB.

A review aid for two things: spotting a "junk drawer" category (a catch-all quietly
full of restaurants/shops), and seeing which recurring merchants are worth a rule in
config/merchants.yaml. Each merchant shows its transaction count and source
(`rule` = a pattern matched it; `llm` = the model proposed it). Reads your real
(gitignored) DB and prints to the terminal — nothing is written or committed.

    uv run python scripts/list_merchants.py [path/to/cruzar.db]
"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _ROOT / "data" / "cruzar.db"
_CASH = ("checking", "savings")


def main(argv: list[str]) -> int:
    db = Path(argv[1]) if len(argv) > 1 else _DEFAULT_DB
    if not db.exists():
        print(f"No DB at {db}. Run `cruzar process` first.")
        return 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT m.category AS category, m.name AS name, COUNT(t.id) AS n, "
            "GROUP_CONCAT(DISTINCT t.merchant_source) AS src "
            "FROM merchants m JOIN transactions t ON t.merchant_id = m.id "
            "GROUP BY m.category, m.name HAVING n > 0 "
            "ORDER BY m.category, n DESC, m.name"
        ).fetchall()
        by_cat: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            by_cat[row["category"]].append(row)

        print("=== Merchants by category (txn count, [source]) ===")
        for category in sorted(by_cat):
            items = by_cat[category]
            total = sum(r["n"] for r in items)
            print(f"\n## {category}  ({len(items)} merchants, {total} txns)")
            for r in items:
                print(f"  {r['n']:4d}  {r['name']}  [{r['src']}]")

        unc = conn.execute(
            "SELECT t.description_raw AS d, COUNT(*) AS n FROM transactions t "
            "JOIN statements s ON t.statement_id = s.id "
            "JOIN accounts a ON s.account_id = a.id "
            f"WHERE t.merchant_source = 'none' AND t.is_transfer = 0 AND t.superseded = 0 "
            f"AND a.account_type IN ({','.join('?' * len(_CASH))}) "
            "GROUP BY t.description_raw ORDER BY n DESC LIMIT 40",
            _CASH,
        ).fetchall()
        if unc:
            print(f"\n=== Still uncategorized — top {len(unc)} cash descriptions (rule candidates) ===")
            for r in unc:
                print(f"  {r['n']:4d}  {r['d']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
