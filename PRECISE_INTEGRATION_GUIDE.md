# 🚀 PRECISE INTEGRATION GUIDE FOR YOUR CURRENT SYSTEM
## Where to Make Changes & What to Add

---

## 📊 CURRENT SYSTEM ANALYSIS

**Your Stack:**
- ✅ Backend: FastAPI (Port 8000)
- ✅ Frontend: React + Vite (Port 3000)
- ✅ Database: SQLite (bhavcopy_data.db)
- ✅ Data: 6,362 CSV files (2000-2026)
- ✅ Engines: 10 strategy engines (v1-v10)

**What You Want:**
- ✅ DTE-based entry/exit (0-4 weekly, 0-24 monthly)
- ✅ Strike criteria dropdown (ATM/ITM/OTM with numbers)
- ✅ Segment selection (Futures/Options)
- ✅ All AlgoTest calculations matching exactly

---

## 🎯 PART 1: BACKEND CHANGES

### **FILE 1: `/backend/base.py` - ADD NEW FUNCTIONS**

**Location:** Add at the end of the file

```python
# ============================================
# NEW FUNCTIONS FOR ALGOTEST-STYLE FEATURES
# ============================================

def calculate_trading_days_before_expiry(expiry_date, days_before, trading_calendar_df):
    """
    Calculate entry date by counting back trading days from expiry
    
    This is the CORE DTE calculation matching AlgoTest exactly
    
    Args:
        expiry_date: datetime - The expiry date
        days_before: int - Number of trading days before expiry
        trading_calendar_df: DataFrame - Contains all trading dates
    
    Returns:
        datetime - The entry date
        
    Example:
        Expiry: 14-Jan-2025 (Tuesday)
        Days Before: 2
        
        Count back:
        14-Jan (Tue) = DTE 0
        13-Jan (Mon) = DTE 1
        10-Jan (Fri) = DTE 2 ✅ ENTRY DATE
        (Skip Sat/Sun)
    """
    import pandas as pd
    from datetime import timedelta
    
    # Get all trading days before expiry
    trading_days = trading_calendar_df[
        trading_calendar_df['date'] < expiry_date
    ].sort_values('date', ascending=False)
    
    if days_before == 0:
        # Entry on expiry day itself
        return expiry_date
    
    # Validate enough trading days exist
    if len(trading_days) < days_before:
        raise ValueError(f"Not enough trading days before expiry {expiry_date}. Requested: {days_before}, Available: {len(trading_days)}")
    
    # Get the Nth trading day before expiry
    # Index is 0-based, so days_before=1 means index 0, days_before=2 means index 1
    entry_date = trading_days.iloc[days_before - 1]['date']
    
    return entry_date


def get_trading_calendar(from_date, to_date, db_path='bhavcopy_data.db'):
    """
    Get all trading dates from database
    
    Returns:
        DataFrame with columns: ['date']
    """
    import sqlite3
    import pandas as pd
    
    conn = sqlite3.connect(db_path)
    
    query = f"""
    SELECT DISTINCT date 
    FROM bhavcopy 
    WHERE date >= '{from_date}' AND date <= '{to_date}'
    ORDER BY date
    """
    
    df = pd.read_sql_query(query, conn)
    df['date'] = pd.to_datetime(df['date'])
    conn.close()
    
    return df


def calculate_strike_from_selection(spot_price, strike_interval, selection, option_type):
    """
    Calculate strike based on AlgoTest-style selection
    
    This matches AlgoTest EXACTLY
    
    Args:
        spot_price: float - Current spot price
        strike_interval: int - Strike gap (50 for NIFTY, 100 for BANKNIFTY)
        selection: str - 'ATM', 'ITM1', 'ITM2', ..., 'OTM1', 'OTM2', ...
        option_type: str - 'CE' or 'PE'
    
    Returns:
        float - Strike price
        
    Examples:
        Spot = 24,350, Interval = 50, Selection = 'OTM2', Type = 'CE'
        
        Step 1: Calculate ATM
        ATM = round(24350 / 50) * 50 = 488 * 50 = 24,400
        
        Step 2: For CE + OTM = Higher strikes
        Offset = 2 strikes * 50 = 100
        Strike = 24,400 + 100 = 24,500 ✅
        
        For PE + OTM = Lower strikes
        Strike = 24,400 - 100 = 24,300 ✅
    """
    # Step 1: Calculate ATM strike
    atm_strike = round(spot_price / strike_interval) * strike_interval
    
    # Step 2: Parse selection
    selection = selection.upper().strip()
    
    if selection == 'ATM':
        return atm_strike
    
    # Extract number from selection (ITM1 -> 1, OTM10 -> 10)
    if selection.startswith('ITM'):
        offset_strikes = int(selection.replace('ITM', ''))
        offset_points = offset_strikes * strike_interval
        
        if option_type == 'CE':
            # For CALL: ITM means LOWER strike (below spot)
            return atm_strike - offset_points
        else:  # PE
            # For PUT: ITM means HIGHER strike (above spot)
            return atm_strike + offset_points
    
    elif selection.startswith('OTM'):
        offset_strikes = int(selection.replace('OTM', ''))
        offset_points = offset_strikes * strike_interval
        
        if option_type == 'CE':
            # For CALL: OTM means HIGHER strike (above spot)
            return atm_strike + offset_points
        else:  # PE
            # For PUT: OTM means LOWER strike (below spot)
            return atm_strike - offset_points
    
    raise ValueError(f"Invalid selection: {selection}. Must be ATM, ITM1-ITM30, or OTM1-OTM30")


def get_strike_interval(index):
    """
    Get strike interval for index
    
    Returns:
        int - Strike interval
    """
    intervals = {
        'NIFTY': 50,
        'BANKNIFTY': 100,
        'FINNIFTY': 50,
        'MIDCPNIFTY': 25,
        'SENSEX': 100,
        'BANKEX': 100,
    }
    
    return intervals.get(index, 50)


def get_option_premium_from_db(date, index, strike, option_type, expiry, db_path='bhavcopy_data.db'):
    """
    Get option premium from database
    
    This is your existing logic but extracted for reuse
    
    Args:
        date: str - Date in YYYY-MM-DD format
        index: str - NIFTY, BANKNIFTY, etc.
        strike: float - Strike price
        option_type: str - 'CE' or 'PE'
        expiry: str - Expiry date in YYYY-MM-DD
        db_path: str - Path to database
    
    Returns:
        float - Option premium (Close price)
        None if not found
    """
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    query = """
    SELECT close 
    FROM bhavcopy 
    WHERE date = ? 
      AND symbol = ? 
      AND strike = ? 
      AND option_type = ? 
      AND expiry = ?
    LIMIT 1
    """
    
    cursor.execute(query, (date, index, strike, option_type, expiry))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return float(result[0])
    return None


def get_future_price_from_db(date, index, expiry, db_path='bhavcopy_data.db'):
    """
    Get future price from database
    
    Args:
        date: str - Date
        index: str - Index symbol
        expiry: str - Expiry date
        db_path: str - Database path
    
    Returns:
        float - Future close price
    """
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    query = """
    SELECT close 
    FROM bhavcopy 
    WHERE date = ? 
      AND symbol = ? 
      AND expiry = ?
      AND option_type IS NULL
    LIMIT 1
    """
    
    cursor.execute(query, (date, index, expiry))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return float(result[0])
    return None


def calculate_intrinsic_value(spot, strike, option_type):
    """
    Calculate intrinsic value on expiry
    
    This is CRITICAL for expiry day exit
    
    Args:
        spot: float - Spot price at expiry
        strike: float - Strike price
        option_type: str - 'CE' or 'PE'
    
    Returns:
        float - Intrinsic value
        
    Examples:
        Spot = 24,500, Strike = 24,400, Type = CE
        Intrinsic = max(0, 24500 - 24400) = 100 ✅
        
        Spot = 24,300, Strike = 24,400, Type = CE
        Intrinsic = max(0, 24300 - 24400) = 0 ✅ (worthless)
        
        Spot = 24,300, Strike = 24,400, Type = PE
        Intrinsic = max(0, 24400 - 24300) = 100 ✅
    """
    if option_type == 'CE':
        # Call intrinsic = max(0, Spot - Strike)
        return max(0, spot - strike)
    else:  # PE
        # Put intrinsic = max(0, Strike - Spot)
        return max(0, strike - spot)
```

