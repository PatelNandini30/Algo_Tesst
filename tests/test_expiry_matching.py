"""
Test Expiry Matching - Compare with AlgoTest Results
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from datetime import datetime
import pandas as pd

# AlgoTest reference data from your screenshot
ALGOTEST_TRADES = [
    {'index': 1, 'entry_date': '2020-01-07', 'entry_day': 'Tuesday', 'expiry': '2020-01-09', 'strike': 12150, 'entry_price': 43.85},
    {'index': 2, 'entry_date': '2020-01-14', 'entry_day': 'Tuesday', 'expiry': '2020-01-16', 'strike': 12350, 'entry_price': 44.85},
    {'index': 3, 'entry_date': '2020-01-21', 'entry_day': 'Tuesday', 'expiry': '2020-01-23', 'strike': 12200, 'entry_price': 49.4},
    {'index': 4, 'entry_date': '2020-01-28', 'entry_day': 'Tuesday', 'expiry': '2020-01-30', 'strike': 12150, 'entry_price': 60.55},
    {'index': 5, 'entry_date': '2020-02-04', 'entry_day': 'Tuesday', 'expiry': '2020-02-06', 'strike': 11850, 'entry_price': 34.0},
    {'index': 6, 'entry_date': '2020-02-11', 'entry_day': 'Tuesday', 'expiry': '2020-02-13', 'strike': 12150, 'entry_price': 56.5},
    {'index': 7, 'entry_date': '2020-02-18', 'entry_day': 'Tuesday', 'expiry': '2020-02-20', 'strike': 11950, 'entry_price': 65.7},
    {'index': 8, 'entry_date': '2020-02-25', 'entry_day': 'Tuesday', 'expiry': '2020-02-27', 'strike': 11850, 'entry_price': 72.65},
]


def test_expiry_calculation():
    """Test if expiry dates are calculated correctly"""
    print("=" * 80)
    print("TEST 1: Expiry Date Calculation")
    print("=" * 80)
    
    from base import load_expiry, calculate_trading_days_before_expiry, get_strike_data
    
    try:
        # Load expiry data
        expiry_df = load_expiry('NIFTY', 'weekly')
        print(f"\n✓ Loaded {len(expiry_df)} weekly expiries")
        
        # Show first few expiries
        print("\nFirst 10 expiries:")
        print(expiry_df.head(10)[['Current Expiry']])
        
        # Load trading calendar
        spot_df = get_strike_data('NIFTY', '2020-01-01', '2020-02-29')
        trading_calendar = spot_df[['Date']].drop_duplicates().sort_values('Date').reset_index(drop=True)
        trading_calendar.columns = ['date']
        print(f"\n✓ Loaded {len(trading_calendar)} trading days")
        
        # Test DTE calculation for each AlgoTest trade
        print("\n" + "=" * 80)
        print("Verifying DTE Calculation (Entry DTE = 2)")
        print("=" * 80)
        
        for trade in ALGOTEST_TRADES[:5]:
            expiry_date = pd.to_datetime(trade['expiry'])
            expected_entry = pd.to_datetime(trade['entry_date'])
            
            # Calculate entry date using our function
            calculated_entry = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=2,
                trading_calendar_df=trading_calendar
            )
            
            match = calculated_entry.date() == expected_entry.date()
            status = "✓" if match else "✗"
            
            print(f"\nTrade {trade['index']}:")
            print(f"  Expiry: {expiry_date.date()}")
            print(f"  Expected Entry: {expected_entry.date()} ({trade['entry_day']})")
            print(f"  Calculated Entry: {calculated_entry.date()}")
            print(f"  Match: {status}")
        
        print("\n✓ DTE calculation test complete")
        
    except FileNotFoundError as e:
        print(f"\n✗ Error: {e}")
        print("  Make sure expiry data files exist in expiryData/ folder")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


def test_expiry_coverage():
    """Test if all required expiries are in the CSV"""
    print("\n" + "=" * 80)
    print("TEST 2: Expiry Coverage")
    print("=" * 80)
    
    from base import load_expiry
    
    try:
        expiry_df = load_expiry('NIFTY', 'weekly')
        
        # Expected expiries from AlgoTest data
        expected_expiries = [
            '2020-01-09', '2020-01-16', '2020-01-23', '2020-01-30',
            '2020-02-06', '2020-02-13', '2020-02-20', '2020-02-27'
        ]
        
        print("\nChecking if all AlgoTest expiries are present:")
        
        all_present = True
        for exp_str in expected_expiries:
            exp_date = pd.to_datetime(exp_str)
            present = any(expiry_df['Current Expiry'] == exp_date)
            status = "✓" if present else "✗"
            print(f"  {exp_str}: {status}")
            if not present:
                all_present = False
        
        if all_present:
            print("\n✓ All required expiries are present")
        else:
            print("\n✗ Some expiries are missing - this will cause mismatches!")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")


def test_strike_calculation():
    """Test if strikes are calculated correctly"""
    print("\n" + "=" * 80)
    print("TEST 3: Strike Calculation")
    print("=" * 80)
    
    from base import calculate_strike_from_selection, get_strike_data
    
    try:
        # Load spot data
        spot_df = get_strike_data('NIFTY', '2020-01-01', '2020-02-29')
        
        print("\nVerifying strike calculations:")
        
        for trade in ALGOTEST_TRADES[:5]:
            entry_date = pd.to_datetime(trade['entry_date'])
            
            # Get spot price for entry date
            spot_row = spot_df[spot_df['Date'] == entry_date]
            if spot_row.empty:
                print(f"\n✗ Trade {trade['index']}: No spot data for {entry_date.date()}")
                continue
            
            spot_price = float(spot_row.iloc[0]['Close'])
            
            # Calculate ATM strike
            atm_strike = calculate_strike_from_selection(spot_price, 50, 'ATM', 'CE')
            
            # AlgoTest strike
            algotest_strike = trade['strike']
            
            # Check if they match
            match = atm_strike == algotest_strike
            status = "✓" if match else "✗"
            
            print(f"\nTrade {trade['index']} ({entry_date.date()}):")
            print(f"  Spot: {spot_price}")
            print(f"  Calculated ATM: {atm_strike}")
            print(f"  AlgoTest Strike: {algotest_strike}")
            print(f"  Match: {status}")
            
            if not match:
                # Try to understand the difference
                diff = algotest_strike - atm_strike
                print(f"  Difference: {diff} points")
                if diff == 50:
                    print(f"  → AlgoTest might be using OTM1")
                elif diff == -50:
                    print(f"  → AlgoTest might be using ITM1")
        
        print("\n✓ Strike calculation test complete")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


def test_full_backtest():
    """Test full backtest and compare with AlgoTest"""
    print("\n" + "=" * 80)
    print("TEST 4: Full Backtest Comparison")
    print("=" * 80)
    
    print("\n⚠️  This test requires the full backtest engine to be working")
    print("   Run this after fixing any issues found in tests 1-3")
    
    # Uncomment when ready to test
    """
    from engines.generic_algotest_engine import run_algotest_backtest
    
    params = {
        'index': 'NIFTY',
        'from_date': '2020-01-01',
        'to_date': '2020-02-29',
        'expiry_type': 'WEEKLY',
        'entry_dte': 2,
        'exit_dte': 0,
        'legs': [{
            'segment': 'OPTIONS',
            'option_type': 'CE',
            'strike_selection': 'ATM',
            'position': 'SELL',
            'lots': 1
        }]
    }
    
    result = run_algotest_backtest(params)
    
    print("\nComparing first 5 trades:")
    print("=" * 80)
    
    for i in range(min(5, len(result['trades']))):
        your_trade = result['trades'][i]
        algo_trade = ALGOTEST_TRADES[i]
        
        print(f"\nTrade {i+1}:")
        print(f"  Your System:")
        print(f"    Entry: {your_trade['entry_date']}")
        print(f"    Expiry: {your_trade['expiry_date']}")
        print(f"    Strike: {your_trade['strike']}")
        
        print(f"  AlgoTest:")
        print(f"    Entry: {algo_trade['entry_date']}")
        print(f"    Expiry: {algo_trade['expiry']}")
        print(f"    Strike: {algo_trade['strike']}")
        
        entry_match = str(your_trade['entry_date']) == algo_trade['entry_date']
        expiry_match = str(your_trade['expiry_date']) == algo_trade['expiry']
        strike_match = your_trade['strike'] == algo_trade['strike']
        
        print(f"  Matches:")
        print(f"    Entry: {'✓' if entry_match else '✗'}")
        print(f"    Expiry: {'✓' if expiry_match else '✗'}")
        print(f"    Strike: {'✓' if strike_match else '✗'}")
    """


def main():
    print("\n" + "=" * 80)
    print("EXPIRY MATCHING TEST SUITE")
    print("Comparing your system with AlgoTest results")
    print("=" * 80)
    
    print("\nAlgoTest Reference Data (First 8 trades):")
    print("-" * 80)
    for trade in ALGOTEST_TRADES:
        print(f"Trade {trade['index']}: {trade['entry_date']} ({trade['entry_day']}) → "
              f"Expiry {trade['expiry']}, Strike {trade['strike']}")
    
    # Run tests
    test_expiry_calculation()
    test_expiry_coverage()
    test_strike_calculation()
    test_full_backtest()
    
    print("\n" + "=" * 80)
    print("TEST SUITE COMPLETE")
    print("=" * 80)
    print("\nNext Steps:")
    print("1. If tests 1-3 pass: Your system is working correctly!")
    print("2. If any test fails: Check the error messages and fix the issue")
    print("3. After fixes: Uncomment and run test 4 for full comparison")


if __name__ == '__main__':
    main()
