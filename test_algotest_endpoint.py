"""
Test script to verify the /api/algotest-backtest endpoint
"""
import requests
import json

# Test payload - v1_ce_fut requires: call_sell=True, future_buy=True
payload = {
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "date_from": "2024-01-01",
    "date_to": "2024-01-31",
    "expiry_window": "weekly_expiry",
    "call_sell_position": 0.0,
    "call_sell": True,
    "put_sell": False,
    "put_buy": False,
    "future_buy": True,
    "spot_adjustment_type": "None",
    "spot_adjustment": 1.0
}

print("Testing /api/algotest-backtest endpoint...")
print(f"Payload: {json.dumps(payload, indent=2)}")
print("\nSending request...")

try:
    response = requests.post(
        "http://localhost:8000/api/algotest-backtest",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    
    print(f"\nStatus Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    
    if response.status_code == 200:
        print("\n✅ SUCCESS! Endpoint is working.")
        data = response.json()
        print(f"\nResponse keys: {list(data.keys())}")
        print(f"Status: {data.get('status')}")
        print(f"Total trades: {data.get('meta', {}).get('total_trades')}")
    else:
        print(f"\n❌ ERROR: {response.status_code}")
        print(f"Response: {response.text}")
        
except Exception as e:
    print(f"\n❌ EXCEPTION: {e}")
    import traceback
    traceback.print_exc()
