-- Migration: add pre-calculated delta and implied-volatility columns to option_data.
-- Version: 010
-- Source: eod_delta_full_history.xlsx (Nandini Patel share)
-- Nullable — historical rows without greeks stay NULL; backtest engine ignores them.

ALTER TABLE option_data
    ADD COLUMN IF NOT EXISTS delta   NUMERIC(15, 10),
    ADD COLUMN IF NOT EXISTS iv_pct  NUMERIC(15, 10);

CREATE INDEX IF NOT EXISTS idx_option_data_delta
    ON option_data (symbol, date) WHERE delta IS NOT NULL;
