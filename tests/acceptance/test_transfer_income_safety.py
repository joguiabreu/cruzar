"""Transfer/income safety (ADR-15): real third-party income must never be flagged
is_transfer by a description rule (it would silently drop from Earned). Exercises
ADR-15 step-1
with the live config/flows.yaml patterns: outbound / own-funding / internal-FX
descriptions ARE flagged; inbound transfers, ATM withdrawals, and purchases are
NOT.

Loads the committed flows.yaml patterns (not a hardcoded list) so a future edit
that breaks income protection fails here. Pairing (step 2) is covered by AC21; to
isolate the rule, all transactions sit on one account so step 2 stays inert.

Synthetic, obviously-fake values only.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from cruzar import transfers
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement, ParsedTransaction
from cruzar.persist import persist_statement

_FLOWS_YAML = Path(__file__).resolve().parents[2] / "config" / "flows.yaml"

# (description, amount) — flagged is_transfer by a rule (outbound / own-funding / FX).
_FLAGGED = [
    ("Transferência para EXEMPLO UM", Decimal("-100.00")),     # Revolut outbound
    ("P2P Personal Payments", Decimal("-50.00")),              # Revolut peer payment
    ("Carregamento com cartão *1000 De: *1000", Decimal("200.00")),  # Revolut top-up (inflow, own funding)
    ("Conversão cambial para PLN 200.00 PLN", Decimal("-30.00")),    # Revolut internal FX
    ("Trf imediata EXEMPLO DOIS", Decimal("-40.00")),          # Moey outbound (existing)
    ("TRF P/ EXEMPLO TRES", Decimal("-60.00")),                # ActivoBank outbound (existing)
]

# (description, amount) — NOT flagged: inbound income, ATM cash, purchases, salary.
_NOT_FLAGGED = [
    ("Transferência de EXEMPLO QUATRO", Decimal("300.00")),    # inbound — may be income
    ("Transferência de utilizador Revolut", Decimal("25.00")),  # inbound — may be income
    ("IPS/R9999999-EXEMPLO CINCO", Decimal("150.00")),         # Moey inbound — may be income
    ("Levantamento de numerário em ATM EXEMPLO", Decimal("-80.00")),  # ATM cash, not a transfer
    ("COMPRA SUPERMERCADO EXEMPLO", Decimal("-45.00")),        # spending
    ("TRANSFERENCIA - VENCIMENTO", Decimal("1000.00")),        # salary — income (AC19 carve-out)
]


def _add_account(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("testbank", "checking acct", "checking", "manual", "checking", "EUR",
         "2026-01-01T00:00:00+00:00"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _flag(conn: sqlite3.Connection, desc: str) -> int:
    row = conn.execute(
        "SELECT is_transfer FROM transactions WHERE description_raw = ?", (desc,)
    ).fetchone()
    assert row is not None, f"missing transaction {desc!r}"
    return int(row[0])


def test_transfer_income_safety(db_path: Path) -> None:
    patterns = yaml.safe_load(_FLOWS_YAML.read_text(encoding="utf-8"))["transfer_patterns"]

    conn = connect(db_path)
    try:
        init_schema(conn)
        account = _add_account(conn)
        rows = [*_FLAGGED, *_NOT_FLAGGED]
        txns = [
            ParsedTransaction(i, date(2026, 5, 1 + i), amount, desc)
            for i, (desc, amount) in enumerate(rows)
        ]
        persist_statement(conn, account, ParsedStatement(
            currency="EUR",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            closing_balance=Decimal("0.00"),
            transactions=txns,
        ))
        conn.commit()

        transfers.detect(conn, patterns)

        for desc, _ in _FLAGGED:
            assert _flag(conn, desc) == 1, f"expected transfer: {desc!r}"
        for desc, _ in _NOT_FLAGGED:
            assert _flag(conn, desc) == 0, f"expected NOT transfer: {desc!r}"
    finally:
        conn.close()
