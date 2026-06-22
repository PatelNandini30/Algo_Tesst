-- Migration: generic index OHLC table (cross-index overlay legs, e.g. Midcap100)
-- Version: 008
-- Notes:
--   - NEW, additive feature. Existing tables (option_data, spot_data, ...) are untouched.
--   - One row per (symbol, trade_date). Re-loading the same CSV upserts in place.
--   - Holds daily OHLC for cash indices used as overlay legs (no options/futures here).
--     Source CSV shape: Ticker, Date/Time, Open, High, Low, Close.
--   - Read by the Midcap overlay via a per-symbol Arrow/feather export + Rust lookup.

CREATE TABLE IF NOT EXISTS index_ohlc (
    id           BIGSERIAL PRIMARY KEY,
    symbol       VARCHAR(32)    NOT NULL,
    trade_date   DATE           NOT NULL,
    open_price   NUMERIC(20,4),
    high_price   NUMERIC(20,4),
    low_price    NUMERIC(20,4),
    close_price  NUMERIC(20,4)  NOT NULL,
    imported_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_index_ohlc_symbol_date
    ON index_ohlc(symbol, trade_date);
