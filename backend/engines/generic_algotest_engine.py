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


def check_leg_stop_loss_target(entry_date, exit_date, expiry_date, entry_spot, legs_config,
                               index, trading_calendar, square_off_mode='partial'):
    """
    Check per-leg stop loss / target during the holding period.

    Each leg can carry its own:
        stop_loss       – numeric value or None  (absence = no SL)
        stop_loss_type  – 'pct'  → % of entry premium
                          'points' → absolute index points of net P&L on this leg
        target          – numeric value or None
        target_type     – 'pct' | 'points'

    square_off_mode:
        'partial'  – only the triggered leg is marked as "early-exit"; others continue.
        'complete' – the first triggered leg causes ALL legs to early-exit on the same day.

    Returns:
        A list of dicts, one per leg_config, with keys:
            'triggered'    : bool
            'exit_date'    : pd.Timestamp (original exit_date if not triggered)
            'exit_reason'  : str
        If no legs have SL/Target configured, returns None (caller uses original logic).
    """
    # Quick exit: nothing to check
    has_any_sl_target = any(
        (lg.get('stop_loss') is not None or lg.get('target') is not None)
        for lg in legs_config
    )
    if not has_any_sl_target:
        return None

    # All trading days between entry (exclusive) and planned exit (inclusive)
    holding_days = trading_calendar[
        (trading_calendar['date'] > entry_date) &
        (trading_calendar['date'] <= exit_date)
    ]['date'].tolist()

    # Per-leg tracking: once a leg is triggered it stays triggered
    leg_results = [
        {
            'triggered': False,
            'exit_date': exit_date,
            'exit_reason': 'SCHEDULED',
        }
        for _ in legs_config
    ]

    for check_date in holding_days:
        all_triggered = all(r['triggered'] for r in leg_results)
        if all_triggered:
            break  # Nothing left to check

        # Evaluate each still-active leg
        newly_triggered_this_day = []

        for li, leg in enumerate(legs_config):
            if leg_results[li]['triggered']:
                continue  # Already done

            sl_val   = leg.get('stop_loss')
            sl_type  = leg.get('stop_loss_type', 'pct')
            tgt_val  = leg.get('target')
            tgt_type = leg.get('target_type', 'pct')

            if sl_val is None and tgt_val is None:
                continue  # No SL/Target for this leg

            segment = leg.get('segment', 'OPTION')
            if segment == 'FUTURES':
                # For futures we use points P&L only
                current_price = get_future_price_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    expiry=expiry_date.strftime('%Y-%m-%d')
                )
                if current_price is None:
                    continue

                entry_price = leg.get('entry_price')
                if entry_price is None:
                    continue

                position = leg['position']
                lot_size = leg.get('lot_size', get_lot_size(index, entry_date))
                lots     = leg.get('lots', 1)

                if position == 'BUY':
                    raw_pnl  = (current_price - entry_price) * lots * lot_size
                    raw_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
                else:
                    raw_pnl  = (entry_price - current_price) * lots * lot_size
                    raw_pnl_pct = ((entry_price - current_price) / entry_price * 100) if entry_price else 0

            else:  # OPTIONS
                option_type = leg.get('option_type')
                strike      = leg.get('strike')
                if not option_type or not strike:
                    continue

                current_premium = get_option_premium_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    strike=strike,
                    option_type=option_type,
                    expiry=expiry_date.strftime('%Y-%m-%d')
                )
                if current_premium is None:
                    continue

                entry_premium = leg.get('entry_premium')
                if entry_premium is None:
                    continue

                position = leg['position']
                lot_size = leg.get('lot_size', get_lot_size(index, entry_date))
                lots     = leg.get('lots', 1)

                if position == 'BUY':
                    raw_pnl     = (current_premium - entry_premium) * lots * lot_size
                    raw_pnl_pct = ((current_premium - entry_premium) / entry_premium * 100) if entry_premium else 0
                else:
                    raw_pnl     = (entry_premium - current_premium) * lots * lot_size
                    raw_pnl_pct = ((entry_premium - current_premium) / entry_premium * 100) if entry_premium else 0

            # ── Evaluate SL ──
            if sl_val is not None:
                if sl_type == 'pct':
                    hit_sl = raw_pnl_pct <= -sl_val
                else:  # points
                    hit_sl = raw_pnl <= -sl_val
            else:
                hit_sl = False

            # ── Evaluate Target ──
            if tgt_val is not None:
                if tgt_type == 'pct':
                    hit_tgt = raw_pnl_pct >= tgt_val
                else:
                    hit_tgt = raw_pnl >= tgt_val
            else:
                hit_tgt = False

            if hit_sl or hit_tgt:
                reason = 'STOP_LOSS' if hit_sl else 'TARGET'
                _log(f"      {'🛑' if hit_sl else '✅'} Leg {li+1} {reason} hit on {check_date.strftime('%Y-%m-%d')} "
                     f"(pnl={raw_pnl:.2f}, pnl%={raw_pnl_pct:.2f}%)")
                newly_triggered_this_day.append((li, check_date, reason))

        # ── Apply triggers based on square_off_mode ──
        if newly_triggered_this_day:
            if square_off_mode == 'complete':
                # All legs exit on the earliest trigger date of this day
                trigger_date = newly_triggered_this_day[0][1]
                trigger_reason = newly_triggered_this_day[0][2]
                for li2, r in enumerate(leg_results):
                    if not r['triggered']:
                        leg_results[li2] = {
                            'triggered': True,
                            'exit_date': trigger_date,
                            'exit_reason': f'COMPLETE_{trigger_reason}',
                        }
                break  # No need to check further dates
            else:
                # 'partial' – mark only triggered legs
                for (li, tdate, treason) in newly_triggered_this_day:
                    leg_results[li] = {
                        'triggered': True,
                        'exit_date': tdate,
                        'exit_reason': treason,
                    }

    return leg_results


