import pandas as pd
from datetime import timedelta
from typing import Dict, Any, Tuple
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import (
    get_strike_data,
    load_expiry,
    load_bhavcopy,
    compute_analytics,
    build_pivot,
)

# ==========================================================
# V10 STRATEGY — DAYS BEFORE EXPIRY (DYNAMIC)
# Fully configurable from frontend:
# - Entry/Exit days before expiry
# - Option type (Call/Put)
# - Position (Buy/Sell)
# - Strike selection (ATM/OTM/ITM)
# - Expiry type (Weekly/Monthly)
# ==========================================================
def run_v10(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Dynamic days-before-expiry strategy - all parameters from frontend
    
    Parameters (all from frontend):
    - entry_days_before_expiry: Days before expiry to enter (e.g., 5)
    - exit_days_before_expiry: Days before expiry to exit (e.g., 3)
    - option_type: "CE" or "PE"
    - position_type: "Buy" or "Sell"
    - expiry_type: "weekly" or "monthly"
    - strike_offset: 0 for ATM, positive for OTM, negative for ITM (in strikes, not %)
    """
    
    index_name = params.get("index", "NIFTY")
    entry_days = params.get("entry_days_before_expiry", 5)
    exit_days = params.get("exit_days_before_expiry", 3)
    option_type = params.get("option_type", "CE")  # CE or PE
    position_type = params.get("position_type", "Buy")  # Buy or Sell
    expiry_type = params.get("expiry_type", "weekly").lower()
    strike_offset = params.get("strike_offset", 0)  # 0=ATM, +1=1 strike OTM, -1=1 strike ITM
    
    # Load data
    spot_df = get_strike_data(index_name, params["from_date"], params["to_date"])
    expiry_df = load_expiry(index_name, expiry_type)
    
    trades = []
    
    # Determine lot size based on index
    lot_size = 50 if index_name == "NIFTY" else 25  # BANKNIFTY = 25
    
    # Loop through each expiry
    for idx in range(len(expiry_df)):
        curr_expiry = expiry_df.iloc[idx]['Current Expiry']
        
        # Calculate entry and exit dates
        entry_date = curr_expiry - timedelta(days=entry_days)
        exit_date = curr_expiry - timedelta(days=exit_days)
        
        # Find actual trading days for entry and exit
        entry_data = spot_df[spot_df['Date'] >= entry_date].sort_values('Date')
        if entry_data.empty:
            continue
        actual_entry_date = entry_data.iloc[0]['Date']
        
        exit_data = spot_df[
            (spot_df['Date'] >= exit_date) & 
            (spot_df['Date'] <= curr_expiry)
        ].sort_values('Date')
        if exit_data.empty:
            continue
        actual_exit_date = exit_data.iloc[0]['Date']
        
        # Skip if exit is before or same as entry
        if actual_exit_date <= actual_entry_date:
            continue
        
        # Load bhavcopy for entry and exit
        bhav_entry = load_bhavcopy(actual_entry_date)
        bhav_exit = load_bhavcopy(actual_exit_date)
        
        if bhav_entry.empty or bhav_exit.empty:
            continue
        
        # Get ATM strike at entry
        entry_spot = entry_data.iloc[0]['Close']
        atm_strike = round(entry_spot / 50) * 50  # Round to nearest 50
        
        # Apply strike offset (in number of strikes, not percentage)
        selected_strike = atm_strike + (strike_offset * 50)
        
        # Build option symbol
        symbol = f"{index_name}{curr_expiry.strftime('%d%b%y').upper()}{int(selected_strike)}{option_type}"
        
        # Entry price
        entry_option = bhav_entry[bhav_entry['Symbol'] == symbol]
        if entry_option.empty:
            continue
        entry_price = entry_option.iloc[0]['Close']
        
        # Exit price
        exit_option = bhav_exit[bhav_exit['Symbol'] == symbol]
        if exit_option.empty:
            continue
        exit_price = exit_option.iloc[0]['Close']
        
        # Calculate P&L based on position type
        if position_type == "Buy":
            pnl = (exit_price - entry_price) * lot_size
        else:  # Sell
            pnl = (entry_price - exit_price) * lot_size
        
        trades.append({
            'Entry Date': actual_entry_date,
            'Exit Date': actual_exit_date,
            'Expiry': curr_expiry,
            'Days to Expiry at Entry': (curr_expiry - actual_entry_date).days,
            'Days to Expiry at Exit': (curr_expiry - actual_exit_date).days,
            'Spot at Entry': entry_spot,
            'ATM Strike': atm_strike,
            'Selected Strike': selected_strike,
            'Strike Offset': strike_offset,
            'Symbol': symbol,
            'Entry Price': entry_price,
            'Exit Price': exit_price,
            'Net P&L': pnl,
            'Position': f"{position_type} {option_type}"
        })
    
    # Convert to DataFrame
    if not trades:
        return pd.DataFrame(), {}, {}
    
    trades_df = pd.DataFrame(trades)
    
    # Compute analytics
    trades_df, analytics = compute_analytics(trades_df)
    
    # Build pivot table
    pivot = build_pivot(trades_df, 'Expiry')
    
    return trades_df, analytics, pivot
