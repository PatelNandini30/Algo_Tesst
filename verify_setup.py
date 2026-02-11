import os
import sys

def check_python():
    """Check Python installation"""
    print("=== Python Check ===")
    try:
        print(f"Python version: {sys.version}")
        print("✓ Python is installed")
        return True
    except Exception as e:
        print(f"✗ Python error: {e}")
        return False

def check_dependencies():
    """Check required Python dependencies"""
    print("\n=== Python Dependencies Check ===")
    dependencies = ['fastapi', 'uvicorn', 'pandas', 'numpy']
    all_good = True
    
    for dep in dependencies:
        try:
            __import__(dep)
            print(f"✓ {dep} is installed")
        except ImportError:
            print(f"✗ {dep} is not installed")
            all_good = False
    
    return all_good

def check_directories():
    """Check required directories"""
    print("\n=== Directory Structure Check ===")
    required_dirs = [
        'backend',
        'backend/routers',
        'backend/engines',
        'frontend',
        'frontend/src',
        'data',
        'data/cleaned_csvs',
        'data/expiryData',
        'data/strikeData',
        'data/Filter'
    ]
    
    all_good = True
    for dir_path in required_dirs:
        if os.path.exists(dir_path):
            print(f"✓ {dir_path} exists")
        else:
            print(f"✗ {dir_path} is missing")
            all_good = False
    
    return all_good

def check_files():
    """Check required files"""
    print("\n=== File Check ===")
    required_files = [
        'backend/main.py',
        'backend/routers/__init__.py',
        'backend/engines/__init__.py',
        'frontend/package.json',
        'requirements.txt'
    ]
    
    all_good = True
    for file_path in required_files:
        if os.path.exists(file_path):
            print(f"✓ {file_path} exists")
        else:
            print(f"✗ {file_path} is missing")
            all_good = False
    
    return all_good

def check_data_files():
    """Check if data files exist"""
    print("\n=== Data Files Check ===")
    data_files = [
        'data/strikeData/Nifty_strike_data.csv',
        'data/expiryData/NIFTY.csv',
        'data/expiryData/NIFTY_Monthly.csv',
        'data/Filter/base2.csv'
    ]
    
    found_files = 0
    for file_path in data_files:
        if os.path.exists(file_path):
            print(f"✓ {file_path} exists")
            found_files += 1
        else:
            print(f"⚠ {file_path} is missing (optional for testing)")
    
    print(f"Found {found_files}/{len(data_files)} required data files")
    return found_files > 0

def main():
    print("AlgoTest Clone - System Verification")
    print("=" * 40)
    
    checks = [
        ("Python Installation", check_python),
        ("Python Dependencies", check_dependencies),
        ("Directory Structure", check_directories),
        ("Required Files", check_files),
        ("Data Files", check_data_files)
    ]
    
    results = []
    for check_name, check_func in checks:
        print(f"\nRunning {check_name}...")
        result = check_func()
        results.append((check_name, result))
    
    print("\n" + "=" * 40)
    print("VERIFICATION SUMMARY")
    print("=" * 40)
    
    all_passed = True
    for check_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"{check_name}: {status}")
        if not result:
            all_passed = False
    
    print("\n" + "=" * 40)
    if all_passed:
        print("✓ All checks passed! You can start the servers.")
        print("\nTo start the servers:")
        print("1. Backend: cd backend && python -m uvicorn main:app --port 8000")
        print("2. Frontend: cd frontend && npm install && npm run dev")
    else:
        print("✗ Some checks failed. Please fix the issues above before starting.")
    
    return all_passed

if __name__ == "__main__":
    main()