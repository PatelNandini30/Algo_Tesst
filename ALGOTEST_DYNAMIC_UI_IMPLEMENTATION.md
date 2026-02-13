# AlgoTest-Style Dynamic UI Implementation Guide

## Overview
Build a fully dynamic, user-driven options backtesting UI where users configure every aspect of their strategy through the interface - no predefined strategies.

## Current State Analysis

### ✅ What Already Exists in Backend

1. **Generic Multi-Leg Engine** (`backend/strategies/generic_multi_leg_engine.py`)
   - Handles any combination of legs dynamically
   - Strike selection calculations (ATM, OTM%, ITM%, Premium-based)
   - Entry/Exit condition checking
   - Re-entry/spot adjustment logic
   - Base2 range filtering

2. **Strategy Type Definitions** (`backend/strategies/strategy_types.py`)
   - Complete enums for all options (InstrumentType, OptionType, PositionType, etc.)
   - Strike selection types (ATM, Closest Premium, Premium Range, Straddle Width, etc.)
   - Entry/Exit time types
   - Leg and Strategy definition models

3. **Data Infrastructure**
   - Bhavcopy database with historical data
   - Expiry calendars (weekly/monthly)
   - Base2 range data
   - Strike data retrieval functions

### ❌ What's Missing

1. **Frontend Components**
   - Dynamic instrument settings panel
   - Entry/Exit settings with conditional logic
   - Advanced leg builder with all strike criteria
   - Premium range inputs
   - Straddle width calculator

2. **Backend API Endpoints**
   - Dynamic backtest endpoint that accepts user-defined legs
   - Strike criteria validation
   - Entry/Exit timing calculations

3. **Business Logic**
   - Days before expiry calculation (0-4 for weekly, 0-24 for monthly)
   - Strike criteria conditional rendering
   - Premium range validation
   - Straddle width % calculation

---

## UI Structure (Based on AlgoTest Screenshots)

### 1. INSTRUMENT SETTINGS

```
┌─────────────────────────────────────┐
│ Instrument Settings                 │
├─────────────────────────────────────┤
│ Index: [NIFTY ▼]                   │
│                                     │
│ Underlying from: [Cash] [Futures]  │
│                                     │
├─────────────────────────────────────┤
```

**Fields:**
- `index`: Dropdown (NIFTY, SENSEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY)
- `underlying_type`: Radio buttons (Cash, Futures)

**Logic:**
- If Cash selected: Use spot prices for calculations
- If Futures selected: Use futures prices for underlying

---

### 2. ENTRY SETTINGS

```
┌─────────────────────────────────────┐
│ Entry Settings                      │
├─────────────────────────────────────┤
│ Strategy Type: [Intraday][STBT][Positional]│
│                                     │
│ Positional expires on:              │
│   [Weekly Expiry ▼] [basis]        │
│                                     │
│ Entry Time:                         │
│   [09:35] ⟳                        │
│                                     │
│ Entry:                              │
│   [2 ▼] trading days before expiry │
│                                     │
├─────────────────────────────────────┤
```

**Fields:**
- `strategy_type`: Buttons (Intraday, STBT, Positional)
- `expiry_type`: Dropdown (Weekly Expiry, Monthly Expiry)
- `entry_time`: Time picker (HH:MM format)
- `entry_days_before`: Dropdown
  - If Weekly: [0, 1, 2, 3, 4] trading days before expiry
  - If Monthly: [0-24] days before expiry

**Logic:**
```python
if expiry_type == "Weekly Expiry":
    entry_days_options = [0, 1, 2, 3, 4]
elif expiry_type == "Monthly Expiry":
    entry_days_options = list(range(0, 25))  # 0-24
```

---

### 3. EXIT SETTINGS

```
┌─────────────────────────────────────┐
│ Exit Settings                       │
├─────────────────────────────────────┤
│ Exit Time:                          │
│   [15:15] ⟳                        │
│                                     │
│ Exit:                               │
│   [0 ▼] trading days before expiry │
│                                     │
├─────────────────────────────────────┤
```

**Fields:**
- `exit_time`: Time picker (HH:MM format)
- `exit_days_before`: Dropdown
  - If Weekly: [0, 1, 2, 3, 4] trading days before expiry
  - If Monthly: [0-24] days before expiry

**Logic:**
- Same as entry settings
- Validation: exit_days_before <= entry_days_before

---

### 4. LEG BUILDER (Most Complex Part)

