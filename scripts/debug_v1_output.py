import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.engines.v1_ce_fut import run_v1
from datetime import datetime

# Test parameters similar to what would be used in the UI
params = {
    "index": "NIFTY",
    "from_date": "2019-02-20",
    "to_date": "2019-03-20",
    "expiry_window": "weekly_expiry",
    "call_sell_position": 1.0,  # 1% OTM
    "spot_adjustment_type": 0,
    "spot_adjustment": 1.0
}

print("Running V1 strategy with parameters:", params)
df, summary, pivot = run_v1(params)

print("\nDataFrame columns:", df.columns.tolist())
print("\nFirst few rows of the DataFrame:")
print(df.head())

print("\nSummary:", summary)
print("\nPivot:", pivot)

# Save to CSV to inspect the raw data
if not df.empty:
    df.to_csv("debug_v1_output.csv", index=False)
    print("\nOutput saved to debug_v1_output.csv")
else:
    print("\nNo trades were generated.")