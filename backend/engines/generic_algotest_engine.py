"""
Generic AlgoTest-Style Engine
Matches AlgoTest behavior exactly with DTE-based entry/exit
"""

# Set DEBUG = False to disable verbose logging for faster execution
DEBUG = False

def _log(*args, **kwargs):
    """Helper to print only when DEBUG is True"""
    if DEBUG:
        print(*args, **kwargs)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os


def get_lot_size(index, entry_date):
    """
    Returns correct lot size based on index and trade date.
    NSE official lot size history:
    
    NIFTY:
      Jun 2000 – Sep 2010 : 200
      Oct 2010 – Oct 2015 : 50
      Oct 2015 – Oct 2019 : 75
      Nov 2019 – present  : 65  # Updated to match AlgoTest
    
    BANKNIFTY:
      Jun 2000 – Sep 2010 : 50
      Oct 2010 – Oct 2015 : 25
      Oct 2015 – Oct 2019 : 20
      Nov 2019 – present  : 15
    """
    d = pd.Timestamp(entry_date)
    if index.upper() == 'NIFTY':
        if d < pd.Timestamp("2010-10-01"):
            return 200
        elif d < pd.Timestamp("2015-10-29"):
            return 50
        elif d < pd.Timestamp("2019-11-01"):
            return 75
        else:
            return 65  # Changed from 50 to 65 to match AlgoTest
    elif index.upper() == 'BANKNIFTY':
        if d < pd.Timestamp("2010-10-01"):
            return 50
        elif d < pd.Timestamp("2015-10-29"):
            return 25
        elif d < pd.Timestamp("2019-11-01"):
            return 20
        else:
            return 15
    return 1  # fallback for other indexes


# Import from base.py
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from base import (
    calculate_trading_days_before_expiry,
    get_trading_calendar,
    calculate_strike_from_selection,
    calculate_strike_advanced,
    get_expiry_for_selection,
    get_strike_interval,
    get_option_premium_from_db,
    get_future_price_from_db,
    calculate_intrinsic_value,
    get_expiry_dates,
    get_spot_price_from_db,
    get_custom_expiry_dates,
    get_next_expiry_date,
    get_monthly_expiry_date,
    get_strike_data,
    # load_base2,  # Commented out - not using base2 filter
    load_bhavcopy,
    compute_analytics,
    build_pivot
)


