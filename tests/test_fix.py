import pandas as pd
import sys
import os

# Add backend to path
sys.path.insert(0, r'e:\Algo_Test_Software\backend')

# Test the compute_analytics function with sample data
from base import compute_analytics

# Create sample data with the column names that strategy engines actually use
sample_data = pd.DataFrame({
    'entry_date': ['2020-01-01', '2020-01-08', '2020-01-15'],
    'exit_date': ['2020-01-07', '2020-01-14', '2020-01-21'],
    'entry_spot': [10000, 10100, 10200],
    'exit_spot': [10050, 10150, 10250],
    'spot_pnl': [50, 50, 50],
    'net_pnl': [100, -50, 75],  # This is the actual column name used by strategy engines
    'call_strike': [10200, 10300, 10400],
    'call_pnl': [80, -30, 60],
    'put_strike': [9800, 9900, 10000],
    'put_pnl': [20, -20, 15]
})

print("Sample data columns:", sample_data.columns.tolist())
print("Sample data:")
print(sample_data)

print("\nTesting compute_analytics function...")
try:
    result_df, summary = compute_analytics(sample_data)
    print("✓ compute_analytics executed successfully")
    print("Result columns:", result_df.columns.tolist())
    print("Summary:", summary)
except Exception as e:
    print(f"✗ Error in compute_analytics: {e}")
    import traceback
    traceback.print_exc()

print("\nTesting build_pivot function...")
try:
    from base import build_pivot
    pivot_result = build_pivot(sample_data, 'entry_date')
    print("✓ build_pivot executed successfully")
    print("Pivot result:", pivot_result)
except Exception as e:
    print(f"✗ Error in build_pivot: {e}")
    import traceback
    traceback.print_exc()