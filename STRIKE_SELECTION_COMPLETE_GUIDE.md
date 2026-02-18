# Complete Strike Selection System - Trading Logic & Implementation

## Overview
This document explains the complete strike selection system with all supported methods, trading logic, and accurate calculations.

---

## 1. EXPIRY SELECTION

### Supported Expiry Types

#### A. WEEKLY
- **Definition**: Current week's expiry (Thursday)
- **Trading Logic**: 
  - If today is Monday-Thursday before expiry → Current week's Thursday
  - If today is Friday-Sunday → Next week's Thursday
- **Use Case**: Short-term trades, weekly strategies

#### B. NEXT_WEEKLY
- **Definition**: Next week's expiry (next Thursday)
- **Trading Logic**: Always the Thursday of the following week
- **Use Case**: Slightly longer duration, avoiding current week decay

#### C. MONTHLY
- **Definition**: Current month's expiry (last Thursday of month)
- **Trading Logic**: Last Thursday of the current month
- **Use Case**: Monthly strategies, longer duration trades

#### D. NEXT_MONTHLY
- **Definition**: Next month's expiry (last Thursday of next month)
- **Trading Logic**: Last Thursday of the following month
- **Use Case**: Long-term strategies, avoiding near-term decay

### Implementation
```python
expiry = get_expiry_for_selection(
    entry_date='2024-01-15',
    index='NIFTY',
    expiry_selection='WEEKLY'  # or NEXT_WEEKLY, MONTHLY, NEXT_MONTHLY
)
```

---

## 2. STRIKE SELECTION METHODS

### Method 1: ATM (At The Money)

**Trading Logic:**
- Strike closest to current spot price
- Highest liquidity
- Balanced delta (~0.5 for options)

**Calculation:**
```
Spot = 24,350
Strike Interval = 50 (NIFTY)

ATM Strike = round(24,350 / 50) × 50
           = round(487) × 50
           = 487 × 50
           = 24,350
```

**Code:**
```python
result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='ATM',
    expiry_selection='WEEKLY'
)
# Returns: {'strike': 24350, 'expiry': datetime, 'premium': 180.5}
```

---

### Method 2: ITM (In The Money)

**Trading Logic:**
- Strike with intrinsic value
- Higher premium, lower risk
- Higher delta (>0.5)
- For CE: Strike BELOW spot
- For PE: Strike ABOVE spot

**Calculation Examples:**

**ITM1 Call (CE):**
```
Spot = 24,350
Strike Interval = 50
Offset = 1 strike

ATM = 24,350
ITM1 CE = ATM - (1 × 50) = 24,350 - 50 = 24,300 ✅
```

**ITM2 Put (PE):**
```
Spot = 24,350
Strike Interval = 50
Offset = 2 strikes

ATM = 24,350
ITM2 PE = ATM + (2 × 50) = 24,350 + 100 = 24,450 ✅
```

**Code:**
```python
# ITM1 Call
result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='ITM',
    strike_selection_value=1,  # ITM1
    expiry_selection='WEEKLY'
)
# Returns: {'strike': 24300, 'expiry': datetime, 'premium': 220.5}

# ITM5 Put
result = calculate_strike_advanced(
    date='2024-01-15',
    index='BANKNIFTY',
    spot_price=48750,
    strike_interval=100,
    option_type='PE',
    strike_selection_type='ITM',
    strike_selection_value=5,  # ITM5
    expiry_selection='MONTHLY'
)
# Returns: {'strike': 49300, 'expiry': datetime, 'premium': 580.0}
```

---

### Method 3: OTM (Out of The Money)

**Trading Logic:**
- Strike with NO intrinsic value (only time value)
- Lower premium, higher risk/reward
- Lower delta (<0.5)
- For CE: Strike ABOVE spot
- For PE: Strike BELOW spot

**Calculation Examples:**

