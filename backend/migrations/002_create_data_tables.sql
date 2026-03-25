-- ==============================================================================
-- AlgoTest PostgreSQL Schema
-- Version: 2.0
-- 
-- CSV Sources:
--   1. cleaned_csvs/YYYY-MM-DD.csv  -> option_data (options & futures prices)
--   2. strikeData/*_strike_data.csv  -> spot_data (index closing prices)
--   3. expiryData/*.csv            -> expiry_calendar (weekly/monthly expiries)
--   4. Filter/base2.csv           -> trading_holidays (NSE trading halt periods)
--   5. Filter/STR*.csv            -> super_trend_segments (Super Trend periods)
-- ==============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==============================================================================
-- SOURCE DATA TABLES (replaces CSV files)
-- ==============================================================================

-- Option & Future Chain Data (replaces cleaned_csvs/)
-- Stores daily options and futures data from NSE bhavcopy
CREATE TABLE option_data (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    instrument VARCHAR(10) NOT NULL,  -- FUTIDX, FUTSTK, OPTIDX, OPTSTK
    symbol VARCHAR(20) NOT NULL,        -- NIFTY, BANKNIFTY, stock symbols
    strike_price DECIMAL(12,2) DEFAULT 0,
    option_type VARCHAR(3),             -- CE, PE (NULL for futures)
    open DECIMAL(12,2),
    high DECIMAL(12,2),
    low DECIMAL(12,2),
    close DECIMAL(12,2),
    settled_price DECIMAL(12,2),
    contracts INTEGER,
    turnover DECIMAL(15,2),
    open_interest BIGINT,
    imported_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_option_day UNIQUE (date, symbol, expiry_date, strike_price, option_type)
);

-- Indexes for common query patterns
CREATE INDEX idx_option_date ON option_data(date);
CREATE INDEX idx_option_date_symbol ON option_data(date, symbol);
CREATE INDEX idx_option_symbol_expiry ON option_data(symbol, expiry_date);
CREATE INDEX idx_option_symbol_expiry_type ON option_data(symbol, expiry_date, option_type);
CREATE INDEX idx_option_symbol_strike ON option_data(symbol, strike_price);
CREATE INDEX idx_option_instrument ON option_data(instrument);

-- Spot/Index closing prices (replaces strikeData/)
CREATE TABLE spot_data (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    symbol VARCHAR(20) NOT NULL,  -- NIFTY, BANKNIFTY, etc.
    open DECIMAL(12,2),
    high DECIMAL(12,2),
    low DECIMAL(12,2),
    close DECIMAL(12,2),
    volume BIGINT,
    imported_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_spot_day UNIQUE (date, symbol)
);

CREATE INDEX idx_spot_date ON spot_data(date);
CREATE INDEX idx_spot_symbol ON spot_data(symbol);
CREATE INDEX idx_spot_date_symbol ON spot_data(date, symbol);

-- Expiry Calendar (replaces expiryData/)
CREATE TABLE expiry_calendar (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    expiry_type VARCHAR(10) NOT NULL,  -- weekly, monthly
    previous_expiry DATE,
    current_expiry DATE NOT NULL,
    next_expiry DATE,
    imported_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_expiry_symbol_type_date UNIQUE (symbol, expiry_type, current_expiry)
);

CREATE INDEX idx_expiry_symbol_type ON expiry_calendar(symbol, expiry_type);
CREATE INDEX idx_expiry_current ON expiry_calendar(symbol, current_expiry);

-- Trading Holidays / Base2 Filter (replaces Filter/base2.csv)
-- Periods when trading was halted or base data unavailable
CREATE TABLE trading_holidays (
    id SERIAL PRIMARY KEY,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    reason VARCHAR(100),  -- 'market_halt', 'data_unavailable', etc.
    imported_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_holidays_date ON trading_holidays(start_date, end_date);

-- Super Trend Segments (replaces Filter/STR*.csv)
CREATE TABLE super_trend_segments (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    config VARCHAR(10) NOT NULL,  -- '5x1', '5x2' (period x multiplier)
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    trend VARCHAR(10) NOT NULL,  -- 'UP', 'DOWN'
    imported_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_str_segment UNIQUE (symbol, config, start_date)
);

CREATE INDEX idx_str_symbol_config ON super_trend_segments(symbol, config, start_date, end_date);

-- ==============================================================================
-- RESULT TABLES (stores backtest results)
-- ==============================================================================

-- Backtest execution history
CREATE TABLE backtest_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    strategy_name VARCHAR(100) NOT NULL,
    
    -- Parameters (JSON)
    parameters JSONB NOT NULL,
    
    -- Date range
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    index_symbol VARCHAR(20) NOT NULL,
    
    -- Summary metrics
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    win_rate DECIMAL(5,2),
    total_pnl DECIMAL(15,2),
    cagr DECIMAL(10,2),
    max_drawdown DECIMAL(10,2),
    
    -- Raw trades data
    trades JSONB,
    
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_results_strategy ON backtest_results(strategy_name);
CREATE INDEX idx_results_date ON backtest_results(date_from, date_to);
CREATE INDEX idx_results_created ON backtest_results(created_at DESC);

-- ==============================================================================
-- METADATA TABLES
-- ==============================================================================

-- Data import tracking
CREATE TABLE data_import_log (
    id SERIAL PRIMARY KEY,
    source_type VARCHAR(30) NOT NULL,  -- 'option_data', 'spot_data', 'expiry_calendar'
    source_path VARCHAR(255),
    records_imported INTEGER DEFAULT 0,
    records_skipped INTEGER DEFAULT 0,
    status VARCHAR(20) NOT NULL,  -- 'started', 'completed', 'failed'
    error_message TEXT,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_import_log_status ON data_import_log(status);
CREATE INDEX idx_import_log_started ON data_import_log(started_at DESC);

-- Database metadata
CREATE TABLE db_metadata (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Initial metadata
INSERT INTO db_metadata (key, value) VALUES 
    ('schema_version', '2.0'),
    ('last_migration', NOW()::TEXT);

-- ==============================================================================
-- VIEWS FOR COMMON QUERIES
-- ==============================================================================

-- Latest spot prices by symbol
CREATE OR REPLACE VIEW latest_spot_prices AS
SELECT s.symbol, s.date, s.close
FROM spot_data s
WHERE s.date = (SELECT MAX(s2.date) FROM spot_data s2 WHERE s2.symbol = s.symbol);

-- Option chain for a specific date
CREATE OR REPLACE VIEW option_chain_view AS
SELECT 
    date,
    symbol,
    expiry_date,
    strike_price,
    option_type,
    open,
    high,
    low,
    close,
    open_interest
FROM option_data
WHERE instrument IN ('OPTIDX', 'OPTSTK')
ORDER BY date, symbol, expiry_date, strike_price, option_type;

-- Futures data for a specific date
CREATE OR REPLACE VIEW futures_chain_view AS
SELECT 
    date,
    symbol,
    expiry_date,
    close as futures_price,
    open_interest,
    contracts,
    turnover
FROM option_data
WHERE instrument IN ('FUTIDX', 'FUTSTK')
ORDER BY date, symbol, expiry_date;
