import requests
import json

url = "http://localhost:8000/api/dynamic-backtest"

payload = {
    "name": "Test",
    "index": "NIFTY",
    "date_from": "2023-01-01",
    "date_to": "2023-01-31",  # Full month
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

print("Testing with full January 2023...")
try:
    response = requests.post(url, json=payload, timeout=60)
    if response.status_code == 200:
        result = response.json()
        trades = result.get('trades', [])
        
        print(f"\n✅ SUCCESS! Total Trades: {len(trades)}")
        
        if trades:
            print("\nFirst 3 trades:")
            for i, t in enumerate(trades[:3]):
                print(f"\nTrade {i+1}:")
                print(f"  Entry Date: {t.get('Entry Date', 'N/A')}")
                print(f"  Exit Date: {t.get('Exit Date', 'N/A')}")
                print(f"  Entry Spot: {t.get('Entry Spot', 'N/A')}")
                print(f"  Exit Spot: {t.get('Exit Spot', 'N/A')}")
                print(f"  Net P&L: {t.get('Net P&L', 'N/A')}")
                print(f"  Cumulative: {t.get('Cumulative', 'N/A')}")
        
        print(f"\nSummary:")
        print(f"  Total P&L: {result['summary'].get('total_pnl', 'N/A')}")
        print(f"  Win Rate: {result['summary'].get('win_pct', 'N/A')}%")
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"❌ Exception: {e}")
