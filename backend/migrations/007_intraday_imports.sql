-- Migration: intraday import manifest
-- Version: 007
-- Notes:
--   - Mirrors filesystem state for atomic ACID tracking.
--   - One row per (symbol, trading_date). Re-ingest with same SHA256 is a no-op.
--   - On SHA256 change, the row is replaced and the snapshot file is overwritten.

CREATE TABLE IF NOT EXISTS intraday_imports (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    trading_date DATE NOT NULL,
    source_format VARCHAR(20) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    parquet_path TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    expiry_count SMALLINT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT intraday_imports_symbol_check
      CHECK (symbol IN ('NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY')),
    UNIQUE (symbol, trading_date)
);

CREATE INDEX IF NOT EXISTS idx_intraday_imports_symbol_date
    ON intraday_imports(symbol, trading_date);
