"""
Test script to verify monthly expiry selection is working correctly
"""
import sys
sys.path.append('backend')

from backend.engines.generic_algotest_engine import run_algotest_backtest

# Test parameters matching AlgoTest
params = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2024-06-19',
    'expiry_type': 'MONTHLY',  # This should give us only monthly expiries
    'entry_dte': 5,
    'exit_dte': 0,
    'legs': [
        {
            'segment': 'OPTIONS',
            'position': 'SELL',
            'option_type': 'CALL',
            'lots': 1,
            'strike_selection': 'ATM'
        },
        {
            'segment': 'OPTIONS',
            'position': 'BUY',
            'option_type': 'PUT',
            'lots': 1,
            'strike_selection': 'ATM'
        }
    ]
}

print("Running backtest with MONTHLY expiry...")
print(f"Date range: {params['from_date']} to {params['to_date']}")
print(f"Entry DTE: {params['entry_dte']}, Exit DTE: {params['exit_dte']}")
print(f"Expiry Type: {params['expiry_type']}")
print()

trades_df, summary, pivot = run_backtest(params)

print(f"\n{'='*80}")
print(f"RESULTS:")
print(f"{'='*80}")
print(f"Total Trades: {len(trades_df)}")
print(f"Expected: ~53 trades (AlgoTest)")
print(f"Match: {'✓ YES' if 50 <= len(trades_df) <= 56 else '✗ NO - MISMATCH!'}")
print()

if len(trades_df) > 0:
    print("First 5 trades:")
    print(trades_df[['Entry Date', 'Exit Date', 'Net P&L']].head())
    print()
    print(f"Total P&L: ₹{summary.get('total_pnl', 0):,.2f}")
    print(f"Win Rate: {summary.get('win_pct', 0):.2f}%")
