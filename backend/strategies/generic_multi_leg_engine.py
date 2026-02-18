"""
Generic Multi-Leg Strategy Engine
Handles any combination of legs with dynamic strike selection
Uses EXACT formulas from analyse_bhavcopy_02-01-2026.py
"""

import pandas as pd
import numpy as np
from datetime import timedelta, datetime
from typing import Dict, Any, Tuple, List
import sys
import os

# Import from base (existing helper functions)
try:
    from ..base import (
        get_strike_data, load_expiry, # load_base2,  # Disabled - base2 filter not used
        load_bhavcopy,
        build_intervals, compute_analytics, build_pivot
    )
except ImportError:
    # Fallback for direct execution
    import sys
    import os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from base import (
        get_strike_data, load_expiry, # load_base2,  # Disabled - base2 filter not used
        load_bhavcopy,
        build_intervals, compute_analytics, build_pivot
    )

# Import strategy types
try:
    from .strategy_types import (
        InstrumentType, OptionType, PositionType, ExpiryType,
        StrikeSelectionType, StrategyDefinition, Leg,
        EntryTimeType, ExitTimeType, ReEntryMode
    )
except ImportError:
    # Fallback for direct execution
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    from strategy_types import (
        InstrumentType, OptionType, PositionType, ExpiryType,
        StrikeSelectionType, StrategyDefinition, Leg,
        EntryTimeType, ExitTimeType, ReEntryMode
    )

def calculate_strike_from_selection(
    entry_spot: float,
    strike_selection,
    option_type: OptionType,
    available_strikes: List[float],
    bhav_data: pd.DataFrame,
    index_name: str,
    expiry: datetime
) -> float:
    """
    Calculate strike price based on selection method
    Uses EXACT logic from analyse_bhavcopy_02-01-2026.py
    """
    strike_type = strike_selection.type
    
    if strike_type == StrikeSelectionType.ATM:
        # ATM: round((spot/100))*100 - EXACT formula from original
        return round(entry_spot / 100) * 100
    
    elif strike_type == StrikeSelectionType.PERCENT_OF_ATM:
        # % of ATM: round((spot*(1+pct%)/100))*100
        # EXACT formula from v1_ce_fut.py line 510
        pct = strike_selection.value
        return round((entry_spot * (1 + pct / 100)) / 100) * 100
    
    elif strike_type == StrikeSelectionType.CLOSEST_PREMIUM:
        # Find strike with premium closest to target
        target_premium = strike_selection.value
        best_strike = None
        min_diff = float('inf')
        
        for strike in available_strikes:
            # Get premium for this strike
            option_data = bhav_data[
                (bhav_data['Instrument'] == "OPTIDX") &
                (bhav_data['Symbol'] == index_name) &
                (bhav_data['OptionType'] == option_type.value) &
                (bhav_data['StrikePrice'] == strike) &
                (
                    (bhav_data['ExpiryDate'] == expiry) |
                    (bhav_data['ExpiryDate'] == expiry - timedelta(days=1)) |
                    (bhav_data['ExpiryDate'] == expiry + timedelta(days=1))
                ) &
                (bhav_data['TurnOver'] > 0)
            ]
            
            if not option_data.empty:
                premium = option_data.iloc[0]['Close']
                diff = abs(premium - target_premium)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = strike
        
        return best_strike if best_strike else round(entry_spot / 100) * 100
    
    elif strike_type == StrikeSelectionType.PREMIUM_RANGE:
        # Find strikes within premium range
        min_premium = strike_selection.premium_min
        max_premium = strike_selection.premium_max
        valid_strikes = []
        
        for strike in available_strikes:
            option_data = bhav_data[
                (bhav_data['Instrument'] == "OPTIDX") &
                (bhav_data['Symbol'] == index_name) &
                (bhav_data['OptionType'] == option_type.value) &
                (bhav_data['StrikePrice'] == strike) &
                (
                    (bhav_data['ExpiryDate'] == expiry) |
                    (bhav_data['ExpiryDate'] == expiry - timedelta(days=1)) |
                    (bhav_data['ExpiryDate'] == expiry + timedelta(days=1))
                ) &
                (bhav_data['TurnOver'] > 0)
            ]
            
            if not option_data.empty:
                premium = option_data.iloc[0]['Close']
                if min_premium <= premium <= max_premium:
                    valid_strikes.append(strike)
        
        # Return closest to ATM from valid strikes
        if valid_strikes:
            atm = round(entry_spot / 100) * 100
            return min(valid_strikes, key=lambda x: abs(x - atm))
        return round(entry_spot / 100) * 100
    
    elif strike_type == StrikeSelectionType.STRADDLE_WIDTH:
        # For straddle width strategy
        width = strike_selection.value
        atm = round(entry_spot / 100) * 100
        
        if option_type == OptionType.CE:
            return atm + width
        else:  # PE
            return atm - width
    
    elif strike_type == StrikeSelectionType.STRIKE_TYPE:
        # ATM/ITM/OTM with number of strikes
        atm = round(entry_spot / 100) * 100
        strikes_away = int(strike_selection.value or 1)
        strike_type_val = strike_selection.strike_type
        
        if strike_type_val == "ATM":
            return atm
        elif strike_type_val == "OTM":
            if option_type == OptionType.CE:
                return atm + (strikes_away * 100)
            else:  # PE
                return atm - (strikes_away * 100)
        elif strike_type_val == "ITM":
            if option_type == OptionType.CE:
                return atm - (strikes_away * 100)
            else:  # PE
                return atm + (strikes_away * 100)
    
    elif strike_type == StrikeSelectionType.OTM_PERCENT:
        # OTM by percentage
        pct = strike_selection.value
        if option_type == OptionType.CE:
            return round((entry_spot * (1 + pct / 100)) / 100) * 100
        else:  # PE
            return round((entry_spot * (1 - pct / 100)) / 100) * 100
    
    elif strike_type == StrikeSelectionType.ITM_PERCENT:
        # ITM by percentage
        pct = strike_selection.value
        if option_type == OptionType.CE:
            return round((entry_spot * (1 - pct / 100)) / 100) * 100
        else:  # PE
            return round((entry_spot * (1 + pct / 100)) / 100) * 100
    
    # Default: ATM
    return round(entry_spot / 100) * 100


