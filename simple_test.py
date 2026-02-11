import os
import sys
import pandas as pd

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def simple_test():
    print("=== SIMPLE DATA TEST ===")
    
    # Test 1: Check if strike data exists
    strike_file = r'E:\Algo_Test_Software\strikeData\Nifty_strike_data.csv'
    print(f"Strike file exists: {os.path.exists(strike_file)}")
    
    if os.path.exists(strike_file):
        df = pd.read_csv(strike_file)
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True)
        print(f"Strike data: {len(df)} rows")
        print(f"Date range: {df['Date'].min()} to {df['Date'].max()}")
    
    # Test 2: Check cleaned_csvs directory
    csv_dir = r'E:\Algo_Test_Software\cleaned_csvs'
    print(f"CSV directory exists: {os.path.exists(csv_dir)}")
    
    if os.path.exists(csv_dir):
        files = [f for f in os.listdir(csv_dir) if f.endswith('.csv')]
        print(f"CSV files found: {len(files)}")
        
        # Check for 2019 files
        files_2019 = [f for f in files if f.startswith('2019')]
        print(f"2019 files: {len(files_2019)}")
        
        if files_2019:
            print(f"Sample 2019 files: {files_2019[:5]}")
            
            # Try to read one file
            try:
                sample_file = os.path.join(csv_dir, files_2019[0])
                df = pd.read_csv(sample_file)
                print(f"Sample file rows: {len(df)}")
                print(f"Columns: {list(df.columns)}")
            except Exception as e:
                print(f"Error reading sample file: {e}")

if __name__ == "__main__":
    simple_test()