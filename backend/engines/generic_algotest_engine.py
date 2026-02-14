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
    get_expiry_dates,
    get_spot_price_from_db,
    get_custom_expiry_dates,
    get_next_expiry_date,
    get_monthly_expiry_date
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
            - expiry_day_of_week: int (0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday) - Optional, defaults to standard expiry days
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
    expiry_day_of_week = params.get('expiry_day_of_week', None)  # Optional parameter for custom expiry day
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
    if expiry_day_of_week is not None:
        # Use custom expiry days
        expiry_dates = get_custom_expiry_dates(index, expiry_day_of_week, from_date, to_date)
        # Create a dataframe similar to the standard one
        expiry_df = pd.DataFrame({'Current Expiry': expiry_dates})
        print(f"  Loaded {len(expiry_df)} custom expiries (Day {expiry_day_of_week}: {(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])[expiry_day_of_week]})\n")
    else:
        # Use standard expiry dates
        if expiry_type == 'WEEKLY':
            expiry_df = get_expiry_dates(index, 'weekly', from_date, to_date)
        else:  # MONTHLY
            expiry_df = get_expiry_dates(index, 'monthly', from_date, to_date)
        print(f"  Loaded {len(expiry_df)} standard expiries\n")
    
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