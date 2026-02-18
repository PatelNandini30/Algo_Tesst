"""
Test the specific trade from user's AlgoTest data
"""
import pandas as pd
import sys
sys.path.append('backend')

from base import get_option_premium_from_db

print("=" * 80)
print("Testing Trade #1 from AlgoTest")
print("=" * 80)
print("Entry Date: 2020-01-07")
print("Exit Date: 2020-01-09")
print("Strike: 12150 CE")
print("Expected Entry Premium: 43.85")
print("Expected Exit Premium: 66.10")
print("=" * 80)

# Check expiry calendar for 2020-01-07
df = pd.read_csv('expiryData/NIFTY.csv')
df['Current Expiry'] = pd.to_datetime(df['Current Expiry'])
expiry_row = df[df['Current Expiry'] >= '2020-01-07'].iloc[0]
print(f"\nExpiry for entry on 2020-01-07: {expiry_row['Current Expiry']}")

# Test entry premium
print("\n1. Testing ENTRY premium (2020-01-07):")
entry_premium = get_option_premium_from_db(
    date='2020-01-07',
    index='NIFTY',
    strike=12150,
    option_type='CE',
    expiry=expiry_row['Current Expiry'].strftime('%Y-%m-%d')
)
print(f"   Result: {entry_premium}")
print(f"   Expected: 43.85")
print(f"   Match: {'✅' if entry_premium and abs(entry_premium - 43.85) < 5 else '❌'}")

# Test exit premium
print("\n2. Testing EXIT premium (2020-01-09):")
exit_premium = get_option_premium_from_db(
    date='2020-01-09',
    index='NIFTY',
    strike=12150,
    option_type='CE',
    expiry=expiry_row['Current Expiry'].strftime('%Y-%m-%d')
)
print(f"   Result: {exit_premium}")
print(f"   Expected: 66.10")
print(f"   Match: {'✅' if exit_premium and abs(exit_premium - 66.10) < 5 else '❌'}")

# Check what's actually in the CSV
print("\n3. Checking CSV data:")
print("\n   2020-01-07 data:")
df_entry = pd.read_csv('cleaned_csvs/2020-01-07.csv')
df_entry['ExpiryDate'] = pd.to_datetime(df_entry['ExpiryDate'])
matches = df_entry[(df_entry['Symbol'] == 'NIFTY') & 
                   (df_entry['StrikePrice'] == 12150) & 
                   (df_entry['OptionType'] == 'CE')]
print(matches[['Date', 'ExpiryDate', 'StrikePrice', 'OptionType', 'Close']].to_string())

print("\n   2020-01-09 data:")
df_exit = pd.read_csv('cleaned_csvs/2020-01-09.csv')
df_exit['ExpiryDate'] = pd.to_datetime(df_exit['ExpiryDate'])
matches = df_exit[(df_exit['Symbol'] == 'NIFTY') & 
                  (df_exit['StrikePrice'] == 12150) & 
                  (df_exit['OptionType'] == 'CE')]
print(matches[['Date', 'ExpiryDate', 'StrikePrice', 'OptionType', 'Close']].to_string())
