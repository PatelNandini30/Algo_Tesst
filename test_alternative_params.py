import os
import sys
import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

def test_alternative_parameters():
    """Test V1 strategy with different parameters to avoid missing data"""
    
    print("=== Testing Alternative Strategy Parameters ===\n")
    
    try:
        from backend.engines.v1_ce_fut import run_v1
        from backend.base import get_strike_data, load_expiry, load_base2
        
        # Try different date ranges and parameters
        test_params = [
            {
                "name": "Wider Strike Range",
                "params": {
                    "strategy_version": "v1",
                    "expiry_window": "weekly_expiry",
                    "spot_adjustment_type": 0,
                    "spot_adjustment": 1.0,
                    "call_sell_position": 0.0,
                    "put_sell_position": 0.0,
                    "put_strike_pct_below": 2.0,  # Increase from 1.0
                    "protection": False,
                    "protection_pct": 1.0,
                    "call_premium": True,
                    "put_premium": True,
                    "premium_multiplier": 1.0,
                    "call_sell": True,
                    "put_sell": True,
                    "call_hsl_pct": 100,
                    "put_hsl_pct": 100,
                    "max_put_spot_pct": 0.04,
                    "pct_diff": 0.5,  # Increase from 0.3
                    "from_date": "2019-01-01",
                    "to_date": "2019-06-30",  # Shorter period
                    "index": "NIFTY"
                }
            },
            {
                "name": "Different Date Range (2020)",
                "params": {
                    "strategy_version": "v1",
                    "expiry_window": "weekly_expiry",
                    "spot_adjustment_type": 0,
                    "spot_adjustment": 1.0,
                    "call_sell_position": 0.0,
                    "put_sell_position": 0.0,
                    "put_strike_pct_below": 1.0,
                    "protection": False,
                    "protection_pct": 1.0,
                    "call_premium": True,
                    "put_premium": True,
                    "premium_multiplier": 1.0,
                    "call_sell": True,
                    "put_sell": True,
                    "call_hsl_pct": 100,
                    "put_hsl_pct": 100,
                    "max_put_spot_pct": 0.04,
                    "pct_diff": 0.3,
                    "from_date": "2020-01-01",
                    "to_date": "2020-12-31",
                    "index": "NIFTY"
                }
            }
        ]
        
        for test in test_params:
            print(f"\n--- Testing: {test['name']} ---")
            print(f"Date range: {test['params']['from_date']} to {test['params']['to_date']}")
            
            try:
                trades_df, meta, analytics = run_v1(test['params'])
                print(f"✓ Strategy executed successfully")
                print(f"  Trades generated: {len(trades_df)}")
                if len(trades_df) > 0:
                    print(f"  Sample trades:")
                    print(trades_df.head(3)[['Date', 'StrikePrice', 'OptionType', 'Action']].to_string(index=False))
                print(f"  Meta info: {meta}")
            except Exception as e:
                print(f"✗ Strategy failed: {e}")
                
    except Exception as e:
        print(f"Import error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_alternative_parameters()