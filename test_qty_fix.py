"""
Test that Qty is now showing lots × lot_size instead of just lots
"""
import sys
sys.path.append('backend')

from backend.engines.generic_algotest_engine import run_algotest_backtest

params = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2020-02-01',
    'expiry_type': 'MONTHLY',
    'entry_dte': 5,
    'exit_dte': 0,
    'legs': [
        {'segment': 'OPTIONS', 'position': 'SELL', 'option_type': 'CALL', 'lots': 1, 'strike_selection': 'ATM'},
        {'segment': 'OPTIONS', 'position': 'BUY', 'option_type': 'PUT', 'lots': 1, 'strike_selection': 'ATM'}
    ]
}

trades_df, summary, pivot = run_algotest_backtest(params)

print("\n" + "="*80)
print("QTY FIX VERIFICATION")
print("="*80)
print("\nFirst trade details:")
print(trades_df[['Entry Date', 'Type', 'B/S', 'Qty', 'Entry Price', 'Net P&L']].head(2))
print()
print("Expected Qty: 65 (1 lot × 65 lot_size for NIFTY in 2020)")
print(f"Actual Qty: {trades_df.iloc[0]['Qty']}")
print(f"Match: {'✓ YES' if trades_df.iloc[0]['Qty'] == 65 else '✗ NO'}")
print("="*80)
