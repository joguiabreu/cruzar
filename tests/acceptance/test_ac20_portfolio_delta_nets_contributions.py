"""AC20 / ADR-14: Portfolio Δ = (IV_end − IV_prev) − NetContrib over investment
accounts, in EUR, month-to-month.

The four SPEC fixtures:
  (i)   a contribution checking→brokerage is subtracted (Δ unaffected by the transfer);
  (ii)  an internal buy funded by existing cash leaves Δ unchanged;
  (iii) a price-only rise raises Δ by exactly that amount;
  (iv)  no prior snapshot renders "—" (the function returns None).

Plus two that exercise the slice's machinery directly:
  - an account that can't emit cash flows degrades to a flagged GROSS figure (D1/D3);
  - the `flatex Deposit` pattern counts as an external contribution while
    `Flatex Interest Income` stays a return (D4).

Offline: EUR throughout, fetch=None (the suite never hits the network).
"""

from __future__ import annotations

import hashlib
import sqlite3
from decimal import Decimal
from pathlib import Path

from cruzar import metrics
from cruzar.db import connect, init_schema

_PATTERNS = ["flatex Deposit"]


def _account(
    conn: sqlite3.Connection, account_type: str = "brokerage", *, emits_cash_flows: bool = True
) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, emits_cash_flows, created_at) "
        "VALUES (?, ?, ?, 'manual', ?, 'EUR', ?, '2026-01-01T00:00:00+00:00')",
        (f"bank-{account_type}", f"{account_type} acct", f"{account_type}-m",
         account_type, int(emits_cash_flows)),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _statement(conn: sqlite3.Connection, account_id: int, period_end: str, closing: str) -> int:
    cur = conn.execute(
        "INSERT INTO statements(account_id, period_start, period_end, closing_balance, "
        "created_at) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
        (account_id, period_end, period_end, closing),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _holding(conn: sqlite3.Connection, account_id: int, statement_id: int, snapshot: str, value: str) -> None:
    conn.execute(
        "INSERT INTO holdings_snapshot(account_id, statement_id, symbol, snapshot_date, "
        "quantity, cost_basis, value, currency) VALUES (?, ?, 'AAAA', ?, '1', NULL, ?, 'EUR')",
        (account_id, statement_id, snapshot, value),
    )


def _txn(
    conn: sqlite3.Connection, statement_id: int, on: str, amount: str, description: str,
    *, seq: int, is_transfer: int = 0,
) -> None:
    content_hash = hashlib.sha256(f"{statement_id}-{seq}-{amount}".encode()).hexdigest()
    conn.execute(
        "INSERT INTO transactions(statement_id, date, amount, description_raw, "
        "intra_statement_seq, is_transfer, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (statement_id, on, amount, description, seq, is_transfer, content_hash),
    )


def _delta(conn: sqlite3.Connection, ym: str = "2026-05") -> metrics.Delta | None:
    return metrics.portfolio_delta(conn, ym, patterns=_PATTERNS, fetch=None)


def test_ac20_i_contribution_is_subtracted(tmp_path: Path) -> None:
    """checking→brokerage transfer of 2000 funds a 2000 rise in securities → Δ = 0."""
    conn = connect(tmp_path / "pd.db")
    try:
        init_schema(conn)
        broker = _account(conn)
        apr = _statement(conn, broker, "2026-04-30", "0.00")
        _holding(conn, broker, apr, "2026-04-30", "10000.00")
        may = _statement(conn, broker, "2026-05-31", "0.00")
        _holding(conn, broker, may, "2026-05-31", "12000.00")
        _txn(conn, may, "2026-05-10", "2000.00", "Transferência para investimento",
             seq=1, is_transfer=1)
        conn.commit()
        # (12000 − 10000) − 2000 = 0; the transfer itself does not move Δ.
        assert _delta(conn) == metrics.Delta(Decimal("0.00"), False)
    finally:
        conn.close()


def test_ac20_ii_internal_buy_leaves_delta_unchanged(tmp_path: Path) -> None:
    """Cash 1000 → securities (an internal buy, not an external flow) → Δ = 0."""
    conn = connect(tmp_path / "pd.db")
    try:
        init_schema(conn)
        broker = _account(conn)
        apr = _statement(conn, broker, "2026-04-30", "1000.00")  # uninvested cash
        _holding(conn, broker, apr, "2026-04-30", "8000.00")
        may = _statement(conn, broker, "2026-05-31", "0.00")     # cash deployed
        _holding(conn, broker, may, "2026-05-31", "9000.00")
        _txn(conn, may, "2026-05-10", "-1000.00", "Compra", seq=1)  # internal buy
        conn.commit()
        # IV_prev 9000, IV_end 9000, no external flow → 0.
        assert _delta(conn) == metrics.Delta(Decimal("0.00"), False)
    finally:
        conn.close()


def test_ac20_iii_price_rise_raises_delta(tmp_path: Path) -> None:
    """No flows; securities revalue 5000 → 5300 → Δ = +300."""
    conn = connect(tmp_path / "pd.db")
    try:
        init_schema(conn)
        broker = _account(conn)
        apr = _statement(conn, broker, "2026-04-30", "0.00")
        _holding(conn, broker, apr, "2026-04-30", "5000.00")
        may = _statement(conn, broker, "2026-05-31", "0.00")
        _holding(conn, broker, may, "2026-05-31", "5300.00")
        conn.commit()
        assert _delta(conn) == metrics.Delta(Decimal("300.00"), False)
    finally:
        conn.close()


def test_ac20_iv_no_prior_snapshot_renders_dash(tmp_path: Path) -> None:
    """Only a current snapshot, nothing earlier → None (rendered '—')."""
    conn = connect(tmp_path / "pd.db")
    try:
        init_schema(conn)
        broker = _account(conn)
        may = _statement(conn, broker, "2026-05-31", "0.00")
        _holding(conn, broker, may, "2026-05-31", "5000.00")
        conn.commit()
        assert _delta(conn) is None
    finally:
        conn.close()


def test_ac20_gross_when_account_cannot_emit_cash_flows(tmp_path: Path) -> None:
    """An emits_cash_flows=0 account's contributions are undetectable: Δ is gross
    (the transfer is NOT subtracted) and flagged."""
    conn = connect(tmp_path / "pd.db")
    try:
        init_schema(conn)
        broker = _account(conn, emits_cash_flows=False)
        apr = _statement(conn, broker, "2026-04-30", "0.00")
        _holding(conn, broker, apr, "2026-04-30", "10000.00")
        may = _statement(conn, broker, "2026-05-31", "0.00")
        _holding(conn, broker, may, "2026-05-31", "12000.00")
        # A real contribution exists, but this parser can't surface it as a txn we'd
        # trust; even a transfer on this account is excluded from NetContrib.
        _txn(conn, may, "2026-05-10", "2000.00", "Deposit", seq=1, is_transfer=1)
        conn.commit()
        # gross = 12000 − 10000 = 2000; NetContrib excluded → flagged.
        assert _delta(conn) == metrics.Delta(Decimal("2000.00"), True)
    finally:
        conn.close()


def test_ac20_deposit_pattern_is_contribution_interest_is_return(tmp_path: Path) -> None:
    """`flatex Deposit` (is_transfer=0) is an external contribution and is subtracted;
    `Flatex Interest Income` is a return and stays in Δ. IV_end carries both as cash."""
    conn = connect(tmp_path / "pd.db")
    try:
        init_schema(conn)
        broker = _account(conn)
        apr = _statement(conn, broker, "2026-04-30", "0.00")
        _holding(conn, broker, apr, "2026-04-30", "10000.00")
        may = _statement(conn, broker, "2026-05-31", "50.00")   # interest sits as cash
        _holding(conn, broker, may, "2026-05-31", "11000.00")   # deposit was invested
        _txn(conn, may, "2026-05-05", "1000.00", "flatex Deposit", seq=1)
        _txn(conn, may, "2026-05-06", "50.00", "Flatex Interest Income", seq=2)
        conn.commit()
        # IV_end 11050, IV_prev 10000; NetContrib = 1000 (deposit only).
        # Δ = (11050 − 10000) − 1000 = 50 = the interest, counted as return.
        assert _delta(conn) == metrics.Delta(Decimal("50.00"), False)
    finally:
        conn.close()