```
┌──────────────────────────────────────────────────────────────┐
│ Leg Builder                                    [Collapse ▼]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ Select segments: [Futures] [Options]                        │
│                                                              │
│ Total Lot: [1]                                              │
│                                                              │
│ Position: [Buy ▼] [Sell]                                   │
│                                                              │
│ Option Type: [Call ▼] [Put]                                │
│                                                              │
│ Expiry: [Weekly ▼]                                          │
│   Options: Weekly, Next Weekly, Monthly, Next Monthly       │
│                                                              │
│ Strike Criteria: [Strike Type ▼]                           │
│   ┌─────────────────────────────────────────┐              │
│   │ • Strike Type                            │              │
│   │ • ATM Straddle Premium %                │              │
│   │ • Premium Range                          │              │
│   │ • Closest Premium                        │              │
│   │ • Premium >=                             │              │
│   │ • Premium <=                             │              │
│   │ • Straddle Width                         │              │
│   │ • % of ATM                               │              │
│   └─────────────────────────────────────────┘              │
│                                                              │
│ [Conditional UI based on Strike Criteria selection]         │
│                                                              │
│                                    [Add Leg]                 │
└──────────────────────────────────────────────────────────────┘
```

#### 4.1 FUTURES LEG

**Fields:**
- `instrument`: "FUTURE"
- `total_lot`: Number input (1-100)
- `position`: Dropdown (Buy, Sell)
- `expiry`: Dropdown (Monthly, Next Monthly)

**No strike criteria needed for futures**

---

#### 4.2 OPTIONS LEG - Base Fields

**Fields:**
- `instrument`: "OPTION"
- `total_lot`: Number input (1-100)
- `position`: Dropdown (Buy, Sell)
- `option_type`: Dropdown (Call, Put)
- `expiry`: Dropdown (Weekly, Next Weekly, Monthly, Next Monthly)
- `strike_criteria`: Dropdown (see below)

---

#### 4.3 STRIKE CRITERIA - Conditional UI

##### A. Strike Type (ATM/OTM/ITM)

```
Strike Criteria: [Strike Type ▼]
  ↓
Strike Type: [ATM ▼]
  Options: ATM, OTM, ITM
  
If ATM selected:
  → No additional fields

If OTM selected:
  → [2] strikes OTM

If ITM selected:
  → [1] strikes ITM
```

**Backend Calculation:**
```python
if strike_type == "ATM":
    strike = round(spot / 100) * 100
elif strike_type == "OTM":
    if option_type == "CE":
        strike = atm + (otm_strikes * 100)
    else:  # PE
        strike = atm - (otm_strikes * 100)
elif strike_type == "ITM":
    if option_type == "CE":
        strike = atm - (itm_strikes * 100)
    else:  # PE
        strike = atm + (itm_strikes * 100)
```

---

##### B. Premium Range

```
Strike Criteria: [Premium Range ▼]
  ↓
Lower Range: [50]
Upper Range: [150]
```

**Backend Calculation:**
```python
# Find all strikes where premium is between lower and upper
valid_strikes = []
for strike in available_strikes:
    premium = get_option_premium(strike, option_type, expiry)
    if lower_range <= premium <= upper_range:
        valid_strikes.append(strike)

# Select closest to ATM
selected_strike = min(valid_strikes, key=lambda x: abs(x - atm))
```

---

##### C. Closest Premium

```
Strike Criteria: [Closest Premium ▼]
  ↓
Premium: [100]
```

**Backend Calculation:**
```python
# Find strike with premium closest to target
target_premium = 100
best_strike = None
min_diff = float('inf')

for strike in available_strikes:
    premium = get_option_premium(strike, option_type, expiry)
    diff = abs(premium - target_premium)
    if diff < min_diff:
        min_diff = diff
        best_strike = strike
```

---

##### D. Premium >= or Premium <=

```
Strike Criteria: [Premium >= ▼]
  ↓
Premium: [75]
```

**Backend Calculation:**
```python
if criteria == "Premium >=":
    valid_strikes = [s for s in strikes if get_premium(s) >= threshold]
elif criteria == "Premium <=":
    valid_strikes = [s for s in strikes if get_premium(s) <= threshold]

# Select closest to ATM
selected_strike = min(valid_strikes, key=lambda x: abs(x - atm))
```

---

##### E. Straddle Width

```
Strike Criteria: [Straddle Width ▼]
  ↓
% of ATM: [2.5] %
```

**Backend Calculation:**
```python
atm = round(spot / 100) * 100
width_points = atm * (straddle_width_pct / 100)

if option_type == "CE":
    strike = atm + width_points
else:  # PE
    strike = atm - width_points

# Round to nearest 100
strike = round(strike / 100) * 100
```

---

##### F. % of ATM

```
Strike Criteria: [% of ATM ▼]
  ↓
ATM +/-: [+2.0] %
```

**Backend Calculation:**
```python
atm = round(spot / 100) * 100
adjusted_strike = atm * (1 + pct_of_atm / 100)
strike = round(adjusted_strike / 100) * 100
```

---

