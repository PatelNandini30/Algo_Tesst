CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_symbol_date
    ON option_data (symbol, trade_date);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_symbol_date_expiry
    ON option_data (symbol, trade_date, expiry_date);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_symbol_date_expiry_strike_type
    ON option_data (symbol, trade_date, expiry_date, strike_price, option_type);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_spot_symbol_date
    ON spot_data (symbol, trade_date);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_expiry_symbol_type
    ON expiry_calendar (symbol, expiry_type, current_expiry);
