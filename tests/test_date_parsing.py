import os
import sys
import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

try:
    from backend.base import get_strike_data, load_expiry, load_base2
    
    print("=== Testing Date Parsing Fix ===")
    
    # Test 1: Strike data parsing
    print("\n1. Testing strike data date parsing...")
    try:
        strike_data = get_strike_data("NIFTY", "2019-01-01", "2019-01-31")
        print(f"   ✅ Strike data loaded successfully: {len(strike_data)} rows")
        print(f"   Sample dates: {strike_data['Date'].head(3).tolist()}")
    except Exception as e:
        print(f"   ❌ Strike data error: {e}")
    
    # Test 2: Expiry data parsing
    print("\n2. Testing expiry data date parsing...")
    try:
        expiry_data = load_expiry("NIFTY", "weekly")
        print(f"   ✅ Expiry data loaded successfully: {len(expiry_data)} rows")
        print(f"   Sample current expiries: {expiry_data['Current Expiry'].head(3).tolist()}")
    except Exception as e:
        print(f"   ❌ Expiry data error: {e}")
    
    # Test 3: Base2 data parsing
    print("\n3. Testing base2 data date parsing...")
    try:
        base2_data = load_base2()
        print(f"   ✅ Base2 data loaded successfully: {len(base2_data)} rows")
        print(f"   Sample start dates: {base2_data['Start'].head(3).tolist()}")
    except Exception as e:
        print(f"   ❌ Base2 data error: {e}")
        
    print("\n=== Date Parsing Tests Complete ===")
    
except Exception as e:
    print(f"Error importing backend modules: {e}")
    import traceback
    traceback.print_exc()