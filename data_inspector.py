import pandas as pd
import os
from datetime import datetime

def inspect_data():
    print("=== ALGO TEST DATA INSPECTION ===\n")
    
    # Check strike data date range
    project_root = r'E:\Algo_Test_Software'
    strike_file = os.path.join(project_root, 'strikeData', 'Nifty_strike_data.csv')
    
    print("1. STRIKE DATA ANALYSIS:")
    print("-" * 40)
    if os.path.exists(strike_file):
        try:
            strike_df = pd.read_csv(strike_file)
            strike_df['Date'] = pd.to_datetime(strike_df['Date'], dayfirst=True)
            print(f"   Strike Data Date Range:")
            print(f"     Min: {strike_df['Date'].min().strftime('%Y-%m-%d')}")
            print(f"     Max: {strike_df['Date'].max().strftime('%Y-%m-%d')}")
            print(f"     Total rows: {len(strike_df)}")
            print(f"     Sample dates: {strike_df['Date'].head(3).dt.strftime('%Y-%m-%d').tolist()}")
        except Exception as e:
            print(f"   Error reading strike data: {e}")
    else:
        print("   Strike data file not found")
    
    print("\n2. CLEANED CSV FILES:")
    print("-" * 40)
    cleaned_csvs_dir = os.path.join(project_root, 'cleaned_csvs')
    if os.path.exists(cleaned_csvs_dir):
        csv_files = [f for f in os.listdir(cleaned_csvs_dir) if f.endswith('.csv')]
        print(f"   Total CSV files: {len(csv_files)}")
        
        # Get date range from filenames
        dates = []
        for filename in csv_files:
            try:
                date_str = filename.replace('.csv', '')
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                dates.append(date_obj)
            except:
                continue
        
        if dates:
            print(f"   Date range from filenames:")
            print(f"     Min: {min(dates).strftime('%Y-%m-%d')}")
            print(f"     Max: {max(dates).strftime('%Y-%m-%d')}")
            
            # Check a sample file
            sample_file = os.path.join(cleaned_csvs_dir, '2020-01-01.csv')
            if os.path.exists(sample_file):
                print(f"\n3. SAMPLE FILE CONTENT (2020-01-01.csv):")
                print("-" * 40)
                df = pd.read_csv(sample_file)
                print(f"   Total rows: {len(df)}")
                print(f"   Columns: {list(df.columns)}")
                print(f"   Instrument types: {df['Instrument'].unique().tolist()}")
                print(f"   Symbols available: {df['Symbol'].unique().tolist()}")
                
                # Show options data sample
                options_data = df[df['Instrument'] == 'OPTIDX']
                if not options_data.empty:
                    print(f"   Options data rows: {len(options_data)}")
                    print("   Sample options entries:")
                    print(options_data[['Symbol', 'StrikePrice', 'OptionType', 'Close', 'ExpiryDate']].head(3).to_string(index=False))
                else:
                    print("   No options data found in sample file")
    
    print("\n4. BACKTEST CONFIGURATION ISSUE:")
    print("-" * 40)
    print("   Your backtest is configured for dates: 2025-01-01 to 2026-02-01")
    print("   But your data only covers: 2000-01-03 to 2020-01-31")
    print("   SOLUTION: Change your backtest date range to use 2000-2020 data")
    print("\n   Example working dates: 2019-01-01 to 2019-12-31")
    print("   or 2020-01-01 to 2020-01-31")

if __name__ == "__main__":
    inspect_data()