-- FROZEN baseline schema — an intentionally OLD snapshot of schema.sql, used by
-- tests/test_schema_parity.py to prove that init_schema() upgrades any prior DB
-- to match the CURRENT schema.sql via db._migrate().
--
-- DO NOT track every schema.sql edit here. This is the floor the migration chain
-- must rebuild from: leaving it old means every additive migration must exist for
-- a fresh DB and a baseline-upgraded DB to come out identical. Only touch it for a
-- non-additive change (rename/drop) that makes the old snapshot un-upgradable.
--
-- This snapshot predates plan_007's holdings_snapshot.currency column.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS merchants (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL REFERENCES categories(name)
);

CREATE TABLE IF NOT EXISTS merchant_patterns (
    id          INTEGER PRIMARY KEY,
    merchant_id INTEGER NOT NULL REFERENCES merchants(id),
    pattern     TEXT NOT NULL,
    priority    INTEGER NOT NULL,
    UNIQUE(merchant_id, pattern)
);

CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY,
    institution  TEXT NOT NULL,
    name         TEXT NOT NULL,
    account_match TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK (source_type IN ('email', 'manual')),
    account_type TEXT NOT NULL CHECK (account_type IN ('checking', 'savings', 'brokerage', 'retirement')),
    currency     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    closed_at    TEXT,
    UNIQUE(institution, account_match)
);

CREATE TABLE IF NOT EXISTS processed_files (
    file_hash         TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    processed_at      TEXT NOT NULL,
    statement_id      INTEGER REFERENCES statements(id),
    status            TEXT NOT NULL CHECK (status IN ('ok', 'parse_failed', 'extraction_failed', 'unresolved_account'))
);

CREATE TABLE IF NOT EXISTS statements (
    id                INTEGER PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(id),
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    closing_balance   TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE(account_id, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS transactions (
    id                  INTEGER PRIMARY KEY,
    statement_id        INTEGER NOT NULL REFERENCES statements(id),
    date                TEXT NOT NULL,
    amount              TEXT NOT NULL,
    description_raw     TEXT NOT NULL,
    intra_statement_seq INTEGER NOT NULL,
    is_transfer         INTEGER NOT NULL DEFAULT 0,
    merchant_id         INTEGER REFERENCES merchants(id),
    merchant_source     TEXT NOT NULL DEFAULT 'none' CHECK (merchant_source IN ('manual', 'rule', 'llm', 'none')),
    content_hash        TEXT NOT NULL UNIQUE
);

-- NOTE: no `currency` column here — the migration must add it.
CREATE TABLE IF NOT EXISTS holdings_snapshot (
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    statement_id  INTEGER NOT NULL REFERENCES statements(id),
    symbol        TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    quantity      TEXT NOT NULL,
    cost_basis    TEXT NOT NULL,
    value         TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol, snapshot_date)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    date           TEXT NOT NULL,
    base_currency  TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate           TEXT NOT NULL,
    PRIMARY KEY (date, base_currency, quote_currency)
);