def check_entry_condition(
    current_date: datetime,
    expiry_date: datetime,
    entry_condition,
    spot_df: pd.DataFrame
) -> bool:
    """Check if entry condition is met"""
    
    if entry_condition.type == EntryTimeType.DAYS_BEFORE_EXPIRY:
        days_diff = (expiry_date - current_date).days
        return days_diff == entry_condition.days_before_expiry
    
    elif entry_condition.type == EntryTimeType.MARKET_OPEN:
        # First trading day of the week/period
        return True  # Simplified - needs time checking logic
    
    elif entry_condition.type == EntryTimeType.MARKET_CLOSE:
        # Last hour of trading
        return True  # Simplified
    
    elif entry_condition.type == EntryTimeType.SPECIFIC_TIME:
        # Check specific time (needs intraday data)
        return True  # Simplified
    
    return True


def check_exit_condition(
    current_date: datetime,
    expiry_date: datetime,
    exit_condition,
    entry_price: float,
    current_price: float,
    position: PositionType
) -> bool:
    """Check if exit condition is met"""
    
    if exit_condition.type == ExitTimeType.DAYS_BEFORE_EXPIRY:
        days_diff = (expiry_date - current_date).days
        return days_diff == exit_condition.days_before_expiry
    
    elif exit_condition.type == ExitTimeType.EXPIRY:
        return current_date == expiry_date
    
    elif exit_condition.type == ExitTimeType.STOP_LOSS:
        sl_pct = exit_condition.stop_loss_percent / 100
        
        if position == PositionType.SELL:
            # For sell, stop loss hits when price rises
            loss_pct = (current_price - entry_price) / entry_price
            return loss_pct >= sl_pct
        else:  # BUY
            # For buy, stop loss hits when price falls
            loss_pct = (entry_price - current_price) / entry_price
            return loss_pct >= sl_pct
    
    elif exit_condition.type == ExitTimeType.TARGET:
        target_pct = exit_condition.target_percent / 100
        
        if position == PositionType.SELL:
            # For sell, target hits when price falls
            profit_pct = (entry_price - current_price) / entry_price
            return profit_pct >= target_pct
        else:  # BUY
            # For buy, target hits when price rises
            profit_pct = (current_price - entry_price) / entry_price
            return profit_pct >= target_pct
    
    return False


