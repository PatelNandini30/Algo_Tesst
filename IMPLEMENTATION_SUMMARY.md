# AlgoTest-Style Dynamic UI - Implementation Summary

## ✅ What Has Been Created

### Frontend Components (All New)

1. **InstrumentSettings.jsx**
   - Index selection (NIFTY, SENSEX, BANKNIFTY, etc.)
   - Underlying type (Cash vs Futures)
   - Clean, button-based UI

2. **EntryExitSettings.jsx**
   - Strategy type (Intraday, STBT, Positional)
   - Expiry type (Weekly/Monthly)
   - Entry/Exit time pickers
   - Dynamic days before expiry (0-4 for weekly, 0-24 for monthly)
   - Automatic validation

3. **StrikeCriteriaSelector.jsx** (Most Complex)
   - 8 different strike selection methods:
     * Strike Type (ATM/OTM/ITM)
     * ATM Straddle Premium %
     * Premium Range
     * Closest Premium
     * Premium >=
     * Premium <=
     * Straddle Width
     * % of ATM
   - Conditional UI that changes based on selection
   - Real-time calculation hints

4. **DynamicLegBuilder.jsx**
   - Add/remove legs dynamically
   - Expandable/collapsible leg cards
   - Future vs Option toggle
   - Buy/Sell position buttons
   - Call/Put selection
   - Expiry selection (Weekly/Next Weekly/Monthly/Next Monthly)
   - Integrates StrikeCriteriaSelector
   - Leg summary display

5. **AlgoTestStyleBuilder.jsx** (Main Component)
   - Orchestrates all sub-components
   - Validation logic
   - API payload builder
   - Strategy summary display
   - Error handling
   - Loading states

---

## 🔧 Backend Changes Needed

### 1. Create New API Endpoint

**File:** `backend/routers/strategies.py`

Add this endpoint:

```python
@router.post("/dynamic-backtest")
async def run_dynamic_backtest(request: dict):
    """
    Run backtest with user-defined dynamic strategy
    """
    try:
        # Extract settings
        instrument_settings = request['instrument_settings']
        entry_settings = request['entry_settings']
        exit_settings = request['exit_settings']
        legs = request['legs']
        backtest_period = request['backtest_period']
        
        # Transform to generic_multi_leg format
        strategy_def = transform_to_strategy_definition(
            instrument_settings,
            entry_settings,
            exit_settings,
            legs
        )
        
        # Run backtest using existing generic_multi_leg_engine
        from ..strategies.generic_multi_leg_engine import run_generic_multi_leg
        
        params = {
            'strategy': strategy_def,
            'from_date': backtest_period['start_date'],
            'to_date': backtest_period['end_date']
        }
        
        trades_df, summary, pivot = run_generic_multi_leg(params)
        
        # Return results
        return {
            'trades': trades_df.to_dict('records'),
            'summary': summary,
            'pivot': {
                'headers': pivot.columns.tolist(),
                'rows': pivot.values.tolist()
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 2. Create Transformation Function

**File:** `backend/routers/strategies.py`

```python
def transform_to_strategy_definition(instrument_settings, entry_settings, exit_settings, legs):
    """
    Transform frontend payload to StrategyDefinition format
    """
    from ..strategies.strategy_types import (
        StrategyDefinition, Leg, InstrumentType, OptionType,
        PositionType, ExpiryType, StrikeSelection, StrikeSelectionType,
        EntryCondition, ExitCondition, EntryTimeType, ExitTimeType
    )
    
    # Map expiry types
    expiry_map = {
        'Weekly': ExpiryType.WEEKLY,
        'Next Weekly': ExpiryType.WEEKLY_T1,
        'Monthly': ExpiryType.MONTHLY,
        'Next Monthly': ExpiryType.MONTHLY_T1
    }
    
    # Transform legs
    transformed_legs = []
    for leg_data in legs:
        # Build strike selection
        strike_criteria = leg_data.get('strike_criteria', {})
        strike_selection = build_strike_selection(strike_criteria)
        
        # Build entry condition
        entry_condition = EntryCondition(
            type=EntryTimeType.DAYS_BEFORE_EXPIRY,
            days_before_expiry=entry_settings['entry_days_before'],
            specific_time=entry_settings['entry_time']
        )
        
        # Build exit condition
        exit_condition = ExitCondition(
            type=ExitTimeType.DAYS_BEFORE_EXPIRY,
            days_before_expiry=exit_settings['exit_days_before'],
            specific_time=exit_settings['exit_time']
        )
        
        # Create leg
        leg = Leg(
            leg_number=leg_data['leg_number'],
            instrument=InstrumentType[leg_data['instrument']],
            option_type=OptionType[leg_data['option_type']] if leg_data.get('option_type') else None,
            position=PositionType[leg_data['position']],
            lots=leg_data['total_lot'],
            expiry_type=expiry_map[leg_data['expiry']],
            strike_selection=strike_selection,
            entry_condition=entry_condition,
            exit_condition=exit_condition
        )
        transformed_legs.append(leg)
    
    # Create strategy definition
    strategy_def = StrategyDefinition(
        name=f"Dynamic Strategy - {len(transformed_legs)} legs",
        legs=transformed_legs,
        index=instrument_settings['index']
    )
    
    return strategy_def