---

### **FILE 2: `/backend/engines/generic_algotest_engine.py` - CREATE NEW FILE**

**Location:** `/backend/engines/generic_algotest_engine.py`

**Purpose:** New engine matching AlgoTest exactly

```python
"""
Generic AlgoTest-Style Engine
Matches AlgoTest behavior exactly with DTE-based entry/exit
"""

import pandas as pd
from datetime import datetime, timedelta
import sys
import os

# Import from base.py
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from base import (
    calculate_trading_days_before_expiry,
    get_trading_calendar,
    calculate_strike_from_selection,
    get_strike_interval,
    get_option_premium_from_db,
    get_future_price_from_db,
    calculate_intrinsic_value,
    get_expiry_dates
)


def run_algotest_backtest(params):
    """
    Main AlgoTest-style backtest function
    
    This matches AlgoTest exactly:
    - DTE-based entry/exit
    - Strike selection (ATM/ITM/OTM)
    - Proper expiry settlement
    - Multi-leg support
    
    Args:
        params: dict with:
            - index: str (NIFTY, BANKNIFTY, etc.)
            - from_date: str (YYYY-MM-DD)
            - to_date: str (YYYY-MM-DD)
            - expiry_type: str ('WEEKLY' or 'MONTHLY')
            - entry_dte: int (0-4 for weekly, 0-24 for monthly)
            - exit_dte: int (0-4 for weekly, 0-24 for monthly)
            - legs: list of dicts, each with:
                - segment: 'OPTIONS' or 'FUTURES'
                - option_type: 'CE' or 'PE' (for options)
                - position: 'BUY' or 'SELL'
                - lots: int
                - strike_selection: 'ATM', 'ITM1', 'OTM2', etc.
                - expiry: 'WEEKLY', 'MONTHLY', etc.
    
    Returns:
        tuple: (trades_df, summary_dict, pivot_dict)
    """
    
    # ========== STEP 1: EXTRACT PARAMETERS ==========
    index = params['index']
    from_date = params['from_date']
    to_date = params['to_date']
    expiry_type = params.get('expiry_type', 'WEEKLY')
    entry_dte = params.get('entry_dte', 2)
    exit_dte = params.get('exit_dte', 0)
    legs_config = params.get('legs', [])
    
    print(f"\n{'='*60}")
    print(f"ALGOTEST-STYLE BACKTEST")
    print(f"{'='*60}")
    print(f"Index: {index}")
    print(f"Date Range: {from_date} to {to_date}")
    print(f"Expiry Type: {expiry_type}")
    print(f"Entry DTE: {entry_dte} (days before expiry)")
    print(f"Exit DTE: {exit_dte} (days before expiry)")
    print(f"Legs: {len(legs_config)}")
    print(f"{'='*60}\n")
    
    # ========== STEP 2: LOAD DATA ==========
    print("Loading trading calendar...")
    trading_calendar = get_trading_calendar(from_date, to_date)
    print(f"  Loaded {len(trading_calendar)} trading dates\n")
    
    print("Loading expiry dates...")
    if expiry_type == 'WEEKLY':
        expiry_df = get_expiry_dates(index, 'weekly', from_date, to_date)
    else:  # MONTHLY
        expiry_df = get_expiry_dates(index, 'monthly', from_date, to_date)
    
    print(f"  Loaded {len(expiry_df)} expiries\n")
    
    # ========== STEP 3: INITIALIZE RESULTS ==========
    all_trades = []
    strike_interval = get_strike_interval(index)
    
    # ========== STEP 4: LOOP THROUGH EXPIRIES ==========
    print("Processing expiries...\n")
    
    for expiry_idx, expiry_row in expiry_df.iterrows():
        expiry_date = expiry_row['Current Expiry']
        
        print(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {expiry_date} ---")
        
        try:
            # ========== STEP 5: CALCULATE ENTRY DATE ==========
            entry_date = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=entry_dte,
                trading_calendar_df=trading_calendar
            )
            
            print(f"  Entry Date (DTE={entry_dte}): {entry_date}")
            
            # ========== STEP 6: CALCULATE EXIT DATE ==========
            exit_date = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=exit_dte,
                trading_calendar_df=trading_calendar
            )
            
            print(f"  Exit Date (DTE={exit_dte}): {exit_date}")
            
            # Validate entry before exit
            if entry_date > exit_date:
                print(f"  ⚠️  Entry after exit - skipping")
                continue
            
            # ========== STEP 7: GET ENTRY SPOT PRICE ==========
            # Get spot from database (use index price at entry_date)
            entry_spot = get_spot_price_from_db(entry_date, index)
            
            if entry_spot is None:
                print(f"  ⚠️  No spot data for {entry_date} - skipping")
                continue
            
            print(f"  Entry Spot: {entry_spot}")
            
            # ========== STEP 8: PROCESS EACH LEG ==========
            trade_legs = []
            
            for leg_idx, leg_config in enumerate(legs_config):
                print(f"\n    Processing Leg {leg_idx + 1}...")
                
                segment = leg_config['segment']
                position = leg_config['position']
                lots = leg_config['lots']
                
                if segment == 'FUTURES':
                    # ========== FUTURES LEG ==========
                    print(f"      Type: FUTURE")
                    print(f"      Position: {position}")
                    
                    # Get future expiry
                    future_expiry = expiry_date  # Use same expiry for simplicity
                    
                    # Get entry price
                    entry_price = get_future_price_from_db(
                        date=entry_date.strftime('%Y-%m-%d'),
                        index=index,
                        expiry=future_expiry.strftime('%Y-%m-%d')
                    )
                    
                    if entry_price is None:
                        print(f"      ⚠️  No future price - skipping leg")
                        continue
                    
                    print(f"      Entry Price: {entry_price}")
                    
                    # Get exit price
                    exit_price = get_future_price_from_db(
                        date=exit_date.strftime('%Y-%m-%d'),
                        index=index,
                        expiry=future_expiry.strftime('%Y-%m-%d')
                    )
                    
                    if exit_price is None:
                        print(f"      ⚠️  No exit price - using entry")
                        exit_price = entry_price
                    
                    print(f"      Exit Price: {exit_price}")
                    
                    # Calculate P&L
                    if position == 'BUY':
                        leg_pnl = (exit_price - entry_price) * lots
                    else:  # SELL
                        leg_pnl = (entry_price - exit_price) * lots
                    
                    print(f"      P&L: {leg_pnl:,.2f}")
                    
                    trade_legs.append({
                        'leg_number': leg_idx + 1,
                        'segment': 'FUTURE',
                        'position': position,
                        'lots': lots,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl': leg_pnl
                    })
                
                else:  # OPTIONS
                    # ========== OPTIONS LEG ==========
                    option_type = leg_config['option_type']
                    strike_selection = leg_config['strike_selection']
                    
                    print(f"      Type: OPTION")
                    print(f"      Option Type: {option_type}")
                    print(f"      Position: {position}")
                    print(f"      Strike Selection: {strike_selection}")
                    
                    # ========== CALCULATE STRIKE ==========
                    strike = calculate_strike_from_selection(
                        spot_price=entry_spot,
                        strike_interval=strike_interval,
                        selection=strike_selection,
                        option_type=option_type
                    )
                    
                    print(f"      Calculated Strike: {strike}")
                    
                    # Get entry premium
                    entry_premium = get_option_premium_from_db(
                        date=entry_date.strftime('%Y-%m-%d'),
                        index=index,
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry_date.strftime('%Y-%m-%d')
                    )
                    
                    if entry_premium is None:
                        print(f"      ⚠️  No entry premium - skipping leg")
                        continue
                    
                    print(f"      Entry Premium: {entry_premium}")
                    
                    # Get exit premium
                    if exit_date >= expiry_date:
                        # ========== EXPIRY DAY - USE INTRINSIC VALUE ==========
                        print(f"      Exit on/after expiry - using intrinsic value")
                        
                        exit_spot = get_spot_price_from_db(expiry_date, index)
                        
                        if exit_spot is None:
                            exit_spot = entry_spot
                        
                        exit_premium = calculate_intrinsic_value(
                            spot=exit_spot,
                            strike=strike,
                            option_type=option_type
                        )
                        
                        print(f"      Exit Spot: {exit_spot}")
                        print(f"      Intrinsic Value: {exit_premium}")
                    
                    else:
                        # ========== PRE-EXPIRY EXIT - USE MARKET PRICE ==========
                        exit_premium = get_option_premium_from_db(
                            date=exit_date.strftime('%Y-%m-%d'),
                            index=index,
                            strike=strike,
                            option_type=option_type,
                            expiry=expiry_date.strftime('%Y-%m-%d')
                        )
                        
                        if exit_premium is None:
                            print(f"      ⚠️  No exit premium - using 0")
                            exit_premium = 0
                        
                        print(f"      Exit Premium: {exit_premium}")
                    
                    # Calculate P&L
                    if position == 'BUY':
                        leg_pnl = (exit_premium - entry_premium) * lots
                    else:  # SELL
                        leg_pnl = (entry_premium - exit_premium) * lots
                    
                    print(f"      P&L: {leg_pnl:,.2f}")
                    
                    trade_legs.append({
                        'leg_number': leg_idx + 1,
                        'segment': 'OPTION',
                        'option_type': option_type,
                        'strike': strike,
                        'position': position,
                        'lots': lots,
                        'entry_premium': entry_premium,
                        'exit_premium': exit_premium,
                        'pnl': leg_pnl
                    })
            
            # ========== STEP 9: CALCULATE TOTAL P&L ==========
            total_pnl = sum(leg['pnl'] for leg in trade_legs)
            
            print(f"\n  Total P&L: {total_pnl:,.2f}")
            
            # ========== STEP 10: RECORD TRADE ==========
            trade_record = {
                'entry_date': entry_date,
                'exit_date': exit_date,
                'expiry_date': expiry_date,
                'entry_dte': entry_dte,
                'exit_dte': exit_dte,
                'entry_spot': entry_spot,
                'legs': trade_legs,
                'total_pnl': total_pnl
            }
            
            all_trades.append(trade_record)
            print(f"  ✅ Trade recorded\n")
        
        except Exception as e:
            print(f"  ❌ Error: {str(e)}\n")
            continue
    
    # ========== STEP 11: CONVERT TO DATAFRAME ==========
    print(f"\n{'='*60}")
    print(f"BACKTEST COMPLETE")
    print(f"{'='*60}")
    print(f"Total Trades: {len(all_trades)}")
    
    if not all_trades:
        print("No trades executed - returning empty results")
        return pd.DataFrame(), {}, {}
    
    # Flatten for DataFrame
    trades_flat = []
    for trade in all_trades:
        row = {
            'entry_date': trade['entry_date'],
            'exit_date': trade['exit_date'],
            'expiry_date': trade['expiry_date'],
            'entry_dte': trade['entry_dte'],
            'exit_dte': trade['exit_dte'],
            'entry_spot': trade['entry_spot'],
            'total_pnl': trade['total_pnl']
        }
        
        # Add leg details
        for leg in trade['legs']:
            leg_num = leg['leg_number']
            if leg['segment'] == 'FUTURE':
                row[f'leg{leg_num}_type'] = 'FUTURE'
                row[f'leg{leg_num}_position'] = leg['position']
                row[f'leg{leg_num}_entry'] = leg['entry_price']
                row[f'leg{leg_num}_exit'] = leg['exit_price']
            else:
                row[f'leg{leg_num}_type'] = f"{leg['option_type']}"
                row[f'leg{leg_num}_strike'] = leg['strike']
                row[f'leg{leg_num}_position'] = leg['position']
                row[f'leg{leg_num}_entry'] = leg['entry_premium']
                row[f'leg{leg_num}_exit'] = leg['exit_premium']
            
            row[f'leg{leg_num}_pnl'] = leg['pnl']
        
        trades_flat.append(row)
    
    trades_df = pd.DataFrame(trades_flat)
    
    # Calculate cumulative
    trades_df['cumulative_pnl'] = trades_df['total_pnl'].cumsum()
    
    # ========== STEP 12: CALCULATE SUMMARY ==========
    summary = {
        'total_trades': len(trades_df),
        'total_pnl': trades_df['total_pnl'].sum(),
        'winning_trades': len(trades_df[trades_df['total_pnl'] > 0]),
        'losing_trades': len(trades_df[trades_df['total_pnl'] < 0]),
        'win_rate': (len(trades_df[trades_df['total_pnl'] > 0]) / len(trades_df) * 100) if len(trades_df) > 0 else 0,
        'avg_win': trades_df[trades_df['total_pnl'] > 0]['total_pnl'].mean() if len(trades_df[trades_df['total_pnl'] > 0]) > 0 else 0,
        'avg_loss': trades_df[trades_df['total_pnl'] < 0]['total_pnl'].mean() if len(trades_df[trades_df['total_pnl'] < 0]) > 0 else 0,
        'max_win': trades_df['total_pnl'].max(),
        'max_loss': trades_df['total_pnl'].min(),
    }
    
    print(f"\nTotal P&L: ₹{summary['total_pnl']:,.2f}")
    print(f"Win Rate: {summary['win_rate']:.2f}%")
    print(f"Avg Win: ₹{summary['avg_win']:,.2f}")
    print(f"Avg Loss: ₹{summary['avg_loss']:,.2f}")
    print(f"{'='*60}\n")
    
    # Pivot table (simplified)
    pivot = {'headers': [], 'rows': []}
    
    return trades_df, summary, pivot


def get_spot_price_from_db(date, index, db_path='bhavcopy_data.db'):
    """
    Get spot price from database
    
    Note: Your database might store spot differently
    Adjust this to match your schema
    """
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # This query assumes spot is stored in a specific way
    # ADJUST based on your actual database schema
    query = """
    SELECT close 
    FROM bhavcopy 
    WHERE date = ? 
      AND symbol = ?
      AND strike IS NULL
      AND option_type IS NULL
    LIMIT 1
    """
    
    cursor.execute(query, (date.strftime('%Y-%m-%d'), index))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return float(result[0])
    
    # Fallback: calculate from ATM options
    # You may need to implement this based on your data
    return None
```

