"""
Run a minimal backtest to see debug output for exit price lookup
"""
import sys
import pandas as pd
sys.path.append('backend')

from engines.generic_algotest_engine import run_algotest_backtest
from datetime import datetime

# Minimal config matching user's setup
config = {
    'index': 'NIFTY',
    'entry_dte': 2,  # 2 days before expiry
    'exit_dte': 0,   # On expiry day
    'legs': [
        {
            'segment': 'OPTIONS',
            'option_type': 'CE',
            'strike_selection': 'ATM',
            'position': 'SELL',
            'lots': 1
        }
    ],
    'from_date': '2020-01-07',
    'to_date': '2020-04-15',  # Match user's AlgoTest data
    'initial_capital': 100000,
    'stop_loss_pct': None,
    'target_pct': None,
    'spot_adjustment_type': 0,
    'spot_adjustment': 0
}

print("=" * 80)
print("RUNNING BACKTEST WITH DEBUG LOGGING")
print("=" * 80)
print(f"Index: {config['index']}")
print(f"Entry DTE: {config['entry_dte']}")
print(f"Exit DTE: {config['exit_dte']}")
print(f"Period: {config['from_date']} to {config['to_date']}")
print("=" * 80)
print()

# Run backtest
trades_df, analytics, monthly = run_algotest_backtest(config)

print("\n" + "=" * 80)
print("RESULTS")
print("=" * 80)
print(f"\nTotal Trades: {len(trades_df)}")
print("\nFirst 15 trades:")
if not trades_df.empty:
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    print(trades_df[['Entry_Date', 'Exit_Date', 'Leg_1_Strike', 'Leg_1_Entry', 'Leg_1_Exit', 'Net_PnL']].head(15).to_string())
    
    # Check for zero exit prices
    zero_exits = trades_df[trades_df['Leg_1_Exit'] == 0.0]
    print(f"\n⚠️  Trades with ZERO exit price: {len(zero_exits)}")
    if not zero_exits.empty:
        print(zero_exits[['Entry_Date', 'Exit_Date', 'Leg_1_Strike', 'Leg_1_Entry', 'Leg_1_Exit']].to_string())
