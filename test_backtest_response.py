"""
Test script to check what the backtest endpoint is returning
"""
import requests
import json

# Test payload (same as what the UI is sending)
payload = {
    "name": "Test Strategy",
    "index": "NIFTY",
    "date_from": "2024-01-01",
    "date_to": "2024-12-31",
    "legs": [{
        "id": 1771052552596,
        "segment": "options",
        "position": "sell",
        "option_type": "call",
        "expiry": "weekly",
        "lot": 1,
        "strike_selection": {
            "type": "strike_type",
            "strike_type": "atm",
            "strikes_away": 0
        }
    }],
    "entry_dte": 2,
    "exit_dte": 0,
    "expiry_type": "WEEKLY"
}

# Make request
response = requests.post("http://localhost:8000/api/dynamic-backtest", json=payload)

print(f"Status Code: {response.status_code}")
print(f"\nResponse Headers: {dict(response.headers)}")
print(f"\nResponse Body:")
print(json.dumps(response.json(), indent=2))

# Check what's in the response
data = response.json()
print(f"\n=== ANALYSIS ===")
print(f"Status: {data.get('status')}")
print(f"Total Trades: {data.get('meta', {}).get('total_trades')}")
print(f"Trades List Length: {len(data.get('trades', []))}")
print(f"Summary: {data.get('summary')}")
print(f"Pivot Headers: {data.get('pivot', {}).get('headers')}")
print(f"Pivot Rows: {len(data.get('pivot', {}).get('rows', []))}")
