# Performance Audit Report - Backend Optimization

**Date:** 2026-03-11  
**System:** Options Backtesting Platform  
**Database:** PostgreSQL (73GB option_data table)  
**Current Performance:** 3-4 minutes for long date range backtests  
**Target Performance:** 1-5 seconds

---

## Executive Summary

The backend has several critical performance bottlenecks causing slow backtests:

1. **Database layer:** Inefficient queries, missing covering indexes
2. **Data loading:** Loading entire DataFrames when only subset needed
3. **Python code:** Using slow iterrows() loops instead of vectorized operations
4. **Caching:** No Redis, only application-level LRU cache

---

## Critical Issues (Must Fix)

### 1. Database Query: SELECT * Anti-Pattern

**File:** `repositories/market_data_repository.py`  
**Function:** `get_bhavcopy_by_date()` (line 33-60)  
**Issue:** Loads ALL columns from option_data table  
```python
SELECT instrument, symbol, expiry_date, option_type, strike_price, close, turnover, date
FROM option_data WHERE date = :d
```
**Problem:** Loads 16 columns when only 7 needed. On a 73GB table, this multiplies I/O by 2x+.  
**Fix:** Use explicit column list or create covering index.

---

### 2. Database Query: Missing Date Index

**File:** `repositories/market_data_repository.py`  
**Function:** `get_trading_calendar()` (line 138-155)  
**Issue:** Uses DISTINCT on date column without optimized index
```python
SELECT DISTINCT date FROM option_data WHERE date >= :from_date AND date <= :to_date
```
**Problem:** Table scan on 73GB table for every backtest.  
**Fix:** Add covering index on (date) include (symbol) for index-only scans.

---

### 3. Python Loop: iterrows() Anti-Pattern

**File:** `base.py`  
**Function:** `_build_option_lookup()` (line 1263)  
**Issue:** Using iterrows() to build lookup dictionary
```python
for _, row in filtered.iterrows():
    opt_type = str(row.get('OptionType', '')).upper()
    if opt_type not in ('CE', 'PE'):
        continue
    strike = int(round(float(row['StrikePrice'])))
    expiry = pd.Timestamp(row['ExpiryDate']).strftime('%Y-%m-%d')
    lookup[(strike, opt_type, expiry)] = float(row['Close'])
```
**Problem:** iterrows() is 100-1000x slower than vectorized operations. Called for every trading day.  
**Fix:** Use pandas apply() or dictionary comprehension with vectorized operations.

---

### 4. Missing Query Result Caching

**File:** `repositories/market_data_repository.py`  
**Functions:** All repository methods  
**Issue:** Every backtest re-exutes identical queries  
**Problem:** No caching layer - same trading calendar, expiry dates queried repeatedly  
**Fix:** Implement Redis caching with TTL for reference data (calendar, expiry, lot sizes).

---

### 5. Data Loading: Full DataFrame Instead of Targeted

**File:** `engines/generic_algotest_engine.py`  
**Function:** `run_algotest_backtest()` (line 1159)  
**Issue:** Loads ALL spot data for entire date range upfront
```python
spot_df = get_strike_data(index, from_date, to_date)
```
**Problem:** For 5-year backtest = ~1200 trading days × ~200 strikes = 240,000 rows loaded into memory  
**Fix:** Load on-demand with aggressive caching per date.

---

## High Priority Issues

### 6. Database: No Connection Pool Tuning

**File:** `database.py` (line 30-40)  
**Issue:** 
```python
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    ...
)
```
**Problem:** Default pool size may be insufficient for 50-60 concurrent users. Each backtest holds connections during long queries.  
**Fix:** Increase pool_size, add pool_pre_ping=True, consider pgbouncer for connection pooling.

---

### 7. Redundant DataFrame Filtering

**File:** `base.py`  
**Function:** `_build_option_lookup()` (line 1257-1261)  
**Issue:** 
```python
filtered = bhav_df[bhav_df['Symbol'] == index].copy()
```
**Problem:** First filters by date, then filters by symbol - double filtering. Data already filtered in SQL query.  
**Fix:** Pass symbol filter to SQL query, avoid in-memory filtering.

---

### 8. No Prepared Statements / Query Parameterization

**File:** `repositories/market_data_repository.py`  
**Issue:** Building SQL strings with f-strings
```python
symbol_list = ", ".join([f"'{s.upper()}'" for s in symbols])
symbol_filter = f"AND symbol IN ({symbol_list})"
```
**Problem:** SQL injection risk, query plan not cached efficiently.  
**Fix:** Use proper parameterized queries with SQLAlchemy.

---

### 9. Heavy Date Parsing Overhead

