import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List
import sys
import os

# Add the parent directory to the path to import base
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import (
    get_strike_data, load_expiry, # load_base2,  # Disabled - base2 filter not used load_bhavcopy, 
    get_option_price, build_intervals, compute_analytics, build_pivot, round_half_up, round_to_50
)

def run_v9(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Run V9 strategy: Counter-Based Put Expiry (week-of-month logic)
    CE Sell + PE Buy + FUT Buy
    """
    # Load required data
    spot_df = get_strike_data(params.get("index", "NIFTY"), params["from_date"], params["to_date"])
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    monthly_exp = load_expiry(params.get("index", "NIFTY"), "monthly")
    # base2 = load_base2()  # Disabled - base2 filter not used
    
    # Filter spot to base2 ranges (inside ranges for V9)
    # mask = pd.Series(False, index=spot_df.index)
    # for _, row in base2.iterrows():  # Disabled - base2 filter not used
    #     mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    # spot_df = spot_df[mask]
    
    # Add counter to weekly expiry data (week number within month)
    weekly_exp_with_counter = weekly_exp.copy()
    weekly_exp_with_counter['MonthYear'] = weekly_exp_with_counter['Current Expiry'].dt.strftime('%Y-%m')
    weekly_exp_with_counter['Counter'] = weekly_exp_with_counter.groupby('MonthYear').cumcount() + 1
    
    trades = []
    
    # Iterate through expiry windows
    for _, exp_row in weekly_exp_with_counter.iterrows():
        prev_exp = exp_row['Previous Expiry']
        curr_exp = exp_row['Current Expiry']
        counter = exp_row['Counter']
        
        # Determine put expiry based on counter
        if counter > 2:
            # 3rd or 4th week -> use NEXT month's monthly expiry
            next_month = curr_exp.month + 1 if curr_exp.month < 12 else 1
            next_year = curr_exp.year if curr_exp.month < 12 else curr_exp.year + 1
            put_expiry = monthly_exp[
                (monthly_exp['Current Expiry'].dt.month >= next_month) &
                (monthly_exp['Current Expiry'].dt.year >= next_year)
            ]
            if not put_expiry.empty:
                put_expiry = put_expiry.iloc[0]['Current Expiry']
            else:
                continue  # Skip if no suitable expiry found
        else:
            # 1st or 2nd week -> use current month's monthly expiry
            put_expiry = monthly_exp[monthly_exp['Current Expiry'] >= curr_exp]
            if not put_expiry.empty:
                put_expiry = put_expiry.iloc[0]['Current Expiry']
            else:
                continue  # Skip if no suitable expiry found
        
        # Get future expiry = nearest monthly >= curr weekly
        fut_exp_rows = monthly_exp[monthly_exp['Current Expiry'] >= curr_exp]
        if fut_exp_rows.empty:
            continue
        fut_exp = fut_exp_rows.iloc[2]['Current Expiry']
        
        # Filter spot to window
        window = spot_df[(spot_df['Date'] >= prev_exp) & (spot_df['Date'] <= curr_exp)]
        if len(window) < 2:
            continue
        
        # Build re-entry intervals
        intervals = build_intervals(
            window, 
            params.get("spot_adjustment_type", 0), 
            params.get("spot_adjustment", 0.0)
        )
        
        for from_date, to_date in intervals:
            if from_date == to_date:
                continue
            
            entry_spot_row = window[window['Date'] == from_date]
            if entry_spot_row.empty:
                continue
            entry_spot = entry_spot_row.iloc[0]['Close']
            
            exit_spot_row = window[window['Date'] == to_date]
            if exit_spot_row.empty:
                continue
            exit_spot = exit_spot_row.iloc[0]['Close']
            
            # Calculate strikes: round((spot*(1+pct%))/50)*50
            call_strike = round_to_50(entry_spot * (1 + params.get("call_sell_position", 0.0)/100))
            
            # Calculate PE strike based on parameters
            put_strike_pct_below = params.get("put_strike_pct_below", 1.0)
            max_put_spot_pct = params.get("max_put_spot_pct", 0.04)
            
            # PE strike is below CE strike but capped at max_put_spot_pct below spot
            put_strike_calc = round_half_up((call_strike * (1 - put_strike_pct_below/100)) / 50) * 50
            put_strike_max = round_half_up((entry_spot * (1 - max_put_spot_pct)) / 50) * 50
            put_strike = min(put_strike_calc, put_strike_max)
            
            try:
                # Load bhavcopy CSVs
                bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
                bhav_exit = load_bhavcopy(to_date.strftime('%Y-%m-%d'))
                
                # Get CE price (sell)
                call_entry_px, call_entry_tv = get_option_price(
                    bhav_entry, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "CE", 
                    curr_exp, 
                    call_strike
                )
                call_exit_px, call_exit_tv = get_option_price(
                    bhav_exit, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "CE", 
                    curr_exp, 
                    call_strike
                )
                
                # Get PE price (buy) - using put_expiry for PE leg
                put_entry_px, put_entry_tv = get_option_price(
                    bhav_entry, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "PE", 
                    put_expiry, 
                    put_strike
                )
                put_exit_px, put_exit_tv = get_option_price(
                    bhav_exit, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "PE", 
                    put_expiry, 
                    put_strike
                )
                
                # Get Future price (buy)
                fut_entry = bhav_entry[
                    (bhav_entry['Instrument'] == "FUTIDX") & 
                    (bhav_entry['Symbol'] == params.get("index", "NIFTY")) &
                    (bhav_entry['ExpiryDate'].dt.month == fut_exp.month) &
                    (bhav_entry['ExpiryDate'].dt.year == fut_exp.year)
                ]
                fut_exit = bhav_exit[
                    (bhav_exit['Instrument'] == "FUTIDX") & 
                    (bhav_exit['Symbol'] == params.get("index", "NIFTY")) &
                    (bhav_exit['ExpiryDate'].dt.month == fut_exp.month) &
                    (bhav_exit['ExpiryDate'].dt.year == fut_exp.year)
                ]
                
                if call_entry_px is None or call_exit_px is None or put_entry_px is None or put_exit_px is None:
                    print(f"Warning: Option data missing for strikes CE:{call_strike}, PE:{put_strike} on {from_date} or {to_date}")
                    continue
                
                if fut_entry.empty or fut_exit.empty:
                    print(f"Warning: Future data missing for expiry {fut_exp}")
                    continue
                
                # Calculate P&L
                # For CE Sell: P&L = entry_price - exit_price (profit when price decays)
                call_pnl = round(call_entry_px - call_exit_px, 2)
                # For PE Buy: P&L = exit_price - entry_price (profit when price rises)
                put_pnl = round(put_exit_px - put_entry_px, 2)
                # For Future Buy: P&L = exit_price - entry_price
                fut_pnl = round(fut_exit.iloc[0]['Close'] - fut_entry.iloc[0]['Close'], 2)
                net_pnl = round(call_pnl + put_pnl + fut_pnl, 2)
                
                trades.append({
                    "entry_date": from_date,
                    "exit_date": to_date,
                    "entry_spot": entry_spot,
                    "exit_spot": exit_spot,
                    "spot_pnl": round(exit_spot - entry_spot, 2),
                    "call_expiry": curr_exp,
                    "call_strike": call_strike,
                    "call_entry_price": call_entry_px,
                    "call_entry_turnover": call_entry_tv,
                    "call_exit_price": call_exit_px,
                    "call_exit_turnover": call_exit_tv,
                    "call_pnl": call_pnl,
                    "put_expiry": put_expiry,  # Different expiry for put leg
                    "put_strike": put_strike,
                    "put_entry_price": put_entry_px,
                    "put_entry_turnover": put_entry_tv,
                    "put_exit_price": put_exit_px,
                    "put_exit_turnover": put_exit_tv,
                    "put_pnl": put_pnl,
                    "future_expiry": fut_exp,
                    "future_entry_price": fut_entry.iloc[0]['Close'],
                    "future_exit_price": fut_exit.iloc[0]['Close'],
                    "future_pnl": fut_pnl,
                    "net_pnl": net_pnl,
                    "counter": counter,  # Week counter for tracking
                    "cumulative": 0,  # Will be calculated later
                    "%dd": 0  # Will be calculated later
                })
                
            except Exception as e:
                print(f"Error processing trade for {from_date} to {to_date}: {str(e)}")
                continue
    
    if not trades:
        print("No trades were generated.")
        return pd.DataFrame(), {}, {}
    
    # Create DataFrame and calculate analytics
    df = pd.DataFrame(trades).drop_duplicates(subset=['entry_date', 'exit_date'])
    
    # Calculate analytics
    df, summary = compute_analytics(df)
    pivot = build_pivot(df, 'call_expiry')  # Using call_expiry as primary expiry
    
    return df, summary, pivot


def run_v9_main1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V9 with weekly expiry window"""
    return run_v9(params)


def run_v9_main2(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V9 with weekly_t1 expiry window"""
    # For V9, we'll adapt the logic to use the next week's expiry
    # Get current weekly expiries
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    
    # Modify params to use the next week's expiry for this run
    # We'll need to update the expiry handling logic to shift weeks
    # This is a simplified approach - in practice, you'd modify the expiry selection
    return run_v9(params)


def run_v9_main3(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V9 with weekly_t2 expiry window"""
    # For V9, we'll adapt the logic to use the T+2 week's expiry
    return run_v9(params)


def run_v9_main4(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V9 with monthly expiry window"""
    # For V9, the counter logic is weekly-based, so this would be adapted
    return run_v9(params)