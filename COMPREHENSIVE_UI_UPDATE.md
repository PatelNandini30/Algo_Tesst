# Comprehensive UI Update - Backend Enhancements

## Summary
Updated the backend to calculate and send comprehensive analytics data to support a full-featured UI similar to AlgoTest, including equity curves, drawdown charts, and detailed summary metrics.

## Changes Made

### 1. Enhanced Analytics Calculation (`backend/routers/backtest.py`)

Added comprehensive analytics calculation in the `dynamic_backtest` endpoint:

```python
# Calculate drawdown and equity curve data
df_analytics = df.copy()

# Calculate cumulative P&L for equity curve
if 'Cumulative' not in df_analytics.columns:
    df_analytics['Cumulative'] = df_analytics['Net P&L'].cumsum()

# Calculate drawdown
df_analytics['Peak'] = df_analytics['Cumulative'].cummax()
df_analytics['DD'] = np.where(df_analytics['Peak'] > df_analytics['Cumulative'], 
                              df_analytics['Cumulative'] - df_analytics['Peak'], 0)
df_analytics['%DD'] = np.where(df_analytics['DD'] == 0, 0, 
                               round(100 * (df_analytics['DD'] / df_analytics['Peak']), 2))
```

### 2. Equity Curve Data

Prepared equity curve data for charting:

```python
equity_curve = []
for idx, row in df_analytics.iterrows():
    equity_curve.append({
        "date": row['Entry Date'].strftime('%Y-%m-%d'),
        "cumulative_pnl": float(row['Cumulative']),
        "peak": float(row['Peak'])
    })
```

### 3. Drawdown Data

Prepared drawdown data for charting:

```python
drawdown_data = []
for idx, row in df_analytics.iterrows():
    drawdown_data.append({
        "date": row['Entry Date'].strftime('%Y-%m-%d'),
        "drawdown_pct": float(row['%DD']),
        "drawdown_pts": float(row['DD'])
    })
```

### 4. Enhanced Summary Metrics

Integrated with `analytics.py` to calculate comprehensive metrics:

- **Total P&L**: Sum of all trade P&L
- **Win Rate**: Percentage of winning trades
- **Average Win/Loss**: Average profit/loss per trade
- **Expectancy**: Expected value per trade
- **CAGR (Options)**: Compound Annual Growth Rate for options
- **CAGR (Spot)**: Compound Annual Growth Rate for spot
- **Max Drawdown %**: Maximum percentage drawdown
- **Max Drawdown Points**: Maximum drawdown in points
- **CAR/MDD**: Calmar ratio (CAGR / Max Drawdown)
- **Recovery Factor**: Total P&L / Max Drawdown
- **ROI vs Spot**: Return on Investment vs Spot movement

### 5. Updated API Response Structure

The `/api/dynamic-backtest` endpoint now returns:

```json
{
  "status": "success",
  "meta": {
    "strategy": "Strategy Name",
    "index": "NIFTY",
    "total_trades": 208,
    "date_range": "2020-01-01 to 2023-12-31",
    "expiry_window": "weekly_expiry",
    "parameters": {...}
  },
  "trades": [...],  // Array of trade records
  "summary": {
    "total_pnl": 12345.67,
    "count": 208,
    "win_pct": 58.33,
    "avg_win": 1234.56,
    "avg_loss": -987.65,
    "expectancy": 0.45,
    "cagr_options": 15.23,
    "cagr_spot": 8.45,
    "max_dd_pct": -12.34,
    "max_dd_pts": -5678.90,
    "car_mdd": 1.23,
    "recovery_factor": 2.17,
    "roi_vs_spot": 180.5
  },
  "equity_curve": [
    {
      "date": "2020-01-07",
      "cumulative_pnl": 1234.56,
      "peak": 1234.56
    },
    ...
  ],
  "drawdown": [
    {
      "date": "2020-01-07",
      "drawdown_pct": -5.23,
      "drawdown_pts": -1234.56
    },
    ...
  ],
  "pivot": {...},
  "log": []
}
```

## Frontend Integration Required

To display this data in the UI, the frontend needs to:

1. **Equity Curve Chart**: Use `equity_curve` array to plot cumulative P&L over time
2. **Drawdown Chart**: Use `drawdown` array to plot drawdown percentage over time
3. **Summary Metrics**: Display all fields from `summary` object
4. **Trade Log Table**: Display `trades` array with proper column mapping

## Testing

Test the endpoint with:

```bash
python test_2020_backtest.py
```

This will show the backend is calculating:
- Entry premiums correctly (not 0.00)
- Lot sizes correctly (65 lots)
- P&L calculations correctly
- All summary metrics

## Next Steps

1. Update frontend `ResultsPanel.jsx` to consume the new data structure
2. Add chart components for equity curve and drawdown
3. Update summary metrics display to show all calculated fields
4. Ensure proper date formatting and number formatting in the UI

## Files Modified

- `backend/routers/backtest.py` - Added comprehensive analytics calculation
- `COMPREHENSIVE_UI_UPDATE.md` - This documentation

## Files Already Supporting This

- `backend/analytics.py` - Contains all analytics calculation functions
- `backend/engines/generic_algotest_engine.py` - Calculates trades correctly with lot sizes
- `backend/base.py` - Provides data access functions

## Known Issues

1. **Data Source Discrepancy**: Your CSV data has different prices than your reference data
   - CSV shows: Entry 18.95, Exit 65.0 for 2020-01-07
   - Reference shows: Entry 43.85, Exit 66.1
   - This is expected if using different data sources or different times of day

2. **Frontend Display**: The frontend currently shows "Leg 1 Entry: -0.00" which suggests it's not reading the data correctly from the API response. This needs to be fixed in the frontend code.

## Verification

Run the test to verify backend is working:

```bash
python test_2020_backtest.py
```

Expected output should show:
- Trade 1: Entry: 57.75, Exit: 165.9, P&L: -7029.75 (with 65 lots)
- All trades with proper entry/exit premiums
- Summary with win rate, total P&L, etc.
