"""
Test backtest with 2020 data to match user's expected output
"""
import sys
sys.path.append('backend')

from engines.generic_algotest_engine import run_algotest_backtest

# Match user's expected configuration
params = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2020-03-31',
    'expiry_type': 'WEEKLY',
    'entry_dte': 2,
    'exit_dte': 0,
    'legs': [
        {
            'segment': 'OPTIONS',
            'option_type': 'CE',
            'position': 'SELL',
            'lots': 65,
            'strike_selection': 'ATM',
            'expiry': 'WEEKLY'
        }
    ]
}

print("Running backtest with 2020 data...")
print(f"Config: {params}")
print()

df, summary, pivot = run_algotest_backtest(params)

if not df.empty:
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Total trades: {len(df)}")
    print()
    
    # Show first 3 trades
    print("First 3 trades:")
    for idx, row in df.head(3).iterrows():
        print(f"\nTrade {idx + 1}:")
        print(f"  Entry Date: {row['entry_date']}")
        print(f"  Exit Date: {row['exit_date']}")
        print(f"  Entry Spot: {row['entry_spot']}")
        print(f"  Exit Spot: {row.get('exit_spot', 'N/A')}")
        print(f"  Net P&L: {row['total_pnl']}")
        print(f"  Cumulative: {row['cumulative_pnl']}")
        
        # Show leg details
        if 'leg1_type' in row:
            print(f"  Leg 1 Type: {row['leg1_type']}")
        if 'leg1_strike' in row:
            print(f"  Leg 1 Strike: {row['leg1_strike']}")
        if 'leg1_entry' in row:
            print(f"  Leg 1 Entry: {row['leg1_entry']}")
        if 'leg1_exit' in row:
            print(f"  Leg 1 Exit: {row['leg1_exit']}")
        if 'leg1_pnl' in row:
            print(f"  Leg 1 P&L: {row['leg1_pnl']}")
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total P&L: {summary.get('total_pnl', 0)}")
    print(f"Win Rate: {summary.get('win_rate', 0)}%")
    print(f"Total Trades: {summary.get('total_trades', 0)}")
else:
    print("❌ No trades generated")