**OTM2 Call (CE):**
```
Spot = 24,350
Strike Interval = 50
Offset = 2 strikes

ATM = 24,350
OTM2 CE = ATM + (2 × 50) = 24,350 + 100 = 24,450 ✅
```

**OTM3 Put (PE):**
```
Spot = 24,350
Strike Interval = 50
Offset = 3 strikes

ATM = 24,350
OTM3 PE = ATM - (3 × 50) = 24,350 - 150 = 24,200 ✅
```

**Code:**
```python
# OTM2 Call
result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='OTM',
    strike_selection_value=2,  # OTM2
    expiry_selection='NEXT_WEEKLY'
)
# Returns: {'strike': 24450, 'expiry': datetime, 'premium': 95.5}
```

---

### Method 4: PREMIUM_RANGE

**Trading Logic:**
- Select strike where premium falls within specified range
- Ensures consistent risk/reward across trades
- Useful for capital allocation strategies
- Automatically finds most liquid strike in range

**Calculation Process:**
```
Step 1: Get all available strikes with premiums
Available Strikes:
  24200 → ₹280
  24250 → ₹230
  24300 → ₹185
  24350 → ₹145
  24400 → ₹110
  24450 → ₹80
  24500 → ₹55

Step 2: Filter by premium range (100-200)
In Range:
  24300 → ₹185 ✅
  24350 → ₹145 ✅
  24400 → ₹110 ✅

Step 3: Select closest to ATM
ATM = 24,350
Distances: |24300-24350|=50, |24350-24350|=0, |24400-24350|=50
Selected: 24350 (₹145) ✅
```

**Code:**
```python
result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='PREMIUM_RANGE',
    min_premium=100,
    max_premium=200,
    expiry_selection='WEEKLY'
)
# Returns: {'strike': 24350, 'expiry': datetime, 'premium': 145.0}
```

**Trading Use Cases:**
- Risk management: Limit maximum loss per trade
- Portfolio allocation: Consistent position sizing
- Strategy testing: Compare strategies with similar premium ranges

---

### Method 5: CLOSEST_PREMIUM

**Trading Logic:**
- Find strike with premium closest to target value
- Ensures exact risk/reward profile
- Useful for replicating specific strategies
- Adapts to market volatility

**Calculation Process:**
```
Target Premium = 150

Step 1: Get all available strikes with premiums
Available Strikes:
  24200 → ₹280
  24250 → ₹230
  24300 → ₹185
  24350 → ₹145
  24400 → ₹110
  24450 → ₹80

Step 2: Calculate differences from target
Differences:
  24200 → |280-150| = 130
  24250 → |230-150| = 80
  24300 → |185-150| = 35 ✅ (smallest)
  24350 → |145-150| = 5  ✅✅ (SMALLEST)
  24400 → |110-150| = 40
  24450 → |80-150| = 70

Step 3: Select strike with minimum difference
Selected: 24350 (₹145) ✅
```

**Code:**
```python
result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='PE',
    strike_selection_type='CLOSEST_PREMIUM',
    strike_selection_value=150,  # Target premium
    expiry_selection='MONTHLY'
)
# Returns: {'strike': 24300, 'expiry': datetime, 'premium': 148.5}
```

**Trading Use Cases:**
- Consistent premium collection strategies
- Iron condor/butterfly with specific credit targets
- Volatility-adjusted position sizing

---

## 3. COMPLETE TRADING EXAMPLES

### Example 1: Conservative Weekly Call Selling
```python
# Sell OTM2 Call on current weekly expiry
result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='OTM',
    strike_selection_value=2,
    expiry_selection='WEEKLY'
)
# Strike: 24450, Premium: ₹95
# Risk: Unlimited above 24545 (strike + premium)
# Probability of profit: ~70% (OTM2)
```

