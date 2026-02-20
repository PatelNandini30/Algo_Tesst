"""Generic Multi-Leg Strategy Engine

Handles arbitrary multi-leg strategies that don't map to existing engines.
Calculates P&L per leg and aggregates across all legs.
Maintains same data flow as existing engines.
"""

import pandas as pd
from datetime import timedelta
from typing import Dict, Any, Tuple
import sys
import os

# Add backend to path for direct imports
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Import base functions
try:
    from base import (
        get_strike_data,
        load_expiry,
        # load_base2,  # Disabled - base2 filter not used
        load_bhavcopy,
        build_intervals,
        compute_analytics,
        build_pivot,
        apply_spot_adjustment,
        get_atm_strike,
        get_otm_strike,
        get_itm_strike,
        get_nearest_strike
    )
except ImportError as e:
    print(f"Failed to import from base: {e}")
    raise

try:
    from strategies.strategy_types import (
        InstrumentType,
        OptionType,
        PositionType,
        ExpiryType,
        StrikeSelectionType,
        StrategyDefinition,
        Leg
    )
    print("[OK] Successfully imported from strategies.strategy_types")
except ImportError as e:
    print(f"Failed to import from strategies.strategy_types: {e}")
    # Fallback for different execution contexts
    try:
        strategies_dir = os.path.join(backend_dir, 'strategies')
        if strategies_dir not in sys.path:
            sys.path.insert(0, strategies_dir)
        from strategy_types import (
            InstrumentType,
            OptionType,
            PositionType,
            ExpiryType,
            StrikeSelectionType,
            StrategyDefinition,
            Leg
        )
        print("[OK] Successfully imported from strategy_types (fallback)")
    except ImportError as e2:
        print(f"CRITICAL: Both imports failed! Error: {e2}")
        print(f"backend_dir: {backend_dir}")
        print(f"strategies_dir: {strategies_dir}")
        print(f"sys.path: {sys.path}")
        raise


