-- Performance indexes for option_data queries
-- These indexes dramatically speed up date-based queries

-- Index on trade_date/date column (most common filter)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_data_trade_date 
ON option_data (COALESCE(trade_date, date));

-- Composite index for common query patterns
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_data_date_symbol 
ON option_data (COALESCE(trade_date, date), symbol);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_data_date_expiry 
ON option_data (COALESCE(trade_date, date), expiry_date);

-- Index for spot_data queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_spot_data_symbol_date 
ON spot_data (symbol, COALESCE(trade_date, date));

-- Index for expiry calendar lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_expiry_calendar_symbol_type 
ON expiry_calendar (symbol, expiry_type, current_expiry);

-- Index for super trend segments
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_super_trend_symbol_config 
ON super_trend_segments (symbol, config, start_date);

-- Analyze tables to update statistics
ANALYZE option_data;
ANALYZE spot_data;
ANALYZE expiry_calendar;
ANALYZE super_trend_segments;
