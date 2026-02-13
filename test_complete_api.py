"""
Complete API Test Script
Tests all endpoints to ensure the backend is working properly
"""
import requests
import json
from datetime import datetime

# API base URL
BASE_URL = "http://localhost:8000"

def print_section(title):
    """Print a formatted section header"""
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80)

def test_health():
    """Test health check endpoint"""
    print_section("Testing Health Check")
    try:
        response = requests.get(f"{BASE_URL}/health")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_root():
    """Test root endpoint"""
    print_section("Testing Root Endpoint")
    try:
        response = requests.get(f"{BASE_URL}/")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_strategies():
    """Test strategies list endpoint"""
    print_section("Testing Strategies List")
    try:
        response = requests.get(f"{BASE_URL}/api/strategies")
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Number of strategies: {len(data.get('strategies', []))}")
        
        # Print first strategy as example
        if data.get('strategies'):
            print(f"\nExample Strategy:")
            print(json.dumps(data['strategies'][0], indent=2))
        
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_date_range():
    """Test date range endpoint"""
    print_section("Testing Date Range")
    try:
        response = requests.get(f"{BASE_URL}/api/data/dates?index=NIFTY")
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Min Date: {data.get('min_date')}")
        print(f"Max Date: {data.get('max_date')}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_expiry():
    """Test expiry dates endpoint"""
    print_section("Testing Expiry Dates")
    try:
        response = requests.get(f"{BASE_URL}/api/expiry?index=NIFTY&type=weekly")
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Index: {data.get('index')}")
        print(f"Type: {data.get('type')}")
        print(f"Number of expiries: {len(data.get('expiries', []))}")
        
        # Print first 5 expiries
        if data.get('expiries'):
            print(f"\nFirst 5 expiries:")
            for exp in data['expiries'][:5]:
                print(f"  - {exp}")
        
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_backtest():
    """Test backtest endpoint with V1 strategy"""
    print_section("Testing Backtest - V1 CE Sell + Future Buy")
    
    # Prepare backtest request
    backtest_request = {
        "strategy": "v1_ce_fut",
        "index": "NIFTY",
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
        "expiry_window": "weekly_expiry",
        "call_sell_position": 0.0,
        "put_sell_position": 0.0,
        "spot_adjustment_type": "None",
        "spot_adjustment": 1.0,
        "call_sell": True,
        "put_sell": False,
        "call_buy": False,
        "put_buy": False,
        "future_buy": True,
        "protection": False,
        "protection_pct": 1.0,
        "premium_multiplier": 1.0,
        "call_premium": True,
        "put_premium": True,
        "put_strike_pct_below": 1.0,
        "max_put_spot_pct": 0.04,
        "call_hsl_pct": 100,
        "put_hsl_pct": 100,
        "pct_diff": 0.3
    }
    
    try:
        print("Sending backtest request...")
        print(f"Request payload: {json.dumps(backtest_request, indent=2)}")
        
        response = requests.post(
            f"{BASE_URL}/api/backtest",
            json=backtest_request,
            timeout=120  # 2 minute timeout for backtest
        )
        
        print(f"\nStatus Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            print(f"\n✅ Backtest completed successfully!")
            print(f"\nMeta Information:")
            print(json.dumps(data.get('meta', {}), indent=2))
            
            print(f"\nSummary Statistics:")
            summary = data.get('summary', {})
            print(f"  Total P&L: {summary.get('total_pnl', 0):.2f}")
            print(f"  Total Trades: {summary.get('count', 0)}")
            print(f"  Win Rate: {summary.get('win_pct', 0):.2f}%")
            print(f"  CAGR: {summary.get('cagr_options', 0):.2f}%")
            print(f"  Max Drawdown: {summary.get('max_dd_pct', 0):.2f}%")
            print(f"  CAR/MDD: {summary.get('car_mdd', 0):.2f}")
            
            trades = data.get('trades', [])
            print(f"\nTrades: {len(trades)} total")
            
            if trades:
                print(f"\nFirst Trade:")
                print(json.dumps(trades[0], indent=2))
                
                print(f"\nLast Trade:")
                print(json.dumps(trades[-1], indent=2))
            
            pivot = data.get('pivot', {})
            print(f"\nPivot Table:")
            print(f"  Headers: {pivot.get('headers', [])}")
            print(f"  Rows: {len(pivot.get('rows', []))} years")
            
            return True
        else:
            print(f"❌ Error: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"❌ Request timed out after 120 seconds")
        return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_backtest_v4():
    """Test backtest endpoint with V4 strategy (Short Strangle)"""
    print_section("Testing Backtest - V4 Short Strangle")
    
    backtest_request = {
        "strategy": "v4_strangle",
        "index": "NIFTY",
        "date_from": "2024-01-01",
        "date_to": "2024-06-30",
        "expiry_window": "weekly_expiry",
        "call_sell_position": 1.0,
        "put_sell_position": 1.0,
        "spot_adjustment_type": "None",
        "spot_adjustment": 1.0,
        "call_sell": True,
        "put_sell": True,
        "call_buy": False,
        "put_buy": False,
        "future_buy": False,
        "protection": False,
        "protection_pct": 1.0,
        "premium_multiplier": 1.0,
        "call_premium": True,
        "put_premium": True,
        "put_strike_pct_below": 1.0,
        "max_put_spot_pct": 0.04,
        "call_hsl_pct": 100,
        "put_hsl_pct": 100,
        "pct_diff": 0.3
    }
    
    try:
        print("Sending backtest request...")
        
        response = requests.post(
            f"{BASE_URL}/api/backtest",
            json=backtest_request,
            timeout=120
        )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ V4 Backtest completed!")
            print(f"Total Trades: {data.get('summary', {}).get('count', 0)}")
            print(f"Total P&L: {data.get('summary', {}).get('total_pnl', 0):.2f}")
            return True
        else:
            print(f"❌ Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def run_all_tests():
    """Run all API tests"""
    print("\n" + "="*80)
    print("  ALGOTEST API - COMPLETE TEST SUITE")
    print("="*80)
    print(f"Testing API at: {BASE_URL}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {
        "Health Check": test_health(),
        "Root Endpoint": test_root(),
        "Strategies List": test_strategies(),
        "Date Range": test_date_range(),
        "Expiry Dates": test_expiry(),
        "Backtest V1": test_backtest(),
        "Backtest V4": test_backtest_v4(),
    }
    
    # Print summary
    print_section("TEST SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! API is working correctly.")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the errors above.")
    
    return passed == total

if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
