"""
Test script to diagnose the 422 error by simulating the exact frontend payload
"""
import requests
import json

# Simulate the exact payload that ConfigPanel.jsx sends for v1_ce_fut strategy
payload_v1 = {
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "date_from": "2019-01-01",
    "date_to": "2026-01-01",
    "spot_adjustment_type": "None",
    "spot_adjustment": 1.0,
    "expiry_window": "weekly_expiry",
    "call_sell_position": 1.0,
    "call_sell": True,
    "put_sell": False,
    "put_buy": False,
    "future_buy": True
}

# Test the endpoint
url = "http://localhost:8000/api/backtest"

print("Testing /api/backtest endpoint with v1_ce_fut payload...")
print(f"\nPayload being sent:")
print(json.dumps(payload_v1, indent=2))

try:
    response = requests.post(url, json=payload_v1)
    print(f"\nStatus Code: {response.status_code}")
    
    if response.status_code == 422:
        print("\n422 Validation Error Details:")
        print(json.dumps(response.json(), indent=2))
    elif response.status_code == 200:
        print("\nSuccess! Response:")
        result = response.json()
        print(f"Status: {result.get('status')}")
        print(f"Trades: {len(result.get('trades', []))}")
    else:
        print(f"\nUnexpected status code. Response:")
        print(response.text)
        
except requests.exceptions.ConnectionError:
    print("\nError: Could not connect to backend. Is it running on http://localhost:8000?")
except Exception as e:
    print(f"\nError: {e}")

# Also test a v4_strangle payload
print("\n" + "="*60)
print("\nTesting v4_strangle payload...")

payload_v4 = {
    "strategy": "v4_strangle",
    "index": "NIFTY",
    "date_from": "2019-01-01",
    "date_to": "2026-01-01",
    "spot_adjustment_type": "None",
    "spot_adjustment": 1.0,
    "call_sell_position": 1.0,
    "put_sell_position": -1.0,
    "call_sell": True,
    "put_sell": True,
    "put_buy": False,
    "future_buy": False
}

print(f"\nPayload being sent:")
print(json.dumps(payload_v4, indent=2))

try:
    response = requests.post(url, json=payload_v4)
    print(f"\nStatus Code: {response.status_code}")
    
    if response.status_code == 422:
        print("\n422 Validation Error Details:")
        print(json.dumps(response.json(), indent=2))
    elif response.status_code == 200:
        print("\nSuccess!")
    else:
        print(f"\nResponse: {response.text}")
        
except Exception as e:
    print(f"\nError: {e}")
