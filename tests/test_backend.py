import requests
import json

# Test the health endpoint
try:
    response = requests.get("http://localhost:8000/health", timeout=5)
    print(f"Health check status: {response.status_code}")
    print(f"Response: {response.json()}")
except Exception as e:
    print(f"Error connecting to server: {e}")

# Test the backtest endpoint with a simple request
try:
    test_data = {
        "strategy_version": "v1",
        "from_date": "2020-01-01",
        "to_date": "2020-01-31",
        "index": "NIFTY"
    }
    
    print(f"\nTesting backtest endpoint with data: {test_data}")
    response = requests.post("http://localhost:8000/api/backtest", 
                           json=test_data, 
                           headers={"Content-Type": "application/json"},
                           timeout=30)
    
    print(f"Backtest status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print("Backtest successful!")
        print(f"Trades generated: {len(result.get('trades', []))}")
        print(f"Summary: {result.get('summary', {})}")
    else:
        print(f"Error response: {response.text}")
        
except Exception as e:
    print(f"Error testing backtest: {e}")