### Example 2: Aggressive Put Buying
```python
# Buy ITM3 Put on next monthly expiry
result = calculate_strike_advanced(
    date='2024-01-15',
    index='BANKNIFTY',
    spot_price=48750,
    strike_interval=100,
    option_type='PE',
    strike_selection_type='ITM',
    strike_selection_value=3,
    expiry_selection='NEXT_MONTHLY'
)
# Strike: 49050, Premium: ₹650
# Intrinsic value: ₹300 (49050-48750)
# Time value: ₹350
# Breakeven: 48400 (strike - premium)
```

### Example 3: Premium-Based Iron Condor
```python
# Sell Call with premium 100-150
call_result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='PREMIUM_RANGE',
    min_premium=100,
    max_premium=150,
    expiry_selection='WEEKLY'
)

# Sell Put with premium 100-150
put_result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='PE',
    strike_selection_type='PREMIUM_RANGE',
    min_premium=100,
    max_premium=150,
    expiry_selection='WEEKLY'
)
# Total credit: ₹250 (125+125)
# Max profit: ₹250
# Profit range: Between put and call strikes
```

### Example 4: Volatility-Adjusted Straddle
```python
# Buy ATM Call with target premium 200
call_result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='CE',
    strike_selection_type='CLOSEST_PREMIUM',
    strike_selection_value=200,
    expiry_selection='MONTHLY'
)

# Buy ATM Put with target premium 200
put_result = calculate_strike_advanced(
    date='2024-01-15',
    index='NIFTY',
    spot_price=24350,
    strike_interval=50,
    option_type='PE',
    strike_selection_type='CLOSEST_PREMIUM',
    strike_selection_value=200,
    expiry_selection='MONTHLY'
)
# Total cost: ₹400
# Breakeven: 23950 or 24750 (strike ± total premium)
# Profit: Unlimited on both sides beyond breakeven
```

---

## 4. STRIKE INTERVAL REFERENCE

| Index | Strike Interval | Example Strikes |
|-------|----------------|-----------------|
| NIFTY | 50 | 24300, 24350, 24400, 24450 |
| BANKNIFTY | 100 | 48600, 48700, 48800, 48900 |
| FINNIFTY | 50 | 22300, 22350, 22400, 22450 |

---

## 5. FRONTEND INTEGRATION

### Data Structure to Send to Backend

```javascript
// Example 1: OTM2 Weekly Call
const legConfig = {
  instrument_type: 'OPTION',
  option_type: 'CE',
  position: 'SELL',
  strike_selection_type: 'OTM',
  strike_selection_value: 2,
  expiry_selection: 'WEEKLY',
  lots: 1
};

// Example 2: Premium Range 100-200
const legConfig = {
  instrument_type: 'OPTION',
  option_type: 'PE',
  position: 'BUY',
  strike_selection_type: 'PREMIUM_RANGE',
  min_premium: 100,
  max_premium: 200,
  expiry_selection: 'MONTHLY',
  lots: 2
};

// Example 3: Closest Premium 150
const legConfig = {
  instrument_type: 'OPTION',
  option_type: 'CE',
  position: 'BUY',
  strike_selection_type: 'CLOSEST_PREMIUM',
  strike_selection_value: 150,
  expiry_selection: 'NEXT_WEEKLY',
  lots: 1
};
```

### UI Components Needed

1. **Expiry Selection Dropdown**
   - Options: Weekly, Next Weekly, Monthly, Next Monthly

2. **Strike Selection Type Dropdown**
   - Options: ATM, ITM, OTM, Premium Range, Closest Premium

3. **Conditional Inputs**
   - For ITM/OTM: Number input (1-30)
   - For Premium Range: Two inputs (min, max)
   - For Closest Premium: Single input (target premium)

---

## 6. BACKEND API STRUCTURE

### Request Format
```json
{
  "index": "NIFTY",
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "entry_time": "09:20",
  "exit_time": "15:15",
  "entry_dte": 5,
  "exit_dte": 0,
  "legs": [
    {
      "instrument_type": "OPTION",
      "option_type": "CE",
      "position": "SELL",
      "strike_selection_type": "OTM",
      "strike_selection_value": 2,
      "expiry_selection": "WEEKLY",
      "lots": 1
    }
  ]
}
```