##### G. ATM Straddle Premium %

```
Strike Criteria: [ATM Straddle Premium % ▼]
  ↓
% of Straddle: [40] %
```

**Backend Calculation:**
```python
atm = round(spot / 100) * 100

# Get ATM straddle premium
atm_ce_premium = get_option_premium(atm, "CE", expiry)
atm_pe_premium = get_option_premium(atm, "PE", expiry)
straddle_premium = atm_ce_premium + atm_pe_premium

# Target premium for this leg
target_premium = straddle_premium * (straddle_pct / 100)

# Find strike with closest premium
selected_strike = find_closest_premium_strike(target_premium, option_type)
```

---

## Frontend Component Structure

```
src/
├── components/
│   ├── InstrumentSettings.jsx       ← NEW
│   ├── EntrySettings.jsx            ← NEW
│   ├── ExitSettings.jsx             ← NEW
│   ├── LegBuilder.jsx               ← ENHANCE EXISTING
│   │   ├── FutureLegForm.jsx       ← NEW
│   │   ├── OptionLegForm.jsx       ← NEW
│   │   └── StrikeCriteriaSelector.jsx ← NEW (Complex!)
│   ├── LegwiseSettings.jsx          ← NEW
│   ├── OverallStrategySettings.jsx  ← NEW
│   └── ResultsPanel.jsx             ← EXISTS
```

---

## Backend API Structure

### Endpoint: POST /api/dynamic-backtest

**Request Payload:**
```json
{
  "instrument_settings": {
    "index": "NIFTY",
    "underlying_type": "Futures"
  },
  "entry_settings": {
    "strategy_type": "Positional",
    "expiry_type": "Weekly",
    "entry_time": "09:35",
    "entry_days_before": 2
  },
  "exit_settings": {
    "exit_time": "15:15",
    "exit_days_before": 0
  },
  "legs": [
    {
      "leg_number": 1,
      "instrument": "OPTION",
      "option_type": "CE",
      "position": "SELL",
      "total_lot": 1,
      "expiry": "Weekly",
      "strike_criteria": {
        "type": "Premium Range",
        "lower_range": 50,
        "upper_range": 150
      }
    },
    {
      "leg_number": 2,
      "instrument": "FUTURE",
      "position": "BUY",
      "total_lot": 1,
      "expiry": "Monthly"
    }
  ],
  "overall_settings": {
    "overall_stoploss": null,
    "overall_target": null,
    "trailing_options": null
  },
  "backtest_period": {
    "start_date": "2020-01-01",
    "end_date": "2023-12-31"
  }
}
```

---

## Implementation Priority

### Phase 1: Core UI Components (Week 1)
1. InstrumentSettings.jsx
2. EntrySettings.jsx with conditional days logic
3. ExitSettings.jsx with conditional days logic
4. Basic LegBuilder with Future/Option toggle

### Phase 2: Strike Criteria (Week 2)
1. StrikeCriteriaSelector.jsx with all 7 types
2. Conditional rendering logic
3. Validation for each criteria type

### Phase 3: Backend Integration (Week 3)
1. Create /api/dynamic-backtest endpoint
2. Implement strike calculation functions
3. Entry/Exit timing calculations
4. Connect to generic_multi_leg_engine.py

### Phase 4: Advanced Features (Week 4)
1. Overall strategy settings (SL/Target)
2. Legwise settings
3. Multiple leg management
4. Leg reordering/deletion

---

## Key Calculations Reference

### Entry/Exit Days Calculation

```python
def calculate_entry_date(expiry_date, days_before, expiry_type):
    """
    Calculate entry date based on days before expiry
    """
    if expiry_type == "Weekly":
        # Weekly has max 5 trading days
        # Days before: 0 = expiry day, 1 = 1 day before, etc.
        pass
    elif expiry_type == "Monthly":
        # Monthly can have up to 24 days before
        pass
    
    # Use trading calendar to count backwards
    trading_days = get_trading_days_before(expiry_date, days_before)
    return trading_days[0]  # First trading day in the window
```

### Strike Rounding

```python
def round_strike(value, base=50):
    """
    Round strike to nearest base (50 or 100)
    NIFTY: 50
    BANKNIFTY: 100
    """
    return round(value / base) * base
```

---

## Validation Rules

1. **Entry before Exit**: entry_days_before >= exit_days_before
2. **At least one leg**: len(legs) >= 1
3. **Max 4 legs**: len(legs) <= 4
4. **Premium ranges**: lower_range < upper_range
5. **Lot size**: 1 <= total_lot <= 100
6. **Date range**: start_date < end_date

---

## Next Steps

1. Review this document
2. Confirm UI matches AlgoTest screenshots
3. Start with Phase 1 implementation
4. Test each component independently
5. Integrate with backend