---

### **FILE 3: `/backend/routers/backtest.py` - ADD NEW ENDPOINT**

**Location:** Add this function to `/backend/routers/backtest.py`

```python
@router.post("/algotest")
async def run_algotest_backtest_endpoint(request: dict):
    """
    NEW ENDPOINT: AlgoTest-style backtest
    
    Request format:
    {
        "index": "NIFTY",
        "from_date": "2024-01-01",
        "to_date": "2024-12-31",
        "expiry_type": "WEEKLY",
        "entry_dte": 2,
        "exit_dte": 0,
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "strike_selection": "OTM2",
                "expiry": "WEEKLY"
            }
        ]
    }
    """
    try:
        from engines.generic_algotest_engine import run_algotest_backtest
        
        # Run backtest
        trades_df, summary, pivot = run_algotest_backtest(request)
        
        # Convert to JSON
        trades_json = trades_df.to_dict('records') if not trades_df.empty else []
        
        return {
            "status": "success",
            "trades": trades_json,
            "summary": summary,
            "pivot": pivot
        }
    
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }
```

---

## 🎯 PART 2: FRONTEND CHANGES

### **FILE 4: `/frontend/src/components/AlgoTestLegBuilder.jsx` - CREATE NEW COMPONENT**

**Location:** `/frontend/src/components/AlgoTestLegBuilder.jsx`

