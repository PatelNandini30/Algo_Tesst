#!/usr/bin/env python
"""
Debug script to compare trade generation between UI parameters and script
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.engines.v1_ce_fut import run_v1
import pandas as pd

def debug_trade_generation():
    """Debug why fewer trades are generated in UI vs script"""
    
    print("=== DEBUGGING TRADE GENERATION ===\n")
    
    # Test with exact UI parameters
    ui_params = {
        "index": "NIFTY",
        "from_date": "2019-01-20",  # From UI date selection
        "to_date": "2025-12-31",    # To UI date selection (approx)
        "call_sell_position": 1.0,  # CE Strike % = 1.0
        "spot_adjustment_type": 0,
        "spot_adjustment": 1.0,
        "expiry_window": "weekly_expiry"
    }
    
    print("UI Parameters:")
    for k, v in ui_params.items():
        print(f"  {k}: {v}")
    
    try:
        print("\n--- Running with UI parameters ---")
        df_ui, summary_ui, pivot_ui = run_v1(ui_params)
        
        print(f"UI Generated Trades: {len(df_ui)}")
        if not df_ui.empty:
            print("First few UI trades:")
            print(df_ui[['Entry Date', 'Exit Date', 'Entry Spot', 'Call Strike', 'Future Expiry']].head(10))
            print(f"\nDate range: {df_ui['Entry Date'].min()} to {df_ui['Entry Date'].max()}")
        
        # Test with parameters that should match your script
        print("\n--- Running with extended date range ---")
        script_params = {
            "index": "NIFTY", 
            "from_date": "2019-02-20",  # Based on your script output start
            "to_date": "2019-12-31",    # Based on your script output end
            "call_sell_position": 1.0,
            "spot_adjustment_type": 0,
            "spot_adjustment": 1.0,
            "expiry_window": "weekly_expiry"
        }
        
        df_script, summary_script, pivot_script = run_v1(script_params)
        
        print(f"Script-like Generated Trades: {len(df_script)}")
        if not df_script.empty:
            print("First few script-like trades:")
            print(df_script[['Entry Date', 'Exit Date', 'Entry Spot', 'Call Strike', 'Future Expiry']].head(10))
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_trade_generation()