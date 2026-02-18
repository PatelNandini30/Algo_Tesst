# Expiry Handling Fix - Match AlgoTest Behavior

## Problem Identified

Your current system and AlgoTest handle expiries differently:

### Current System (Incorrect)
```
1. Load all expiries from CSV (e.g., Jan 9, Jan 16, Jan 23, Jan 30)
2. For each expiry:
   - Calculate entry date = expiry - entry_dte days
   - Calculate exit date = expiry - exit_dte days
   - Execute trade

Example:
Expiry: Jan 9 (Thursday)
Entry DTE: 2
Entry Date: Jan 7 (Tuesday) ✓
```

### AlgoTest Behavior (Correct)
```
1. Loop through trading days
2. For each trading day:
   - Find the next upcoming expiry (Thursday)
   - Check if DTE matches entry condition
   - If yes, enter trade
   - Exit based on exit DTE

Example:
Entry Date: Jan 7 (Tuesday)
Next Thursday: Jan 9
DTE: 2 days ✓
Enter trade
```

## Key Difference

**Current**: Expiry-driven (loops through expiries)
**AlgoTest**: Date-driven (loops through trading days, finds expiry)

---

## Analysis of Your Data

From your AlgoTest results:

| Entry Date | Entry Day | Expiry Date | Expiry Day | Days to Expiry |
|------------|-----------|-------------|------------|----------------|
| 07-01-2020 | Tuesday | 09-01-2020 | Thursday | 2 |
| 14-01-2020 | Tuesday | 16-01-2020 | Thursday | 2 |
| 21-01-2020 | Tuesday | 23-01-2020 | Thursday | 2 |
| 28-01-2020 | Tuesday | 30-01-2020 | Thursday | 2 |
| 04-02-2020 | Tuesday | 06-02-2020 | Thursday | 2 |

**Pattern**: All entries are on Tuesday, 2 days before Thursday expiry (DTE=2)

This confirms AlgoTest:
1. Checks each trading day
2. Finds next Thursday
3. Calculates DTE
4. Enters if DTE matches entry_dte parameter

---

## Solution: Two Approaches

### Approach 1: Keep Current Logic (Simpler)

Your current logic actually works correctly IF:
- Entry DTE is set correctly
- Expiry CSV has all weekly expiries
- The calculation is correct

**Verification**:
```python
# Current code
entry_date = calculate_trading_days_before_expiry(
    expiry_date=expiry_date,  # Jan 9 (Thursday)
    days_before=entry_dte,     # 2
    trading_calendar_df=trading_calendar
)
# Result: Jan 7 (Tuesday) ✓ CORRECT
```

**This should already match AlgoTest!**

### Approach 2: Date-Driven (More Flexible)

Change to loop through trading days instead of expiries:

```python
def run_algotest_backtest_date_driven(params):
    # Load trading calendar
    trading_days = get_trading_calendar(from_date, to_date)
    
    # Load expiry data
    expiry_df = load_expiry(index, expiry_type)
    
    trades = []
    
    for trade_date in trading_days:
        # Find next expiry from this date
        next_expiry = find_next_expiry(trade_date, expiry_df)
        
        # Calculate DTE
        dte = calculate_trading_days_between(trade_date, next_expiry)
        
        # Check if this is an entry day
        if dte == entry_dte:
            # Enter trade
            entry_spot = get_spot_price(trade_date)
            strike = calculate_strike(entry_spot, ...)
            
            # Calculate exit date
            exit_date = calculate_trading_days_before_expiry(
                next_expiry, exit_dte, trading_calendar
            )
            
            # Execute trade
            trade = execute_trade(
                entry_date=trade_date,
                exit_date=exit_date,
                expiry=next_expiry,
                strike=strike,
                ...
            )
            trades.append(trade)
    
    return trades
```

---

## Recommended Fix

**I recommend Approach 1** because your current logic should already work correctly. The issue might be:

1. **Missing expiries in CSV** - Ensure all weekly Thursdays are in the expiry file
2. **Incorrect DTE calculation** - Verify trading days are counted correctly
3. **Date filtering** - Check if some dates are being skipped

### Verification Steps

1. **Check Expiry CSV**:
```python
# Load expiry data
expiry_df = load_expiry('NIFTY', 'weekly')
print(expiry_df.head(20))

# Verify all Thursdays are present
# Jan 2020: 2, 9, 16, 23, 30
# Feb 2020: 6, 13, 20, 27
```

2. **Check DTE Calculation**:
```python
# Test case from your data
expiry_date = datetime(2020, 1, 9)  # Thursday
entry_dte = 2

entry_date = calculate_trading_days_before_expiry(
    expiry_date, entry_dte, trading_calendar
)

print(f"Expiry: {expiry_date}")
print(f"Entry: {entry_date}")
print(f"Expected: 2020-01-07 (Tuesday)")
# Should match!
```

3. **Check Trading Calendar**:
```python
# Verify no dates are missing
trading_calendar = get_trading_calendar('2020-01-01', '2020-02-29')
print(f"Total trading days: {len(trading_calendar)}")
# Should be ~40-45 days
```

---

## Implementation: Enhanced Expiry Selection

If you want to add the new expiry selection methods (WEEKLY, NEXT_WEEKLY, MONTHLY, NEXT_MONTHLY), here's how to integrate:

### Update Generic Engine

