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

def run_v3(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Run V3 strategy: Strike-Breach Re-entry (roll call expiry when spot breaches strike * (1+pct_diff))
    Handles all expiry windows (weekly_expiry, weekly_t1, weekly_t2, monthly_expiry, monthly_t1)
    """
    # Load required data
    spot_df = get_strike_data(params.get("index", "NIFTY"), params["from_date"], params["to_date"])
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    monthly_exp = load_expiry(params.get("index", "NIFTY"), "monthly")
    # base2 = load_base2()  # Disabled - base2 filter not used
    
    # Filter spot to base2 ranges (inside ranges for V3)
    # mask = pd.Series(False, index=spot_df.index)
    # for _, row in base2.iterrows():  # Disabled - base2 filter not used
    #     mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    # spot_df = spot_df[mask]
    
    trades = []
    
    # Determine expiry type
    expiry_type = params.get("expiry_window", "weekly_expiry")
    
    # Select appropriate expiry dataframe based on window
    if "monthly" in expiry_type:
        exp_df = monthly_exp
    else:
        exp_df = weekly_exp
    
    # Iterate through expiry windows
    for _, exp_row in exp_df.iterrows():
        if "weekly" in expiry_type:
            if expiry_type == "weekly_expiry":
                prev_exp = exp_row['Previous Expiry']
                curr_exp = exp_row['Current Expiry']
            elif expiry_type == "weekly_t1":
                curr_exp = exp_row['Current Expiry']
                # Get next expiry for T+1
                next_row_idx = exp_df.index[exp_df['Current Expiry'] == curr_exp].tolist()[0] + 1
                if next_row_idx < len(exp_df):
                    next_exp = exp_df.iloc[next_row_idx]['Current Expiry']
                    prev_exp = curr_exp
                    curr_exp = next_exp
                else:
                    continue
            elif expiry_type == "weekly_t2":
                curr_exp = exp_row['Current Expiry']
                # Get next next expiry for T+2
                curr_idx = exp_df.index[exp_df['Current Expiry'] == curr_exp].tolist()[0]
                next_idx = curr_idx + 2
                if next_idx < len(exp_df):
                    next_exp = exp_df.iloc[next_idx]['Current Expiry']
                    prev_exp = curr_exp
                    curr_exp = next_exp
                else:
                    continue
            else:
                prev_exp = exp_row['Previous Expiry']
                curr_exp = exp_row['Current Expiry']
        else:  # monthly
            if expiry_type == "monthly_expiry":
                prev_exp = exp_row['Previous Expiry']
                curr_exp = exp_row['Current Expiry']
            elif expiry_type == "monthly_t1":
                curr_exp = exp_row['Current Expiry']
                # Get next monthly expiry
                next_row_idx = exp_df.index[exp_df['Current Expiry'] == curr_exp].tolist()[0] + 1
                if next_row_idx < len(exp_df):
                    next_exp = exp_df.iloc[next_row_idx]['Current Expiry']
                    prev_exp = curr_exp
                    curr_exp = next_exp
                else:
                    continue
            else:
                prev_exp = exp_row['Previous Expiry']
                curr_exp = exp_row['Current Expiry']
        
        # Get future expiry = nearest monthly >= curr weekly/monthly
        fut_exp_rows = monthly_exp[monthly_exp['Current Expiry'] >= curr_exp]
        if fut_exp_rows.empty:
            continue
        # Check if there are enough rows to access iloc[2]
        if len(fut_exp_rows) < 3:
            # If not enough rows, use the last available row
            fut_exp = fut_exp_rows.iloc[-1]['Current Expiry']
        else:
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
            
            # Calculate strike: round((spot*(1+pct%))/50)*50
            call_strike = round_to_50(entry_spot * (1 + params.get("call_sell_position", 0.0)/100))
            
            # Check for strike breach during the period
            breach_occurred = False
            breach_date = None
            breach_spot = None
            
            # Get all dates between from_date and to_date in the window
            trading_days = window[(window['Date'] > from_date) & (window['Date'] <= to_date)]
            
            pct_diff = params.get("pct_diff", 0.3)  # Default to 0.3%
            
            for _, day_row in trading_days.iterrows():
                day_spot = day_row['Close']
                # Check if spot breaches the strike by more than pct_diff
                if call_strike > 0:  # Avoid division by zero
                    if day_spot > call_strike:
                        # Spot is above call strike
                        breach_threshold = call_strike * (1 + pct_diff / 100)
                        if day_spot >= breach_threshold:
                            breach_occurred = True
                            breach_date = day_row['Date']
                            breach_spot = day_row['Close']
                            break
                    else:
                        # Spot is below call strike
                        breach_threshold = call_strike * (1 - pct_diff / 100)
                        if day_spot <= breach_threshold:
                            breach_occurred = True
                            breach_date = day_row['Date']
                            breach_spot = day_row['Close']
                            break
            
            try:
                # Load bhavcopy CSVs
                bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
                
                # Determine exit date for option pricing
                exit_date_for_pricing = breach_date if breach_occurred else to_date
                bhav_exit = load_bhavcopy(exit_date_for_pricing.strftime('%Y-%m-%d'))
                
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
                
                if call_entry_px is None or call_exit_px is None:
                    print(f"Warning: Call data missing for strike {call_strike} on {from_date} or {exit_date_for_pricing}")
                    continue
                
                # Get Future price
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
                
                if fut_entry.empty or fut_exit.empty:
                    print(f"Warning: Future data missing for expiry {fut_exp}")
                    continue
                
                # Calculate P&L
                # For CE Sell: P&L = entry_price - exit_price (profit when price decays)
                call_pnl = round(call_entry_px - call_exit_px, 2)
                # For Future Buy: P&L = exit_price - entry_price
                fut_pnl = round(fut_exit.iloc[0]['Close'] - fut_entry.iloc[0]['Close'], 2)
                net_pnl = round(call_pnl + fut_pnl, 2)
                
                # Use original to_date for exit_date in the results
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
                    "future_expiry": fut_exp,
                    "future_entry_price": fut_entry.iloc[0]['Close'],
                    "future_exit_price": fut_exit.iloc[0]['Close'],
                    "future_pnl": fut_pnl,
                    "net_pnl": net_pnl,
                    "breach_occurred": breach_occurred,
                    "breach_date": breach_date,
                    "breach_spot": breach_spot,
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
    pivot = build_pivot(df, 'call_expiry')
    
    return df, summary, pivot


def run_v3_main1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V3 with weekly expiry window"""
    params['expiry_window'] = 'weekly_expiry'
    return run_v3(params)


def run_v3_main2(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V3 with weekly_t1 expiry window"""
    params['expiry_window'] = 'weekly_t1'
    return run_v3(params)


def run_v3_main3(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V3 with weekly_t2 expiry window"""
    params['expiry_window'] = 'weekly_t2'
    return run_v3(params)


def run_v3_main4(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V3 with monthly expiry window"""
    params['expiry_window'] = 'monthly_expiry'
    return run_v3(params)


def run_v3_main5(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V3 with monthly_t1 expiry window"""
    params['expiry_window'] = 'monthly_t1'
    return run_v3(params)