import os
import pandas as pd
from datetime import datetime

def test_data_loading():
    output = []
    output.append("=== Testing Data Loading ===\n")
    
    # Test 1: Check cleaned_csvs directory
    output.append("1. CLEANED_CSVS DIRECTORY:")
    try:
        csv_files = [f for f in os.listdir("cleaned_csvs") if f.endswith(".csv")]
        output.append(f"   Total files: {len(csv_files)}")
        output.append(f"   Sample files: {csv_files[:5]}")
        
        # Read a sample file
        if csv_files:
            sample_file = f"cleaned_csvs/{csv_files[0]}"
            try:
                df = pd.read_csv(sample_file, nrows=3)
                output.append(f"   Sample file: {sample_file}")
                output.append(f"   Columns: {list(df.columns)}")
                output.append(f"   Data:\n{df.to_string(index=False)}")
            except Exception as e:
                output.append(f"   Error reading sample file: {e}")
    except Exception as e:
        output.append(f"   Error accessing cleaned_csvs directory: {e}")
    output.append("")
    
    # Test 2: Check strikeData directory
    output.append("2. STRIKE DATA DIRECTORY:")
    try:
        strike_files = [f for f in os.listdir("strikeData") if f.endswith(".csv")]
        output.append(f"   Total files: {len(strike_files)}")
        output.append(f"   Files: {strike_files}")
        
        # Read NIFTY strike data
        nifty_file = "strikeData/Nifty_strike_data.csv"
        if os.path.exists(nifty_file):
            try:
                df = pd.read_csv(nifty_file, nrows=3)
                output.append(f"   NIFTY data sample:")
                output.append(f"   Columns: {list(df.columns)}")
                output.append(f"   Data:\n{df.to_string(index=False)}")
            except Exception as e:
                output.append(f"   Error reading NIFTY file: {e}")
    except Exception as e:
        output.append(f"   Error accessing strikeData directory: {e}")
    output.append("")
    
    # Test 3: Check expiryData directory
    output.append("3. EXPIRY DATA DIRECTORY:")
    try:
        expiry_files = [f for f in os.listdir("expiryData") if f.endswith(".csv")]
        output.append(f"   Total files: {len(expiry_files)}")
        output.append(f"   Files: {expiry_files}")
        
        # Read NIFTY expiry data
        nifty_expiry = "expiryData/NIFTY.csv"
        if os.path.exists(nifty_expiry):
            try:
                df = pd.read_csv(nifty_expiry, nrows=3)
                output.append(f"   NIFTY expiry sample:")
                output.append(f"   Columns: {list(df.columns)}")
                output.append(f"   Data:\n{df.to_string(index=False)}")
            except Exception as e:
                output.append(f"   Error reading NIFTY expiry: {e}")
    except Exception as e:
        output.append(f"   Error accessing expiryData directory: {e}")
    output.append("")
    
    # Test 4: Check Filter directory
    output.append("4. FILTER DIRECTORY:")
    try:
        filter_files = [f for f in os.listdir("Filter") if f.endswith(".csv")]
        output.append(f"   Total files: {len(filter_files)}")
        output.append(f"   Files: {filter_files}")
        
        # Read base2 data
        base2_file = "Filter/base2.csv"
        if os.path.exists(base2_file):
            try:
                df = pd.read_csv(base2_file)
                output.append(f"   Base2 data sample:")
                output.append(f"   Columns: {list(df.columns)}")
                output.append(f"   Data:\n{df.head(3).to_string(index=False)}")
            except Exception as e:
                output.append(f"   Error reading base2 file: {e}")
    except Exception as e:
        output.append(f"   Error accessing Filter directory: {e}")
    output.append("")
    
    # Write output to file
    with open("data_loading_output.txt", "w") as f:
        f.write("\n".join(output))
    
    print("Output written to data_loading_output.txt")

if __name__ == "__main__":
    test_data_loading()