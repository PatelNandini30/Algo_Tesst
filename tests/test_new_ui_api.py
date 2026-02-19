"""
Test script to verify the new UI API endpoints are working
"""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_health():
    """Test if server is running"""
    try:
        response = requests.get(f"{BASE_URL}/health")
        print("✓ Health check:", response.json())
        return True
    except Exception as e:
        print("✗ Health check failed:", str(e))
        return False

def test_strategies():
    """Test strategies endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/api/strategies")
        data = response.json()
        print(f"✓ Strategies endpoint: Found {len(data['strategies'])} strategies")
        for strategy in data['strategies'][:3]:
            print(f"  - {strategy['name']} ({strategy['version']})")
        return True
    except Exception as e:
        print("✗ Strategies endpoint failed:", str(e))
        return False

def test_backtest():
    """Test backtest endpoint with V1 strategy"""
    try:
        payload = {
            "strategy": "v1_ce_fut",
            "index": "NIFTY",
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
            "expiry_window": "weekly_expiry",
            "spot_adjustment_type": "None",
            "spot_adjustment": 1.0,
            "call_sell_position": 1.0,
            "call_sell": True,
            "put_sell": False,
            "put_buy": False,
            "future_buy": True
        }
        
        print("\n✓ Testing backtest with payload:")
        print(json.dumps(payload, indent=2))
        
        response = requests.post(
            f"{BASE_URL}/api/backtest",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Backtest successful!")
            print(f"  - Trades: {len(data.get('trades', []))}")
            print(f"  - Total P&L: {data.get('summary', {}).get('total_pnl', 'N/A')}")
            return True
        else:
            print(f"✗ Backtest failed with status {response.status_code}")
            print(f"  Response: {response.text}")
            return False
            
    except Exception as e:
        print("✗ Backtest endpoint failed:", str(e))
        return False

def main():
    print("=" * 60)
    print("Testing New UI API Endpoints")
    print("=" * 60)
    print()
    
    # Test health
    if not test_health():
        print("\n⚠ Server is not running. Start it with:")
        print("  cd backend && python start_server.py")
        return
    
    print()
    
    # Test strategies
    test_strategies()
    
    print()
    
    # Test backtest
    print("Testing backtest (this may take a few seconds)...")
    test_backtest()
    
    print()
    print("=" * 60)
    print("Testing Complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
