"""
Test the dynamic-backtest endpoint with the UI payload
"""
import requests
import json

# Simulate what AlgoTestBacktest UI sends
payload = {
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
                "type": "DaysBeforeExpiry",
                "days_before_expiry": 2,
                "specific_time": None
            },
            "exit_condition": {
                "type": "DaysBeforeExpiry",
                "days_before_expiry": 0,
                "specific_time": None,
                "stop_loss_percent": None,
                "target_percent": None
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

print("Testing /api/dynamic-backtest endpoint...")
print(f"Payload: {json.dumps(payload, indent=2)}")

try:
    response = requests.post(
        "http://localhost:8000/api/dynamic-backtest",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    
    print(f"\nStatus Code: {response.status_code}")
    
    if response.status_code == 200:
        print("\n✅ SUCCESS!")
        data = response.json()
        print(f"Status: {data.get('status')}")
        print(f"Total trades: {data.get('meta', {}).get('total_trades')}")
    else:
        print(f"\n❌ ERROR: {response.status_code}")
        print(f"Response: {response.text}")
        
except requests.exceptions.Timeout:
    print("\n⏱️ Request timed out - backtest is taking too long")
except Exception as e:
    print(f"\n❌ EXCEPTION: {e}")
    import traceback
    traceback.print_exc()
