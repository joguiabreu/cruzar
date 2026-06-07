"""Config seeding + statement/transaction persistence (ADR-3, ADR-7).

SQLite is the source of truth. yaml configs are seeded in idempotently each
run. Money is stored as canonical Decimal strings (see ``canonical_amount``),
which also feed the content_hash so scale drift cannot defeat dedup.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

from cruzar.config import Config
from cruzar.models import ParsedStatement

# ISO 4217 minor-unit scale (decimal places) per currency. EUR -> 2.
_CURRENCY_SCALE: dict[str, int] = {
    "EUR": 2,
    "USD": 2,
    "GBP": 2,
}


def canonical_amount(value: Decimal, currency: str) -> str:
    """Quantize ``value`` to the currency's ISO 4217 minor-unit scale and return
    the canonical string used for BOTH the stored amount and the content_hash.

    So Decimal("-100.0") and Decimal("-100.00") both serialize to "-100.00" and
    hash identically — cross-statement dedup cannot miss a duplicate on scale
    drift (plan decision 7).
    """
    scale = _CURRENCY_SCALE.get(currency.upper())
    if scale is None:
        raise ValueError(f"unknown currency scale for {currency!r}")
    quantum = Decimal(1).scaleb(-scale)  # e.g. Decimal("0.01") for scale 2
    return str(value.quantize(quantum))


def content_hash(
    account_id: int,
    posting_date: str,
    canonical_amt: str,
    description_raw: str,
    intra_statement_seq: int,
) -> str:
    """sha256(account_id, posting_date, amount, description_raw, intra_statement_seq).

    Spec §Transaction identity. ``canonical_amt`` must already be the canonical
    string from ``canonical_amount``.
    """
    payload = "\x1f".join(
        [
            str(account_id),
            posting_date,
            canonical_amt,
            description_raw,
            str(intra_statement_seq),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def seed_config(conn: sqlite3.Connection, config: Config) -> None:
    """Idempotently seed categories, merchants, patterns and accounts."""
    for name in config.categories:
        conn.execute(
            "INSERT INTO categories(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (name,),
        )
    for merchant in config.merchants:
        conn.execute(
            "INSERT INTO merchants(name, category) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET category = excluded.category",
            (merchant.name, merchant.category),
        )
        row = conn.execute(
            "SELECT id FROM merchants WHERE name = ?", (merchant.name,)
        ).fetchone()
        merchant_id = row["id"]
        for pat in merchant.patterns:
            conn.execute(
                "INSERT INTO merchant_patterns(merchant_id, pattern, priority) "
                "VALUES (?, ?, ?) ON CONFLICT(merchant_id, pattern) "
                "DO UPDATE SET priority = excluded.priority",
                (merchant_id, pat.pattern, pat.priority),
            )
    for acct in config.accounts:
        conn.execute(
            "INSERT INTO accounts(institution, name, account_match, source_type, "
            "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(institution, account_match) DO UPDATE SET "
            "name = excluded.name, source_type = excluded.source_type, "
            "account_type = excluded.account_type, currency = excluded.currency",
            (
                acct.institution,
                acct.name,
                acct.account_match,
                acct.source_type,
                acct.account_type,
                acct.currency,
                _now(),
            ),
        )
    conn.commit()


def persist_statement(
    conn: sqlite3.Connection,
    account_id: int,
    statement: ParsedStatement,
) -> int:
    """Insert the statement and its transactions atomically. Returns statement id.

    Transaction inserts are guarded on UNIQUE(content_hash) (ADR-7); a duplicate
    line is skipped rather than raising. The caller owns the surrounding
    transaction boundary (commit/rollback). Provenance (which file produced this
    statement) is recorded on processed_files.statement_id by the caller.
    """
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, "
        "closing_balance, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            account_id,
            statement.period_start.isoformat(),
            statement.period_end.isoformat(),
            canonical_amount(statement.closing_balance, statement.currency),
            _now(),
        ),
    )
    statement_id = cur.lastrowid
    assert statement_id is not None
    for txn in statement.transactions:
        amt = canonical_amount(txn.amount, statement.currency)
        posting_date = txn.date.isoformat()
        chash = content_hash(
            account_id, posting_date, amt, txn.description_raw, txn.intra_statement_seq
        )
        conn.execute(
            "INSERT INTO transactions(statement_id, date, amount, description_raw, "
            "intra_statement_seq, content_hash) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(content_hash) DO NOTHING",
            (statement_id, posting_date, amt, txn.description_raw,
             txn.intra_statement_seq, chash),
        )
    # Holdings snapshots are immutable (ADR-6): INSERT only, deduped on the PK
    # (account_id, symbol, snapshot_date) so reprocessing a statement adds nothing.
    # cost_basis/value are stored in each holding's OWN native currency.
    snapshot_date = statement.period_end.isoformat()
    for h in statement.holdings:
        conn.execute(
            "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, "
            "snapshot_date, quantity, cost_basis, value, currency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, symbol, snapshot_date) DO NOTHING",
            (account_id, statement_id, h.symbol, snapshot_date, str(h.quantity),
             canonical_amount(h.cost_basis, h.currency),
             canonical_amount(h.value, h.currency), h.currency),
        )
    return statement_id