### Response Format
```json
{
  "trades": [
    {
      "entry_date": "2024-01-15",
      "entry_spot": 24350,
      "strike": 24450,
      "expiry": "2024-01-18",
      "entry_premium": 95.5,
      "exit_premium": 45.2,
      "pnl": 3267.5,
      "exit_date": "2024-01-18"
    }
  ],
  "summary": {
    "total_trades": 45,
    "winning_trades": 32,
    "losing_trades": 13,
    "win_rate": 71.11,
    "total_pnl": 125430.50,
    "max_profit": 8500.00,
    "max_loss": -12300.00
  }
}
```

---

## 7. TRADING LOGIC VALIDATION

### ATM Strike Calculation
```python
def test_atm():
    # Test 1: Exact multiple
    assert calculate_strike_from_selection(24350, 50, 'ATM', 'CE') == 24350
    
    # Test 2: Round up
    assert calculate_strike_from_selection(24375, 50, 'ATM', 'CE') == 24400
    
    # Test 3: Round down
    assert calculate_strike_from_selection(24325, 50, 'ATM', 'CE') == 24300
```

### ITM/OTM Logic
```python
def test_itm_otm():
    # Call ITM (below spot)
    assert calculate_strike_from_selection(24350, 50, 'ITM1', 'CE') == 24300
    
    # Call OTM (above spot)
    assert calculate_strike_from_selection(24350, 50, 'OTM2', 'CE') == 24450
    
    # Put ITM (above spot)
    assert calculate_strike_from_selection(24350, 50, 'ITM1', 'PE') == 24400
    
    # Put OTM (below spot)
    assert calculate_strike_from_selection(24350, 50, 'OTM2', 'PE') == 24250
```

---

## 8. ERROR HANDLING

### Common Errors and Solutions

1. **No strikes in premium range**
   ```python
   # Error: No strike found with premium between 500 and 600
   # Solution: Widen the range or check market volatility
   ```

2. **Invalid expiry selection**
   ```python
   # Error: No weekly expiry found for NIFTY on 2024-01-15
   # Solution: Check expiry data files exist and are up to date
   ```

3. **Missing bhavcopy data**
   ```python
   # Error: Bhavcopy file not found: 2024-01-15.csv
   # Solution: Ensure cleaned_csvs directory has data for date range
   ```

---

## 9. PERFORMANCE OPTIMIZATION

### Caching Strategy
- Bhavcopy data: LRU cache (500 files)
- Expiry data: Loaded once per backtest
- Strike calculations: No caching (fast computation)

### Database Queries
- Premium lookup: Direct CSV read (faster than DB)
- Batch processing: Load full day's data once

---

## 10. TESTING CHECKLIST

- [ ] ATM strike calculation for NIFTY (interval 50)
- [ ] ATM strike calculation for BANKNIFTY (interval 100)
- [ ] ITM1-ITM10 for both CE and PE
- [ ] OTM1-OTM10 for both CE and PE
- [ ] Weekly expiry selection
- [ ] Next weekly expiry selection
- [ ] Monthly expiry selection
- [ ] Next monthly expiry selection
- [ ] Premium range with valid range
- [ ] Premium range with no strikes in range
- [ ] Closest premium with exact match
- [ ] Closest premium with approximate match
- [ ] Multi-leg strategy with different selection types
- [ ] Edge case: Spot exactly at strike
- [ ] Edge case: Very high/low spot prices

---

## Summary

This system provides complete flexibility for strike selection with trading-accurate logic:

1. **5 Strike Selection Methods**: ATM, ITM, OTM, Premium Range, Closest Premium
2. **4 Expiry Options**: Weekly, Next Weekly, Monthly, Next Monthly
3. **Premium-Based Selection**: Range and closest target
4. **Multi-Leg Support**: Different methods per leg
5. **Trading Accuracy**: Matches real-world option behavior

All calculations follow NSE standards and real trading logic.