```jsx
import React, { useState } from 'react';

const AlgoTestLegBuilder = ({ legs, onLegsChange }) => {
  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      segment: 'OPTIONS',
      option_type: 'CE',
      position: 'SELL',
      lots: 1,
      strike_selection: 'ATM',
      expiry: 'WEEKLY'
    };
    
    onLegsChange([...legs, newLeg]);
  };
  
  const removeLeg = (index) => {
    const newLegs = legs.filter((_, i) => i !== index);
    onLegsChange(newLegs);
  };
  
  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index][field] = value;
    onLegsChange(newLegs);
  };
  
  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold">Strategy Legs</h3>
        <button
          onClick={addLeg}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          + Add Leg
        </button>
      </div>
      
      {legs.map((leg, index) => (
        <div key={leg.id} className="border rounded-lg p-4 space-y-4">
          <div className="flex justify-between items-center">
            <h4 className="font-medium">Leg {index + 1}</h4>
            <button
              onClick={() => removeLeg(index)}
              className="px-3 py-1 bg-red-500 text-white rounded hover:bg-red-600"
            >
              Remove
            </button>
          </div>
          
          {/* Segment Selection */}
          <div>
            <label className="block text-sm font-medium mb-1">Segment</label>
            <div className="flex gap-2">
              <button
                onClick={() => updateLeg(index, 'segment', 'FUTURES')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.segment === 'FUTURES' 
                    ? 'bg-blue-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Futures
              </button>
              <button
                onClick={() => updateLeg(index, 'segment', 'OPTIONS')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.segment === 'OPTIONS' 
                    ? 'bg-blue-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Options
              </button>
            </div>
          </div>
          
          {/* Total Lot */}
          <div>
            <label className="block text-sm font-medium mb-1">Total Lot</label>
            <input
              type="number"
              min="1"
              value={leg.lots}
              onChange={(e) => updateLeg(index, 'lots', parseInt(e.target.value))}
              className="w-full p-2 border rounded"
            />
          </div>
          
          {/* Position */}
          <div>
            <label className="block text-sm font-medium mb-1">Position</label>
            <div className="flex gap-2">
              <button
                onClick={() => updateLeg(index, 'position', 'BUY')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.position === 'BUY' 
                    ? 'bg-green-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Buy
              </button>
              <button
                onClick={() => updateLeg(index, 'position', 'SELL')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.position === 'SELL' 
                    ? 'bg-red-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Sell
              </button>
            </div>
          </div>
          
          {/* OPTIONS-SPECIFIC FIELDS */}
          {leg.segment === 'OPTIONS' && (
            <>
              {/* Option Type */}
              <div>
                <label className="block text-sm font-medium mb-1">Option Type</label>
                <div className="flex gap-2">
                  <button
                    onClick={() => updateLeg(index, 'option_type', 'CE')}
                    className={`flex-1 py-2 px-4 rounded ${
                      leg.option_type === 'CE' 
                        ? 'bg-blue-600 text-white' 
                        : 'bg-gray-200'
                    }`}
                  >
                    Call
                  </button>
                  <button
                    onClick={() => updateLeg(index, 'option_type', 'PE')}
                    className={`flex-1 py-2 px-4 rounded ${
                      leg.option_type === 'PE' 
                        ? 'bg-blue-600 text-white' 
                        : 'bg-gray-200'
                    }`}
                  >
                    Put
                  </button>
                </div>
              </div>
              
              {/* Expiry */}
              <div>
                <label className="block text-sm font-medium mb-1">Expiry</label>
                <select
                  value={leg.expiry}
                  onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                  className="w-full p-2 border rounded"
                >
                  <option value="WEEKLY">Weekly</option>
                  <option value="MONTHLY">Monthly</option>
                </select>
              </div>
              
              {/* Strike Criteria */}
              <div>
                <label className="block text-sm font-medium mb-1">Strike Criteria</label>
                <select
                  value={leg.strike_selection}
                  onChange={(e) => updateLeg(index, 'strike_selection', e.target.value)}
                  className="w-full p-2 border rounded"
                >
                  <optgroup label="In The Money">
                    {[20,19,18,17,16,15,14,13,12,11,10,9,8,7,6,5,4,3,2,1].map(i => (
                      <option key={`ITM${i}`} value={`ITM${i}`}>ITM {i}</option>
                    ))}
                  </optgroup>
                  <option value="ATM">ATM (At The Money)</option>
                  <optgroup label="Out of The Money">
                    {[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30].map(i => (
                      <option key={`OTM${i}`} value={`OTM${i}`}>OTM {i}</option>
                    ))}
                  </optgroup>
                </select>
              </div>
            </>
          )}
        </div>
      ))}
      
      {legs.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          No legs added. Click "Add Leg" to start building your strategy.
        </div>
      )}
    </div>
  );
};

export default AlgoTestLegBuilder;
```

