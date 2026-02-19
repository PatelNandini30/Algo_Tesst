import os
import sys

# Add the backend directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

try:
    from backend.base import STRIKE_DATA_DIR, PROJECT_ROOT
    print("=== Path Debug Information ===")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Script location: {os.path.abspath(__file__)}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Strike data directory: {STRIKE_DATA_DIR}")
    
    # Check if the file exists
    strike_file = os.path.join(STRIKE_DATA_DIR, "Nifty_strike_data.csv")
    print(f"Strike file path: {strike_file}")
    print(f"Strike file exists: {os.path.exists(strike_file)}")
    
    if not os.path.exists(strike_file):
        print("Looking for alternative paths...")
        # Try some common alternative paths
        alternative_paths = [
            os.path.join(os.getcwd(), 'strikeData', 'Nifty_strike_data.csv'),
            os.path.join(os.path.dirname(os.getcwd()), 'strikeData', 'Nifty_strike_data.csv'),
            os.path.join(os.path.dirname(__file__), 'strikeData', 'Nifty_strike_data.csv')
        ]
        
        for alt_path in alternative_paths:
            print(f"Checking: {alt_path} - Exists: {os.path.exists(alt_path)}")
            
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()