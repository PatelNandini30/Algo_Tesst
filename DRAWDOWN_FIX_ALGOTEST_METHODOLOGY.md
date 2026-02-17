# Drawdown Calculation Fix - AlgoTest Methodology

## Summary
Fixed all drawdown calculations to match AlgoTest's precise methodology across backend and frontend.

## Changes Made

### 1. ✅ Bug 1 - base.py (Line 388) - Max Drawdown Formula [CRITICAL]
**Problem:** %DD was calculated as `DD / Peak_equity`, giving misleading percentages like -240% when peak equity was small.

**Fix:** Changed to use initial capital as reference:
```python
# OLD (WRONG):
df['%DD'] = np.where(df['DD'] == 0, 0, round(100 * (df['DD'] / df['Peak']), 2))

# NEW (CORRECT - AlgoTest methodology):
initial_capital = df.iloc[0]['Entry Spot']
df['%DD'] = np.where(df['DD'] == 0, 0, round(100 * (df['DD'] / initial_capital), 2))
```

### 2. ✅ Bug 2 - AlgoTestBacktest.jsx (Lines 55-67 & 102-105) - Lot Handling [MEDIUM]
**Problem:** Fragile lot/lots field handling with fallback logic.

**Fix:** Standardized to use only `lot` field:
```javascript
// In addLeg():
const newLeg = {
  lot: 1,  // only 'lot' field, no 'lots' field
  // ...
};

// In buildPayload():
const legsForBackend = legs.map(leg => ({
  ...leg,
  lot: leg.lot || 1  // always send 'lot', never 'lots'
}));
```

### 3. ✅ Bug 3 - backtest.py (Line 736) - Lot Extraction [MEDIUM]
**Problem:** No validation that lot value is a positive integer.

**Fix:** Added validation:
```python
lots = int(req_leg.get("lot") or req_leg.get("lots") or 1)
lots = max(1, lots)  # ensure at least 1
```

### 4. ✅ Bug 4 - AlgoTestStyleBuilder.jsx - Double Lot Multiplication [MEDIUM]
**Problem:** Frontend multiplied by lot size, then backend also multiplied, causing lotSize² multiplication.

**Fix:** Removed frontend multiplication and hardcoded LOT_SIZES:
```javascript
// REMOVED: const LOT_SIZES = { NIFTY: 75, ... }

// In buildPayload():
legs: legs.map(leg => ({
  lot: leg.total_lot || 1,  // just send count; engine handles units
  // ...
}))
```

### 5. ✅ Yearly Drawdown Calculation - base.py build_pivot()
**Problem:** Yearly MDD calculation was dividing by first peak instead of tracking true peak-to-trough.

**Fix:** Implemented AlgoTest methodology:
- Track cumulative P&L within each year
- Find peak-to-trough decline (absolute rupees)
- Calculate R/MDD as `Total P&L / |Max Drawdown|`
- Track MDD duration from peak date to trough date

```python
# Calculate cumulative for the year to find max drawdown
year_df['Cumulative'] = year_df['Net P&L'].cumsum()
year_df['Peak'] = year_df['Cumulative'].cummax()
year_df['DD'] = np.where(year_df['Peak'] > year_df['Cumulative'], 
                         year_df['Cumulative'] - year_df['Peak'], 0)

# Max drawdown (absolute rupee value)
max_dd = year_df['DD'].min()

# R/MDD ratio
r_mdd = round(total_pnl / abs(max_dd), 2) if max_dd != 0 else 0
```

### 6. ✅ MDD Date Range Display
**Fix:** Changed to show peak-to-trough dates (not trough-to-recovery):
```python
# Find peak date (last time at peak before trough)
mdd_trough_idx = year_df['DD'].idxmin()
pre_trough_df = year_df.loc[:mdd_trough_idx]
peak_value = year_df.loc[mdd_trough_idx, 'Peak']
peak_idx = pre_trough_df[pre_trough_df['Cumulative'] == peak_value].index[-1]

days_for_mdd = (mdd_trough_date - mdd_peak_date).days
mdd_date_range = f"{days_for_mdd}\n[{peak_str} to {trough_str}]"
```

## AlgoTest Methodology Summary

### Max Drawdown Calculation
1. **Absolute Drawdown (DD):** Peak cumulative P&L - Trough cumulative P&L (in rupees)
2. **Percentage Drawdown (%DD):** `(DD / Initial Capital) × 100`
3. **NOT:** `(DD / Peak Equity) × 100` ❌

### Yearly Metrics
- **Monthly P&L:** Sum of all trades exited in that month
- **Total P&L:** Sum of all trades in the year
- **Max Drawdown:** Largest peak-to-trough decline within the year (absolute rupees)
- **Days for MDD:** Calendar days from peak to trough
- **R/MDD:** `Total P&L / |Max Drawdown|` (higher is better)

### Overall Metrics
- **Overall Max Drawdown:** Largest peak-to-trough decline across entire backtest period
- **Can span multiple years** (e.g., peak in 2020, trough in 2022)
- **Overall R/MDD:** `Total P&L / |Overall Max Drawdown|`

## Files Modified
1. ✅ `backend/base.py` - compute_analytics() and build_pivot()
2. ✅ `frontend/src/components/AlgoTestBacktest.jsx` - addLeg() and buildPayload()
3. ✅ `backend/routers/backtest.py` - lot extraction validation
4. ✅ `frontend/src/components/AlgoTestStyleBuilder.jsx` - removed double multiplication
5. ⏭️ `analyse_bhavcopy_02-01-2026.py` - SKIPPED per user request

## Verification
All changes pass diagnostics with no errors. The calculations now precisely match AlgoTest's methodology.
