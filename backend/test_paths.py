import os
from base import STRIKE_DATA_DIR, CLEANED_CSV_DIR, EXPIRY_DATA_DIR, FILTER_DIR

print("=== Path Configuration Test ===")
print(f"STRIKE_DATA_DIR: {STRIKE_DATA_DIR}")
print(f"CLEANED_CSV_DIR: {CLEANED_CSV_DIR}")
print(f"EXPIRY_DATA_DIR: {EXPIRY_DATA_DIR}")
print(f"FILTER_DIR: {FILTER_DIR}")

print("\n=== File Existence Check ===")
strike_file = os.path.join(STRIKE_DATA_DIR, "Nifty_strike_data.csv")
print(f"Strike data file path: {strike_file}")
print(f"Strike data file exists: {os.path.exists(strike_file)}")

expiry_file = os.path.join(EXPIRY_DATA_DIR, "NIFTY.csv")
print(f"Expiry data file path: {expiry_file}")
print(f"Expiry data file exists: {os.path.exists(expiry_file)}")

filter_file = os.path.join(FILTER_DIR, "base2.csv")
print(f"Filter data file path: {filter_file}")
print(f"Filter data file exists: {os.path.exists(filter_file)}")