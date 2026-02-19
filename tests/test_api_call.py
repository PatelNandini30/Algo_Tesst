import requests
import json

# Test payload that should work
payload = {
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "date_from": "2019-01-01",
    "date_to": "2026-01-01",
    "expiry_window": "weekly_expiry",
    "call_sell_position": 1.0,
    "call_sell": True,
    "put_sell": False,
    "put_buy": False,
    "future_buy": True,
    "spot_adjustment_type": "None",  # String, not number
    "spot_adjustment": 1.0
}

print("Sending payload:")
print(json.dumps(payload, indent=2))

try:
    response = requests.post(
        "http://localhost:8000/api/backtest",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    
    print(f"\nStatus Code: {response.status_code}")
    
    if response.status_code == 200:
        print("Success!")
        result = response.json()
        print(f"Trades returned: {len(result.get('trades', []))}")
        print(f"Total PnL: {result.get('summary', {}).get('total_pnl', 0)}")
    else:
        print("Error:")
        print(response.text)
        
except Exception as e:
    print(f"Exception: {e}")