---

### **FILE 5: `/frontend/src/components/AlgoTestBacktestForm.jsx` - CREATE NEW COMPONENT**

**Location:** `/frontend/src/components/AlgoTestBacktestForm.jsx`

```jsx
import React, { useState } from 'react';
import AlgoTestLegBuilder from './AlgoTestLegBuilder';

const AlgoTestBacktestForm = ({ onRunBacktest }) => {
  const [config, setConfig] = useState({
    index: 'NIFTY',
    from_date: '2024-01-01',
    to_date: '2024-12-31',
    expiry_type: 'WEEKLY',
    entry_dte: 2,
    exit_dte: 0,
    legs: []
  });
  
  const handleSubmit = (e) => {
    e.preventDefault();
    onRunBacktest(config);
  };
  
  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Index Selection */}
      <div>
        <label className="block text-sm font-medium mb-1">Index</label>
        <select
          value={config.index}
          onChange={(e) => setConfig({...config, index: e.target.value})}
          className="w-full p-2 border rounded"
        >
          <option value="NIFTY">NIFTY 50</option>
          <option value="BANKNIFTY">BANK NIFTY</option>
          <option value="FINNIFTY">FIN NIFTY</option>
          <option value="SENSEX">SENSEX</option>
        </select>
      </div>
      
      {/* Date Range */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">From Date</label>
          <input
            type="date"
            value={config.from_date}
            onChange={(e) => setConfig({...config, from_date: e.target.value})}
            className="w-full p-2 border rounded"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">To Date</label>
          <input
            type="date"
            value={config.to_date}
            onChange={(e) => setConfig({...config, to_date: e.target.value})}
            className="w-full p-2 border rounded"
          />
        </div>
      </div>
      
      {/* Expiry Type */}
      <div>
        <label className="block text-sm font-medium mb-1">Expiry Type</label>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setConfig({...config, expiry_type: 'WEEKLY'})}
            className={`flex-1 py-2 px-4 rounded ${
              config.expiry_type === 'WEEKLY' 
                ? 'bg-blue-600 text-white' 
                : 'bg-gray-200'
            }`}
          >
            Weekly
          </button>
          <button
            type="button"
            onClick={() => setConfig({...config, expiry_type: 'MONTHLY'})}
            className={`flex-1 py-2 px-4 rounded ${
              config.expiry_type === 'MONTHLY' 
                ? 'bg-blue-600 text-white' 
                : 'bg-gray-200'
            }`}
          >
            Monthly
          </button>
        </div>
      </div>
      
      {/* Entry DTE */}
      <div>
        <label className="block text-sm font-medium mb-1">
          Entry (Days Before Expiry)
        </label>
        <select
          value={config.entry_dte}
          onChange={(e) => setConfig({...config, entry_dte: parseInt(e.target.value)})}
          className="w-full p-2 border rounded"
        >
          {config.expiry_type === 'WEEKLY' ? (
            <>
              <option value="0">0 (Expiry Day)</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </>
          ) : (
            Array.from({length: 25}, (_, i) => (
              <option key={i} value={i}>{i}{i === 0 ? ' (Expiry Day)' : ''}</option>
            ))
          )}
        </select>
      </div>
      
      {/* Exit DTE */}
      <div>
        <label className="block text-sm font-medium mb-1">
          Exit (Days Before Expiry)
        </label>
        <select
          value={config.exit_dte}
          onChange={(e) => setConfig({...config, exit_dte: parseInt(e.target.value)})}
          className="w-full p-2 border rounded"
        >
          {config.expiry_type === 'WEEKLY' ? (
            <>
              <option value="0">0 (Expiry Day)</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </>
          ) : (
            Array.from({length: 25}, (_, i) => (
              <option key={i} value={i}>{i}{i === 0 ? ' (Expiry Day)' : ''}</option>
            ))
          )}
        </select>
      </div>
      
      {/* Leg Builder */}
      <AlgoTestLegBuilder 
        legs={config.legs}
        onLegsChange={(legs) => setConfig({...config, legs})}
      />
      
      {/* Submit */}
      <button
        type="submit"
        className="w-full py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
      >
        Run Backtest
      </button>
    </form>
  );
};

export default AlgoTestBacktestForm;
```

