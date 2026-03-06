-- Migration: PostgreSQL schema for CSV replacement (normalized + practical)
-- Version: 003
-- Notes:
--   - Designed from current CSV files and backend code paths.
--   - Keeps source table names close to existing migrate_data.py usage.
--   - Adds processed/result/audit tables for full DB-backed workflow.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1) IMPORT / AUDIT METADATA
-- ============================================================================

CREATE TABLE IF NOT EXISTS import_batches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_family VARCHAR(40) NOT NULL, -- cleaned_csvs, strikeData, expiryData, Filter, Output
    triggered_by VARCHAR(100) DEFAULT 'system',
    status VARCHAR(20) NOT NULL DEFAULT 'started' CHECK (status IN ('started', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_import_batches_status_started
    ON import_batches(status, started_at DESC);

CREATE TABLE IF NOT EXISTS import_files (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 CHAR(64),
    source_mtime TIMESTAMPTZ,
    row_count_read INTEGER DEFAULT 0,
    row_count_loaded INTEGER DEFAULT 0,
    row_count_rejected INTEGER DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'started' CHECK (status IN ('started', 'completed', 'failed')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_path, source_filename, source_sha256)
);

CREATE INDEX IF NOT EXISTS idx_import_files_batch
    ON import_files(batch_id);

CREATE INDEX IF NOT EXISTS idx_import_files_status
    ON import_files(status, created_at DESC);

-- ============================================================================
-- 2) SOURCE / RAW TABLES (CSV replacement)
-- ============================================================================

-- Replaces cleaned_csvs/YYYY-MM-DD.csv
CREATE TABLE IF NOT EXISTS option_data (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,          -- CSV: Date
    expiry_date DATE NOT NULL,         -- CSV: ExpiryDate
    instrument VARCHAR(10) NOT NULL,   -- FUTIDX/FUTSTK/OPTIDX/OPTSTK
    symbol VARCHAR(30) NOT NULL,       -- CSV: Symbol
    strike_price NUMERIC(12,2),        -- CSV: StrikePrice
    option_type VARCHAR(2),            -- CSV: OptionType (CE/PE), NULL for futures
    open_price NUMERIC(14,4),          -- CSV: Open
    high_price NUMERIC(14,4),          -- CSV: High
    low_price NUMERIC(14,4),           -- CSV: Low
    close_price NUMERIC(14,4),         -- CSV: Close
    settled_price NUMERIC(14,4),       -- CSV: SettledPrice
    contracts BIGINT,                  -- CSV: Contracts
    turnover NUMERIC(20,2),            -- CSV: TurnOver
    open_interest BIGINT,              -- CSV: OpenInterest
    import_file_id BIGINT REFERENCES import_files(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (option_type IS NULL OR option_type IN ('CE', 'PE')),
    CHECK (instrument IN ('FUTIDX', 'FUTSTK', 'OPTIDX', 'OPTSTK')),
    CHECK (
        (instrument LIKE 'OPT%' AND option_type IS NOT NULL)
        OR
        (instrument LIKE 'FUT%' AND option_type IS NULL)
    ),
    CHECK (
        (instrument LIKE 'OPT%' AND strike_price IS NOT NULL)
        OR
        (instrument LIKE 'FUT%' AND strike_price IS NOT NULL)
    )
);

-- Upsert key for one quote row
CREATE UNIQUE INDEX IF NOT EXISTS uq_option_data_quote
    ON option_data (trade_date, symbol, instrument, expiry_date, option_type, strike_price);

-- Matches lookup patterns in base.py
CREATE INDEX IF NOT EXISTS idx_option_data_lookup_opt
    ON option_data (trade_date, symbol, option_type, expiry_date, strike_price)
    WHERE instrument IN ('OPTIDX', 'OPTSTK');

CREATE INDEX IF NOT EXISTS idx_option_data_lookup_fut
    ON option_data (trade_date, symbol, expiry_date)
    WHERE instrument IN ('FUTIDX', 'FUTSTK');

CREATE INDEX IF NOT EXISTS idx_option_data_symbol_date
    ON option_data (symbol, trade_date);

-- Replaces strikeData/*_strike_data.csv and index_strike_data.csv
CREATE TABLE IF NOT EXISTS spot_data (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,          -- CSV: Date
    symbol VARCHAR(30) NOT NULL,       -- CSV: Ticker (or filename-derived)
    close_price NUMERIC(14,4) NOT NULL, -- CSV: Close
    open_price NUMERIC(14,4),          -- present in some strikeData files
    high_price NUMERIC(14,4),
    low_price NUMERIC(14,4),
    volume BIGINT,
    average_price NUMERIC(14,4),       -- for DailyNC files
    supertrend_1 NUMERIC(14,4),        -- optional, currently unused by engine
    supertrend_2 NUMERIC(14,4),
    supertrend_3 NUMERIC(14,4),
    trade_time TIME,                   -- optional from DailyNC files
    import_file_id BIGINT REFERENCES import_files(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_spot_data_symbol_date
    ON spot_data (symbol, trade_date);

-- Replaces expiryData/*.csv
CREATE TABLE IF NOT EXISTS expiry_calendar (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(30) NOT NULL,
    expiry_type VARCHAR(10) NOT NULL CHECK (expiry_type IN ('weekly', 'monthly')),
    previous_expiry DATE,
    current_expiry DATE NOT NULL,
    next_expiry DATE,
    import_file_id BIGINT REFERENCES import_files(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, expiry_type, current_expiry)
);

CREATE INDEX IF NOT EXISTS idx_expiry_calendar_symbol_type_current
    ON expiry_calendar (symbol, expiry_type, current_expiry);

-- Replaces Filter/base2.csv
CREATE TABLE IF NOT EXISTS trading_holidays (
    id BIGSERIAL PRIMARY KEY,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    reason VARCHAR(100) DEFAULT 'data_unavailable',
    import_file_id BIGINT REFERENCES import_files(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date),
    UNIQUE (start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_trading_holidays_range
    ON trading_holidays (start_date, end_date);

-- Replaces Filter/STR*.csv
CREATE TABLE IF NOT EXISTS super_trend_segments (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(30) NOT NULL DEFAULT 'NIFTY', -- current code assumes NIFTY
    config VARCHAR(10) NOT NULL,                 -- e.g. 5x1, 5x2
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    trend VARCHAR(10),                            -- source CSV does not provide this
    import_file_id BIGINT REFERENCES import_files(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date),
    UNIQUE (symbol, config, start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_super_trend_segments_lookup
    ON super_trend_segments (symbol, config, start_date, end_date);

-- ============================================================================
-- 3) PROCESSED / CALCULATION TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    strategy_name VARCHAR(120) NOT NULL,
    engine_name VARCHAR(60) NOT NULL, -- generic_multi_leg / generic_algotest_engine
    index_symbol VARCHAR(30) NOT NULL,
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    request_payload JSONB NOT NULL,
    request_hash CHAR(32),            -- MD5 for cache identity
    status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_lookup
    ON backtest_runs (index_symbol, date_from, date_to, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_hash
    ON backtest_runs (request_hash);

-- Detailed per-leg rows; aligns with engines' output columns.
CREATE TABLE IF NOT EXISTS backtest_trade_legs (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    trade_no INTEGER NOT NULL,          -- Trade (aggregated id)
    leg_no INTEGER,                     -- Leg number (for algotest engine)
    trade_index_label VARCHAR(30),      -- "Index" field from multi-leg engine
    entry_date DATE NOT NULL,
    exit_date DATE NOT NULL,
    leg_type VARCHAR(10) NOT NULL,      -- CE/PE/FUT
    strike_price NUMERIC(12,2),
    side VARCHAR(6),                    -- BUY/SELL
    quantity INTEGER,
    entry_price NUMERIC(14,4),
    exit_price NUMERIC(14,4),
    entry_spot NUMERIC(14,4),
    exit_spot NUMERIC(14,4),
    spot_pnl NUMERIC(16,4),
    future_expiry DATE,
    net_pnl NUMERIC(16,4) NOT NULL,
    pct_pnl NUMERIC(10,4),
    exit_reason VARCHAR(80),
    str_segment TEXT,
    cumulative NUMERIC(20,4),
    peak NUMERIC(20,4),
    drawdown NUMERIC(20,4),             -- DD
    drawdown_pct NUMERIC(10,4),         -- %DD
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_trade_legs_run_trade
    ON backtest_trade_legs (run_id, trade_no, leg_no);

CREATE INDEX IF NOT EXISTS idx_backtest_trade_legs_dates
    ON backtest_trade_legs (run_id, entry_date, exit_date);

-- One row per run summary; aligns with compute_analytics() output keys.
CREATE TABLE IF NOT EXISTS backtest_run_summary (
    run_id UUID PRIMARY KEY REFERENCES backtest_runs(id) ON DELETE CASCADE,
    total_pnl NUMERIC(20,4),
    trade_count INTEGER,
    win_pct NUMERIC(10,4),
    loss_pct NUMERIC(10,4),
    avg_win NUMERIC(16,4),
    avg_loss NUMERIC(16,4),
    max_win NUMERIC(16,4),
    max_loss NUMERIC(16,4),
    avg_profit_per_trade NUMERIC(16,4),
    expectancy NUMERIC(12,4),
    reward_to_risk NUMERIC(12,4),
    profit_factor NUMERIC(12,4),
    cagr_options NUMERIC(12,4),
    cagr_spot NUMERIC(12,4),
    max_dd_pct NUMERIC(12,4),
    max_dd_pts NUMERIC(16,4),
    mdd_duration_days INTEGER,
    mdd_start_date DATE,
    mdd_end_date DATE,
    mdd_trade_number INTEGER,
    car_mdd NUMERIC(12,4),
    recovery_factor NUMERIC(12,4),
    max_win_streak INTEGER,
    max_loss_streak INTEGER,
    spot_change NUMERIC(16,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest_run_pivot_yearly (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    jan NUMERIC(16,4),
    feb NUMERIC(16,4),
    mar NUMERIC(16,4),
    apr NUMERIC(16,4),
    may NUMERIC(16,4),
    jun NUMERIC(16,4),
    jul NUMERIC(16,4),
    aug NUMERIC(16,4),
    sep NUMERIC(16,4),
    oct NUMERIC(16,4),
    nov NUMERIC(16,4),
    dec NUMERIC(16,4),
    total NUMERIC(16,4),
    max_drawdown_text TEXT,            -- keeps current API payload semantics
    days_for_mdd INTEGER,
    r_mdd NUMERIC(12,4),
    UNIQUE (run_id, year)
);

CREATE INDEX IF NOT EXISTS idx_backtest_run_pivot_run
    ON backtest_run_pivot_yearly (run_id, year);

-- ============================================================================
-- 4) COMPATIBILITY VIEWS (optional helpers when replacing CSV loaders)
-- ============================================================================

CREATE OR REPLACE VIEW v_cleaned_csv_shape AS
SELECT
    trade_date AS "Date",
    expiry_date AS "ExpiryDate",
    instrument AS "Instrument",
    symbol AS "Symbol",
    strike_price AS "StrikePrice",
    option_type AS "OptionType",
    open_price AS "Open",
    high_price AS "High",
    low_price AS "Low",
    close_price AS "Close",
    settled_price AS "SettledPrice",
    contracts AS "Contracts",
    turnover AS "TurnOver",
    open_interest AS "OpenInterest"
FROM option_data;

CREATE OR REPLACE VIEW v_strike_data_shape AS
SELECT
    symbol AS "Ticker",
    trade_date AS "Date",
    close_price AS "Close"
FROM spot_data;

-- ============================================================================
-- 5) SCHEMA VERSION BUMP
-- ============================================================================

CREATE TABLE IF NOT EXISTS db_metadata (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO db_metadata (key, value)
VALUES ('schema_version', '003')
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, updated_at = NOW();

