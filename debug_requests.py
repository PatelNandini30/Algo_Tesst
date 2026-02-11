"""
Debug Script - Capture and Show Actual Requests
"""
import requests
import json

def debug_requests():
    print("=== DEBUGGING 422 ERRORS ===\n")
    
    # Test the working payload first
    working_payload = {
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
    
    print("1. Testing KNOWN WORKING payload:")
    response = requests.post(
        "http://localhost:8000/api/backtest",
        json=working_payload,
        headers={"Content-Type": "application/json"}
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"   ✅ Success: {len(result['trades'])} trades generated")
    print()
    
    # Test common incorrect formats
    print("2. Testing COMMON INCORRECT formats:\n")
    
    incorrect_formats = [
        {
            "name": "Old strategy_version format",
            "payload": {
                "strategy_version": "v1",  # WRONG
                "from_date": "2020-01-01",  # WRONG
                "to_date": "2020-12-31",    # WRONG
                "index": "NIFTY"
            }
        },
        {
            "name": "Missing required fields",
            "payload": {
                "strategy": "v1_ce_fut",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31"
                # Missing other required fields
            }
        },
        {
            "name": "Wrong spot_adjustment_type format",
            "payload": {
                "strategy": "v1_ce_fut",
                "index": "NIFTY",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31",
                "expiry_window": "weekly_expiry",
                "spot_adjustment_type": 0,  # Should be string "None"
                "call_sell": True,
                "put_sell": False,
                "future_buy": True
            }
        }
    ]
    
    for test in incorrect_formats:
        print(f"Testing: {test['name']}")
        response = requests.post(
            "http://localhost:8000/api/backtest",
            json=test['payload'],
            headers={"Content-Type": "application/json"}
        )
        print(f"   Status: {response.status_code}")
        if response.status_code == 422:
            error_detail = response.json()
            print(f"   ❌ Validation Error: {error_detail.get('detail', 'Unknown error')}")
        print()

if __name__ == "__main__":
    debug_requests()