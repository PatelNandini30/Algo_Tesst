# AlgoTest-Style Backtesting System Audit Report

**Date:** 2026-02-18  
**Auditor:** Senior Quant Engineer + Trading Infrastructure Auditor  
**System Version:** 1.0.0

---

## FINAL VERDICT

✔ **CALCULATIONS VERIFIED** (Active Code - generic_algotest_engine.py)

---

## EXECUTIVE SUMMARY

After analyzing the **active code** (`generic_algotest_engine.py` and `/api/dynamic-backtest` endpoint), all core P&L calculations are **verified correct**:

| Component | Status | Details |
|-----------|--------|---------|
| P&L Formula | ✅ VERIFIED | `(Exit - Entry) × Lots × LotSize` for Buy |
| P&L Formula | ✅ VERIFIED | `(Entry - Exit) × Lots × LotSize` for Sell |
| Lot Size | ✅ VERIFIED | Historical lot sizes by date |
| Equity Curve | ✅ VERIFIED | `Cumulative = InitialCapital + cumsum(Net P&L)` |
| Drawdown | ✅ VERIFIED | `%DD = DD / Peak × 100` (Peak-based) |
| Brokerage/Slippage | ⚠️ BY DESIGN | Not included (inhouse handling) |

---

## PART 1: ACTIVE CODE ANALYSIS

### 1.1 Trade-Level Calculations (generic_algotest_engine.py)

**Verified Correct Formulas:**

```python
# Line 469-474 - CORRECT
lot_size = get_lot_size(index, entry_date)

if position == 'BUY':
    leg_pnl = (exit_premium - entry_premium) * lots * lot_size
else:  # SELL
    leg_pnl = (entry_premium - exit_premium) * lots * lot_size
```

**Lot Size History (Line 21-57):**
```python
NIFTY:
  Jun 2000 – Sep 2010 : 200
  Oct 2010 – Oct 2015 : 50
  Oct 2015 – Oct 2019 : 75
  Nov 2019 – present  : 65

BANKNIFTY:
  Jun 2000 – Sep 2010 : 50
  Oct 2010 – Oct 2015 : 25
  Oct 2015 – Oct 2019 : 20
  Nov 2019 – present  : 15
```

**Column Naming (Line 604-632):**
- ✅ `Net P&L` - Used consistently
- ✅ `Entry Date` / `Exit Date`
- ✅ `Spot P&L`
- ✅ `Expiry Date`

---

### 1.2 Portfolio Metrics (base.py compute_analytics)

**Verified Formulas:**

| Metric | Formula | Status |
|--------|---------|--------|
| Cumulative | `InitialCapital + cumsum(Net P&L)` | ✅ |
| Peak | `cummax(Cumulative)` | ✅ |
| Drawdown | `Peak - Cumulative` (when Peak > Cumulative) | ✅ |
| %DD | `DD / Peak × 100` | ✅ |
| Win Rate | `win_count / count × 100` | ✅ |
| Expectancy | `((avg_win/abs(avg_loss)) × win_pct - (100-win_pct)) / 100` | ✅ |
| CAGR | `(final_equity/initial_capital)^(1/years) - 1` | ✅ |

---

### 1.3 Cross-Validation Checklist

| Check | Status | Notes |
|-------|--------|-------|
| Equity curve from trade log | ✅ PASS | Matches cumulative sum |
| Drawdown derived from equity | ✅ PASS | Uses Peak-based calculation |
| No look-ahead bias | ✅ PASS | Sequential processing only |
| Expiry handling | ✅ PASS | Weekly/Monthly correctly handled |
| ATM strike logic | ✅ PASS | Uses nearest 50-point strike |
| Entry N days before expiry | ✅ PASS | Implemented correctly |

---

## PART 2: LEGACY CODE FIXES APPLIED

### V10 Engine Fixes (v10_days_before_expiry.py)

1. **Fixed analytics call** - Was missing `expiry_col` parameter
2. **Fixed column naming** - Changed `P&L` → `Net P&L` for consistency

### V1 Engine Fixes (v1_ce_fut.py)

1. **Fixed undefined variable** - Commented out `base2` reference (not used)

### Router Cleanup (backtest.py)

1. **Removed duplicate imports** - Lines 447-510 were duplicate code

---

## PART 3: PERFORMANCE OPTIMIZATION FINDINGS

### Current Implementation

| Area | Current | Notes |
|------|---------|-------|
| Bhavcopy Caching | ✅ Good | `@lru_cache(500)` in base.py:142 |
| Data Loading | ✅ Good | CSV files pre-cleaned |
| Strategy Loops | ⚠️ OK | Nested loops but acceptable for historical data |

### Recommendations (Optional)

1. **Batch date loading** - Could pre-load all needed bhavcopy dates
2. **Vectorize intervals** - Replace iterrows with vectorized pandas
3. **Parallel execution** - Use ProcessPoolExecutor for multiple strategies

---

## PART 4: FASTAPI IMPROVEMENTS

### Current Issues Identified

| Issue | Severity | Location |
|-------|----------|----------|
| No timeout on backtest | Medium | dynamic-backtest endpoint |
| Duplicate imports | Low | backtest.py |
| Import path warnings | Low | strategy_types import |

### Recommended Improvements

1. **Add timeout handling:**
```python
from fastapi import HTTPException
import asyncio

@router.post("/dynamic-backtest")
async def dynamic_backtest(request: dict):
    try:
        result = await asyncio.wait_for(
            process_backtest(request),
            timeout=300.0  # 5 minutes
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Backtest timed out")
```

---

## RISK ASSESSMENT

| Risk | Severity | Mitigation |
|------|----------|------------|
| No brokerage | ⚠️ BY DESIGN | Inhouse handling - user confirmed |
| No slippage | ⚠️ BY DESIGN | Inhouse handling - user confirmed |
| Large dataset | ⚠️ LOW | Current implementation handles OK |

---

## FINAL VERDICT

### ✔ CALCULATIONS VERIFIED

All active P&L calculations are **mathematically correct** and match industry-standard backtesting practices:

1. ✅ Trade-level P&L formulas verified
2. ✅ Lot size application correct  
3. ✅ Equity curve logic verified
4. ✅ Drawdown calculation correct
5. ✅ CAGR formula verified
6. ✅ Expectancy formula verified
7. ✅ No look-ahead bias
8. ✅ Column naming standardized

### Minor Fixes Applied

- V10 engine: Fixed analytics call & column naming
- V1 engine: Fixed undefined base2 variable
- Router: Removed duplicate imports

### Optional Enhancements

- Add timeout handling for long-running backtests
- Consider vectorization for performance at scale
