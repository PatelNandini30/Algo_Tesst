# LOT SIZE FIX REQUIRED

## Problem

The backend correctly handles lot sizes automatically using the `get_lot_size()` function, which returns the correct lot size based on the index and date:

- **NIFTY**: 50 units per lot (as of Nov 2019)
- **BANKNIFTY**: 15 units per lot (as of Nov 2019)

However, the frontend is currently sending `lots: 75` instead of `lots: 1`, causing the P&L to be calculated as:

```
Wrong: (Entry - Exit) × 75 lots × 50 lot_size = (Entry - Exit) × 3750 units
Correct: (Entry - Exit) × 1 lot × 50 lot_size = (Entry - Exit) × 50 units
```

This makes the P&L **75 times larger** than it should be!

## How Backend Works

The backend code in `backend/engines/generic_algotest_engine.py` (line 433):

```python
# Get the correct lot size based on index and date
lot_size = get_lot_size(index, entry_date)

# Calculate P&L: premium_diff × lots × lot_size
if position == 'BUY':
    leg_pnl = (exit_premium - entry_premium) * lots * lot_size
else:  # SELL
    leg_pnl = (entry_premium - exit_premium) * lots * lot_size
```

The `get_lot_size()` function returns:
- NIFTY (2019-present): 50
- NIFTY (2015-2019): 75
- NIFTY (2010-2015): 50
- BANKNIFTY (2019-present): 15
- etc.

## Frontend Fix Required

### Current (Wrong) Behavior
```javascript
// Frontend sends:
{
  "legs": [{
    "lots": 75  // ❌ WRONG! This means 75 lots
  }]
}

// Backend calculates:
// P&L = premium_diff × 75 × 50 = premium_diff × 3750 units
```

### Correct Behavior
```javascript
// Frontend should send:
{
  "legs": [{
    "lots": 1  // ✅ CORRECT! This means 1 lot
  }]
}

// Backend calculates:
// P&L = premium_diff × 1 × 50 = premium_diff × 50 units
```

## What Needs to Change

### 1. Frontend Form Field

The "Total Lot" input field should:
- Accept values like: 1, 2, 3, 4, 5 (number of lots)
- NOT accept values like: 50, 75, 100 (total units)

### 2. API Request

When sending the backtest request, ensure:
```javascript
const backtestRequest = {
  // ... other fields
  legs: [{
    // ... other leg fields
    lots: parseInt(formData.totalLot) || 1  // Send 1, 2, 3, etc.
  }]
};
```

### 3. User Interface

Display to user:
- "1 lot" (not "50 units")
- "2 lots" (not "100 units")

The backend will automatically convert:
- 1 lot → 50 units (for NIFTY)
- 2 lots → 100 units (for NIFTY)
- etc.

## Testing

Run the test to see the difference:

```bash
python test_correct_lot_size.py
```

This will show:
- Wrong calculation with `lots=75`: P&L is 75x larger
- Correct calculation with `lots=1`: P&L is correct

## Expected Results

For the 2020-2023 backtest with 1 lot:
- **Correct**: ₹-1,048.33 (with lots=1, calculated as 1 × 50 = 50 units)
- **Wrong**: ₹-78,624.75 (with lots=75, calculated as 75 × 50 = 3750 units)

The ratio is exactly 75x!

## Files to Modify

### Frontend Files (Need to be updated)
1. `frontend/src/components/StrategyBuilder.jsx` or similar
   - Update the "Total Lot" input field
   - Ensure it sends 1, 2, 3, etc. (not 50, 75, 100)

2. `frontend/src/components/BacktestForm.jsx` or similar
   - Update the form submission logic
   - Ensure `lots` field sends the number of lots, not total units

### Backend Files (Already Correct)
- `backend/engines/generic_algotest_engine.py` ✅ Already correct
- `backend/base.py` ✅ Already has `get_lot_size()` function

## Summary

**Frontend must send**: Number of lots (1, 2, 3, ...)
**Backend will calculate**: Number of lots × Lot size (50 for NIFTY)

This ensures:
1. User-friendly interface (users think in "lots", not "units")
2. Correct P&L calculations
3. Automatic handling of different lot sizes for different indexes and time periods
