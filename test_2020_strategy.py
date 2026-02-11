import os
import sys
import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

def test_2020_data():
    """Test V1 strategy with 2020 data which might have better coverage"""
    
    print("=== Testing V1 Strategy with 2020 Data ===\n")
    
    try:
        from backend.engines.v1_ce_fut import run_v1
        from backend.base import get_strike_data, load_expiry, load_base2
        
        # Test with 2020 data
        params = {
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
            "to_date": "2020-12-31",    # 2020 data
            "index": "NIFTY"
        }
        
        print("Testing with 2020 data:")
        print(f"  Date range: {params['from_date']} to {params['to_date']}")
        print(f"  Index: {params['index']}")
        print()
        
        # Load data first
        try:
            spot_data = get_strike_data("NIFTY", params["from_date"], params["to_date"])
            print(f"✓ Spot data loaded: {len(spot_data)} rows")
        except Exception as e:
            print(f"✗ Error loading spot data: {e}")
            return
            
        # Run strategy
        print("\n=== Running V1 Strategy (2020) ===")
        try:
            trades_df, meta, analytics = run_v1(params)
            print(f"✓ Strategy executed successfully")
            print(f"  Trades generated: {len(trades_df)}")
            print(f"  Meta info: {meta}")
            if len(trades_df) > 0:
                print(f"  Sample trades:")
                print(trades_df.head())
        except Exception as e:
            print(f"✗ Strategy execution failed: {e}")
            import traceback
            traceback.print_exc()
            
    except Exception as e:
        print(f"Import error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_2020_data()