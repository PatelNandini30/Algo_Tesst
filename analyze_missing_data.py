import pandas as pd
import os
from datetime import datetime

def analyze_missing_data():
    print("=== ANALYZING MISSING OPTIONS DATA ===\n")
    
    # Check what data is available for 2019
    target_date = "2019-02-28"  # One of the dates mentioned in warnings
    csv_file = f"cleaned_csvs/{target_date}.csv"
    
    if os.path.exists(csv_file):
        print(f"Checking data for {target_date}:")
        df = pd.read_csv(csv_file)
        
        # Filter for NIFTY options
        nifty_options = df[df['Symbol'] == 'NIFTY']
        print(f"Total NIFTY records: {len(nifty_options)}")
        
        # Show available strikes and option types
        call_options = nifty_options[nifty_options['OptionType'] == 'CE']
        put_options = nifty_options[nifty_options['OptionType'] == 'PE']
        
        print(f"Call options (CE): {len(call_options)}")
        print(f"Put options (PE): {len(put_options)}")
        
        if len(call_options) > 0:
            print("\nAvailable call strikes:")
            strikes = sorted(call_options['StrikePrice'].unique())
            print(f"Strike range: {min(strikes)} to {max(strikes)}")
            print(f"Sample strikes: {strikes[:10]}")
            
            # Check for the specific missing strike (10800)
            strike_10800 = call_options[call_options['StrikePrice'] == 10800]
            print(f"\nData for strike 10800:")
            if len(strike_10800) > 0:
                print(strike_10800[['ExpiryDate', 'Open', 'High', 'Low', 'Close']].to_string(index=False))
            else:
                print("NO DATA FOUND for strike 10800")
        
        # Show expiry dates available
        expiries = sorted(nifty_options['ExpiryDate'].unique())
        print(f"\nAvailable expiry dates: {expiries}")
        
    else:
        print(f"No data file found for {target_date}")
    
    print("\n" + "="*50)
    
    # Check overall data coverage
    print("\nChecking data coverage for 2019:")
    total_files = 0
    files_with_data = 0
    
    for filename in os.listdir("cleaned_csvs"):
        if filename.startswith("2019-") and filename.endswith(".csv"):
            total_files += 1
            file_path = f"cleaned_csvs/{filename}"
            try:
                df = pd.read_csv(file_path)
                nifty_data = df[df['Symbol'] == 'NIFTY']
                if len(nifty_data) > 0:
                    files_with_data += 1
            except:
                pass
    
    print(f"Total 2019 files: {total_files}")
    print(f"Files with NIFTY data: {files_with_data}")
    print(f"Coverage: {files_with_data/total_files*100:.1f}%")

if __name__ == "__main__":
    analyze_missing_data()