def run_generic_multi_leg(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Execute generic multi-leg strategy
    Uses EXACT calculation logic from analyse_bhavcopy_02-01-2026.py
    """
    
    strategy_def: StrategyDefinition = params['strategy']
    index_name = strategy_def.index
    
    # Load data (EXACT same as existing engines)
    spot_df = get_strike_data(index_name, params["from_date"], params["to_date"])
    weekly_exp = load_expiry(index_name, "weekly")
    monthly_exp = load_expiry(index_name, "monthly")
    # base2 = load_base2()  # Disabled - base2 filter not used
    
    # Base2 Filter (EXACT same logic) - DISABLED
    # if strategy_def.use_base2_filter:
    #     mask = pd.Series(False, index=spot_df.index)
    #     for _, row in base2.iterrows():
    #         mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    #     
    #     if strategy_def.inverse_base2:
    #         mask = ~mask  # Invert for v6-style strategies
    #     
    #     spot_df = spot_df[mask].reset_index(drop=True)
    
    trades = []
    
    # Weekly Expiry Loop (EXACT same as existing engines)
    for w in range(len(weekly_exp)):
        prev_expiry = weekly_exp.iloc[w]['Previous Expiry']
        curr_expiry = weekly_exp.iloc[w]['Current Expiry']
        
        # Future expiry default (EXACT copy from v1)
        curr_monthly_expiry = monthly_exp[
            monthly_exp['Current Expiry'] >= curr_expiry
        ].sort_values(by='Current Expiry').reset_index(drop=True)
        
        if curr_monthly_expiry.empty:
            continue
        
        fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        
        # Filter window
        filtered_data = spot_df[
            (spot_df['Date'] >= prev_expiry) &
            (spot_df['Date'] <= curr_expiry)
        ].sort_values(by='Date').reset_index(drop=True)
        
        if len(filtered_data) < 2:
            continue
        
        # Re-entry intervals (uses build_intervals from base.py)
        re_entry_type = 0  # Default: no re-entry
        if strategy_def.re_entry_mode == ReEntryMode.UP_MOVE:
            re_entry_type = 1
        elif strategy_def.re_entry_mode == ReEntryMode.DOWN_MOVE:
            re_entry_type = 2
        elif strategy_def.re_entry_mode == ReEntryMode.EITHER_MOVE:
            re_entry_type = 3
        
        intervals = build_intervals(
            filtered_data,
            re_entry_type,
            strategy_def.re_entry_percent or 1.0
        )
        
        if not intervals:
            continue
        
        interval_df = pd.DataFrame(intervals, columns=['From', 'To'])
        
        # Trade loop over intervals
        for i in range(len(interval_df)):
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if fromDate == toDate:
                continue
            
            # BASE START OVERRIDE (EXACT copy from v1)
            is_base_start = (base2['Start'] == fromDate).any()
            if is_base_start:
                override_df = monthly_exp[
                    monthly_exp['Current Expiry'] > fromDate
                ].reset_index(drop=True)
                if len(override_df) > 1:
                    fut_expiry = override_df.iloc[1]['Current Expiry']
            
            # Entry / Exit spots
            entry_row = filtered_data[filtered_data['Date'] == fromDate]
            exit_row = filtered_data[filtered_data['Date'] == toDate]
            
            if entry_row.empty:
                continue
            
            entry_spot = entry_row.iloc[0]['Close']
            exit_spot = exit_row.iloc[0]['Close'] if not exit_row.empty else None
            
            # Load bhavcopy
            bhav_entry = load_bhavcopy(fromDate.strftime('%Y-%m-%d'))
            bhav_exit = load_bhavcopy(toDate.strftime('%Y-%m-%d'))
            
            if bhav_entry is None or bhav_exit is None:
                continue
            
            # Process each leg
            total_pnl = 0
            leg_details = {}
            all_legs_valid = True
            
            for leg_idx, leg in enumerate(strategy_def.legs):
                leg_num = leg.leg_number
                
                if leg.instrument == InstrumentType.OPTION:
                    # Get available strikes
                    available_strikes = bhav_entry[
                        (bhav_entry['Instrument'] == "OPTIDX") &
                        (bhav_entry['Symbol'] == index_name) &
                        (bhav_entry['OptionType'] == leg.option_type.value) &
                        (bhav_entry['TurnOver'] > 0)
                    ]['StrikePrice'].unique()
                    
                    # Calculate strike
                    selected_strike = calculate_strike_from_selection(
                        entry_spot,
                        leg.strike_selection,
                        leg.option_type,
                        available_strikes,
                        bhav_entry,
                        index_name,
                        curr_expiry
                    )
                    
                    # Get entry price (EXACT logic from v1)
                    if leg.strike_selection.value >= 0:
                        call_entry_data = bhav_entry[
                            (bhav_entry['Instrument'] == "OPTIDX") &
                            (bhav_entry['Symbol'] == index_name) &
                            (bhav_entry['OptionType'] == leg.option_type.value) &
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
                        call_entry_data = bhav_entry[
                            (bhav_entry['Instrument'] == "OPTIDX") &
                            (bhav_entry['Symbol'] == index_name) &
                            (bhav_entry['OptionType'] == leg.option_type.value) &
                            (
                                (bhav_entry['ExpiryDate'] == curr_expiry) |
                                (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                            ) &
                            (bhav_entry['StrikePrice'] <= selected_strike) &
                            (bhav_entry['TurnOver'] > 0) &
                            (bhav_entry['StrikePrice'] % 100 == 0)
                        ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                    
                    if call_entry_data.empty:
                        all_legs_valid = False
                        break
                    
                    selected_strike = call_entry_data.iloc[0]['StrikePrice']
                    entry_price = call_entry_data.iloc[0]['Close']
                    entry_turnover = call_entry_data.iloc[0]['TurnOver']
                    
                    # Get exit price (EXACT logic from v1)
                    call_exit_data = bhav_exit[
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
                    
                    if call_exit_data.empty:
                        all_legs_valid = False
                        break
                    
                    exit_price = call_exit_data.iloc[0]['Close']
                    exit_turnover = call_exit_data.iloc[0]['TurnOver']
                    
                    # Calculate P&L (EXACT formula from v1 line 664)
                    if leg.position == PositionType.SELL:
                        leg_pnl = round(entry_price - exit_price, 2)
                    else:  # BUY
                        leg_pnl = round(exit_price - entry_price, 2)
                    
                    # Store leg details
                    leg_details.update({
                        f"Leg{leg_num}_Instrument": f"{leg.option_type.value}",
                        f"Leg{leg_num}_Position": leg.position.value,
                        f"Leg{leg_num}_Strike": selected_strike,
                        f"Leg{leg_num}_Expiry": curr_expiry,
                        f"Leg{leg_num}_EntryPrice": entry_price,
                        f"Leg{leg_num}_EntryTurnover": entry_turnover,
                        f"Leg{leg_num}_ExitPrice": exit_price,
                        f"Leg{leg_num}_ExitTurnover": exit_turnover,
                        f"Leg{leg_num}_P&L": leg_pnl,
                    })
                    
                    total_pnl += leg_pnl
                
                elif leg.instrument == InstrumentType.FUTURE:
                    # Future leg (EXACT logic from v1 lines 618-640)
                    fut_entry_data = bhav_entry[
                        (bhav_entry['Instrument'] == "FUTIDX") &
                        (bhav_entry['Symbol'] == index_name) &
                        (bhav_entry['ExpiryDate'].dt.month == fut_expiry.month) &
                        (bhav_entry['ExpiryDate'].dt.year == fut_expiry.year)
                    ]
                    fut_exit_data = bhav_exit[
                        (bhav_exit['Instrument'] == "FUTIDX") &
                        (bhav_exit['Symbol'] == index_name) &
                        (bhav_exit['ExpiryDate'].dt.month == fut_expiry.month) &
                        (bhav_exit['ExpiryDate'].dt.year == fut_expiry.year)
                    ]
                    
                    if fut_entry_data.empty or fut_exit_data.empty:
                        all_legs_valid = False
                        break
                    
                    fut_entry_price = fut_entry_data.iloc[0]['Close']
                    fut_exit_price = fut_exit_data.iloc[0]['Close']
                    
                    # Calculate P&L (EXACT formula from v1 line 662)
                    if leg.position == PositionType.BUY:
                        fut_pnl = round(fut_exit_price - fut_entry_price, 2)
                    else:  # SELL
                        fut_pnl = round(fut_entry_price - fut_exit_price, 2)
                    
                    leg_details.update({
                        f"Leg{leg_num}_Instrument": "FUT",
                        f"Leg{leg_num}_Position": leg.position.value,
                        f"Leg{leg_num}_Expiry": fut_expiry,
                        f"Leg{leg_num}_EntryPrice": fut_entry_price,
                        f"Leg{leg_num}_ExitPrice": fut_exit_price,
                        f"Leg{leg_num}_P&L": fut_pnl,
                    })
                    
                    total_pnl += fut_pnl
            
            if not all_legs_valid:
                continue
            
            # Create trade record (EXACT structure from v1 lines 644-666)
            trade_record = {
                "Entry Date": fromDate,
                "Exit Date": toDate,
                "Entry Spot": entry_spot,
                "Exit Spot": exit_spot,
                "Spot P&L": round(exit_spot - entry_spot, 2) if exit_spot else None,
                "Net P&L": round(total_pnl, 2),
            }
            trade_record.update(leg_details)
            
            trades.append(trade_record)
    
    # Final output (EXACT same as existing engines)
    if not trades:
        return pd.DataFrame(), {}, {}
    
    df = pd.DataFrame(trades)
    df, summary = compute_analytics(df)  # Uses EXACT formulas from validation file
    pivot = build_pivot(df, 'Entry Date')
    
    return df, summary, pivot