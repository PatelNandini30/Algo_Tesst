"""
Check exit premium for the first trade
User expects: Entry 43.85, Exit 66.1, P&L -1446.25 with 65 lots
"""
import sys
sys.path.append('backend')

from base import get_option_premium_from_db, load_bhavcopy
import pandas as pd

# Trade 1 from user's data
entry_date = '2020-01-07'
exit_date = '2020-01-09'
strike = 12150
option_type = 'CE'
expiry = '2020-01-09'
lots = 65

print("User's Expected Trade 1:")
print(f"  Entry Date: {entry_date}")
print(f"  Exit Date: {exit_date}")
print(f"  Strike: {strike}")
print(f"  Option Type: {option_type}")
print(f"  Position: SELL")
print(f"  Lots: {lots}")
print(f"  Expected Entry: 43.85")
print(f"  Expected Exit: 66.1")
print(f"  Expected P&L: -1446.25")
print()

# Get entry premium
print(f"Getting entry premium for {entry_date}...")
entry_premium = get_option_premium_from_db(entry_date, 'NIFTY', strike, option_type, expiry)
print(f"  Result: {entry_premium}")
print()

# Get exit premium
print(f"Getting exit premium for {exit_date}...")
exit_premium = get_option_premium_from_db(exit_date, 'NIFTY', strike, option_type, expiry)
print(f"  Result: {exit_premium}")
print()

# Calculate P&L with our data
if entry_premium and exit_premium:
    pnl = (entry_premium - exit_premium) * lots
    print(f"Calculated P&L with our data:")
    print(f"  ({entry_premium} - {exit_premium}) × {lots} = {pnl}")
    print()

# Verify the user's calculation
expected_pnl = (43.85 - 66.1) * 65
print(f"User's expected calculation:")
print(f"  (43.85 - 66.1) × 65 = {expected_pnl}")
print()

# Check what's in the exit date CSV
print(f"Checking exit date CSV ({exit_date})...")
bhav_df = load_bhavcopy(exit_date)
if bhav_df is not None and not bhav_df.empty:
    nifty_options = bhav_df[bhav_df['Symbol'] == 'NIFTY']
    expiry_ts = pd.to_datetime(expiry)
    
    target = nifty_options[
        (nifty_options['OptionType'] == 'CE') &
        (abs(nifty_options['StrikePrice'] - strike) <= 1) &
        (abs(nifty_options['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1))
    ]
    
    if not target.empty:
        print(f"  Found: Close = {target.iloc[0]['Close']}")