# ── Legacy wrapper kept for backward-compat with old code paths ──────────────
def check_stop_loss_target(entry_date, exit_date, expiry_date, entry_spot, legs_config,
                           index, stop_loss_pct, target_pct, trading_calendar):
    """
    Legacy overall-level SL/Target check (used by old engines).
    Checks combined portfolio P&L, exits ALL legs on breach.
    """
    if stop_loss_pct is None and target_pct is None:
        return None, None

    holding_days = trading_calendar[
        (trading_calendar['date'] > entry_date) &
        (trading_calendar['date'] <= exit_date)
    ]['date'].tolist()

    for check_date in holding_days:
        total_pnl_pct = 0
        has_data = False

        for leg in legs_config:
            segment = leg.get('segment', 'OPTION')
            if segment == 'FUTURES':
                continue

            position      = leg['position']
            lots          = leg['lots']
            option_type   = leg['option_type']
            strike        = leg.get('strike')
            if strike is None:
                continue

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

            if position == 'BUY':
                leg_pnl_pct = ((current_premium - entry_premium) / entry_premium) * 100 if entry_premium > 0 else 0
            else:
                leg_pnl_pct = ((entry_premium - current_premium) / entry_premium) * 100 if entry_premium > 0 else 0

            total_pnl_pct += leg_pnl_pct

        if not has_data:
            continue

        if stop_loss_pct is not None and total_pnl_pct <= -stop_loss_pct:
            _log(f"      🛑 STOP LOSS HIT at {check_date.strftime('%Y-%m-%d')} (P&L: {total_pnl_pct:.2f}%)")
            return check_date, 'STOP_LOSS'

        if target_pct is not None and total_pnl_pct >= target_pct:
            _log(f"      ✅ TARGET HIT at {check_date.strftime('%Y-%m-%d')} (P&L: {total_pnl_pct:.2f}%)")
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
    square_off_mode = params.get('square_off_mode', 'partial')  # 'partial' | 'complete'
    
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
            
            # ========== STEP 8B: CHECK PER-LEG STOP LOSS / TARGET ==========
            # Also supports legacy overall stop_loss_pct / target_pct for backward compat.
            
            # ── Per-leg SL/Target (new path) ──
            # Attach per-leg SL/Target config from frontend payload into trade_legs
            for li, tleg in enumerate(trade_legs):
                src = legs_config[li] if li < len(legs_config) else {}
                tleg['stop_loss']       = src.get('stop_loss', None)
                tleg['stop_loss_type']  = src.get('stop_loss_type', 'pct')
                tleg['target']          = src.get('target', None)
                tleg['target_type']     = src.get('target_type', 'pct')

            per_leg_results = check_leg_stop_loss_target(
                entry_date=entry_date,
                exit_date=exit_date,
                expiry_date=expiry_date,
                entry_spot=entry_spot,
                legs_config=trade_legs,
                index=index,
                trading_calendar=trading_calendar,
                square_off_mode=square_off_mode
            )

            # ── Overall (legacy) SL/Target fallback ──
            sl_hit_date = None
            sl_reason   = None

            if per_leg_results is None and (stop_loss_pct is not None or target_pct is not None):
                # No per-leg SL/Target configured → use legacy overall check
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
                    # Legacy path: all legs exit together
                    per_leg_results = [
                        {'triggered': True, 'exit_date': sl_hit_date, 'exit_reason': sl_reason}
                        for _ in trade_legs
                    ]

            # ── Apply per-leg exit dates & recalculate P&L where needed ──
            if per_leg_results is not None:
                lot_size_sl = get_lot_size(index, entry_date)
                any_early = False

                for li, tleg in enumerate(trade_legs):
                    res = per_leg_results[li]
                    leg_exit_date = res['exit_date']

                    if res['triggered'] and leg_exit_date < exit_date:
                        any_early = True
                        _log(f"  ⚡ Leg {li+1} exits early on {leg_exit_date.strftime('%Y-%m-%d')} "
                             f"({res['exit_reason']})")

                        # Recalculate exit price / premium for the early exit date
                        if tleg.get('segment') == 'OPTION':
                            opt_type     = tleg.get('option_type')
                            strike       = tleg.get('strike')
                            position_leg = tleg.get('position')
                            lots_leg     = tleg.get('lots', 1)
                            entry_prem   = tleg.get('entry_premium')

                            new_exit_prem = get_option_premium_from_db(
                                date=leg_exit_date.strftime('%Y-%m-%d'),
                                index=index,
                                strike=strike,
                                option_type=opt_type,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            if new_exit_prem is None:
                                e_spot = get_spot_price_from_db(leg_exit_date, index) or entry_spot
                                new_exit_prem = calculate_intrinsic_value(spot=e_spot, strike=strike, option_type=opt_type)

                            if position_leg == 'BUY':
                                tleg['pnl'] = (new_exit_prem - entry_prem) * lots_leg * lot_size_sl
                            else:
                                tleg['pnl'] = (entry_prem - new_exit_prem) * lots_leg * lot_size_sl

                            tleg['exit_premium']   = new_exit_prem
                            tleg['early_exit_date'] = leg_exit_date
                            tleg['exit_reason']     = res['exit_reason']

                        elif tleg.get('segment') == 'FUTURE':
                            position_leg = tleg.get('position')
                            lots_leg     = tleg.get('lots', 1)
                            entry_price  = tleg.get('entry_price')

                            new_exit_price = get_future_price_from_db(
                                date=leg_exit_date.strftime('%Y-%m-%d'),
                                index=index,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            if new_exit_price is None:
                                new_exit_price = entry_price

                            if position_leg == 'BUY':
                                tleg['pnl'] = (new_exit_price - entry_price) * lots_leg * lot_size_sl
                            else:
                                tleg['pnl'] = (entry_price - new_exit_price) * lots_leg * lot_size_sl

                            tleg['exit_price']      = new_exit_price
                            tleg['early_exit_date'] = leg_exit_date
                            tleg['exit_reason']     = res['exit_reason']

                # For trade-level exit_reason, use the first triggered reason (if any)
                if any_early:
                    triggered = [r for r in per_leg_results if r['triggered']]
                    sl_reason = triggered[0]['exit_reason'] if triggered else None
            
            # ========== STEP 9: CALCULATE TOTAL P&L ==========
            total_pnl = sum(leg['pnl'] for leg in trade_legs)
            
            _log(f"\n  Total P&L: {total_pnl:,.2f}")
            
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
                'total_pnl': total_pnl,
                'square_off_mode': square_off_mode,
                'per_leg_results': per_leg_results,  # None if no SL/Target
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
        per_leg_res    = trade.get('per_leg_results')  # May be None

        # Create SEPARATE row for EACH leg (like AlgoTest CSV format)
        for leg in trade['legs']:
            leg_num = leg['leg_number']
            li      = leg_num - 1  # 0-based index

            # Resolve per-leg exit date/reason
            if per_leg_res is not None and li < len(per_leg_res):
                leg_exit_date   = per_leg_res[li]['exit_date']
                leg_exit_reason = per_leg_res[li]['exit_reason']
            else:
                leg_exit_date   = trade['exit_date']
                leg_exit_reason = trade.get('exit_reason', 'SCHEDULED')

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
            lots = leg.get('lots', 1)
            lot_size = leg.get('lot_size', 65)
            qty = lots * lot_size
            
            row = {
                'Trade': trade_idx,
                'Leg': leg_num,
                'Index': trade_counter + leg_num,
                'Entry Date': trade['entry_date'],
                'Exit Date': leg_exit_date,         # Per-leg exit date (may differ in partial mode)
                'Type': option_type,
                'Strike': strike,
                'B/S': position,
                'Qty': qty,
                'Entry Price': entry_price,
                'Exit Price': exit_price,
                'Entry Spot': entry_spot_val,
                'Exit Spot': exit_spot_val,
                'Spot P&L': round(exit_spot_val - entry_spot_val, 2) if exit_spot_val and entry_spot_val else 0,
                'Future Expiry': trade['expiry_date'],
                'Net P&L': leg_pnl,
                'Exit Reason': leg_exit_reason,     # Per-leg reason
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