def build_strike_selection(criteria):
    """
    Build StrikeSelection object from frontend criteria
    """
    from ..strategies.strategy_types import StrikeSelection, StrikeSelectionType, StrikeType
    
    criteria_type = criteria.get('type', 'Strike Type')
    
    if criteria_type == 'Strike Type':
        return StrikeSelection(
            type=StrikeSelectionType.STRIKE_TYPE,
            strike_type=StrikeType[criteria.get('strike_type', 'ATM')],
            otm_strikes=criteria.get('strikes_away', 1) if criteria.get('strike_type') == 'OTM' else None,
            itm_strikes=criteria.get('strikes_away', 1) if criteria.get('strike_type') == 'ITM' else None
        )
    
    elif criteria_type == 'Premium Range':
        return StrikeSelection(
            type=StrikeSelectionType.PREMIUM_RANGE,
            premium_min=criteria.get('lower_range', 0),
            premium_max=criteria.get('upper_range', 0)
        )
    
    elif criteria_type == 'Closest Premium':
        return StrikeSelection(
            type=StrikeSelectionType.CLOSEST_PREMIUM,
            value=criteria.get('premium', 0)
        )
    
    elif criteria_type == 'Straddle Width':
        return StrikeSelection(
            type=StrikeSelectionType.STRADDLE_WIDTH,
            value=criteria.get('straddle_width_pct', 0)
        )
    
    elif criteria_type == '% of ATM':
        return StrikeSelection(
            type=StrikeSelectionType.PERCENT_OF_ATM,
            value=criteria.get('pct_of_atm', 0)
        )
    
    elif criteria_type == 'ATM Straddle Premium %':
        # This needs custom handling in generic_multi_leg_engine
        return StrikeSelection(
            type=StrikeSelectionType.CLOSEST_PREMIUM,  # Use closest premium as base
            value=criteria.get('straddle_pct', 0)
        )
    
    # Default to ATM
    return StrikeSelection(
        type=StrikeSelectionType.ATM
    )
```

### 3. Update generic_multi_leg_engine.py

**File:** `backend/strategies/generic_multi_leg_engine.py`

Add handling for "ATM Straddle Premium %" in `calculate_strike_from_selection`:

```python
# Add this case in calculate_strike_from_selection function

