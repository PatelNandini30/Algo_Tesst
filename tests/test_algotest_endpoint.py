import requests
import json

# Test the new algotest endpoint
def test_algotest_endpoint():
    url = "http://localhost:8000/api/algotest"
    
    # Sample payload as specified in the integration guide
    payload = {
        "index": "NIFTY",
        "from_date": "2024-01-01",
        "to_date": "2024-03-31",
        "expiry_type": "WEEKLY",
        "entry_dte": 2,
        "exit_dte": 0,
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "strike_selection": "OTM2",
                "expiry": "WEEKLY"
            }
        ]
    }
    
    print("Testing /api/algotest endpoint...")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text[:500]}...")  # Print first 500 chars
        
        if response.status_code == 200:
            print("✅ Endpoint working correctly!")
            data = response.json()
            print(f"Success: {data.get('status', 'unknown')}")
        else:
            print("❌ Endpoint returned error")
    except Exception as e:
        print(f"❌ Error connecting to endpoint: {e}")

if __name__ == "__main__":
    test_algotest_endpoint()