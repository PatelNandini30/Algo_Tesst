"""
Test what payload the frontend is sending
"""
import requests
import json

# Simulate what StrategyBuilder sends
payload = {
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "date_from": "2019-01-01",
    "date_to": "2026-01-01",
    "expiry_window": "weekly_expiry",
    "spot_adjustment_type": "None",
    "spot_adjustment": 1.0,
    "call_sell_position": 0.0,
    "call_sell": True,
    "put_sell": False,
    "call_buy": False,
    "put_buy": False,
    "future_buy": True
}

print("Testing /api/backtest with frontend payload...")
print(f"Payload: {json.dumps(payload, indent=2)}")

try:
    response = requests.post(
        "http://localhost:8000/api/backtest",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    
    print(f"\nStatus Code: {response.status_code}")
    
    if response.status_code == 200:
        print("\n✅ SUCCESS!")
        data = response.json()
        print(f"Total trades: {data.get('meta', {}).get('total_trades')}")
    else:
        print(f"\n❌ ERROR: {response.status_code}")
        print(f"Response: {response.text}")
        
except Exception as e:
    print(f"\n❌ EXCEPTION: {e}")