def check_stop_loss_target(entry_date, exit_date, expiry_date, entry_spot, legs_config, 
                          index, stop_loss_pct, target_pct, trading_calendar):
    """
    Check if stop loss or target is hit during the holding period.
    Returns (exit_date, exit_reason) if hit, else (None, None)
    """
    if stop_loss_pct is None and target_pct is None:
        return None, None
    
    # Get all trading days between entry and exit (exclusive of entry)
    holding_days = trading_calendar[
        (trading_calendar['date'] > entry_date) & 
        (trading_calendar['date'] <= exit_date)
    ]['date'].tolist()
    
    for check_date in holding_days:
        # Calculate P&L for each leg at this date
        total_pnl_pct = 0
        has_data = False
        
        for leg in legs_config:
            segment = leg.get('segment', 'OPTION')
            if segment == 'FUTURES':
                continue
            
            position = leg['position']
            lots = leg['lots']
            option_type = leg['option_type']
            strike = leg.get('strike')
            
            if strike is None:
                continue
            
            # Get premium at this date
            current_premium = get_option_premium_from_db(
                date=check_date.strftime('%Y-%m-%d'),
                index=index,
                strike=strike,
                option_type=option_type,
                expiry=expiry_date.strftime('%Y-%m-%d')
            )
            
            if current_premium is None:
                continue
            
            has_data = True
            entry_premium = leg.get('entry_premium')
            if entry_premium is None:
                continue
            
            # Calculate P&L percentage for this leg
            if position == 'BUY':
                leg_pnl_pct = ((current_premium - entry_premium) / entry_premium) * 100 if entry_premium > 0 else 0
            else:  # SELL
                leg_pnl_pct = ((entry_premium - current_premium) / entry_premium) * 100 if entry_premium > 0 else 0
            
            total_pnl_pct += leg_pnl_pct
        
        if not has_data:
            continue
        
        # Check stop loss (negative P&L)
        if stop_loss_pct is not None and total_pnl_pct <= -stop_loss_pct:
            print(f"      🛑 STOP LOSS HIT at {check_date.strftime('%Y-%m-%d')} (P&L: {total_pnl_pct:.2f}%)")
            return check_date, 'STOP_LOSS'
        
        # Check target (positive P&L)
        if target_pct is not None and total_pnl_pct >= target_pct:
            print(f"      TARGET HIT at {check_date.strftime('%Y-%m-%d')} (P&L: {total_pnl_pct:.2f}%)")
            return check_date, 'TARGET'
    
    return None, None


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
    expiry_day_of_week = params.get('expiry_day_of_week', None)
    entry_dte = params.get('entry_dte', 2)
    exit_dte = params.get('exit_dte', 0)
    legs_config = params.get('legs', [])
    
    # Stop loss and target parameters
    stop_loss_pct = params.get('stop_loss_pct', None)  # e.g., 50 = exit if loss exceeds 50%
    target_pct = params.get('target_pct', None)  # e.g., 100 = exit if profit reaches 100%
    
    print(f"\n{'='*60}")
    print(f"ALGOTEST-STYLE BACKTEST")
    print(f"{'='*60}")
    print(f"Index: {index}")
    print(f"Date Range: {from_date} to {to_date}")
    print(f"Expiry Type: {expiry_type}")
    print(f"Entry DTE: {entry_dte} (days before expiry)")
    print(f"Exit DTE: {exit_dte} (days before expiry)")
    print(f"Stop Loss: {stop_loss_pct}%")
    print(f"Target: {target_pct}%")
    print(f"Legs: {len(legs_config)}")
    print(f"{'='*60}\n")
    
    # ========== STEP 2: LOAD DATA FROM CSV (like generic_multi_leg) ==========
    print("Loading spot data from CSV...")
    spot_df = get_strike_data(index, from_date, to_date)
    print(f"  Loaded {len(spot_df)} spot records\n")
    
    # Create trading calendar from spot data
    trading_calendar = spot_df[['Date']].drop_duplicates().sort_values('Date').reset_index(drop=True)
    trading_calendar.columns = ['date']
    print(f"  Trading calendar: {len(trading_calendar)} trading days\n")
    
    # NOTE: base2 filter removed - using all trading days
    print("  Using all trading days (base2 filter disabled)\n")
    
    print("Loading expiry dates...")
    if expiry_day_of_week is not None:
        # Use custom expiry days
        expiry_dates = get_custom_expiry_dates(index, expiry_day_of_week, from_date, to_date)
        # Create a dataframe similar to the standard one
        expiry_df = pd.DataFrame({'Current Expiry': expiry_dates})
        print(f"  Loaded {len(expiry_df)} custom expiries (Day {expiry_day_of_week})\n")
    else:
        # Use standard expiry dates
        if expiry_type == 'WEEKLY':
            expiry_df = get_expiry_dates(index, 'weekly', from_date, to_date)
        else:  # MONTHLY
            expiry_df = get_expiry_dates(index, 'monthly', from_date, to_date)
        print(f"  Loaded {len(expiry_df)} standard expiries\n")
    
    # ========== STEP 3: SPOT ADJUSTMENT FILTERING ==========
    spot_adjustment_type = params.get('spot_adjustment_type', 0)
    spot_adjustment = params.get('spot_adjustment', 1.0)
    
    if spot_adjustment_type != 0:
        print(f"Applying spot adjustment filter...")
        print(f"  Type: {['None', 'Rises', 'Falls', 'RisesOrFalls'][spot_adjustment_type]}")
        print(f"  Threshold: {spot_adjustment}%\n")
        
        # Build intervals based on spot movement
        intervals = build_intervals(spot_df, spot_adjustment_type, spot_adjustment)
        print(f"  Generated {len(intervals)} trading intervals\n")
        
        # Filter expiry dates to only those within valid intervals
        valid_expiries = []
        for expiry_row in expiry_df.itertuples():
            expiry_date = expiry_row[1]  # Current Expiry column
            
            # Check if this expiry falls within any valid interval
            for interval_start, interval_end in intervals:
                if interval_start <= expiry_date <= interval_end:
                    valid_expiries.append(expiry_row)
                    break
        
        # Update expiry_df to only include valid expiries
        if valid_expiries:
            expiry_df = pd.DataFrame(valid_expiries, columns=expiry_df.columns)
            print(f"  Filtered to {len(expiry_df)} expiries (from {len(expiry_df) + len(expiry_df.index) - len(valid_expiries)} total)\n")
        else:
            print(f"  WARNING: No expiries match spot adjustment criteria - no trades will be executed\n")
            return pd.DataFrame(), {}, {}
    
    # ========== STEP 4: INITIALIZE RESULTS ==========
    all_trades = []
    strike_interval = get_strike_interval(index)
    
    # ========== STEP 4: LOOP THROUGH EXPIRIES ==========
    if DEBUG:
        print("Processing expiries...\n")
    
    for expiry_idx, expiry_row in expiry_df.iterrows():
        expiry_date = expiry_row['Current Expiry']
        
        _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {expiry_date} ---")
        
        try:
            # ========== STEP 5: CALCULATE ENTRY DATE ==========
            entry_date = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=entry_dte,
                trading_calendar_df=trading_calendar
            )
            
            _log(f"  Entry Date (DTE={entry_dte}): {entry_date}")
            
            # ========== STEP 6: CALCULATE EXIT DATE ==========
            exit_date = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=exit_dte,
                trading_calendar_df=trading_calendar
            )
            
            _log(f"  Exit Date (DTE={exit_dte}): {exit_date}")
            
            # Validate entry before exit
            if entry_date > exit_date:
                _log(f"  WARNING: Entry after exit - skipping")
                continue
            
            # ========== STEP 7: GET ENTRY SPOT PRICE ==========
            # Get spot from database (use index price at entry_date)
            entry_spot = get_spot_price_from_db(entry_date, index)
            
            if entry_spot is None:
                _log(f"  WARNING: No spot data for {entry_date} - skipping")
                continue
            
            _log(f"  Entry Spot: {entry_spot}")
            
            # ========== STEP 8: PROCESS EACH LEG ==========
            trade_legs = []
            
            for leg_idx, leg_config in enumerate(legs_config):
                _log(f"\n    Processing Leg {leg_idx + 1}...")
                
                segment = leg_config['segment']
                position = leg_config['position']
                lots = leg_config['lots']
                
                if segment == 'FUTURES':
                    # ========== FUTURES LEG ==========
                    _log(f"      Type: FUTURE")
                    _log(f"      Position: {position}")
                    
                    # Get future expiry
                    future_expiry = expiry_date  # Use same expiry for simplicity
                    
                    # Get entry price
                    entry_price = get_future_price_from_db(
                        date=entry_date.strftime('%Y-%m-%d'),
                        index=index,
                        expiry=future_expiry.strftime('%Y-%m-%d')
                    )
                    
                    if entry_price is None:
                        _log(f"      WARNING: No future price - skipping leg")
                        continue
                    
                    _log(f"      Entry Price: {entry_price}")
                    
                    # Get exit price
                    exit_price = get_future_price_from_db(
                        date=exit_date.strftime('%Y-%m-%d'),
                        index=index,
                        expiry=future_expiry.strftime('%Y-%m-%d')
                    )
                    
                    if exit_price is None:
                        _log(f"      WARNING: No exit price - using entry")
                        exit_price = entry_price
                    
                    _log(f"      Exit Price: {exit_price}")
                    
                    # Calculate P&L with correct lot size
                    lot_size = get_lot_size(index, entry_date)
                    
                    if position == 'BUY':
                        leg_pnl = (exit_price - entry_price) * lots * lot_size
                    else:  # SELL
                        leg_pnl = (entry_price - exit_price) * lots * lot_size
                    
                    _log(f"      Lots: {lots}, P&L: {leg_pnl:,.2f}")
                    
                    trade_legs.append({
                        'leg_number': leg_idx + 1,
                        'segment': 'FUTURE',
                        'position': position,
                        'lots': lots,
                        'lot_size': lot_size,  # Store lot_size for DataFrame
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl': leg_pnl
                    })
                
                else:  # OPTIONS
                    # ========== OPTIONS LEG ==========
                    option_type = leg_config['option_type']
                    strike_selection = leg_config['strike_selection']
                    
                    _log(f"      Type: OPTION")
                    _log(f"      Option Type: {option_type}")
                    _log(f"      Position: {position}")
                    _log(f"      Strike Selection: {strike_selection}")
                    
                    # ========== CALCULATE STRIKE ==========
                    strike = calculate_strike_from_selection(
                        spot_price=entry_spot,
                        strike_interval=strike_interval,
                        selection=strike_selection,
                        option_type=option_type
                    )
                    
                    _log(f"      Calculated Strike: {strike}")
                    
                    # Get entry premium
                    entry_premium = get_option_premium_from_db(
                        date=entry_date.strftime('%Y-%m-%d'),
                        index=index,
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry_date.strftime('%Y-%m-%d')
                    )
                    
                    if entry_premium is None:
                        _log(f"      WARNING: No entry premium - skipping leg")
                        continue
                    
                    _log(f"      Entry Premium: {entry_premium}")
                    
                    # ========== GET EXIT PREMIUM ==========
                    # CRITICAL FIX: Always try to fetch MARKET premium first,
                    # regardless of whether exit is at expiry or before.
                    # AlgoTest uses actual closing prices even on expiry day.
                    # Only fall back to intrinsic value if market data is missing.
                    
                    exit_premium = get_option_premium_from_db(
                        date=exit_date.strftime('%Y-%m-%d'),
                        index=index,
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry_date.strftime('%Y-%m-%d')
                    )
                    
                    if exit_premium is not None:
                        # Market data found — use it
                        _log(f"      SUCCESS: Exit Premium (market data): {exit_premium}")
                    else:
                        # Market data missing — fallback to intrinsic value
                        _log(f"      WARNING: No market data found for exit - calculating intrinsic value")
                        
                        exit_spot = get_spot_price_from_db(exit_date, index)
                        if exit_spot is None:
                            _log(f"      WARNING: No exit spot data - using entry spot")
                            exit_spot = entry_spot
                        
                        exit_premium = calculate_intrinsic_value(
                            spot=exit_spot,
                            strike=strike,
                            option_type=option_type
                        )
                        
                        _log(f"      Exit Spot Price: {exit_spot}")
                        _log(f"      Strike Price: {strike}")
                        _log(f"      Option Type: {option_type}")
                        
                        if option_type.upper() == 'CE':
                            intrinsic_calc = f"max(0, {exit_spot} - {strike}) = max(0, {exit_spot - strike})"
                        else:  # PE
                            intrinsic_calc = f"max(0, {strike} - {exit_spot}) = max(0, {strike - exit_spot})"
                        
                        _log(f"      🧮 Intrinsic Value Calculation: {intrinsic_calc}")
                        _log(f"      💰 Exit Premium (intrinsic): {exit_premium}")
                        
                        if exit_premium == 0:
                            _log(f"      INFO: Option expired WORTHLESS (OTM)")
                    
                    # Calculate P&L with correct lot size based on index and entry date
                    lot_size = get_lot_size(index, entry_date)
                    
                    if position == 'BUY':
                        leg_pnl = (exit_premium - entry_premium) * lots * lot_size
                    else:  # SELL
                        leg_pnl = (entry_premium - exit_premium) * lots * lot_size
                    
                    _log(f"      Lots: {lots}, P&L: {leg_pnl:,.2f}")
                    
                    trade_legs.append({
                        'leg_number': leg_idx + 1,
                        'segment': 'OPTION',
                        'option_type': option_type,
                        'strike': strike,
                        'position': position,
                        'lots': lots,
                        'lot_size': lot_size,  # Store lot_size for DataFrame
                        'entry_premium': entry_premium,
                        'exit_premium': exit_premium,
                        'pnl': leg_pnl
                    })
            
            # ========== STEP 8B: CHECK STOP LOSS / TARGET ==========
            sl_hit_date = None
            sl_reason = None
            
            if trade_legs and (stop_loss_pct is not None or target_pct is not None):
                sl_hit_date, sl_reason = check_stop_loss_target(
                    entry_date=entry_date,
                    exit_date=exit_date,
                    expiry_date=expiry_date,
                    entry_spot=entry_spot,
                    legs_config=trade_legs,
                    index=index,
                    stop_loss_pct=stop_loss_pct,
                    target_pct=target_pct,
                    trading_calendar=trading_calendar
                )
                
                if sl_hit_date is not None:
                    original_exit_date = exit_date
                    exit_date = sl_hit_date
                    print(f"  WARNING: Exiting on {exit_date.strftime('%Y-%m-%d')} due to {sl_reason}")

                    # FIX: get lot_size here so SL P&L matches normal path
                    lot_size_sl = get_lot_size(index, entry_date)

                    # Recalculate exit premium for each leg based on new exit_date
                    for leg in trade_legs:
                        if leg.get('segment') == 'OPTION':
                            opt_type = leg.get('option_type')
                            strike = leg.get('strike')
                            position = leg.get('position')
                            lots = leg.get('lots')
                            entry_prem = leg.get('entry_premium')
                            
                            if opt_type and strike and position and entry_prem:
                                # Calculate exit premium based on new SL exit date
                                # Apply same fix: try market data first, fallback to intrinsic
                                new_exit_premium = get_option_premium_from_db(
                                    date=exit_date.strftime('%Y-%m-%d'),
                                    index=index,
                                    strike=strike,
                                    option_type=opt_type,
                                    expiry=expiry_date.strftime('%Y-%m-%d')
                                )
                                
                                if new_exit_premium is None:
                                    # Market data missing — use intrinsic value as fallback
                                    exit_spot = get_spot_price_from_db(exit_date, index)
                                    if exit_spot is None:
                                        exit_spot = entry_spot
                                    new_exit_premium = calculate_intrinsic_value(
                                        spot=exit_spot,
                                        strike=strike,
                                        option_type=opt_type
                                    )
                                
                                # Recalculate P&L
                                if position == 'BUY':
                                    leg_pnl = (new_exit_premium - entry_prem) * lots * lot_size_sl
                                else:
                                    leg_pnl = (entry_prem - new_exit_premium) * lots * lot_size_sl
                                
                                leg['exit_premium'] = new_exit_premium
                                leg['pnl'] = leg_pnl
                                print(f"      Recalculated P&L for leg {leg['leg_number']}: {leg_pnl:,.2f} (lot_size={lot_size_sl})")
            
            # ========== STEP 9: CALCULATE TOTAL P&L ==========
            total_pnl = sum(leg['pnl'] for leg in trade_legs)
            
            print(f"\n  Total P&L: {total_pnl:,.2f}")
            
            # ========== STEP 10: GET EXIT SPOT ==========
            # Get exit spot price for the trade
            exit_spot = get_spot_price_from_db(exit_date, index)
            if exit_spot is None:
                exit_spot = entry_spot
            
            # ========== STEP 11: RECORD TRADE ==========
            trade_record = {
                'entry_date': entry_date,
                'exit_date': exit_date,
                'expiry_date': expiry_date,
                'entry_dte': entry_dte,
                'exit_dte': exit_dte,
                'entry_spot': entry_spot,
                'exit_spot': exit_spot,
                'exit_reason': sl_reason if sl_reason else 'SCHEDULED',
                'legs': trade_legs,
                'total_pnl': total_pnl
            }
            
            all_trades.append(trade_record)
            print(f"  SUCCESS: Trade recorded\n")
        
        except Exception as e:
            print(f"  ERROR: {str(e)}\n")
            continue
    
    # ========== STEP 11: CONVERT TO DATAFRAME ==========
    print(f"\n{'='*60}")
    print(f"BACKTEST COMPLETE")
    print(f"{'='*60}")
    print(f"Total Trades: {len(all_trades)}")
    
    if not all_trades:
        print("No trades executed - returning empty results")
        return pd.DataFrame(), {}, {}
    
    # Flatten for DataFrame - Create rows for EACH leg (AlgoTest format)
    # But we'll aggregate them back for analytics
    trades_flat = []
    trade_counter = 0
    for trade_idx, trade in enumerate(all_trades, 1):
        entry_spot_val = trade['entry_spot']
        exit_spot_val = trade.get('exit_spot', trade['entry_spot'])
        
        # Create SEPARATE row for EACH leg (like AlgoTest CSV format)
        for leg in trade['legs']:
            leg_num = leg['leg_number']
            
            # Determine Type and Position
            if leg['segment'] == 'FUTURE':
                option_type = 'FUT'
                position = leg['position']
                strike = ''
                entry_price = leg['entry_price']
                exit_price = leg.get('exit_price', 0)
            else:
                option_type = leg['option_type']
                position = leg['position']
                strike = leg['strike']
                entry_price = leg['entry_premium']
                exit_price = leg.get('exit_premium', 0)
            
            leg_pnl = leg['pnl']
            lot_size = leg.get('lot_size', 65)  # Get stored lot_size
            qty = lots * lot_size  # Calculate total quantity
            
            row = {
                'Trade': trade_idx,  # Trade group number
                'Leg': leg_num,  # Leg number within trade
                'Index': trade_counter + leg_num,  # Unique row index
                'Entry Date': trade['entry_date'],
                'Exit Date': trade['exit_date'],
                'Type': option_type,
                'Strike': strike,
                'B/S': position,
                'Qty': qty,  # Use lots × lot_size
                'Entry Price': entry_price,
                'Exit Price': exit_price,
                'Entry Spot': entry_spot_val,
                'Exit Spot': exit_spot_val,
                'Spot P&L': round(exit_spot_val - entry_spot_val, 2) if exit_spot_val and entry_spot_val else 0,
                'Future Expiry': trade['expiry_date'],
                'Net P&L': leg_pnl,
                'Exit Reason': trade.get('exit_reason', 'SCHEDULED')
            }
            
            trades_flat.append(row)
        
        trade_counter += len(trade['legs'])
    
    trades_df = pd.DataFrame(trades_flat)
    
    # ========== AGGREGATE LEGS INTO TRADES FOR ANALYTICS ==========
    # Group by Trade number and sum P&L to get one row per trade
    trades_aggregated = trades_df.groupby('Trade').agg({
        'Entry Date': 'first',
        'Exit Date': 'first',
        'Entry Spot': 'first',
        'Exit Spot': 'first',
        'Spot P&L': 'first',
        'Net P&L': 'sum',  # Sum P&L across all legs
        'Exit Reason': 'first'
    }).reset_index()
    
    # ========== STEP 12: COMPUTE ANALYTICS (ADDS CUMULATIVE, PEAK, DD, %DD) ==========
    if DEBUG:
        print(f"Computing analytics on {len(trades_aggregated)} trades...")
    
    # Call compute_analytics on AGGREGATED trades (one row per trade)
    # This adds: Cumulative, Peak, DD, %DD columns
    # And returns enhanced summary with CAGR, Max DD, etc.
    trades_aggregated, summary = compute_analytics(trades_aggregated)
    
    print(f"\n{'='*60}")
    print(f"ANALYTICS COMPLETE")
    print(f"{'='*60}")
    print(f"Total P&L: ₹{summary.get('total_pnl', 0):,.2f}")
    print(f"Win Rate: {summary.get('win_pct', 0):.2f}%")
    print(f"CAGR: {summary.get('cagr_options', 0):.2f}%")
    print(f"Max Drawdown: {summary.get('max_dd_pct', 0):.2f}%")
    print(f"CAR/MDD: {summary.get('car_mdd', 0):.2f}")
    print(f"{'='*60}\n")
    
    # ========== MERGE ANALYTICS BACK TO DETAILED TRADES ==========
    # Merge Cumulative, Peak, DD, %DD from aggregated back to detailed leg-by-leg DataFrame
    analytics_cols = ['Trade', 'Cumulative', 'Peak', 'DD', '%DD']
    trades_aggregated_subset = trades_aggregated[analytics_cols]
    trades_df = trades_df.merge(trades_aggregated_subset, on='Trade', how='left')
    
    print(f"\nDEBUG: trades_df columns after merge: {list(trades_df.columns)}")
    print(f"DEBUG: First row Cumulative: {trades_df.iloc[0]['Cumulative'] if 'Cumulative' in trades_df.columns else 'MISSING'}")
    
    # ========== STEP 13: BUILD PIVOT TABLE ==========
    pivot = build_pivot(trades_aggregated, 'Exit Date')
    
    return trades_df, summary, pivot