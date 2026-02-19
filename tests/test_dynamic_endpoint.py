import requests
import json

def test_backend():
    """Test if backend is running and the dynamic endpoint works"""
    
    # Test 1: Health check
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        print(f"Health check: {response.status_code}")
        if response.status_code == 200:
            print("✓ Backend health check passed")
        else:
            print(f"✗ Health check failed: {response.text}")
    except Exception as e:
        print(f"✗ Health check error: {e}")
        return False
    
    # Test 2: Dynamic backtest endpoint
    test_data = {
        "name": "Test Strategy",
        "legs": [
            {
                "leg_number": 1,
                "instrument": "Option",
                "option_type": "CE",
                "position": "Sell",
                "lots": 1,
                "expiry_type": "Weekly",
                "strike_selection": {
                    "type": "ATM"
                },
                "entry_condition": {
                    "type": "Days Before Expiry",
                    "days_before_expiry": 5
                },
                "exit_condition": {
                    "type": "Days Before Expiry",
                    "days_before_expiry": 3
                }
            }
        ],
        "parameters": {
            "re_entry_mode": "None",
            "re_entry_percent": 1.0,
            "use_base2_filter": True,
            "inverse_base2": False
        },
        "index": "NIFTY",
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
        "expiry_window": "weekly_expiry",
        "spot_adjustment_type": "None",
        "spot_adjustment": 1.0
    }
    
    try:
        response = requests.post("http://localhost:8000/api/dynamic-backtest", json=test_data, timeout=30)
        print(f"Dynamic backtest: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print("✓ Dynamic backtest successful!")
            print(f"  Trades generated: {len(result.get('trades', []))}")
            if result.get('trades'):
                print(f"  Sample trade: {result['trades'][0]}")
        else:
            print(f"✗ Dynamic backtest failed: {response.status_code}")
            print(f"  Error: {response.text}")
            return False
    except Exception as e:
        print(f"✗ Dynamic backtest error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    print("=== Testing Backend Dynamic Endpoint ===")
    success = test_backend()
    if success:
        print("\n✓ All tests passed! Backend is working correctly.")
    else:
        print("\n✗ Tests failed. Check backend logs for details.")