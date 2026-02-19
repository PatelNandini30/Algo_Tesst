import os

def check_files():
    print("=== FILE CHECK ===")
    
    # Check directories
    dirs = ['cleaned_csvs', 'strikeData', 'expiryData', 'Filter']
    
    for dir_name in dirs:
        dir_path = os.path.join(r'E:\Algo_Test_Software', dir_name)
        print(f"\n{dir_name}:")
        print("-" * 20)
        if os.path.exists(dir_path):
            try:
                files = os.listdir(dir_path)
                print(f"  Exists: YES ({len(files)} files)")
                if files:
                    print(f"  Sample files: {files[:5]}")
                    # Check for specific years
                    years = set()
                    for f in files[:20]:  # Check first 20 files
                        if f.endswith('.csv') and '-' in f:
                            year = f.split('-')[0]
                            if year.isdigit() and len(year) == 4:
                                years.add(year)
                    if years:
                        print(f"  Years found: {sorted(list(years))}")
            except Exception as e:
                print(f"  Error reading directory: {e}")
        else:
            print(f"  Exists: NO")

if __name__ == "__main__":
    check_files()