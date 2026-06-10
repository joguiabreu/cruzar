-- Cruzar SQLite schema (spec §Data model). Money columns are TEXT holding
-- canonical Decimal strings; aggregation happens in Python, never SUM() in SQL.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY            -- controlled vocabulary, seeded from categories.yaml
);

CREATE TABLE IF NOT EXISTS merchants (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL REFERENCES categories(name)
);

CREATE TABLE IF NOT EXISTS merchant_patterns (
    id          INTEGER PRIMARY KEY,
    merchant_id INTEGER NOT NULL REFERENCES merchants(id),
    pattern     TEXT NOT NULL,       -- regex
    priority    INTEGER NOT NULL,    -- lower wins; ties broken by id
    UNIQUE(merchant_id, pattern)
);

CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY,
    institution  TEXT NOT NULL,
    name         TEXT NOT NULL,
    account_match TEXT NOT NULL,     -- folder/filename token (manual) or matched entry (email)
    source_type  TEXT NOT NULL CHECK (source_type IN ('email', 'manual')),
    account_type TEXT NOT NULL CHECK (account_type IN ('checking', 'savings', 'brokerage', 'retirement')),
    currency     TEXT NOT NULL,      -- ISO 4217
    created_at   TEXT NOT NULL,      -- immutable
    closed_at    TEXT,               -- nullable
    -- Parser capability (ADR-14): can this account's parser emit cash-flow
    -- transactions (deposits/withdrawals)? 0 => external contributions are
    -- undetectable, so its Portfolio Δ is reported gross and flagged.
    emits_cash_flows INTEGER NOT NULL DEFAULT 1,
    UNIQUE(institution, account_match)
);

CREATE TABLE IF NOT EXISTS processed_files (
    file_hash         TEXT PRIMARY KEY,   -- sha256 of file contents
    original_filename TEXT NOT NULL,
    processed_at      TEXT NOT NULL,
    statement_id      INTEGER REFERENCES statements(id),  -- nullable if parse/resolution failed
    status            TEXT NOT NULL CHECK (status IN ('ok', 'parse_failed', 'extraction_failed', 'unresolved_account'))
);

CREATE TABLE IF NOT EXISTS statements (
    id                INTEGER PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(id),
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    closing_balance   TEXT NOT NULL,      -- native currency, signed Decimal string
    created_at        TEXT NOT NULL,
    -- Provenance is one-directional: processed_files.statement_id points here.
    -- "which file produced this statement?" is answered by querying the other
    -- way, so no back-reference is stored (avoids a circular FK).
    UNIQUE(account_id, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS transactions (
    id                  INTEGER PRIMARY KEY,
    statement_id        INTEGER NOT NULL REFERENCES statements(id),
    date                TEXT NOT NULL,
    amount              TEXT NOT NULL,    -- signed, native currency, canonical Decimal string
    description_raw     TEXT NOT NULL,    -- immutable
    intra_statement_seq INTEGER NOT NULL, -- line ordinal within statement; feeds content_hash
    is_transfer         INTEGER NOT NULL DEFAULT 0,
    -- ADR-8: a restated line on a LATER statement hashes differently (amount
    -- changed) and survives dedup as a second row. conflicts.detect sets this 1
    -- on the later leg; aggregates exclude it so it's never double-counted. The
    -- earliest leg (first write) stays 0 and is kept.
    superseded          INTEGER NOT NULL DEFAULT 0,
    merchant_id         INTEGER REFERENCES merchants(id),  -- nullable, mutable
    merchant_source     TEXT NOT NULL DEFAULT 'none' CHECK (merchant_source IN ('manual', 'rule', 'llm', 'none')),
    content_hash        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS holdings_snapshot (
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    statement_id  INTEGER NOT NULL REFERENCES statements(id),
    symbol        TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,        -- = statement.period_end
    quantity      TEXT NOT NULL,
    cost_basis    TEXT,                 -- broker-reported aggregate, native currency; NULL if the broker doesn't report it
    value         TEXT NOT NULL,        -- market value at snapshot_date, native currency
    currency      TEXT NOT NULL,        -- holding's native currency; convert to base at report time (ADR-5)
    PRIMARY KEY (account_id, symbol, snapshot_date)  -- IMMUTABLE: INSERT only
);

CREATE TABLE IF NOT EXISTS fx_rates (
    date           TEXT NOT NULL,
    base_currency  TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate           TEXT NOT NULL,
    PRIMARY KEY (date, base_currency, quote_currency)
);

-- Persisted LLM categorization proposals (ADR-2/12/13). Keyed by exact raw
-- description so an identical line reuses one proposal and a re-run makes zero
-- LLM calls. 'applied' rows were confident + in-vocabulary and linked a merchant;
-- 'needs_review' rows (low confidence or off-vocabulary category) are surfaced in
-- the report's Needs-Categorization section but never auto-assigned. An LLM OUTAGE
-- writes nothing here, so the line is retried when the model is back.
CREATE TABLE IF NOT EXISTS llm_categorizations (
    description_raw   TEXT PRIMARY KEY,
    proposed_merchant TEXT NOT NULL,
    proposed_category TEXT NOT NULL,
    confidence        REAL NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('applied', 'needs_review')),
    model             TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
