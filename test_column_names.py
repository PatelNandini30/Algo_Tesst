import requests
import json

url = "http://localhost:8000/api/dynamic-backtest"

payload = {
    "name": "Test",
    "index": "NIFTY",
    "date_from": "2023-01-01",
    "date_to": "2023-01-05",  # Just one week
    "entry_dte": 2,
    "exit_dte": 0,
    "expiry_type": "WEEKLY",
    "legs": [{
        "segment": "options",
        "option_type": "call",
        "position": "sell",
        "lot": 1,
        "expiry": "weekly",
        "strike_selection": {"strike_type": "atm"}
    }]
}

response = requests.post(url, json=payload, timeout=60)
result = response.json()

trades = result.get('trades', [])
if trades:
    print("Column names in response:")
    print(list(trades[0].keys()))
    print("\nLooking for capitalized names:")
    print(f"  'Entry Date' in keys: {'Entry Date' in trades[0]}")
    print(f"  'Net P&L' in keys: {'Net P&L' in trades[0]}")
    print(f"  'entry_date' in keys: {'entry_date' in trades[0]}")
    print(f"  'total_pnl' in keys: {'total_pnl' in trades[0]}")
