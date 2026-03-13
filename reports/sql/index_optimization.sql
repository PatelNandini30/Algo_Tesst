-- ============================================================================
-- PHASE 3: Database Index Optimization
-- 
-- Purpose: Optimize PostgreSQL indexes for 73GB option_data table
-- Target: Enable index-only scans for all backtest queries
-- ============================================================================

-- ============================================================================
-- STEP 1: Fix Invalid Index
-- ============================================================================

-- Drop the invalid unique index (causes import failures)
DROP INDEX IF EXISTS uq_option_day_idx;

-- Recreate as non-unique covering index (allows duplicates, but still fast)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_day 
ON option_data(date, symbol, expiry_date, strike_price, option_type);

-- ============================================================================
-- STEP 2: Create High-Performance Covering Index
-- ============================================================================

-- This is the PRIMARY index for backtest queries
-- Covers all common query patterns:
--   - symbol + date (filter)
--   - expiry_date (filter)  
--   - option_type (filter)
--   - strike_price (filter)
--   - INCLUDE columns are stored in index but not in index key (faster)

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_backtest_cover
ON option_data(symbol, date, expiry_date, option_type, strike_price)
INCLUDE (open, high, low, close, turnover);

-- ============================================================================
-- STEP 3: Additional Performance Indexes
-- ============================================================================

-- For trading calendar queries (get distinct dates per symbol)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_date_symbol
ON option_data(date, symbol);

-- For spot/future lookups (when instrument = FUT)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_futures
ON option_data(symbol, date, expiry_date)
WHERE instrument LIKE 'FUT%';

-- For very specific premium lookups (exact match on all fields)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_exact_lookup
ON option_data(symbol, date, expiry_date, option_type, strike_price)
WHERE option_type IN ('CE', 'PE');

-- ============================================================================
-- STEP 4: Spot Data Indexes (NEW)
-- ============================================================================

-- Index for spot data queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_spot_date_symbol
ON spot_data(date, symbol);

-- Covering index for spot data
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_spot_cover
ON spot_data(symbol, date)
INCLUDE (open, high, low, close);

-- ============================================================================
-- STEP 5: Analyze Tables (update statistics)
-- ============================================================================

-- Analyze option_data to update query planner statistics
ANALYZE option_data;

-- Also analyze related tables
ANALYZE spot_data;
ANALYZE expiry_calendar;
ANALYZE super_trend_segments;

-- ============================================================================
-- VERIFICATION: Check Index Usage
-- ============================================================================

-- View all indexes on option_data
SELECT 
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'option_data'
ORDER BY indexname;

-- Check index size (should be much smaller than table)
SELECT 
    pg_size_pretty(pg_total_relation_size('option_data')) as table_size,
    pg_size_pretty(pg_indexes_size('option_data')) as indexes_size;

-- ============================================================================
-- TEST QUERIES (Run with EXPLAIN ANALYZE)
-- ============================================================================

-- Test 1: Single option premium lookup
EXPLAIN ANALYZE
SELECT close 
FROM option_data 
WHERE symbol = 'NIFTY' 
  AND date = '2024-01-15' 
  AND expiry_date = '2024-01-25'
  AND option_type = 'CE' 
  AND strike_price = 22000;

-- Test 2: Strike selection (multiple rows)
EXPLAIN ANALYZE
SELECT strike_price, close, turnover
FROM option_data 
WHERE symbol = 'NIFTY' 
  AND date = '2024-01-15' 
  AND expiry_date = '2024-01-25'
  AND option_type = 'CE'
ORDER BY strike_price;

-- Test 3: Trading calendar
EXPLAIN ANALYZE
SELECT DISTINCT date 
FROM option_data 
WHERE symbol = 'NIFTY'
  AND date >= '2024-01-01' 
  AND date <= '2024-12-31'
ORDER BY date;

-- Test 4: Option chain (multiple expiries)
EXPLAIN ANALYZE
SELECT strike_price, close, expiry_date, option_type
FROM option_data 
WHERE symbol = 'NIFTY' 
  AND date = '2024-01-15' 
  AND expiry_date IN ('2024-01-25', '2024-02-01', '2024-02-08')
  AND option_type IN ('CE', 'PE')
ORDER BY expiry_date, option_type, strike_price;

-- ============================================================================
-- Expected Results After Optimization:
-- ============================================================================
--
-- BEFORE: Seq Scan on 73GB table (minutes)
-- AFTER:  Index Only Scan using idx_option_backtest_cover (< 10ms)
--
-- Key metrics to verify:
-- 1. "Index Only Scan" in query plan (not "Seq Scan")
-- 2. "Buffers: shared hit" indicates cache hit
-- 3. Execution time < 50ms for single date queries
-- 4. Index size < 10% of table size
-- ============================================================================
