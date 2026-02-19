"""
Check what price fields are available in the CSV
"""
import sys
sys.path.append('backend')

from base import load_bhavcopy
import pandas as pd

test_date = '2020-01-07'
strike = 12150
option_type = 'CE'
expiry = '2020-01-09'

print(f"Loading bhavcopy for {test_date}...")
bhav_df = load_bhavcopy(test_date)

if bhav_df is not None and not bhav_df.empty:
    print(f"✓ Loaded {len(bhav_df)} rows")
    print()
    
    # Show all columns
    print("Available columns:")
    print(bhav_df.columns.tolist())
    print()
    
    # Find the specific option
    nifty_options = bhav_df[bhav_df['Symbol'] == 'NIFTY']
    expiry_ts = pd.to_datetime(expiry)
    
    target = nifty_options[
        (nifty_options['OptionType'] == 'CE') &
        (abs(nifty_options['StrikePrice'] - strike) <= 1) &
        (abs(nifty_options['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1))
    ]
    
    if not target.empty:
        print(f"Found option: NIFTY CE {strike} expiring {expiry}")
        print()
        print("All price fields:")
        row = target.iloc[0]
        for col in ['Open', 'High', 'Low', 'Close', 'LastPrice', 'SettlePrice']:
            if col in bhav_df.columns:
                print(f"  {col}: {row.get(col, 'N/A')}")
        print()
        
        # Show the full row
        print("Full row data:")
        print(row)
