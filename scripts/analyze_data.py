import os
import pandas as pd
from datetime import datetime

def analyze_data_coverage():
    """Analyze what data is available in your CSV files"""
    
    project_root = r'E:\Algo_Test_Software'
    cleaned_csvs_dir = os.path.join(project_root, 'cleaned_csvs')
    
    print("=== Data Coverage Analysis ===\n")
    
    # Check date range in strike data
    strike_file = os.path.join(project_root, 'strikeData', 'Nifty_strike_data.csv')
    if os.path.exists(strike_file):
        try:
            strike_df = pd.read_csv(strike_file)
            strike_df['Date'] = pd.to_datetime(strike_df['Date'], dayfirst=True)
            print(f"Strike Data Date Range:")
            print(f"  Min: {strike_df['Date'].min()}")
            print(f"  Max: {strike_df['Date'].max()}")
            print(f"  Total rows: {len(strike_df)}")
            print()
        except Exception as e:
            print(f"Error reading strike data: {e}\n")
    
    # Check available bhavcopy files
    if os.path.exists(cleaned_csvs_dir):
        csv_files = [f for f in os.listdir(cleaned_csvs_dir) if f.endswith('.csv')]
        print(f"Found {len(csv_files)} bhavcopy CSV files")
        
        if csv_files:
            # Check a sample file to understand structure
            sample_file = os.path.join(cleaned_csvs_dir, csv_files[0])
            try:
                sample_df = pd.read_csv(sample_file)
                print(f"\nSample file structure ({csv_files[0]}):")
                print(f"  Columns: {list(sample_df.columns)}")
                print(f"  Rows: {len(sample_df)}")
                
                # Check date range in sample
                if 'Date' in sample_df.columns:
                    sample_df['Date'] = pd.to_datetime(sample_df['Date'], dayfirst=True)
                    print(f"  Date range: {sample_df['Date'].min()} to {sample_df['Date'].max()}")
                
                # Check available strikes if it's options data
                if 'StrikePrice' in sample_df.columns:
                    strikes = sorted(sample_df['StrikePrice'].unique())
                    print(f"  Available strikes: {len(strikes)} unique values")
                    print(f"  Strike range: {min(strikes)} to {max(strikes)}")
                    print(f"  Sample strikes: {strikes[:10]}...")
                    
            except Exception as e:
                print(f"Error reading sample file: {e}")
    
    print("\n=== Recommendations ===")
    print("1. Ensure your date range (01-01-2025 to 02-01-2026) is covered by your data")
    print("2. Verify strike prices in your range exist in the bhavcopy files")
    print("3. Check that you have both OPTIDX (options) and FUTIDX (futures) data")

if __name__ == "__main__":
    analyze_data_coverage()