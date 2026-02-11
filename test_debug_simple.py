import os
import pandas as pd
from datetime import datetime

def test_data_loading():
    print("=== Testing Data Loading ===\n")
    
    # Test 1: Check cleaned_csvs directory
    print("1. CLEANED_CSVS DIRECTORY:")
    csv_files = [f for f in os.listdir("cleaned_csvs") if f.endswith(".csv")]
    print(f"   Total files: {len(csv_files)}")
    print(f"   Sample files: {csv_files[:5]}")
    
    # Read a sample file
    if csv_files:
        sample_file = f"cleaned_csvs/{csv_files[0]}"
        try:
            df = pd.read_csv(sample_file, nrows=3)
            print(f"   Sample file: {sample_file}")
            print(f"   Columns: {list(df.columns)}")
            print(f"   Data:\n{df.to_string(index=False)}")
        except Exception as e:
            print(f"   Error reading sample file: {e}")
    print()
    
    # Test 2: Check strikeData directory
    print("2. STRIKE DATA DIRECTORY:")
    strike_files = [f for f in os.listdir("strikeData") if f.endswith(".csv")]
    print(f"   Total files: {len(strike_files)}")
    print(f"   Files: {strike_files}")
    
    # Read NIFTY strike data
    nifty_file = "strikeData/Nifty_strike_data.csv"
    if os.path.exists(nifty_file):
        try:
            df = pd.read_csv(nifty_file, nrows=3)
            print(f"   NIFTY data sample:")
            print(f"   Columns: {list(df.columns)}")
            print(f"   Data:\n{df.to_string(index=False)}")
        except Exception as e:
            print(f"   Error reading NIFTY file: {e}")
    print()
    
    # Test 3: Check expiryData directory
    print("3. EXPIRY DATA DIRECTORY:")
    expiry_files = [f for f in os.listdir("expiryData") if f.endswith(".csv")]
    print(f"   Total files: {len(expiry_files)}")
    print(f"   Files: {expiry_files}")
    
    # Read NIFTY expiry data
    nifty_expiry = "expiryData/NIFTY.csv"
    if os.path.exists(nifty_expiry):
        try:
            df = pd.read_csv(nifty_expiry, nrows=3)
            print(f"   NIFTY expiry sample:")
            print(f"   Columns: {list(df.columns)}")
            print(f"   Data:\n{df.to_string(index=False)}")
        except Exception as e:
            print(f"   Error reading NIFTY expiry: {e}")
    print()
    
    # Test 4: Check Filter directory
    print("4. FILTER DIRECTORY:")
    filter_files = [f for f in os.listdir("Filter") if f.endswith(".csv")]
    print(f"   Total files: {len(filter_files)}")
    print(f"   Files: {filter_files}")
    
    # Read base2 data
    base2_file = "Filter/base2.csv"
    if os.path.exists(base2_file):
        try:
            df = pd.read_csv(base2_file)
            print(f"   Base2 data sample:")
            print(f"   Columns: {list(df.columns)}")
            print(f"   Data:\n{df.head(3).to_string(index=False)}")
        except Exception as e:
            print(f"   Error reading base2 file: {e}")
    print()

if __name__ == "__main__":
    test_data_loading()