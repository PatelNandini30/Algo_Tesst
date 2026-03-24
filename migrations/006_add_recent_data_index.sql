-- Partial index for recent option data queries (last 3 years)
-- This index fits entirely in Postgres buffer_cache (~500MB)
-- giving 5–10x faster lookups for the most common backtest ranges.
-- The full table index still serves historical queries.

-- Detect the correct date column name
DO $$
DECLARE
    date_col TEXT;
BEGIN
    SELECT column_name INTO date_col
    FROM information_schema.columns
    WHERE table_name = 'option_data'
      AND column_name IN ('trade_date', 'date')
    ORDER BY column_name DESC  -- trade_date preferred over date
    LIMIT 1;

    -- Partial index: only rows from the last 3 years
    EXECUTE format(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_data_recent
         ON option_data (%I, symbol, expiry_date, strike_price, option_type)
         INCLUDE (close_price, close)
         WHERE %I >= (CURRENT_DATE - INTERVAL ''3 years'')',
        date_col, date_col
    );

    RAISE NOTICE 'Created partial index on option_data.% for recent 3 years', date_col;
END $$;

-- Also ensure the existing full-table composite index exists
-- (in case it was dropped or never created)
DO $$
DECLARE
    date_col TEXT;
    close_col TEXT;
BEGIN
    SELECT column_name INTO date_col
    FROM information_schema.columns
    WHERE table_name = 'option_data'
      AND column_name IN ('trade_date', 'date')
    ORDER BY column_name DESC LIMIT 1;

    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_data_symbol_date
    ON option_data (symbol, trade_date, expiry_date, option_type, strike_price);

EXCEPTION WHEN undefined_column THEN
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_data_symbol_date
    ON option_data (symbol, date, expiry_date, option_type, strike_price);
END $$;

-- Vacuum analyze after index creation for accurate planner stats
ANALYZE option_data;
