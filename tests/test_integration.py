#!/usr/bin/env python3
"""
Test script to verify the synchronous integration between frontend and backend
"""

import requests
import json
from datetime import datetime

def test_backend_connection():
    """Test if backend is running and responding"""
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        if response.status_code == 200:
            print("✅ Backend is running and healthy")
            return True
        else:
            print(f"❌ Backend returned status: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Backend is not running. Start with: uvicorn backend.main:app --reload")
        return False
    except Exception as e:
        print(f"❌ Error connecting to backend: {e}")
        return False

def test_backtest_endpoint():
    """Test the backtest endpoint with a simple V1 strategy"""
    test_payload = {
        "strategy": "v1_ce_fut",
        "index": "NIFTY",
        "date_from": "2023-01-01",
        "date_to": "2023-12-31",
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
        print("🚀 Sending test backtest request...")
        response = requests.post(
            "http://localhost:8000/api/backtest",
            json=test_payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Backtest successful!")
            print(f"📊 Trades generated: {len(result.get('trades', []))}")
            print(f"💰 Total P&L: {result.get('summary', {}).get('total_pnl', 0):.2f}")
            print(f"📈 Win Rate: {result.get('summary', {}).get('win_pct', 0):.2f}%")
            return True
        else:
            print(f"❌ Backtest failed with status {response.status_code}")
            print(f"Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error during backtest: {e}")
        return False

def test_leg_combinations():
    """Test different leg combinations to verify engine inference"""
    test_cases = [
        {
            "name": "V1 - CE Sell + Future Buy",
            "legs": {"ce_sell": True, "pe_sell": False, "pe_buy": False, "fut_buy": True},
            "params": {"call_sell_position": 1.0}
        },
        {
            "name": "V2 - PE Sell + Future Buy",
            "legs": {"ce_sell": False, "pe_sell": True, "pe_buy": False, "fut_buy": True},
            "params": {"put_sell_position": -1.0}
        },
        {
            "name": "V4 - Short Strangle",
            "legs": {"ce_sell": True, "pe_sell": True, "pe_buy": False, "fut_buy": False},
            "params": {"call_sell_position": 1.0, "put_sell_position": -1.0}
        }
    ]
    
    base_payload = {
        "index": "NIFTY",
        "date_from": "2023-01-01",
        "date_to": "2023-06-30",
        "spot_adjustment_type": 0,
        "spot_adjustment": 0.0
    }
    
    print("\n🧪 Testing leg combinations:")
    
    for test_case in test_cases:
        print(f"\n--- {test_case['name']} ---")
        
        # Build payload based on leg combination
        payload = base_payload.copy()
        payload.update(test_case["params"])
        
        # Set leg flags
        for leg, value in test_case["legs"].items():
            payload[leg] = value
            
        # Determine strategy
        if payload.get("ce_sell") and payload.get("fut_buy") and not payload.get("pe_sell"):
            payload["strategy"] = "v1_ce_fut"
            payload["expiry_window"] = "weekly_expiry"
        elif payload.get("pe_sell") and payload.get("fut_buy") and not payload.get("ce_sell"):
            payload["strategy"] = "v2_pe_fut"
            payload["expiry_window"] = "weekly_expiry"
        elif payload.get("ce_sell") and payload.get("pe_sell") and not payload.get("fut_buy"):
            payload["strategy"] = "v4_strangle"
            # No expiry_window for V4
        
        try:
            response = requests.post(
                "http://localhost:8000/api/backtest",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                trades_count = len(result.get('trades', []))
                total_pnl = result.get('summary', {}).get('total_pnl', 0)
                print(f"✅ Success - Trades: {trades_count}, P&L: {total_pnl:.2f}")
            else:
                print(f"❌ Failed - {response.status_code}: {response.text}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

def main():
    print("🔍 Testing AlgoTest Integration")
    print("=" * 50)
    
    # Test backend connection
    if not test_backend_connection():
        print("\nPlease start the backend server:")
        print("cd backend && uvicorn main:app --reload")
        return
    
    # Test backtest endpoint
    if test_backtest_endpoint():
        # Test different leg combinations
        test_leg_combinations()
    
    print("\n" + "=" * 50)
    print("✅ Integration test completed!")

if __name__ == "__main__":
    main()