```python
def run_algotest_backtest(params):
    # ... existing code ...
    
    # Get expiry selection method
    expiry_selection = params.get('expiry_selection', 'WEEKLY')
    
    # Load trading calendar
    trading_days = get_trading_calendar(from_date, to_date)
    
    trades = []
    
    for trade_date in trading_days:
        # Get expiry based on selection method
        expiry_date = get_expiry_for_selection(
            entry_date=trade_date,
            index=index,
            expiry_selection=expiry_selection
        )
        
        # Calculate DTE
        dte = calculate_trading_days_between(trade_date, expiry_date)
        
        # Check if this is an entry day
        if dte == entry_dte:
            # Enter trade
            entry_spot = get_spot_price(trade_date)
            
            # Calculate strike
            strike = calculate_strike_advanced(
                date=trade_date,
                index=index,
                spot_price=entry_spot,
                strike_interval=get_strike_interval(index),
                option_type=leg_config['option_type'],
                strike_selection_type=leg_config['strike_selection_type'],
                strike_selection_value=leg_config.get('strike_selection_value'),
                expiry_selection=expiry_selection,
                min_premium=leg_config.get('min_premium'),
                max_premium=leg_config.get('max_premium')
            )
            
            # Calculate exit date
            exit_date = calculate_trading_days_before_expiry(
                expiry_date, exit_dte, trading_calendar
            )
            
            # Execute trade
            # ... rest of trade logic ...
```

---

## Testing Checklist

To verify your system matches AlgoTest:

### Test 1: Basic Weekly Trade
```python
params = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2020-01-31',
    'expiry_type': 'WEEKLY',
    'entry_dte': 2,
    'exit_dte': 0,
    'legs': [{
        'segment': 'OPTIONS',
        'option_type': 'CE',
        'strike_selection': 'ATM',
        'position': 'SELL',
        'lots': 1
    }]
}

result = run_algotest_backtest(params)

# Expected trades (from your AlgoTest data):
# Trade 1: Entry 07-01-2020, Expiry 09-01-2020
# Trade 2: Entry 14-01-2020, Expiry 16-01-2020
# Trade 3: Entry 21-01-2020, Expiry 23-01-2020
# Trade 4: Entry 28-01-2020, Expiry 30-01-2020
```

### Test 2: Verify Entry Dates
```python
# Your system should produce:
expected_entries = [
    '2020-01-07',  # Tuesday, 2 days before Jan 9
    '2020-01-14',  # Tuesday, 2 days before Jan 16
    '2020-01-21',  # Tuesday, 2 days before Jan 23
    '2020-01-28',  # Tuesday, 2 days before Jan 30
]

actual_entries = [trade['entry_date'] for trade in result['trades']]

assert actual_entries == expected_entries, "Entry dates don't match!"
```

### Test 3: Verify Expiry Dates
```python
# Your system should produce:
expected_expiries = [
    '2020-01-09',  # Thursday
    '2020-01-16',  # Thursday
    '2020-01-23',  # Thursday
    '2020-01-30',  # Thursday
]

actual_expiries = [trade['expiry_date'] for trade in result['trades']]

assert actual_expiries == expected_expiries, "Expiry dates don't match!"
```

---

## Quick Diagnostic

Run this to check if your system is working correctly:

```python
# test_expiry_matching.py

from backend.engines.generic_algotest_engine import run_algotest_backtest

params = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2020-02-29',
    'expiry_type': 'WEEKLY',
    'entry_dte': 2,
    'exit_dte': 0,
    'legs': [{
        'segment': 'OPTIONS',
        'option_type': 'CE',
        'strike_selection': 'ATM',
        'position': 'SELL',
        'lots': 1
    }]
}

result = run_algotest_backtest(params)

print("Your System Results:")
print("=" * 60)
for i, trade in enumerate(result['trades'][:5], 1):
    print(f"Trade {i}:")
    print(f"  Entry: {trade['entry_date']}")
    print(f"  Expiry: {trade['expiry_date']}")
    print(f"  Strike: {trade['strike']}")
    print()

print("\nAlgoTest Results (Expected):")
print("=" * 60)
algotest_data = [
    {'entry': '2020-01-07', 'expiry': '2020-01-09', 'strike': 12150},
    {'entry': '2020-01-14', 'expiry': '2020-01-16', 'strike': 12350},
    {'entry': '2020-01-21', 'expiry': '2020-01-23', 'strike': 12200},
    {'entry': '2020-01-28', 'expiry': '2020-01-30', 'strike': 12150},
    {'entry': '2020-02-04', 'expiry': '2020-02-06', 'strike': 11850},
]

for i, trade in enumerate(algotest_data, 1):
    print(f"Trade {i}:")
    print(f"  Entry: {trade['entry']}")
    print(f"  Expiry: {trade['expiry']}")
    print(f"  Strike: {trade['strike']}")
    print()

print("\nComparison:")
print("=" * 60)
matches = 0
for i in range(min(len(result['trades']), len(algotest_data))):
    your_trade = result['trades'][i]
    algo_trade = algotest_data[i]
    
    entry_match = str(your_trade['entry_date']) == algo_trade['entry']
    expiry_match = str(your_trade['expiry_date']) == algo_trade['expiry']
    
    print(f"Trade {i+1}:")
    print(f"  Entry: {'✓' if entry_match else '✗'}")
    print(f"  Expiry: {'✓' if expiry_match else '✗'}")
    
    if entry_match and expiry_match:
        matches += 1

print(f"\nMatches: {matches}/{len(algotest_data)}")
```

---

## Summary

**Your current system should already work correctly** if:
1. Expiry CSV has all weekly Thursdays
2. DTE calculation is correct
3. Trading calendar is complete

**To verify**: Run the diagnostic script above and compare with AlgoTest results.

**If mismatches found**: The issue is likely in:
- Missing expiry dates in CSV
- Incorrect trading day calculation
- Date filtering removing valid dates

**For new features** (WEEKLY/NEXT_WEEKLY/MONTHLY/NEXT_MONTHLY): Use the `get_expiry_for_selection()` function I implemented in `backend/base.py`.
