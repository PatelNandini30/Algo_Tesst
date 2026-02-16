# Frontend Display Fix - Field Name Mapping

## Problem Identified

The backend is returning field names that don't match what the frontend expects:

### Backend Returns:
```json
{
  "cumulative_pnl": 74.3,
  "total_pnl": 74.3,
  "entry_date": "2023-01-03",
  "exit_date": "2023-01-05"
}
```

### Frontend Expects (from ResultsPanel.jsx lines 15-20):
```javascript
cumulative: trade.Cumulative || trade.cumulative || 0,
pnl: trade['Net P&L'] || trade.net_pnl || 0,
date: trade['Exit Date'] || trade.exit_date || `Trade ${index + 1}`,
```

### Summary Fields:
Backend returns `win_rate` but frontend expects `win_pct`

## Fix Applied

In `backend/routers/backtest.py` around line 1030-1045:

```python
# Fix trade field names to match frontend expectations
for trade in trades_list:
    # Frontend looks for 'cumulative' not 'cumulative_pnl'
    if 'cumulative_pnl' in trade:
        trade['cumulative'] = trade['cumulative_pnl']
    # Frontend looks for 'net_pnl' not 'total_pnl'  
    if 'total_pnl' in trade:
        trade['net_pnl'] = trade['total_pnl']

# Summary mapping
summary_mapped = {
    "win_pct": summary.get("win_rate", 0),  # Changed from win_rate
    ...
}
```

## CRITICAL: Server Restart Required

The changes have been made to the code but **the server MUST be restarted** for them to take effect.

### To Restart:
1. Stop the current backend server (Ctrl+C or kill the process)
2. Restart using: `python backend/start_server.py` or `kill_and_restart.bat`

### After Restart, Expected Behavior:
- ✅ Equity curve will show actual P&L values
- ✅ Summary stats will display (Win Rate, Total P&L, etc.)
- ✅ Trade table will show all trade details
- ✅ Drawdown chart will display

## Verification

After restart, run:
```bash
python test_column_names.py
```

Should show:
```
Has cumulative: True
Has net_pnl: True
cumulative value: 74.3
net_pnl value: 74.3
Summary win_pct: 100.0
```

## Status

🔴 **BLOCKED ON SERVER RESTART**

All code fixes are complete. The system will work perfectly once the server is restarted with the new code.