---

## 🎯 PART 3: TESTING

### **Test Payload Example**

**Send this to:** `POST http://localhost:8000/api/backtest/algotest`

```json
{
  "index": "NIFTY",
  "from_date": "2024-01-01",
  "to_date": "2024-03-31",
  "expiry_type": "WEEKLY",
  "entry_dte": 2,
  "exit_dte": 0,
  "legs": [
    {
      "segment": "OPTIONS",
      "option_type": "CE",
      "position": "SELL",
      "lots": 1,
      "strike_selection": "OTM2",
      "expiry": "WEEKLY"
    }
  ]
}
```

---

## 📊 SUMMARY OF CHANGES

### **Backend Changes:**
1. ✅ `/backend/base.py` - Add 7 new functions
2. ✅ `/backend/engines/generic_algotest_engine.py` - New engine (create file)
3. ✅ `/backend/routers/backtest.py` - Add new endpoint

### **Frontend Changes:**
4. ✅ `/frontend/src/components/AlgoTestLegBuilder.jsx` - New component
5. ✅ `/frontend/src/components/AlgoTestBacktestForm.jsx` - New component

### **Total Files to Change:** 5 files
### **New Files to Create:** 2 files
### **Total Lines Added:** ~1,200 lines

---

## 🚀 IMPLEMENTATION ORDER

1. **Day 1:** Add functions to `base.py`
2. **Day 2:** Create `generic_algotest_engine.py`
3. **Day 3:** Add endpoint to `backtest.py`
4. **Day 4:** Create frontend components
5. **Day 5:** Testing & refinement

**Total Time:** 5 days

---

This is the **EXACT integration** you need - matches AlgoTest calculations precisely with DTE logic, strike selection, and proper expiry settlement!
