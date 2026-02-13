"""
Quick test for V10 Days Before Expiry Strategy
"""
import sys
sys.path.append('backend')

from algotest_engine import run_backtest, format_response

# Test parameters
params = {
    "index": "NIFTY",
    "from_date": "2024-01-01",
    "to_date": "2024-03-31",
    "entry_days_before_expiry": 5,
    "exit_days_before_expiry": 3,
    "option_type": "CE",
    "position_type": "Buy",
    "strike_offset": 0,
    "expiry_type": "weekly",
    "initial_capital": 100000
}

print("Testing V10 Strategy - Days Before Expiry")
print("=" * 60)
print(f"Parameters:")
for key, value in params.items():
    print(f"  {key}: {value}")
print("=" * 60)

try:
    # Run backtest
    df, summary, pivot = run_backtest("v10", params)
    
    print(f"\nBacktest completed successfully!")
    print(f"Total trades: {len(df)}")
    print(f"\nSummary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    
    if not df.empty:
        print(f"\nFirst 3 trades:")
        print(df.head(3).to_string())
        
except Exception as e:
    print(f"\nError: {str(e)}")
    import traceback
    traceback.print_exc()
