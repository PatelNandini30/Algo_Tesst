import pandas as pd
import sys
import os

sys.path.append('backend')
from base import load_bhavcopy, get_option_premium_from_db

# Test the exact scenario from the logs
date = '2023-01-03'
index = 'NIFTY'
strike = 18300
option_type = 'CE'
expiry = '2023-01-05'

print(f"Testing option lookup:")
print(f"Date: {date}")
print(f"Index: {index}")
print(f"Strike: {strike}")
print(f"Option Type: {option_type}")
print(f"Expiry: {expiry}")
print("="*70)

# Load the CSV directly
df = load_bhavcopy(date)
print(f"\nTotal rows in CSV: {len(df)}")
print(f"NIFTY rows: {len(df[df['Symbol']=='NIFTY'])}")

# Check the specific option
expiry_ts = pd.to_datetime(expiry)
mask = (
    (df['Symbol'] == index) &
    (df['OptionType'].str.upper() == option_type) &
    (abs(df['StrikePrice'] - strike) <= 1) &
    (abs(df['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1))
)

matches = df[mask]
print(f"\nMatches found: {len(matches)}")
if not matches.empty:
    print(matches[['Symbol', 'StrikePrice', 'OptionType', 'ExpiryDate', 'Close']])
else:
    print("\nNo matches. Let's check each condition:")
    print(f"Symbol matches: {len(df[df['Symbol'] == index])}")
    print(f"OptionType matches: {len(df[df['OptionType'].str.upper() == option_type])}")
    print(f"Strike matches: {len(df[abs(df['StrikePrice'] - strike) <= 1])}")
    print(f"Expiry matches: {len(df[abs(df['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1)])}")
    
    # Check combined
    print(f"\nSymbol + OptionType: {len(df[(df['Symbol'] == index) & (df['OptionType'].str.upper() == option_type)])}")
    print(f"Symbol + Strike: {len(df[(df['Symbol'] == index) & (abs(df['StrikePrice'] - strike) <= 1)])}")
    print(f"Symbol + Expiry: {len(df[(df['Symbol'] == index) & (abs(df['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1))])}")

print("\n" + "="*70)
print("Now testing via get_option_premium_from_db function:")
premium = get_option_premium_from_db(date, index, strike, option_type, expiry)
print(f"Result: {premium}")
