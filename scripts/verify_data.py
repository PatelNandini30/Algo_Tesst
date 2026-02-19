import os
import pandas as pd

def verify_data_availability():
    print("=== DATA AVAILABILITY VERIFICATION ===\n")
    
    project_root = r'E:\Algo_Test_Software'
    
    # Check directories
    print("1. DIRECTORY CHECK:")
    print("-" * 30)
    dirs_to_check = ['cleaned_csvs', 'strikeData', 'expiryData']
    for dir_name in dirs_to_check:
        dir_path = os.path.join(project_root, dir_name)
        if os.path.exists(dir_path):
            files = os.listdir(dir_path)
            print(f"✅ {dir_name}: {len(files)} files")
            if files:
                print(f"   Sample files: {files[:3]}")
        else:
            print(f"❌ {dir_name}: NOT FOUND")
    
    print("\n2. STRIKE DATA CHECK:")
    print("-" * 30)
    strike_file = os.path.join(project_root, 'strikeData', 'Nifty_strike_data.csv')
    if os.path.exists(strike_file):
        try:
            df = pd.read_csv(strike_file)
            df['Date'] = pd.to_datetime(df['Date'], dayfirst=True)
            print(f"✅ Strike data loaded: {len(df)} rows")
            print(f"   Date range: {df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}")
        except Exception as e:
            print(f"❌ Error reading strike data: {e}")
    else:
        print("❌ Strike data file not found")
    
    print("\n3. OPTIONS DATA CHECK:")
    print("-" * 30)
    csv_dir = os.path.join(project_root, 'cleaned_csvs')
    if os.path.exists(csv_dir):
        csv_files = [f for f in os.listdir(csv_dir) if f.endswith('.csv')]
        print(f"✅ CSV files found: {len(csv_files)}")
        
        if csv_files:
            # Check a few sample files
            sample_files = csv_files[:5]
            years_found = set()
            for file in sample_files:
                try:
                    file_path = os.path.join(csv_dir, file)
                    df = pd.read_csv(file_path)
                    if not df.empty:
                        # Extract year from filename or first date
                        if '-' in file:
                            year = file.split('-')[0]
                            years_found.add(year)
                        print(f"   {file}: {len(df)} rows")
                except Exception as e:
                    print(f"   {file}: Error - {e}")
            
            if years_found:
                print(f"   Years found: {sorted(list(years_found))}")
    else:
        print("❌ cleaned_csvs directory not found")

if __name__ == "__main__":
    verify_data_availability()