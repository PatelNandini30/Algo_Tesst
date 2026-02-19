"""
Diagnose why entry premiums are showing as 0.00
"""
import sys
sys.path.append('backend')

from base import get_option_premium_from_db, load_bhavcopy
import pandas as pd

# Test with the user's expected data
# Trade 1: 07-01-2020, CE 12150, Entry: 43.85

test_date = '2020-01-07'
index = 'NIFTY'
strike = 12150
option_type = 'CE'
expiry = '2020-01-09'  # From user's data

print(f"Testing get_option_premium_from_db:")
print(f"  Date: {test_date}")
print(f"  Index: {index}")
print(f"  Strike: {strike}")
print(f"  Option Type: {option_type}")
print(f"  Expiry: {expiry}")
print()

# First, check if the CSV file exists
print("Loading bhavcopy for date...")
bhav_df = load_bhavcopy(test_date)

if bhav_df is None or bhav_df.empty:
    print("❌ No bhavcopy data found!")
else:
    print(f"✓ Loaded {len(bhav_df)} rows")
    print()
    
    # Check what symbols are available
    print("Available symbols:")
    print(bhav_df['Symbol'].unique()[:20])
    print()
    
    # Check NIFTY options
    nifty_options = bhav_df[bhav_df['Symbol'] == 'NIFTY']
    print(f"NIFTY rows: {len(nifty_options)}")
    
    if not nifty_options.empty:
        print("\nSample NIFTY options:")
        print(nifty_options[['Symbol', 'OptionType', 'StrikePrice', 'ExpiryDate', 'Close']].head(10))
        print()
        
        # Check for the specific strike
        strike_matches = nifty_options[abs(nifty_options['StrikePrice'] - strike) <= 1]
        print(f"\nOptions near strike {strike}:")
        print(strike_matches[['Symbol', 'OptionType', 'StrikePrice', 'ExpiryDate', 'Close']])
        print()
        
        # Check expiry dates
        print("Available expiry dates for NIFTY:")
        print(nifty_options['ExpiryDate'].unique()[:10])
        print()

# Now test the function
print("\nCalling get_option_premium_from_db...")
premium = get_option_premium_from_db(test_date, index, strike, option_type, expiry)
print(f"\nResult: {premium}")
print(f"Expected: 43.85")
