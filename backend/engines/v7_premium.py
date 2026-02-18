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

def run_v7(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Run V7 strategy: Premium-Based Strike Selection
    On entry day: find ATM strike, get ATM premium, compute target, walk OTM to find strike
    """
    # Load required data
    spot_df = get_strike_data(params.get("index", "NIFTY"), params["from_date"], params["to_date"])
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    # base2 = load_base2()  # Disabled - base2 filter not used
    
    # Filter spot to base2 ranges (inside ranges for V7)
    # mask = pd.Series(False, index=spot_df.index)
    # for _, row in base2.iterrows():  # Disabled - base2 filter not used
    #     mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    # spot_df = spot_df[mask]
    
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
            
            try:
                # Load bhavcopy CSVs
                bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
                bhav_exit = load_bhavcopy(to_date.strftime('%Y-%m-%d'))
                
                # Find ATM strike and premiums
                atm_strike = round_half_up(entry_spot / 50) * 50
                
                # Get ATM Call and Put prices
                atm_call_px, _ = get_option_price(
                    bhav_entry, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "CE", 
                    curr_exp, 
                    atm_strike
                )
                atm_put_px, _ = get_option_price(
                    bhav_entry, 
                    params.get("index", "NIFTY"), 
                    "OPTIDX", 
                    "PE", 
                    curr_exp, 
                    atm_strike
                )
                
                if atm_call_px is None or atm_put_px is None:
                    print(f"Warning: ATM option data missing for strike {atm_strike} on {from_date}")
                    continue
                
                # Compute target premium based on parameters
                call_premium = params.get("call_premium", True)
                put_premium = params.get("put_premium", True)
                premium_multiplier = params.get("premium_multiplier", 1.0)
                
                if call_premium and put_premium:
                    target = (atm_call_px + atm_put_px) * premium_multiplier
                elif call_premium:
                    target = atm_call_px * premium_multiplier
                else:
                    target = atm_put_px * premium_multiplier
                
                # Determine if we're selecting call or put based on params
                call_sell = params.get("call_sell", True)
                put_sell = params.get("put_sell", True)
                
                # For this strategy, we'll select based on which options to sell
                selected_strike = None
                selected_option_type = None
                selected_entry_px = None
                selected_exit_px = None
                
                if call_sell and put_sell:
                    # Select both call and put based on premium target
                    # Find OTM Call strike (above ATM)
                    call_target_strike = find_strike_by_premium(
                        bhav_entry, bhav_exit, params.get("index", "NIFTY"), 
                        "CE", curr_exp, atm_strike, target, "OTM"
                    )
                    
                    # Find OTM Put strike (below ATM)
                    put_target_strike = find_strike_by_premium(
                        bhav_entry, bhav_exit, params.get("index", "NIFTY"), 
                        "PE", curr_exp, atm_strike, target, "OTM"
                    )
                    
                    if call_target_strike and put_target_strike:
                        # Get prices for both
                        call_entry_px, _ = get_option_price(
                            bhav_entry, params.get("index", "NIFTY"), "OPTIDX", "CE", curr_exp, call_target_strike
                        )
                        call_exit_px, _ = get_option_price(
                            bhav_exit, params.get("index", "NIFTY"), "OPTIDX", "CE", curr_exp, call_target_strike
                        )
                        
                        put_entry_px, _ = get_option_price(
                            bhav_entry, params.get("index", "NIFTY"), "OPTIDX", "PE", curr_exp, put_target_strike
                        )
                        put_exit_px, _ = get_option_price(
                            bhav_exit, params.get("index", "NIFTY"), "OPTIDX", "PE", curr_exp, put_target_strike
                        )
                        
                        if call_entry_px and call_exit_px and put_entry_px and put_exit_px:
                            # Calculate P&L for both legs
                            call_pnl = round(call_entry_px - call_exit_px, 2)  # Sell
                            put_pnl = round(put_entry_px - put_exit_px, 2)    # Sell
                            net_pnl = round(call_pnl + put_pnl, 2)
                            
                            trades.append({
                                "entry_date": from_date,
                                "exit_date": to_date,
                                "entry_spot": entry_spot,
                                "exit_spot": exit_spot,
                                "spot_pnl": round(exit_spot - entry_spot, 2),
                                "call_expiry": curr_exp,
                                "call_strike": call_target_strike,
                                "call_entry_price": call_entry_px,
                                "call_exit_price": call_exit_px,
                                "call_pnl": call_pnl,
                                "put_expiry": curr_exp,
                                "put_strike": put_target_strike,
                                "put_entry_price": put_entry_px,
                                "put_exit_price": put_exit_px,
                                "put_pnl": put_pnl,
                                "net_pnl": net_pnl,
                                "cumulative": 0,  # Will be calculated later
                                "%dd": 0  # Will be calculated later
                            })
                
                elif call_sell:
                    # Select only call based on premium target
                    call_target_strike = find_strike_by_premium(
                        bhav_entry, bhav_exit, params.get("index", "NIFTY"), 
                        "CE", curr_exp, atm_strike, target, "OTM"
                    )
                    
                    if call_target_strike:
                        call_entry_px, _ = get_option_price(
                            bhav_entry, params.get("index", "NIFTY"), "OPTIDX", "CE", curr_exp, call_target_strike
                        )
                        call_exit_px, _ = get_option_price(
                            bhav_exit, params.get("index", "NIFTY"), "OPTIDX", "CE", curr_exp, call_target_strike
                        )
                        
                        if call_entry_px and call_exit_px:
                            call_pnl = round(call_entry_px - call_exit_px, 2)
                            net_pnl = round(call_pnl, 2)
                            
                            trades.append({
                                "entry_date": from_date,
                                "exit_date": to_date,
                                "entry_spot": entry_spot,
                                "exit_spot": exit_spot,
                                "spot_pnl": round(exit_spot - entry_spot, 2),
                                "call_expiry": curr_exp,
                                "call_strike": call_target_strike,
                                "call_entry_price": call_entry_px,
                                "call_exit_price": call_exit_px,
                                "call_pnl": call_pnl,
                                "net_pnl": net_pnl,
                                "cumulative": 0,  # Will be calculated later
                                "%dd": 0  # Will be calculated later
                            })
                
                elif put_sell:
                    # Select only put based on premium target
                    put_target_strike = find_strike_by_premium(
                        bhav_entry, bhav_exit, params.get("index", "NIFTY"), 
                        "PE", curr_exp, atm_strike, target, "OTM"
                    )
                    
                    if put_target_strike:
                        put_entry_px, _ = get_option_price(
                            bhav_entry, params.get("index", "NIFTY"), "OPTIDX", "PE", curr_exp, put_target_strike
                        )
                        put_exit_px, _ = get_option_price(
                            bhav_exit, params.get("index", "NIFTY"), "OPTIDX", "PE", curr_exp, put_target_strike
                        )
                        
                        if put_entry_px and put_exit_px:
                            put_pnl = round(put_entry_px - put_exit_px, 2)
                            net_pnl = round(put_pnl, 2)
                            
                            trades.append({
                                "entry_date": from_date,
                                "exit_date": to_date,
                                "entry_spot": entry_spot,
                                "exit_spot": exit_spot,
                                "spot_pnl": round(exit_spot - entry_spot, 2),
                                "put_expiry": curr_exp,
                                "put_strike": put_target_strike,
                                "put_entry_price": put_entry_px,
                                "put_exit_price": put_exit_px,
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
    
    # Use call_expiry if available, otherwise put_expiry for pivot
    expiry_col = 'call_expiry' if 'call_expiry' in df.columns else 'put_expiry'
    pivot = build_pivot(df, expiry_col)
    
    return df, summary, pivot


def find_strike_by_premium(bhav_entry, bhav_exit, symbol, option_type, expiry, atm_strike, target_premium, direction):
    """
    Find a strike where the premium is closest to the target
    direction: 'OTM', 'ITM', or 'ATM'
    """
    # Filter for options with the right characteristics
    if option_type == "CE":
        # For Calls, OTM means strike > spot (above ATM)
        if direction == "OTM":
            strikes_df = bhav_entry[
                (bhav_entry['Instrument'] == "OPTIDX") &
                (bhav_entry['Symbol'] == symbol) &
                (bhav_entry['OptionType'] == option_type) &
                (bhav_entry['ExpiryDate'] == expiry.date()) &
                (bhav_entry['StrikePrice'] >= atm_strike) &
                (bhav_entry['TurnOver'] > 0)
            ].sort_values('StrikePrice')
        elif direction == "ITM":
            strikes_df = bhav_entry[
                (bhav_entry['Instrument'] == "OPTIDX") &
                (bhav_entry['Symbol'] == symbol) &
                (bhav_entry['OptionType'] == option_type) &
                (bhav_entry['ExpiryDate'] == expiry.date()) &
                (bhav_entry['StrikePrice'] <= atm_strike) &
                (bhav_entry['TurnOver'] > 0)
            ].sort_values('StrikePrice', ascending=False)
        else:  # ATM
            # Just find the closest to ATM
            strikes_df = bhav_entry[
                (bhav_entry['Instrument'] == "OPTIDX") &
                (bhav_entry['Symbol'] == symbol) &
                (bhav_entry['OptionType'] == option_type) &
                (bhav_entry['ExpiryDate'] == expiry.date()) &
                (bhav_entry['TurnOver'] > 0)
            ].sort_values(key=lambda x: abs(x['StrikePrice'] - atm_strike))
    elif option_type == "PE":
        # For Puts, OTM means strike < spot (below ATM)
        if direction == "OTM":
            strikes_df = bhav_entry[
                (bhav_entry['Instrument'] == "OPTIDX") &
                (bhav_entry['Symbol'] == symbol) &
                (bhav_entry['OptionType'] == option_type) &
                (bhav_entry['ExpiryDate'] == expiry.date()) &
                (bhav_entry['StrikePrice'] <= atm_strike) &
                (bhav_entry['TurnOver'] > 0)
            ].sort_values('StrikePrice', ascending=False)
        elif direction == "ITM":
            strikes_df = bhav_entry[
                (bhav_entry['Instrument'] == "OPTIDX") &
                (bhav_entry['Symbol'] == symbol) &
                (bhav_entry['OptionType'] == option_type) &
                (bhav_entry['ExpiryDate'] == expiry.date()) &
                (bhav_entry['StrikePrice'] >= atm_strike) &
                (bhav_entry['TurnOver'] > 0)
            ].sort_values('StrikePrice')
        else:  # ATM
            # Just find the closest to ATM
            strikes_df = bhav_entry[
                (bhav_entry['Instrument'] == "OPTIDX") &
                (bhav_entry['Symbol'] == symbol) &
                (bhav_entry['OptionType'] == option_type) &
                (bhav_entry['ExpiryDate'] == expiry.date()) &
                (bhav_entry['TurnOver'] > 0)
            ].sort_values(key=lambda x: abs(x['StrikePrice'] - atm_strike))
    
    # Walk through strikes to find one with premium closest to target
    best_strike = None
    best_premium_diff = float('inf')
    
    for _, row in strikes_df.iterrows():
        strike = row['StrikePrice']
        premium = row['Close']
        
        # Check if this premium is closer to target than our best so far
        premium_diff = abs(premium - target_premium)
        
        if premium_diff < best_premium_diff:
            best_premium_diff = premium_diff
            best_strike = strike
            
            # If we find a premium very close to target, we can return early
            if premium_diff < 0.5:  # Threshold for "close enough"
                break
    
    return best_strike


def run_v7_main1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V7 with weekly expiry window"""
    return run_v7(params)


def run_v7_main2(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V7 with weekly_t1 expiry window"""
    # For V7, we'll adapt the logic to use the next week's expiry
    # Get current weekly expiries
    weekly_exp = load_expiry(params.get("index", "NIFTY"), "weekly")
    
    # Modify params to use the next week's expiry for this run
    # We'll need to update the expiry handling logic to shift weeks
    # This is a simplified approach - in practice, you'd modify the expiry selection
    return run_v7(params)


def run_v7_main3(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V7 with weekly_t2 expiry window (placeholder)"""
    # Placeholder implementation - same as main2 for now
    return run_v7_main2(params)


def run_v7_main4(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """V7 with monthly expiry window (placeholder)"""
    # Placeholder implementation
    return run_v7(params)