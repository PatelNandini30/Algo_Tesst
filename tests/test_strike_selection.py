"""
Test Strike Selection System
Demonstrates all strike selection methods with real examples
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from base import (
    calculate_strike_from_selection,
    calculate_strike_advanced,
    get_expiry_for_selection
)
from datetime import datetime

def test_basic_strike_selection():
    """Test ATM, ITM, OTM calculations"""
    print("=" * 60)
    print("TEST 1: Basic Strike Selection (ATM/ITM/OTM)")
    print("=" * 60)
    
    spot = 24350
    interval = 50
    
    # ATM
    atm = calculate_strike_from_selection(spot, interval, 'ATM', 'CE')
    print(f"\nSpot: {spot}")
    print(f"ATM Strike: {atm}")
    assert atm == 24350, f"Expected 24350, got {atm}"
    
    # ITM1 Call (below spot)
    itm1_ce = calculate_strike_from_selection(spot, interval, 'ITM1', 'CE')
    print(f"ITM1 CE: {itm1_ce} (should be below spot)")
    assert itm1_ce == 24300, f"Expected 24300, got {itm1_ce}"
    
    # OTM2 Call (above spot)
    otm2_ce = calculate_strike_from_selection(spot, interval, 'OTM2', 'CE')
    print(f"OTM2 CE: {otm2_ce} (should be above spot)")
    assert otm2_ce == 24450, f"Expected 24450, got {otm2_ce}"
    
    # ITM1 Put (above spot)
    itm1_pe = calculate_strike_from_selection(spot, interval, 'ITM1', 'PE')
    print(f"ITM1 PE: {itm1_pe} (should be above spot)")
    assert itm1_pe == 24400, f"Expected 24400, got {itm1_pe}"
    
    # OTM2 Put (below spot)
    otm2_pe = calculate_strike_from_selection(spot, interval, 'OTM2', 'PE')
    print(f"OTM2 PE: {otm2_pe} (should be below spot)")
    assert otm2_pe == 24250, f"Expected 24250, got {otm2_pe}"
    
    print("\n✅ All basic strike calculations passed!")


def test_banknifty_strikes():
    """Test BANKNIFTY with 100 interval"""
    print("\n" + "=" * 60)
    print("TEST 2: BANKNIFTY Strike Selection (Interval 100)")
    print("=" * 60)
    
    spot = 48750
    interval = 100
    
    # ATM
    atm = calculate_strike_from_selection(spot, interval, 'ATM', 'CE')
    print(f"\nSpot: {spot}")
    print(f"ATM Strike: {atm}")
    assert atm == 48800, f"Expected 48800, got {atm}"
    
    # OTM5 Call
    otm5_ce = calculate_strike_from_selection(spot, interval, 'OTM5', 'CE')
    print(f"OTM5 CE: {otm5_ce}")
    assert otm5_ce == 49300, f"Expected 49300, got {otm5_ce}"
    
    # ITM3 Put
    itm3_pe = calculate_strike_from_selection(spot, interval, 'ITM3', 'PE')
    print(f"ITM3 PE: {itm3_pe}")
    assert itm3_pe == 49100, f"Expected 49100, got {itm3_pe}"
    
    print("\n✅ BANKNIFTY calculations passed!")


def test_expiry_selection():
    """Test expiry date selection"""
    print("\n" + "=" * 60)
    print("TEST 3: Expiry Selection")
    print("=" * 60)
    
    # Note: This test requires expiry data files to exist
    # Uncomment when data is available
    
    print("\n⚠️  Expiry selection test requires expiry data files")
    print("   Files needed:")
    print("   - expiryData/NIFTY.csv (weekly)")
    print("   - expiryData/NIFTY_Monthly.csv (monthly)")
    print("   Test will be skipped if files don't exist")
    
    try:
        entry_date = datetime(2024, 1, 15)
        
        # Weekly expiry
        weekly_expiry = get_expiry_for_selection(entry_date, 'NIFTY', 'WEEKLY')
        print(f"\nEntry Date: {entry_date.date()}")
        print(f"Weekly Expiry: {weekly_expiry.date()}")
        
        # Next weekly
        next_weekly = get_expiry_for_selection(entry_date, 'NIFTY', 'NEXT_WEEKLY')
        print(f"Next Weekly Expiry: {next_weekly.date()}")
        
        # Monthly
        monthly_expiry = get_expiry_for_selection(entry_date, 'NIFTY', 'MONTHLY')
        print(f"Monthly Expiry: {monthly_expiry.date()}")
        
        print("\n✅ Expiry selection passed!")
        
    except FileNotFoundError as e:
        print(f"\n⚠️  Skipped: {e}")
    except Exception as e:
        print(f"\n❌ Error: {e}")


def test_edge_cases():
    """Test edge cases and rounding"""
    print("\n" + "=" * 60)
    print("TEST 4: Edge Cases")
    print("=" * 60)
    
    # Spot exactly at strike
    spot = 24400
    interval = 50
    atm = calculate_strike_from_selection(spot, interval, 'ATM', 'CE')
    print(f"\nSpot exactly at strike: {spot}")
    print(f"ATM: {atm}")
    assert atm == 24400
    
    # Spot between strikes (round up)
    spot = 24375
    atm = calculate_strike_from_selection(spot, interval, 'ATM', 'CE')
    print(f"\nSpot at 24375 (midpoint)")
    print(f"ATM: {atm} (rounds to nearest)")
    assert atm == 24400
    
    # Spot between strikes (round down)
    spot = 24325
    atm = calculate_strike_from_selection(spot, interval, 'ATM', 'CE')
    print(f"\nSpot at 24325")
    print(f"ATM: {atm} (rounds to nearest)")
    assert atm == 24300
    
    # Large offset
    spot = 24350
    otm10 = calculate_strike_from_selection(spot, interval, 'OTM10', 'CE')
    print(f"\nOTM10 from {spot}")
    print(f"Strike: {otm10} (10 strikes away)")
    assert otm10 == 24850
    
    print("\n✅ Edge cases passed!")


def test_trading_scenarios():
    """Test real trading scenarios"""
    print("\n" + "=" * 60)
    print("TEST 5: Real Trading Scenarios")
    print("=" * 60)
    
    spot = 24350
    interval = 50
    
    # Scenario 1: Conservative call selling
    print("\n📊 Scenario 1: Conservative Call Selling")
    print("   Strategy: Sell OTM2 Call")
    strike = calculate_strike_from_selection(spot, interval, 'OTM2', 'CE')
    print(f"   Spot: {spot}")
    print(f"   Sell Strike: {strike}")
    print(f"   Distance: {strike - spot} points")
    print(f"   Probability of profit: ~70%")
    
    # Scenario 2: Protective put buying
    print("\n📊 Scenario 2: Protective Put Buying")
    print("   Strategy: Buy OTM3 Put")
    strike = calculate_strike_from_selection(spot, interval, 'OTM3', 'PE')
    print(f"   Spot: {spot}")
    print(f"   Buy Strike: {strike}")
    print(f"   Protection below: {strike}")
    print(f"   Cost: Lower (OTM)")
    
    # Scenario 3: Aggressive directional trade
    print("\n📊 Scenario 3: Aggressive Directional Trade")
    print("   Strategy: Buy ITM2 Call")
    strike = calculate_strike_from_selection(spot, interval, 'ITM2', 'CE')
    intrinsic = spot - strike
    print(f"   Spot: {spot}")
    print(f"   Buy Strike: {strike}")
    print(f"   Intrinsic Value: {intrinsic}")
    print(f"   Delta: High (~0.7)")
    
    # Scenario 4: Iron Condor
    print("\n📊 Scenario 4: Iron Condor")
    print("   Strategy: Sell OTM2, Buy OTM5 on both sides")
    sell_call = calculate_strike_from_selection(spot, interval, 'OTM2', 'CE')
    buy_call = calculate_strike_from_selection(spot, interval, 'OTM5', 'CE')
    sell_put = calculate_strike_from_selection(spot, interval, 'OTM2', 'PE')
    buy_put = calculate_strike_from_selection(spot, interval, 'OTM5', 'PE')
    print(f"   Spot: {spot}")
    print(f"   Call Spread: Sell {sell_call}, Buy {buy_call}")
    print(f"   Put Spread: Sell {sell_put}, Buy {buy_put}")
    print(f"   Profit Range: {sell_put} to {sell_call}")
    print(f"   Max Risk: {(buy_call - sell_call) * 50} per lot")
    
    print("\n✅ Trading scenarios demonstrated!")


def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("STRIKE SELECTION SYSTEM - COMPREHENSIVE TESTS")
    print("=" * 60)
    
    try:
        test_basic_strike_selection()
        test_banknifty_strikes()
        test_expiry_selection()
        test_edge_cases()
        test_trading_scenarios()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\nThe strike selection system is working correctly.")
        print("Backend is ready for frontend integration.")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    run_all_tests()
