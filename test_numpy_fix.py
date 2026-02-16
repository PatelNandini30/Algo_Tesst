import requests
import json

# Test the dynamic backtest endpoint with AlgoTest format
url = "http://localhost:8000/api/dynamic-backtest"

payload = {
    "name": "Test Strategy",
    "index": "NIFTY",
    "date_from": "2023-01-01",
    "date_to": "2023-01-31",
    "entry_dte": 2,
    "exit_dte": 0,
    "expiry_type": "WEEKLY",
    "legs": [
        {
            "segment": "options",
            "option_type": "call",
            "position": "sell",
            "lot": 1,
            "expiry": "weekly",
            "strike_selection": {
                "strike_type": "atm"
            }
        }
    ]
}

print("Sending request to:", url)
print("Payload:", json.dumps(payload, indent=2))
print("\n" + "="*70)

try:
    response = requests.post(url, json=payload, timeout=60)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("✅ SUCCESS! Response received:")
        result = response.json()
        print(f"Total Trades: {result.get('meta', {}).get('total_trades', 0)}")
        print(f"\nSummary: {json.dumps(result.get('summary', {}), indent=2)}")
        
        # Print first few trades with ACTUAL column names from response
        trades = result.get('trades', [])
        if trades:
            print(f"\nFirst trade columns: {list(trades[0].keys())}")
            print(f"\nFirst 3 trades:")
            for i, trade in enumerate(trades[:3]):
                print(f"\nTrade {i+1}:")
                # Show key fields
                print(f"  Entry Date: {trade.get('Entry Date', trade.get('entry_date', 'N/A'))}")
                print(f"  Exit Date: {trade.get('Exit Date', trade.get('exit_date', 'N/A'))}")
                print(f"  Net P&L: {trade.get('Net P&L', trade.get('total_pnl', 0))}")
                print(f"  Cumulative: {trade.get('Cumulative', trade.get('cumulative_pnl', 0))}")
                print(f"  Leg 1 Strike: {trade.get('Leg 1 Strike', trade.get('leg1_strike', 'N/A'))}")
        else:
            print("\n⚠️  No trades in response!")
    else:
        print("❌ ERROR Response:")
        print(response.text)
        
except Exception as e:
    print(f"❌ Exception occurred: {e}")
    import traceback
    traceback.print_exc()
