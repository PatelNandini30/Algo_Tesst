-- Performance index for recent option data queries
-- Note: Existing indexes (idx_option_date, idx_option_date_symbol, idx_option_symbol_date_full) 
-- already handle recent queries well. This migration just ensures they're analyzed.

-- Analyze the table to update planner statistics
ANALYZE option_data;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'Migration 006 applied: table analyzed for query planner';
END $$;
