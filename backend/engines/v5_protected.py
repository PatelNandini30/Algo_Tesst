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

def run_v5_call(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Run V5 strategy: CE Sell + Optional Protective CE Buy
    """
    # Load required data
    spot_df = get_strike_data(params.get("index", "NIFTY"), params["from_date"], params["to_date"])
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    monthly_exp = load_expiry(params.get("index", "NIFTY"), "monthly")
    # base2 = load_base2()  # Disabled - base2 filter not used
    
    # Filter spot to base2 ranges (inside ranges for V5)
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
            
            # Calculate strikes
            call_strike = round_to_50(entry_spot * (1 + params.get("call_sell_position", 0.0)/100))
            
            # Calculate protective strike (if protection is enabled)
            protection_enabled = params.get("protection", False)
            protective_strike = None
            if protection_enabled:
                protection_pct = params.get("protection_pct", 1.0)
                protective_strike = round_half_up((call_strike * (1 + protection_pct/100)) / 50) * 50
            
            try:
                # Load bhavcopy CSVs
                bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
                bhav_exit = load_bhavcopy(to_date.strftime('%Y-%m-%d'))
                
                # Get main CE price
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
                    print(f"Warning: Call data missing for strike {call_strike} on {from_date} or {to_date}")
                    continue
                
                # Get protective CE price (if protection enabled)
                protective_entry_px = None
                protective_exit_px = None
                if protection_enabled and protective_strike:
                    protective_entry_px, _ = get_option_price(
                        bhav_entry, 
                        params.get("index", "NIFTY"), 
                        "OPTIDX", 
                        "CE", 
                        curr_exp, 
                        protective_strike
                    )
                    protective_exit_px, _ = get_option_price(
                        bhav_exit, 
                        params.get("index", "NIFTY"), 
                        "OPTIDX", 
                        "CE", 
                        curr_exp, 
                        protective_strike
                    )
                    
                    if protective_entry_px is None or protective_exit_px is None:
                        print(f"Warning: Protective call data missing for strike {protective_strike} on {from_date} or {to_date}")
                        continue
                
                # Calculate P&L
                # For CE Sell: P&L = entry_price - exit_price (profit when price decays)
                call_pnl = round(call_entry_px - call_exit_px, 2)
                
                # For Protective CE Buy: P&L = exit_price - entry_price (profit when price rises)
                protective_pnl = 0
                if protection_enabled and protective_strike:
                    protective_pnl = round(protective_exit_px - protective_entry_px, 2)
                
                net_pnl = round(call_pnl + protective_pnl, 2)
                
                trade_record = {
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
                    "net_pnl": net_pnl,
                    "cumulative": 0,  # Will be calculated later
                    "%dd": 0  # Will be calculated later
                }
                
                if protection_enabled:
                    trade_record.update({
                        "protective_strike": protective_strike,
                        "protective_entry_price": protective_entry_px,
                        "protective_exit_price": protective_exit_px,
                        "protective_pnl": protective_pnl
                    })
                
                trades.append(trade_record)
                
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


def run_v5_put(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Run V5 strategy: PE Sell + Optional Protective PE Buy
    """
    # Load required data
    spot_df = get_strike_data(params.get("index", "NIFTY"), params["from_date"], params["to_date"])
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    monthly_exp = load_expiry(params.get("index", "NIFTY"), "monthly")
    # base2 = load_base2()  # Disabled - base2 filter not used
    
    # Filter spot to base2 ranges (inside ranges for V5)
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
            
            # Calculate strikes
            put_strike = round_to_50(entry_spot * (1 + params.get("put_sell_position", 0.0)/100))
            
            # Calculate protective strike (if protection is enabled)
            protection_enabled = params.get("protection", False)
            protective_strike = None
            if protection_enabled:
                protection_pct = params.get("protection_pct", 1.0)
                # For PE protection, the protective leg is usually at a lower strike (more ITM)
                protective_strike = round_half_up((put_strike * (1 - protection_pct/100)) / 50) * 50
            
            try:
                # Load bhavcopy CSVs
                bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
                bhav_exit = load_bhavcopy(to_date.strftime('%Y-%m-%d'))
                
                # Get main PE price
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
                
                if put_entry_px is None or put_exit_px is None:
                    print(f"Warning: Put data missing for strike {put_strike} on {from_date} or {to_date}")
                    continue
                
                # Get protective PE price (if protection enabled)
                protective_entry_px = None
                protective_exit_px = None
                if protection_enabled and protective_strike:
                    protective_entry_px, _ = get_option_price(
                        bhav_entry, 
                        params.get("index", "NIFTY"), 
                        "OPTIDX", 
                        "PE", 
                        curr_exp, 
                        protective_strike
                    )
                    protective_exit_px, _ = get_option_price(
                        bhav_exit, 
                        params.get("index", "NIFTY"), 
                        "OPTIDX", 
                        "PE", 
                        curr_exp, 
                        protective_strike
                    )
                    
                    if protective_entry_px is None or protective_exit_px is None:
                        print(f"Warning: Protective put data missing for strike {protective_strike} on {from_date} or {to_date}")
                        continue
                
                # Calculate P&L
                # For PE Sell: P&L = entry_price - exit_price (profit when price decays)
                put_pnl = round(put_entry_px - put_exit_px, 2)
                
                # For Protective PE Buy: P&L = exit_price - entry_price (profit when price rises)
                protective_pnl = 0
                if protection_enabled and protective_strike:
                    protective_pnl = round(protective_exit_px - protective_entry_px, 2)
                
                net_pnl = round(put_pnl + protective_pnl, 2)
                
                trade_record = {
                    "entry_date": from_date,
                    "exit_date": to_date,
                    "entry_spot": entry_spot,
                    "exit_spot": exit_spot,
                    "spot_pnl": round(exit_spot - entry_spot, 2),
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
                }
                
                if protection_enabled:
                    trade_record.update({
                        "protective_strike": protective_strike,
                        "protective_entry_price": protective_entry_px,
                        "protective_exit_price": protective_exit_px,
                        "protective_pnl": protective_pnl
                    })
                
                trades.append(trade_record)
                
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
    pivot = build_pivot(df, 'put_expiry')
    
    return df, summary, pivot


def run_v5_call_main1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V5 Call with weekly expiry window"""
    params['expiry_window'] = 'weekly_expiry'
    return run_v5_call(params)


def run_v5_call_main2(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V5 Call with weekly_t1 expiry window"""
    params['expiry_window'] = 'weekly_t1'
    return run_v5_call(params)


def run_v5_put_main1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V5 Put with weekly expiry window"""
    params['expiry_window'] = 'weekly_expiry'
    return run_v5_put(params)


def run_v5_put_main2(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V5 Put with weekly_t1 expiry window"""
    params['expiry_window'] = 'weekly_t1'
    return run_v5_put(params)