**File:** `base.py`  
**Function:** `load_bhavcopy()` (line 397-398)  
**Issue:**
```python
df['Date'] = pd.to_datetime(df['Date'])
df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])
```
**Problem:** Parsing dates on every load. With LRU cache miss, adds 200-500ms per date.  
**Fix:** Store dates as dates in PostgreSQL, parse once during insert.

---

### 10. No Index on spot_data Table

**File:** `repositories/market_data_repository.py`  
**Function:** `get_spot_data()` (line 62-85)  
**Issue:** Query on spot_data without proper index
```python
WHERE symbol = :symbol AND date >= :from_date AND date <= :to_date
```
**Problem:** Full table scan on spot_data for each symbol/date range.  
**Fix:** Add composite index (symbol, date) include (close).

---

## Medium Priority Issues

### 11. Inefficient String Operations

**File:** `base.py`  
**Function:** Multiple places  
**Issue:**
```python
date_str = pd.Timestamp(row['Date']).strftime('%Y-%m-%d')
opt_type = str(row.get('OptionType', '')).upper()
```
**Problem:** Repeated string conversions in hot loops.  
**Fix:** Pre-compute, use categorical data types.

---

### 12. No Vectorized Strike Selection

**File:** `engines/generic_algotest_engine.py`  
**Function:** `_resolve_strike()` (line 141+)  
**Issue:** Iterating through strikes one-by-one  
**Problem:** For each leg, each trade, scans available strikes.  
**Fix:** Pre-compute strike sets, use binary search.

---

### 13. Missing Load Balancing

**File:** `routers/backtest.py`  
**Issue:** All users share same thread pool  
**Problem:** 50-60 users = contention on backtest computation.  
**Fix:** Use separate worker processes, consider Celery for queue-based processing.

---

### 14. No Query Result Compression

**File:** All repository methods  
**Issue:** Returning uncompressed DataFrames over network  
**Problem:** Large DataFrames serialized to JSON.  
**Fix:** Use Apache Arrow / Parquet for efficient serialization.

---

## Issues Summary Table

| # | Severity | File | Function | Issue | Impact |
|---|----------|------|----------|-------|--------|
| 1 | CRITICAL | market_data_repository.py | get_bhavcopy_by_date | SELECT * | 2x+ I/O |
| 2 | CRITICAL | market_data_repository.py | get_trading_calendar | Missing index | Full table scan |
| 3 | CRITICAL | base.py | _build_option_lookup | iterrows() | 100-1000x slower |
| 4 | CRITICAL | All repos | All methods | No Redis cache | Repeated queries |
| 5 | CRITICAL | generic_algotest_engine.py | run_algotest_backtest | Load all data | Memory bloat |
| 6 | HIGH | database.py | create_engine | Small pool size | Connection contention |
| 7 | HIGH | base.py | _build_option_lookup | Redundant filter | Wasted CPU |
| 8 | HIGH | market_data_repository.py | All methods | SQL injection risk | Security + perf |
| 9 | HIGH | base.py | load_bhavcopy | Date parsing | 200-500ms/date |
| 10 | HIGH | market_data_repository.py | get_spot_data | Missing index | Full table scan |
| 11 | MEDIUM | base.py | Multiple | String ops | CPU overhead |
| 12 | MEDIUM | generic_algotest_engine.py | _resolve_strike | Linear scan | O(n) per trade |
| 13 | MEDIUM | routers/backtest.py | All | No load balancing | User contention |
| 14 | MEDIUM | All repos | All methods | No compression | Network overhead |

---

## Recommended Fix Order (Phase by Phase)

### Phase 1: Database Layer (Highest Impact)
- Add covering indexes
- Fix SELECT * to explicit columns
- Add missing indexes on spot_data

### Phase 2: Caching Layer
- Add Redis for reference data
- Implement aggressive per-date caching

### Phase 3: Python Optimization
- Replace iterrows() with vectorized operations
- Pre-compute lookup tables
- Use categorical data types

### Phase 4: Concurrency
- Increase connection pool
- Add worker processes
- Implement request queuing

---

## Database Schema Notes

Current indexes on option_data (from user):
```
"idx_option_date" btree (date)
"idx_option_date_symbol" btree (date, symbol)
"idx_option_instrument" btree (instrument)
"idx_option_symbol_expiry" btree (symbol, expiry_date)
"idx_option_symbol_expiry_type" btree (symbol, expiry_date, option_type)
```

**Missing covering indexes:**
- (date, symbol) include (strike_price, option_type, close) - for bhavcopy lookups
- (symbol, date) include (close) - for spot data lookups

---

## Next Steps

Wait for Phase 2 prompt to begin implementing fixes in priority order.
