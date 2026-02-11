import pandas as pd
import os
from datetime import datetime

def show_available_strikes():
    print("=== AVAILABLE STRIKE PRICES ANALYSIS ===\n")
    
    # Check multiple dates to see strike availability
    test_dates = ["2019-01-31", "2019-02-28", "2019-03-28", "2019-04-25", "2019-05-30"]
    
    for date in test_dates:
        csv_file = f"cleaned_csvs/{date}.csv"
        if os.path.exists(csv_file):
            try:
                df = pd.read_csv(csv_file)
                nifty_options = df[df['Symbol'] == 'NIFTY']
                call_options = nifty_options[nifty_options['OptionType'] == 'CE']
                
                if len(call_options) > 0:
                    strikes = sorted(call_options['StrikePrice'].unique())
                    print(f"{date}: {len(strikes)} call strikes available")
                    print(f"  Range: {min(strikes)} - {max(strikes)}")
                    print(f"  Sample: {strikes[:15]}")
                    print()
                else:
                    print(f"{date}: NO CALL OPTIONS FOUND")
                    print()
            except Exception as e:
                print(f"{date}: Error reading file - {e}")
                print()
        else:
            print(f"{date}: File not found")
            print()

    # Show what the strategy is looking for vs what's available
    print("=== STRATEGY REQUIREMENTS vs AVAILABLE DATA ===")
    print("The strategy is looking for strikes around NIFTY spot price")
    print("Current NIFTY spot range (2019): ~10,000 - 12,000")
    print("Common strike intervals: 50, 100 points")
    print()
    print("Missing strikes mentioned in warnings:")
    missing_strikes = [10800, 11100, 11300, 11500, 11600, 11700, 11900, 12000, 12100, 12200, 12300]
    print(f"Requested strikes: {missing_strikes}")
    print("(These might not exist in your data at the required expiry dates)")

if __name__ == "__main__":
    show_available_strikes()