import pandas as pd
import os
from datetime import datetime

def diagnose_strike_availability():
    print("=== STRIKE AVAILABILITY DIAGNOSTIC ===\n")
    
    # Check what strikes are actually available in your data
    test_dates = ["2019-02-28", "2019-03-07", "2019-03-14", "2019-03-28", "2019-04-04"]
    missing_strikes = [10800, 11100, 11300, 11500, 11600, 11700, 11800, 11900, 12000, 12100, 12200, 12300]
    
    print("ANALYZING AVAILABLE STRIKES FOR KEY DATES:")
    print("=" * 60)
    
    for date in test_dates:
        csv_file = f"cleaned_csvs/{date}.csv"
        if os.path.exists(csv_file):
            try:
                df = pd.read_csv(csv_file)
                nifty_options = df[df['Symbol'] == 'NIFTY']
                call_options = nifty_options[nifty_options['OptionType'] == 'CE']
                
                if len(call_options) > 0:
                    available_strikes = sorted(call_options['StrikePrice'].unique())
                    print(f"\n{date}:")
                    print(f"  Available strikes: {len(available_strikes)}")
                    print(f"  Strike range: {min(available_strikes)} - {max(available_strikes)}")
                    print(f"  Sample strikes: {available_strikes[:20]}")
                    
                    # Check which missing strikes are actually missing
                    missing_on_date = [strike for strike in missing_strikes if strike not in available_strikes]
                    if missing_on_date:
                        print(f"  ❌ Missing requested strikes: {missing_on_date}")
                    else:
                        print(f"  ✅ All requested strikes available")
                else:
                    print(f"\n{date}: NO CALL OPTIONS FOUND")
            except Exception as e:
                print(f"\n{date}: Error reading file - {e}")
        else:
            print(f"\n{date}: File not found")
    
    print("\n" + "=" * 60)
    print("STRATEGY BEHAVIOR ANALYSIS:")
    print("- The strategy calculates required strikes based on spot price and parameters")
    print("- It looks for those exact strikes in your bhavcopy data")
    print("- When strikes are missing, it generates warnings but continues execution")
    print("- This is NORMAL behavior when options chain data is incomplete")
    
    print("\nSOLUTIONS:")
    print("1. Use date ranges with better data coverage")
    print("2. Accept that some warnings are normal with real market data")
    print("3. The strategy can still work - it processes available data")

if __name__ == "__main__":
    diagnose_strike_availability()