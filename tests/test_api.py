import requests
import json

# Test the backend API
try:
    response = requests.get("http://localhost:8000/health")
    print(f"Health check status: {response.status_code}")
    print(f"Response: {response.json()}")
    
    # Test dynamic backtest endpoint
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
    
    response = requests.post("http://localhost:8000/api/dynamic-backtest", json=test_data)
    print(f"Dynamic backtest status: {response.status_code}")
    if response.status_code == 200:
        print("Dynamic backtest endpoint working!")
        result = response.json()
        print(f"Trades found: {len(result.get('trades', []))}")
    else:
        print(f"Error: {response.text}")
        
except Exception as e:
    print(f"Error testing API: {e}")