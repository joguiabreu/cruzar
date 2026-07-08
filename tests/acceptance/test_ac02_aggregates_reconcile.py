"""AC2: report aggregates reconcile in the storage currency.

- Native (exact): an EUR-only Earned/Spent equals the raw SUM(transactions.amount)
  over the same account-class / period / is_transfer / superseded filter (EUR→EUR is
  identity, so the metric must equal the signed sum exactly — transfers and superseded
  legs excluded).
- Base (method-consistent): with a foreign-currency account, the converted Summary
  figure equals the test applying the SAME ADR-5 method (per-currency sum, converted
  at the period-end rate) — asserting same method, not converted == native.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from cruzar import fx, metrics
from cruzar.db import connect, init_schema

_YM = "2026-05"
_END = metrics.month_end(_YM)


def _account(conn: sqlite3.Connection, name: str, currency: str) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES (?, ?, ?, 'manual', 'checking', ?, '2026-01-01T00:00:00+00:00')",
        (name, name, name, currency),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(conn: sqlite3.Connection, account_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, "
        "closing_balance, created_at) VALUES (?, '2026-05-01', '2026-05-31', '0.00', 'x')",
        (account_id,),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _txn(
    conn: sqlite3.Connection,
    statement_id: int,
    seq: int,
    amount: str,
    *,
    is_transfer: int = 0,
    superseded: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO transactions(statement_id, date, amount, description_raw, "
        "intra_statement_seq, is_transfer, superseded, content_hash) "
        "VALUES (?, '2026-05-10', ?, ?, ?, ?, ?, ?)",
        (statement_id, amount, f"T{seq}", seq, is_transfer, superseded, f"h{statement_id}-{seq}"),
    )


def test_ac02_native_exact(tmp_path: Path) -> None:
    conn = connect(tmp_path / "n.db")
    try:
        init_schema(conn)
        eur = _account(conn, "EUR-Checking", "EUR")
        stmt = _statement(conn, eur)
        _txn(conn, stmt, 1, "-10.00")  # spend
        _txn(conn, stmt, 2, "-25.00")  # spend
        _txn(conn, stmt, 3, "2000.00")  # earn
        _txn(conn, stmt, 4, "-5.00", is_transfer=1)  # excluded (transfer)
        _txn(conn, stmt, 5, "-99.00", superseded=1)  # excluded (restated)
        conn.commit()

        # Raw oracle: the exact filter the metric documents, summed in SQL.
        raw_spent = conn.execute(
            "SELECT amount FROM transactions WHERE amount LIKE '-%' "
            "AND is_transfer = 0 AND superseded = 0 AND substr(date,1,7) = ?",
            (_YM,),
        ).fetchall()
        oracle_spent = sum((Decimal(r["amount"]) for r in raw_spent), Decimal(0))

        assert oracle_spent == Decimal("-35.00")  # transfer/superseded not in it
        assert metrics.spent(conn, _YM, fetch=None) == oracle_spent  # EUR→EUR identity
        assert metrics.earned(conn, _YM, fetch=None) == Decimal("2000.00")
    finally:
        conn.close()


def test_ac02_base_method_consistent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "b.db")
    try:
        init_schema(conn)
        eur = _account(conn, "EUR-Checking", "EUR")
        _txn(conn, _statement(conn, eur), 1, "-35.00")
        usd = _account(conn, "USD-Checking", "USD")
        _txn(conn, _statement(conn, usd), 1, "-100.00")
        conn.execute(
            "INSERT INTO fx_rates(date, base_currency, quote_currency, rate) "
            "VALUES ('2026-05-31', 'EUR', 'USD', '2.00')"
        )
        conn.commit()

        # Same ADR-5 method, recomputed in the test: per-currency native sum,
        # converted at the period-end rate. (USD -100 / 2.00 = -50 EUR.)
        expected = Decimal("-35.00") + fx.convert(conn, Decimal("-100.00"), "USD", _END, fetch=None)
        assert expected == Decimal("-85.00")
        assert metrics.spent(conn, _YM, fetch=None) == expected
    finally:
        conn.close()
