#!/usr/bin/env python3
"""
Verification script to check if the integration is working correctly
"""

import requests
import time

def check_services():
    """Check if both frontend and backend services are running"""
    print("🔍 Verifying Integration Services")
    print("=" * 40)
    
    # Check backend
    print("1. Checking Backend (localhost:8000)...")
    try:
        response = requests.get("http://localhost:8000/health", timeout=3)
        if response.status_code == 200:
            print("   ✅ Backend is running")
        else:
            print(f"   ❌ Backend returned status {response.status_code}")
            return False
    except:
        print("   ❌ Backend is not accessible")
        return False
    
    # Check frontend
    print("2. Checking Frontend (localhost:5173)...")
    try:
        response = requests.get("http://localhost:5173", timeout=3)
        if response.status_code == 200:
            print("   ✅ Frontend is running")
        else:
            print(f"   ❌ Frontend returned status {response.status_code}")
            return False
    except:
        print("   ❌ Frontend is not accessible")
        return False
    
    return True

def test_api_endpoints():
    """Test key API endpoints"""
    print("\n3. Testing API Endpoints...")
    
    # Test health endpoint
    try:
        response = requests.get("http://localhost:8000/health")
        if response.json().get("status") == "healthy":
            print("   ✅ /health endpoint working")
        else:
            print("   ❌ /health endpoint not returning expected response")
    except Exception as e:
        print(f"   ❌ Error testing /health: {e}")
    
    # Test backtest endpoint (simple test)
    test_payload = {
        "strategy": "v1_ce_fut",
        "index": "NIFTY",
        "date_from": "2023-01-01",
        "date_to": "2023-01-31",  # Short period for quick test
        "expiry_window": "weekly_expiry",
        "call_sell_position": 1.0,
        "call_sell": True,
        "put_sell": False,
        "put_buy": False,
        "future_buy": True,
        "spot_adjustment_type": 0,
        "spot_adjustment": 0.0
    }
    
    try:
        print("   🚀 Testing backtest endpoint...")
        response = requests.post(
            "http://localhost:8000/api/backtest",
            json=test_payload,
            timeout=15
        )
        
        if response.status_code == 200:
            result = response.json()
            trades_count = len(result.get('trades', []))
            print(f"   ✅ Backtest successful - {trades_count} trades generated")
        else:
            print(f"   ❌ Backtest failed: {response.status_code}")
            print(f"   Error: {response.text}")
            
    except Exception as e:
        print(f"   ❌ Error testing backtest: {e}")

def main():
    print("AlgoTest Integration Verification")
    print("=" * 50)
    
    if check_services():
        test_api_endpoints()
        print("\n" + "=" * 50)
        print("✅ Integration verification complete!")
        print("✅ You can now use the backtest UI at http://localhost:5173")
    else:
        print("\n" + "=" * 50)
        print("❌ Integration verification failed!")
        print("Please ensure both backend and frontend servers are running:")
        print("1. Backend: cd backend && uvicorn main:app --reload")
        print("2. Frontend: cd frontend && npm run dev")

if __name__ == "__main__":
    main()