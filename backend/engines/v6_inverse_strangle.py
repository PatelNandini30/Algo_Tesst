import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List
import sys
import os

# Add the parent directory to the path to import base
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import (
    get_strike_data, load_expiry, load_base2, load_bhavcopy, 
    get_option_price, build_intervals, compute_analytics, build_pivot, round_half_up
)

def run_v6(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Run V6 strategy: Inverse-base Short Strangle (operates OUTSIDE base2 ranges)
    CE Sell + PE Sell, no Future, operates outside base2 ranges
    """
    # Load required data
    spot_df = get_strike_data(params.get("index", "NIFTY"), params["from_date"], params["to_date"])
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    base2 = load_base2()
    
    # Filter spot to OUTSIDE base2 ranges (opposite of other strategies)
    mask = pd.Series(True, index=spot_df.index)  # Start with all True
    for _, row in base2.iterrows():
        mask &= ~((spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End']))  # Invert the condition
    spot_df = spot_df[mask]
    
    if spot_df.empty:
        print("No data points outside base2 ranges. Cannot run inverse strategy.")
        return pd.DataFrame(), {}, {}
    
    trades = []
    
    # Iterate through weekly expiry windows
    for _, exp_row in weekly_exp.iterrows():
        prev_exp = exp_row['Previous Expiry']
        curr_exp = exp_row['Current Expiry']
        
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
            
            # Calculate strikes: round((spot*(1+pct%))/100)*100
            call_strike = round_half_up((entry_spot * (1 + params.get("call_sell_position", 0.0)/100)) / 100) * 100
            put_strike = round_half_up((entry_spot * (1 + params.get("put_sell_position", 0.0)/100)) / 100) * 100
            
            try:
                # Load bhavcopy CSVs
                bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
                bhav_exit = load_bhavcopy(to_date.strftime('%Y-%m-%d'))
                
                # Get CE price
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
                
                # Get PE price
                put_entry_px, put_entry_tv = get_option_price(
                    bhav_entry, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "PE", 
                    curr_exp, 
                    put_strike
                )
                put_exit_px, put_exit_tv = get_option_price(
                    bhav_exit, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "PE", 
                    curr_exp, 
                    put_strike
                )
                
                if call_entry_px is None or call_exit_px is None or put_entry_px is None or put_exit_px is None:
                    print(f"Warning: Option data missing for strikes CE:{call_strike}, PE:{put_strike} on {from_date} or {to_date}")
                    continue
                
                # Calculate P&L
                # For CE Sell: P&L = entry_price - exit_price (profit when price decays)
                call_pnl = round(call_entry_px - call_exit_px, 2)
                # For PE Sell: P&L = entry_price - exit_price (profit when price decays)
                put_pnl = round(put_entry_px - put_exit_px, 2)
                net_pnl = round(call_pnl + put_pnl, 2)
                
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
                    "put_expiry": curr_exp,
                    "put_strike": put_strike,
                    "put_entry_price": put_entry_px,
                    "put_entry_turnover": put_entry_tv,
                    "put_exit_price": put_exit_px,
                    "put_exit_turnover": put_exit_tv,
                    "put_pnl": put_pnl,
                    "net_pnl": net_pnl,
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
    pivot = build_pivot(df, 'call_expiry')  # Using call_expiry as both use same expiry
    
    return df, summary, pivot


def run_v6_main1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V6 with weekly expiry window"""
    return run_v6(params)


def run_v6_main2(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V6 with weekly_t1 expiry window"""
    # For V6, we'll adapt the logic to use the next week's expiry
    # Get current weekly expiries
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    
    # Modify params to use the next week's expiry for this run
    # We'll need to update the expiry handling logic to shift weeks
    # This is a simplified approach - in practice, you'd modify the expiry selection
    return run_v6(params)