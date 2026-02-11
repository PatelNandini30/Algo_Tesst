"""
Validation Script for AlgoTest-Style Alignment
Tests 1:1 mapping between frontend → backend → engine
"""
import requests
import json
import pandas as pd
from datetime import datetime

def test_strategy_alignment():
    """Test each strategy with AlgoTest-style parameters"""
    
    base_url = "http://localhost:8000"
    
    # Test cases following your specification
    test_cases = [
        {
            "name": "V1 CE Sell + FUT Buy - Weekly",
            "payload": {
                "strategy": "v1_ce_fut",
                "index": "NIFTY",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31",
                "expiry_window": "weekly_expiry",
                "call_sell_position": 0.0,
                "put_strike_pct_below": 1.0,
                "spot_adjustment_type": "None",
                "spot_adjustment": 1.0,
                "call_sell": True,
                "put_sell": False,
                "future_buy": True
            }
        },
        {
            "name": "V2 PE Sell + FUT Buy - Weekly T1",
            "payload": {
                "strategy": "v2_pe_fut",
                "index": "NIFTY",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31",
                "expiry_window": "weekly_t1",
                "put_sell_position": 0.0,
                "put_strike_pct_below": 1.0,
                "spot_adjustment_type": "None",
                "spot_adjustment": 1.0,
                "call_sell": False,
                "put_sell": True,
                "future_buy": True
            }
        },
        {
            "name": "V4 Short Strangle - Weekly",
            "payload": {
                "strategy": "v4_strangle",
                "index": "NIFTY",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31",
                "expiry_window": "weekly_expiry",
                "call_sell_position": 0.0,
                "put_sell_position": 0.0,
                "put_strike_pct_below": 1.0,
                "spot_adjustment_type": "None",
                "spot_adjustment": 1.0,
                "call_sell": True,
                "put_sell": True,
                "future_buy": False
            }
        },
        {
            "name": "V8 Hedged Bull - Weekly T1",
            "payload": {
                "strategy": "v8_ce_pe_fut",
                "index": "NIFTY",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31",
                "expiry_window": "weekly_t1",
                "call_sell_position": 0.0,
                "put_strike_pct_below": 2.0,
                "spot_adjustment_type": "RisesOrFalls",
                "spot_adjustment": 4.0,
                "call_sell": True,
                "put_sell": False,
                "put_buy": True,
                "future_buy": True
            }
        }
    ]
    
    print("=== ALGOTEST ALIGNMENT VALIDATION ===\n")
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"Test {i}: {test_case['name']}")
        print(f"Payload: {json.dumps(test_case['payload'], indent=2)}")
        
        try:
            # Test health endpoint first
            health_response = requests.get(f"{base_url}/health", timeout=5)
            if health_response.status_code != 200:
                print("❌ Backend not responding")
                continue
                
            # Test backtest endpoint
            response = requests.post(
                f"{base_url}/api/backtest",
                json=test_case['payload'],
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                print("✅ SUCCESS")
                print(f"   Trades generated: {len(result.get('trades', []))}")
                print(f"   Total P&L: {result.get('summary', {}).get('total_pnl', 0)}")
                print(f"   Win Rate: {result.get('summary', {}).get('win_pct', 0)}%")
                if result.get('trades'):
                    trade = result['trades'][0]
                    required_fields = [
                        'entry_date', 'exit_date', 'entry_spot', 'exit_spot',
                        'call_strike', 'call_entry_price', 'call_exit_price', 'call_pnl',
                        'put_strike', 'put_entry_price', 'put_exit_price', 'put_pnl',
                        'future_entry_price', 'future_exit_price', 'future_pnl',
                        'spot_pnl', 'net_pnl', 'cumulative', 'dd', 'pct_dd'
                    ]
                    missing_fields = [field for field in required_fields if field not in trade]
                    if missing_fields:
                        print(f"   ⚠️  Missing fields: {missing_fields}")
                    else:
                        print("   ✅ All required trade fields present")
            else:
                print(f"❌ FAILED: {response.status_code}")
                print(f"   Error: {response.text}")
                
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
        
        print("-" * 50)

def validate_engine_matrix():
    """Validate engine leg selection matrix"""
    print("\n=== ENGINE LEG SELECTION MATRIX VALIDATION ===\n")
    
    engine_matrix = {
        "v1_ce_fut": {"call_sell": True, "put_sell": False, "put_buy": False, "future_buy": True},
        "v2_pe_fut": {"call_sell": False, "put_sell": True, "put_buy": False, "future_buy": True},
        "v4_strangle": {"call_sell": True, "put_sell": True, "put_buy": False, "future_buy": False},
        "v6_inverse_strangle": {"call_sell": True, "put_sell": True, "put_buy": False, "future_buy": False},
        "v7_premium": {"call_sell": True, "put_sell": True, "put_buy": False, "future_buy": False},
        "v8_ce_pe_fut": {"call_sell": True, "put_sell": False, "put_buy": True, "future_buy": True},
        "v8_hsl": {"call_sell": True, "put_sell": False, "put_buy": False, "future_buy": True},
        "v9_counter": {"call_sell": True, "put_sell": False, "put_buy": True, "future_buy": True},
        "v3_strike_breach": {"call_sell": True, "put_sell": False, "put_buy": False, "future_buy": True},
        "v5_call": {"call_sell": True, "put_sell": False, "put_buy": True, "future_buy": False},
        "v5_put": {"call_sell": False, "put_sell": True, "put_buy": True, "future_buy": False},
    }
    
    for engine, legs in engine_matrix.items():
        print(f"{engine}:")
        print(f"  Call Sell: {'✓' if legs['call_sell'] else '✗'}")
        print(f"  Put Sell: {'✓' if legs['put_sell'] else '✗'}")
        print(f"  Put Buy: {'✓' if legs['put_buy'] else '✗'}")
        print(f"  Future Buy: {'✓' if legs['future_buy'] else '✗'}")
        print()

if __name__ == "__main__":
    validate_engine_matrix()
    test_strategy_alignment()