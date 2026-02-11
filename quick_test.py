"""
Quick Test Script for AlgoTest Alignment
Shows correct payload format and tests connection
"""
import requests
import json

def test_current_backend():
    """Test current backend state and show correct payload"""
    
    # Test health endpoint
    try:
        health_response = requests.get("http://localhost:8000/health", timeout=5)
        print(f"Health Status: {health_response.status_code}")
        if health_response.status_code == 200:
            print("✅ Backend is running")
        else:
            print("❌ Backend not responding properly")
            return
    except Exception as e:
        print(f"❌ Cannot connect to backend: {e}")
        print("Please start backend with: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000")
        return
    
    # Show correct payload format
    print("\n=== CORRECT PAYLOAD FORMAT ===")
    correct_payload = {
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
    
    print("✅ Required fields:")
    for key, value in correct_payload.items():
        print(f"  {key}: {value} ({type(value).__name__})")
    
    # Test the backtest endpoint
    print("\n=== TESTING BACKTEST ENDPOINT ===")
    try:
        response = requests.post(
            "http://localhost:8000/api/backtest",
            json=correct_payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        print(f"Response Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("✅ SUCCESS!")
            print(f"Trades generated: {len(result.get('trades', []))}")
            print(f"Total P&L: {result.get('summary', {}).get('total_pnl', 0)}")
        elif response.status_code == 422:
            error_detail = response.json()
            print("❌ VALIDATION ERROR (422)")
            print("This means the payload structure doesn't match the expected format")
            print("Error details:")
            print(json.dumps(error_detail, indent=2))
        else:
            print(f"❌ ERROR: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"❌ Request failed: {e}")

def show_all_strategy_formats():
    """Show payload formats for all strategies"""
    print("\n=== ALL STRATEGY PAYLOAD FORMATS ===")
    
    strategies = {
        "V1 CE+FUT": {
            "strategy": "v1_ce_fut",
            "call_sell": True, "put_sell": False, "future_buy": True
        },
        "V2 PE+FUT": {
            "strategy": "v2_pe_fut", 
            "call_sell": False, "put_sell": True, "future_buy": True
        },
        "V4 Strangle": {
            "strategy": "v4_strangle",
            "call_sell": True, "put_sell": True, "future_buy": False
        },
        "V8 Hedged": {
            "strategy": "v8_ce_pe_fut",
            "call_sell": True, "put_sell": False, "put_buy": True, "future_buy": True
        }
    }
    
    base_payload = {
        "index": "NIFTY",
        "date_from": "2020-01-01",
        "date_to": "2020-12-31", 
        "expiry_window": "weekly_expiry",
        "call_sell_position": 0.0,
        "put_strike_pct_below": 1.0,
        "spot_adjustment_type": "None",
        "spot_adjustment": 1.0
    }
    
    for name, config in strategies.items():
        payload = base_payload.copy()
        payload["strategy"] = config["strategy"]
        payload["call_sell"] = config["call_sell"]
        payload["put_sell"] = config["put_sell"]
        payload["future_buy"] = config["future_buy"]
        if "put_buy" in config:
            payload["put_buy"] = config["put_buy"]
            
        print(f"\n{name}:")
        print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    test_current_backend()
    show_all_strategy_formats()