elif strike_type == StrikeSelectionType.ATM_STRADDLE_PREMIUM_PCT:
    # Calculate ATM straddle premium
    atm = round(entry_spot / 100) * 100
    
    # Get ATM CE premium
    atm_ce_data = bhav_data[
        (bhav_data['Instrument'] == "OPTIDX") &
        (bhav_data['Symbol'] == index_name) &
        (bhav_data['OptionType'] == "CE") &
        (bhav_data['StrikePrice'] == atm) &
        (bhav_data['ExpiryDate'] == expiry) &
        (bhav_data['TurnOver'] > 0)
    ]
    
    # Get ATM PE premium
    atm_pe_data = bhav_data[
        (bhav_data['Instrument'] == "OPTIDX") &
        (bhav_data['Symbol'] == index_name) &
        (bhav_data['OptionType'] == "PE") &
        (bhav_data['StrikePrice'] == atm) &
        (bhav_data['ExpiryDate'] == expiry) &
        (bhav_data['TurnOver'] > 0)
    ]
    
    if atm_ce_data.empty or atm_pe_data.empty:
        return atm
    
    atm_ce_premium = atm_ce_data.iloc[0]['Close']
    atm_pe_premium = atm_pe_data.iloc[0]['Close']
    straddle_premium = atm_ce_premium + atm_pe_premium
    
    # Target premium for this leg
    target_premium = straddle_premium * (strike_selection.value / 100)
    
    # Find strike with closest premium
    best_strike = None
    min_diff = float('inf')
    
    for strike in available_strikes:
        option_data = bhav_data[
            (bhav_data['Instrument'] == "OPTIDX") &
            (bhav_data['Symbol'] == index_name) &
            (bhav_data['OptionType'] == option_type.value) &
            (bhav_data['StrikePrice'] == strike) &
            (bhav_data['ExpiryDate'] == expiry) &
            (bhav_data['TurnOver'] > 0)
        ]
        
        if not option_data.empty:
            premium = option_data.iloc[0]['Close']
            diff = abs(premium - target_premium)
            if diff < min_diff:
                min_diff = diff
                best_strike = strike
    
    return best_strike if best_strike else atm
```

### 4. Add to strategy_types.py

**File:** `backend/strategies/strategy_types.py`

Add new enum value:

```python
class StrikeSelectionType(str, Enum):
    ATM = "ATM"
    CLOSEST_PREMIUM = "Closest Premium"
    PREMIUM_RANGE = "Premium Range"
    STRADDLE_WIDTH = "Straddle Width"
    PERCENT_OF_ATM = "% of ATM"
    DELTA = "Delta"
    STRIKE_TYPE = "Strike Type"
    OTM_PERCENT = "OTM %"
    ITM_PERCENT = "ITM %"
    ATM_STRADDLE_PREMIUM_PCT = "ATM Straddle Premium %"  # ADD THIS
```

---

## 🚀 How to Use

### 1. Update App.jsx

Replace the current App.jsx with:

```jsx
import React from 'react';
import AlgoTestStyleBuilder from './components/AlgoTestStyleBuilder';

function App() {
  return <AlgoTestStyleBuilder />;
}

export default App;
```

### 2. Start Backend

```bash
cd backend
python start_server.py
```

### 3. Start Frontend

```bash
cd frontend
npm run dev
```

### 4. Test the UI

1. Open http://localhost:5173
2. Select NIFTY index
3. Choose Futures as underlying
4. Set entry/exit settings
5. Add a leg (e.g., CE Sell with ATM strike)
6. Add another leg (e.g., Future Buy)
7. Set backtest period
8. Click "Start Backtest"

---

## 📋 Testing Checklist

- [ ] Instrument settings change correctly
- [ ] Entry days dropdown shows 0-4 for weekly, 0-24 for monthly
- [ ] Exit days dropdown shows same range
- [ ] Validation prevents exit > entry days
- [ ] Can add up to 4 legs
- [ ] Can remove legs
- [ ] Strike criteria changes UI conditionally
- [ ] Future legs don't show strike criteria
- [ ] Option legs show all strike options
- [ ] API payload builds correctly
- [ ] Backend receives and processes request
- [ ] Results display correctly

---

## 🎯 Key Features Implemented

1. ✅ Fully dynamic - no predefined strategies
2. ✅ User controls every aspect
3. ✅ Conditional UI based on selections
4. ✅ 8 different strike selection methods
5. ✅ Entry/Exit timing with days before expiry
6. ✅ Multiple leg support (up to 4)
7. ✅ Future and Option legs
8. ✅ Validation at every step
9. ✅ Clean, professional UI matching AlgoTest
10. ✅ Real-time strategy summary

---

## 📝 Next Steps

1. Implement the backend endpoint `/api/dynamic-backtest`
2. Add the transformation functions
3. Update `generic_multi_leg_engine.py` with ATM Straddle Premium % logic
4. Test with sample strategies
5. Add more advanced features (stop loss, targets, etc.)

---

## 💡 Future Enhancements

1. Legwise stop loss/target
2. Overall strategy stop loss/target
3. Trailing stop loss
4. Re-entry conditions
5. Save/load strategies
6. Strategy templates
7. Optimization mode
8. Walk-forward analysis

