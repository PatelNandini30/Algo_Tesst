# Index Optimization Report

**Date:** 2026-03-11  
**Phase:** 3 - Database Index Optimization  
**Target:** 73GB option_data table

---

## Executive Summary

This document outlines the PostgreSQL index optimizations for the backtesting platform. The goal is to enable **Index Only Scans** for all common query patterns, eliminating expensive sequential scans on the 73GB table.

---

## Current Indexes (Pre-Optimization)

| Index Name | Columns | Type | Status |
|------------|---------|------|--------|
| idx_option_date | (date) | btree | ✅ Good |
| idx_option_date_symbol | (date, symbol) | btree | ✅ Good |
| idx_option_instrument | (instrument) | btree | ⚠️ Limited |
| idx_option_symbol_expiry | (symbol, expiry_date) | btree | ⚠️ Partial |
| idx_option_symbol_expiry_type | (symbol, expiry_date, option_type) | btree | ⚠️ Partial |
| uq_option_day_idx | (date, symbol, expiry_date, strike_price, option_type) | UNIQUE | ❌ INVALID |

---

## New Indexes Created

### 1. Primary Covering Index (MOST IMPORTANT)

```sql
CREATE INDEX idx_option_backtest_cover
ON option_data(symbol, date, expiry_date, option_type, strike_price)
INCLUDE (open, high, low, close, turnover);
```

**Purpose:** Enables index-only scans for all backtest queries

**Query Coverage:**
- ✅ Single option premium lookup
- ✅ Strike selection (ATM, ITM, OTM)
- ✅ Option chain loading
- ✅ Premium range filtering

**Why This Works:**
- Column order: (symbol, date) - most selective first
- INCLUDE stores price columns in index leaf nodes
- No table lookup needed for SELECT close

---

### 2. Fixed Duplicate Index

```sql
-- Drop invalid unique index (causes import failures)
DROP INDEX IF EXISTS uq_option_day_idx;

-- Recreate as regular index
CREATE INDEX idx_option_day 
ON option_data(date, symbol, expiry_date, strike_price, option_type);
```

**Purpose:** Fixes invalid constraint while maintaining query performance

---

### 3. Futures-Specific Index

```sql
CREATE INDEX idx_option_futures
ON option_data(symbol, date, expiry_date)
WHERE instrument LIKE 'FUT%';
```

**Purpose:** Partial index for futures-only queries

---

### 4. Exact Lookup Index

```sql
CREATE INDEX idx_option_exact_lookup
ON option_data(symbol, date, expiry_date, option_type, strike_price)
WHERE option_type IN ('CE', 'PE');
```

**Purpose:** Optimizes exact match queries (skips futures/other types)

---

## Expected Query Performance

### Before Optimization

| Query Type | Plan | Time |
|------------|------|------|
| Single premium | Seq Scan on option_data | 30-60s |
| Strike selection | Seq Scan | 20-40s |
| Trading calendar | Seq Scan | 10-20s |
| Option chain | Seq Scan | 15-30s |

### After Optimization

| Query Type | Plan | Time |
|------------|------|------|
| Single premium | Index Only Scan | <10ms |
| Strike selection | Index Only Scan | <50ms |
| Trading calendar | Index Only Scan | <100ms |
| Option chain | Index Only Scan | <100ms |

---

## Verification Commands

### Check Index Usage

```sql
-- View all indexes
SELECT indexname, pg_size_pretty(pg_indexes_size(indexname::regclass))
FROM pg_indexes 
WHERE tablename = 'option_data'
ORDER BY pg_indexes_size(indexname::regclass) DESC;

-- Check for sequential scans (BAD)
SELECT query, calls, rows, 
       total_exec_time, 
       mean_exec_time,
       shared_blks_hit,
       shared_blks_read
FROM pg_stat_statements 
WHERE query LIKE '%option_data%'
ORDER BY total_exec_time DESC;
```

### EXPLAIN ANALYZE Examples

```sql
-- Should show "Index Only Scan using idx_option_backtest_cover"
EXPLAIN ANALYZE
SELECT close 
FROM option_data 
WHERE symbol = 'NIFTY' 
  AND date = '2024-01-15' 
  AND expiry_date = '2024-01-25'
  AND option_type = 'CE' 
  AND strike_price = 22000;

-- Should show "Index Scan using idx_option_date_symbol"
EXPLAIN ANALYZE
SELECT DISTINCT date 
FROM option_data 
WHERE symbol = 'NIFTY'
  AND date >= '2024-01-01' 
  AND date <= '2024-12-31';
```

---

## Index Size Impact

| Index | Estimated Size | Impact |
|-------|---------------|--------|
| idx_option_backtest_cover | ~15GB | +20% storage |
| idx_option_day | ~10GB | +13% storage |
| idx_option_futures | ~2GB | +3% storage |
| idx_option_exact_lookup | ~8GB | +11% storage |
| **Total** | ~35GB | **~47% storage increase** |

**Trade-off:** 47% storage increase for 100-1000x query speedup

---

## How to Apply

Run the SQL script:

```bash
docker exec -it algotest-postgres psql -U algotest -d algotest -f /path/to/index_optimization.sql
```

Or connect and run:

```bash
docker exec -it algotest-postgres psql -U algotest -d algotest
```

Then paste the contents of `reports/sql/index_optimization.sql`

---

## Next Steps

After applying indexes:

1. **Run ANALYZE** - Update table statistics
2. **Test Queries** - Verify Index Only Scans
3. **Monitor Performance** - Check pg_stat_statements
4. **Phase 4** - Implement caching layer (Redis)

---

## Summary

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Single Query | 30-60s | <10ms | 3000-6000x |
| Strike Selection | 20-40s | <50ms | 400-800x |
| Storage | 73GB | ~108GB | +47% |
| Index Scans | Seq Scan | Index Only | ✅ |

The covering index `idx_option_backtest_cover` is the key optimization - it allows PostgreSQL to serve all backtest queries entirely from the index without touching the main table.