def run_generic_multi_leg(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Execute a generic multi-leg strategy
    
    Args:
        params: Dictionary containing:
            - strategy_definition: StrategyDefinition object
            - index: Index name (e.g., "NIFTY")
            - from_date: Start date
            - to_date: End date
            - other parameters...
    
    Returns:
        Tuple of (trades_df, summary, pivot)
    """
    strategy_def = params['strategy']
    
    index_name = params.get("index", "NIFTY")
    
    # Load data
    spot_df = get_strike_data(index_name, params["from_date"], params["to_date"])
    weekly_exp = load_expiry(index_name, "weekly")
    monthly_exp = load_expiry(index_name, "monthly")
    # base2 = load_base2()  # Disabled - base2 filter not used

    # Base2 Filter - DISABLED (not using base2 filter)
    # mask = pd.Series(False, index=spot_df.index)
    # for _, row in base2.iterrows():
    #     mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    # spot_df = spot_df[mask].reset_index(drop=True)

    trades = []

    # Weekly Expiry Loop (similar to existing engines)
    for w in range(len(weekly_exp)):
        prev_expiry = weekly_exp.iloc[w]['Previous Expiry']
        curr_expiry = weekly_exp.iloc[w]['Current Expiry']

        # Future expiry default (similar to existing engines)
        curr_monthly_expiry = monthly_exp[
            monthly_exp['Current Expiry'] >= curr_expiry
        ].sort_values(by='Current Expiry').reset_index(drop=True)

        if curr_monthly_expiry.empty:
            continue

        fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']

        # Filter window (similar to existing engines)
        filtered_data = spot_df[
            (spot_df['Date'] >= prev_expiry) &
            (spot_df['Date'] <= curr_expiry)
        ].sort_values(by='Date').reset_index(drop=True)

        if len(filtered_data) < 2:
            continue

        # Handle re-entry/intervals (similar to existing engines)
        spot_adjustment_type = params.get("spot_adjustment_type", 0)
        spot_adjustment = params.get("spot_adjustment", 1)

        intervals = build_intervals(filtered_data, spot_adjustment_type, spot_adjustment)

        if not intervals:
            continue

        interval_df = pd.DataFrame(intervals, columns=['From', 'To'])

        # Trade loop over intervals
        for i in range(len(interval_df)):
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            trade_number = i + 1  # Trade number (1, 2, 3...)

            if fromDate == toDate:
                continue

            # BASE START OVERRIDE - DISABLED (base2 not loaded)
            # is_base_start = (base2['Start'] == fromDate).any()
            # if is_base_start:
            #     override_df = monthly_exp[
            #         monthly_exp['Current Expiry'] > fromDate   # strictly >
            #     ].reset_index(drop=True)
            #     if len(override_df) > 1:
            #         fut_expiry = override_df.iloc[1]['Current Expiry']   # iloc[1]

            # Entry / Exit spots (same as existing engines)
            entry_row = filtered_data[filtered_data['Date'] == fromDate]
            exit_row = filtered_data[filtered_data['Date'] == toDate]

            if entry_row.empty:
                continue

            entry_spot = entry_row.iloc[0]['Close']
            exit_spot = exit_row.iloc[0]['Close'] if not exit_row.empty else None

            # Load bhavcopy for entry and exit dates
            bhav_entry = load_bhavcopy(fromDate.strftime('%Y-%m-%d'))
            bhav_exit = load_bhavcopy(toDate.strftime('%Y-%m-%d'))

            if bhav_entry is None or bhav_exit is None:
                continue

            # Process each leg in the strategy
            total_pnl = 0
            leg_details = []
            
            for leg_idx, leg in enumerate(strategy_def.legs):
                leg_pnl = 0
                leg_entry_price = None
                leg_exit_price = None
                
                if leg.instrument == InstrumentType.OPTION:
                    # Calculate strike based on leg's strike selection criteria
                    adjusted_spot = apply_spot_adjustment(
                        entry_spot,
                        leg.strike_selection.spot_adjustment_mode,
                        leg.strike_selection.spot_adjustment
                    )
                    
                    # Get available strikes for the given expiry and option type
                    option_mask = (
                        (bhav_entry['Instrument'] == "OPTIDX") &
                        (bhav_entry['Symbol'] == index_name) &
                        (bhav_entry['OptionType'] == leg.option_type.value) &
                        (
                            (bhav_entry['ExpiryDate'] == curr_expiry) |
                            (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                            (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                        ) &
                        (bhav_entry['TurnOver'] > 0)
                    )
                    available_strikes = bhav_entry[option_mask]['StrikePrice'].unique()
                    available_strikes_series = pd.Series(available_strikes)
                    
                    # Select strike based on strike selection type
                    if leg.strike_selection.type == StrikeSelectionType.ATM:
                        selected_strike = get_atm_strike(adjusted_spot, available_strikes_series)
                    elif leg.strike_selection.type == StrikeSelectionType.OTM_PERCENT:
                        selected_strike = get_otm_strike(
                            adjusted_spot, 
                            available_strikes_series, 
                            leg.strike_selection.value, 
                            leg.option_type.value
                        )
                    elif leg.strike_selection.type == StrikeSelectionType.ITM_PERCENT:
                        selected_strike = get_itm_strike(
                            adjusted_spot, 
                            available_strikes_series, 
                            leg.strike_selection.value, 
                            leg.option_type.value
                        )
                    elif leg.strike_selection.type == StrikeSelectionType.CLOSEST_PREMIUM:
                        # Find strike with premium closest to target value
                        target_premium = leg.strike_selection.value
                        selected_strike = None
                        min_diff = float('inf')
                        
                        for strike in available_strikes:
                            strike_mask = bhav_entry[
                                (bhav_entry['Instrument'] == "OPTIDX") &
                                (bhav_entry['Symbol'] == index_name) &
                                (bhav_entry['OptionType'] == leg.option_type.value) &
                                (
                                    (bhav_entry['ExpiryDate'] == curr_expiry) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                                ) &
                                (bhav_entry['StrikePrice'] == strike) &
                                (bhav_entry['TurnOver'] > 0)
                            ]
                            
                            if not strike_mask.empty:
                                premium = strike_mask.iloc[0]['Close']
                                diff = abs(premium - target_premium)
                                if diff < min_diff:
                                    min_diff = diff
                                    selected_strike = strike
                    
                    elif leg.strike_selection.type == StrikeSelectionType.PREMIUM_RANGE:
                        # Find strikes within premium range
                        premium_min = leg.strike_selection.premium_min or 0.0
                        premium_max = leg.strike_selection.premium_max or float('inf')
                        valid_strikes = []
                        
                        for strike in available_strikes:
                            strike_mask = bhav_entry[
                                (bhav_entry['Instrument'] == "OPTIDX") &
                                (bhav_entry['Symbol'] == index_name) &
                                (bhav_entry['OptionType'] == leg.option_type.value) &
                                (
                                    (bhav_entry['ExpiryDate'] == curr_expiry) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                                ) &
                                (bhav_entry['StrikePrice'] == strike) &
                                (bhav_entry['TurnOver'] > 0)
                            ]
                            
                            if not strike_mask.empty:
                                premium = strike_mask.iloc[0]['Close']
                                if premium_min <= premium <= premium_max:
                                    valid_strikes.append((strike, premium))
                        
                        # Select the strike closest to ATM from valid strikes
                        if valid_strikes:
                            atm_strike = get_atm_strike(adjusted_spot, available_strikes_series)
                            selected_strike = min(valid_strikes, key=lambda x: abs(x[0] - atm_strike))[0]
                        else:
                            selected_strike = None
                    
                    elif leg.strike_selection.type == StrikeSelectionType.SPOT:
                        selected_strike = get_nearest_strike(adjusted_spot, available_strikes_series)
                    else:
                        raise ValueError(f"Invalid strike selection type: {leg.strike_selection.type}")
                    
                    if selected_strike is None:
                        continue  # Skip this leg if no suitable strike found
                    
                    # Get entry data for this leg's strike
                    if leg.option_type == OptionType.CE:
                        # For calls, if value is positive, look for higher strikes; if negative, lower strikes
                        if leg.strike_selection.value >= 0:
                            entry_mask = bhav_entry[
                                (bhav_entry['Instrument'] == "OPTIDX") &
                                (bhav_entry['Symbol'] == index_name) &
                                (bhav_entry['OptionType'] == "CE") &
                                (
                                    (bhav_entry['ExpiryDate'] == curr_expiry) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                                ) &
                                (bhav_entry['StrikePrice'] >= selected_strike) &
                                (bhav_entry['TurnOver'] > 0) &
                                (bhav_entry['StrikePrice'] % 100 == 0)
                            ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
                        else:
                            entry_mask = bhav_entry[
                                (bhav_entry['Instrument'] == "OPTIDX") &
                                (bhav_entry['Symbol'] == index_name) &
                                (bhav_entry['OptionType'] == "CE") &
                                (
                                    (bhav_entry['ExpiryDate'] == curr_expiry) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                    (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                                ) &
                                (bhav_entry['StrikePrice'] <= selected_strike) &
                                (bhav_entry['TurnOver'] > 0) &
                                (bhav_entry['StrikePrice'] % 100 == 0)
                            ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                    
                        if entry_mask.empty:
                            continue
                        
                        selected_strike = entry_mask.iloc[0]['StrikePrice']
                    leg_entry_price = entry_mask.iloc[0]['Close']
                    
                    # Get exit data for this leg's strike
                    exit_mask = bhav_exit[
                        (bhav_exit['Instrument'] == "OPTIDX") &
                        (bhav_exit['Symbol'] == index_name) &
                        (bhav_exit['OptionType'] == leg.option_type.value) &
                        (
                            (bhav_exit['ExpiryDate'] == curr_expiry) |
                            (bhav_exit['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                            (bhav_exit['ExpiryDate'] == curr_expiry + timedelta(days=1))
                        ) &
                        (bhav_exit['StrikePrice'] == selected_strike)
                    ]
                    
                    if exit_mask.empty:
                        continue
                    
                    leg_exit_price = exit_mask.iloc[0]['Close']
                    
                    # Calculate P&L for this option leg
                    if leg.position == PositionType.BUY:
                        # BUY Option: PnL = Exit Premium - Entry Premium
                        leg_pnl = round(leg_exit_price - leg_entry_price, 2)
                    else:  # SELL
                        # SELL Option: PnL = Entry Premium - Exit Premium
                        leg_pnl = round(leg_entry_price - leg_exit_price, 2)
                        
                    leg_details.append({
                        f'Leg_{leg_idx+1}_Type': f'{leg.instrument.value}_{leg.option_type.value}_{leg.position.value}',
                        f'Leg_{leg_idx+1}_Strike': selected_strike,
                        f'Leg_{leg_idx+1}_EntryPrice': leg_entry_price,
                        f'Leg_{leg_idx+1}_ExitPrice': leg_exit_price,
                        f'Leg_{leg_idx+1}_P&L': leg_pnl
                    })
                
                elif leg.instrument == InstrumentType.FUTURE:
                    # Future leg processing
                    # Determine future expiry based on leg's expiry type
                    if leg.expiry_type == ExpiryType.MONTHLY:
                        fut_expiry_for_leg = fut_expiry  # Monthly expiry from earlier
                    else:  # WEEKLY
                        fut_expiry_for_leg = curr_expiry  # Weekly expiry
                        
                    # Get future entry data
                    fut_entry_mask = bhav_entry[
                        (bhav_entry['Instrument'] == "FUTIDX") &
                        (bhav_entry['Symbol'] == index_name) &
                        (bhav_entry['ExpiryDate'].dt.month == fut_expiry_for_leg.month) &
                        (bhav_entry['ExpiryDate'].dt.year == fut_expiry_for_leg.year)
                    ]
                    
                    fut_exit_mask = bhav_exit[
                        (bhav_exit['Instrument'] == "FUTIDX") &
                        (bhav_exit['Symbol'] == index_name) &
                        (bhav_exit['ExpiryDate'].dt.month == fut_expiry_for_leg.month) &
                        (bhav_exit['ExpiryDate'].dt.year == fut_expiry_for_leg.year)
                    ]
                    
                    if fut_entry_mask.empty or fut_exit_mask.empty:
                        continue
                    
                    leg_entry_price = fut_entry_mask.iloc[0]['Close']
                    leg_exit_price = fut_exit_mask.iloc[0]['Close']
                    
                    # Calculate P&L for this future leg
                    if leg.position == PositionType.BUY:
                        # Future BUY: PnL = Exit Spot - Entry Spot
                        leg_pnl = round(leg_exit_price - leg_entry_price, 2)
                    else:  # SELL
                        # Future SELL: PnL = Entry Spot - Exit Spot
                        leg_pnl = round(leg_entry_price - leg_exit_price, 2)
                        
                    leg_details.append({
                        f'Leg_{leg_idx+1}_Type': f'{leg.instrument.value}_{leg.position.value}',
                        f'Leg_{leg_idx+1}_Expiry': fut_expiry_for_leg,
                        f'Leg_{leg_idx+1}_EntryPrice': leg_entry_price,
                        f'Leg_{leg_idx+1}_ExitPrice': leg_exit_price,
                        f'Leg_{leg_idx+1}_P&L': leg_pnl
                    })
                
                # Add this leg's P&L to total
                total_pnl += leg_pnl

            # Create SEPARATE trade record for EACH LEG (like AlgoTest format)
            for idx, leg_detail in enumerate(leg_details):
                # Get the actual leg object for this iteration
                leg_obj = strategy_def.legs[idx]
                
                # Find P&L key dynamically (could be Leg_1_P&L, Leg_2_P&L, etc.)
                pnl_key = [k for k in leg_detail.keys() if 'P&L' in k][0]
                leg_pnl_value = leg_detail.get(pnl_key, 0)
                
                # Extract leg info from Type field (e.g., "OPTION_CE_SELL")
                leg_type = leg_detail.get(f'Leg_{idx+1}_Type', '')
                parts = leg_type.split('_')
                
                # Determine Type, Position
                if len(parts) >= 3:
                    option_type = parts[1] if len(parts) > 1 else ''  # CE or PE
                    position = parts[2] if len(parts) > 2 else ''     # BUY or SELL
                else:
                    option_type = ''
                    position = ''
                
                strike = leg_detail.get(f'Leg_{idx+1}_Strike', '')
                entry_price = leg_detail.get(f'Leg_{idx+1}_EntryPrice', '')
                exit_price = leg_detail.get(f'Leg_{idx+1}_ExitPrice', '')
                
                trade_record = {
                    "Index": trade_number,
                    "Entry Date": fromDate,
                    "Exit Date": toDate,
                    "Type": option_type,
                    "Strike": strike,
                    "B/S": position,
                    "Qty": leg_obj.lots,  # Use lot size from leg configuration
                    "Entry Price": entry_price,
                    "Exit Price": exit_price,
                    "Entry Spot": entry_spot,
                    "Exit Spot": exit_spot,
                    "Spot P&L": round(exit_spot - entry_spot, 2) if exit_spot else None,
                    "Future Expiry": fut_expiry,
                    "Net P&L": leg_pnl_value,
                }
                
                trades.append(trade_record)

    # Final output processing
    if not trades:
        return pd.DataFrame(), {}, {}

    df = pd.DataFrame(trades)
    df, summary = compute_analytics(df)
    pivot = build_pivot(df, 'Future Expiry')

    return df, summary, pivot