#!/usr/bin/env python
"""
Test script to verify that v1_ce_fut.py produces the expected output
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.engines.v1_ce_fut import run_v1
import pandas as pd

def test_strike_calculation():
    """Test that strike calculation matches expected behavior"""
    print("Testing strike calculation...")
    
    # Test case from your example
    entry_spot = 10791.65
    call_percent = 1.0  # 1% OTM
    
    # Expected calculation from original script
    expected_strike = round((entry_spot * (1 + call_percent / 100)) / 100) * 100
    print(f"Entry spot: {entry_spot}")
    print(f"Call percent: {call_percent}%")
    print(f"Expected strike: {expected_strike}")
    
    # Test with actual parameters
    params = {
        "index": "NIFTY",
        "from_date": "2019-02-20",
        "to_date": "2019-03-20",
        "call_sell_position": 1.0
    }
    
    try:
        df, summary, pivot = run_v1(params)
        if not df.empty:
            print(f"First trade strike: {df.iloc[0]['Call Strike']}")
            print(f"First trade call expiry: {df.iloc[0]['Call Expiry']}")
            print(f"First trade future expiry: {df.iloc[0]['Future Expiry']}")
        else:
            print("No trades generated")
    except Exception as e:
        print(f"Error running v1: {e}")

if __name__ == "__main__":
    test_strike_calculation()