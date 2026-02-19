#!/usr/bin/env python
"""
Debug script to test v1_ce_fut.py with the exact parameters from your UI output
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.engines.v1_ce_fut import run_v1
import pandas as pd

def debug_v1_with_exact_params():
    """Test with parameters that should match your UI output"""
    print("Testing v1_ce_fut.py with exact parameters...")
    
    # Parameters that should match your UI output
    params = {
        "index": "NIFTY",
        "from_date": "2019-02-20",  # Adjust to cover your first trade
        "to_date": "2019-05-15",    # Adjust to cover several trades
        "call_sell_position": 0.0,  # This should give round(spot/100)*100
        "spot_adjustment_type": 0,
        "spot_adjustment": 1.0,
        "expiry_window": "weekly_expiry"
    }
    
    print(f"Parameters: {params}")
    
    try:
        df, summary, pivot = run_v1(params)
        if not df.empty:
            print(f"\nGenerated {len(df)} trades")
            print("\nFirst few trades:")
            print(df[['Entry Date', 'Exit Date', 'Entry Spot', 'Call Strike', 'Future Expiry']].head(10))
            
            # Check if first trade matches expected values
            first_trade = df.iloc[0]
            print(f"\nFirst trade details:")
            print(f"  Entry Date: {first_trade['Entry Date']}")
            print(f"  Entry Spot: {first_trade['Entry Spot']}")
            print(f"  Call Strike: {first_trade['Call Strike']}")
            print(f"  Expected strike: {round(first_trade['Entry Spot'] / 100) * 100}")
            print(f"  Future Expiry: {first_trade['Future Expiry']}")
        else:
            print("No trades generated")
    except Exception as e:
        print(f"Error running v1: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_v1_with_exact_params()