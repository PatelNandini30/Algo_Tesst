import requests
import json

def test_dynamic_endpoint():
    """Test the dynamic backtest endpoint"""
    
    # Test data for a simple ATM CE Sell strategy
    test_data = {
        "name": "Simple ATM CE Sell Test",
        "legs": [
            {
                "leg_number": 1,
                "instrument": "OPTION",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry_type": "WEEKLY",
                "strike_selection": {
                    "type": "ATM",
                    "value": 0,
                    "spot_adjustment_mode": 0,
                    "spot_adjustment": 0
                }
            }
        ],
        "parameters": {},
        "index": "NIFTY",
        "date_from": "2024-01-01",
        "date_to": "2024-01-31",
        "expiry_window": "weekly_expiry",
        "spot_adjustment_type": "None",
        "spot_adjustment": 1.0
    }
    
    try:
        print("Testing dynamic backtest endpoint...")
        response = requests.post("http://localhost:8000/api/dynamic-backtest", json=test_data, timeout=30)
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("✓ SUCCESS: Dynamic backtest endpoint working!")
            print(f"Trades generated: {len(result.get('trades', []))}")
            if result.get('trades'):
                print(f"First trade sample: {result['trades'][0]}")
        else:
            print("✗ FAILED: Dynamic backtest endpoint error")
            print(f"Error: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("✗ FAILED: Cannot connect to backend server")
        print("Make sure the backend server is running on port 8000")
    except Exception as e:
        print(f"✗ FAILED: {e}")

if __name__ == "__main__":
    test_dynamic_endpoint()