"""
Diagnostic script to check why exit prices are showing as 0.00
"""
import pandas as pd
from datetime import datetime
import sys
sys.path.append('backend')

from base import get_option_premium_from_db, load_bhavcopy

# Test case from user's data:
# Entry Date: 2020-01-28
# Exit Date: 2020-01-30
# Strike: 12050
# Option Type: CE
# Expected Exit Premium: Should be > 0 (AlgoTest shows small values like 0.05, 0.10)

print("=" * 80)
print("DIAGNOSTIC: Exit Price Lookup Issue")
print("=" * 80)

# Test 1: Check what's in the CSV for 2020-01-30
print("\n1. Checking CSV data for 2020-01-30...")
df = pd.read_csv('cleaned_csvs/2020-01-30.csv')
df['Date'] = pd.to_datetime(df['Date'])
df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])

# Filter for NIFTY 12050 CE
nifty_12050 = df[(df['Symbol'] == 'NIFTY') & 
                  (df['StrikePrice'] == 12050) & 
                  (df['OptionType'] == 'CE')]

print(f"\nFound {len(nifty_12050)} rows for NIFTY 12050 CE:")
print(nifty_12050[['Date', 'ExpiryDate', 'Symbol', 'StrikePrice', 'OptionType', 'Close']].to_string())

# Test 2: Try the actual function call
print("\n" + "=" * 80)
print("2. Testing get_option_premium_from_db() function...")
print("=" * 80)

# Try with expiry = 2020-01-30 (same day expiry)
print("\nTest A: Expiry = 2020-01-30 (same as exit date)")
result = get_option_premium_from_db(
    date='2020-01-30',
    index='NIFTY',
    strike=12050,
    option_type='CE',
    expiry='2020-01-30'
)
print(f"Result: {result}")

# Try with expiry = 2020-02-06 (next week)
print("\nTest B: Expiry = 2020-02-06 (next week)")
result = get_option_premium_from_db(
    date='2020-01-30',
    index='NIFTY',
    strike=12050,
    option_type='CE',
    expiry='2020-02-06'
)
print(f"Result: {result}")

# Test 3: Check all available expiries for this strike on this date
print("\n" + "=" * 80)
print("3. All available expiries for NIFTY 12050 CE on 2020-01-30:")
print("=" * 80)
for _, row in nifty_12050.iterrows():
    print(f"Expiry: {row['ExpiryDate'].strftime('%Y-%m-%d')}, Close: {row['Close']}")

print("\n" + "=" * 80)
print("CONCLUSION:")
print("=" * 80)
print("If the function returns None but data exists in CSV, there's a mismatch")
print("in how the expiry date is being matched.")
