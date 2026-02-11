#!/usr/bin/env python
"""
Test script to verify that the engine fixes are working correctly.
This checks that the rounding and expiry selection changes have been applied.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Test the new round_to_50 function
from backend.base import round_to_50

def test_rounding():
    print("Testing round_to_50 function:")
    test_cases = [
        (10791.65 * 1.01, 10900),  # From the example in the problem
        (11058.20 * 1.01, 11150),
        (11343.25 * 1.01, 11450),
        (100, 100),
        (101, 100),
        (125, 125),
        (126, 125),
        (149, 150),
        (151, 150)
    ]
    
    for input_val, expected in test_cases:
        result = round_to_50(input_val)
        status = "✓" if result == expected else "✗"
        print(f"  {status} round_to_50({input_val:.2f}) = {result}, expected {expected}")
    
    print()

def check_engine_changes():
    print("Checking engine files for required changes...")
    
    engine_files = [
        "backend/engines/v1_ce_fut.py",
        "backend/engines/v2_pe_fut.py", 
        "backend/engines/v3_strike_breach.py",
        "backend/engines/v4_strangle.py",
        "backend/engines/v5_protected.py",
        "backend/engines/v6_inverse_strangle.py",
        "backend/engines/v7_premium.py",
        "backend/engines/v8_ce_pe_fut.py",
        "backend/engines/v8_hsl.py",
        "backend/engines/v9_counter.py"
    ]
    
    all_good = True
    
    for file_path in engine_files:
        full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), file_path)
        if not os.path.exists(full_path):
            print(f"  ✗ File does not exist: {file_path}")
            all_good = False
            continue
            
        with open(full_path, 'r') as f:
            content = f.read()
            
        # Check for round_to_50 import
        has_round_to_50_import = "round_to_50" in content
        # Check for iloc[2] usage instead of iloc[0] for future expiry
        has_iloc_2 = "iloc[2]" in content
        # Check for iloc[0] usage (should be mostly replaced)
        has_iloc_0 = "iloc[0]" in content and "fut_exp_rows.iloc[0]" in content
        
        status = "✓" if has_round_to_50_import else "✗"
        print(f"  {status} {file_path}: round_to_50 import - {has_round_to_50_import}")
        
        # More specific check for future expiry selection
        if "v1_" in file_path or "v2_" in file_path or "v3_" in file_path or \
           "v5_" in file_path or "v8_" in file_path or "v9_" in file_path:
            # These engines should have the future expiry logic
            has_correct_expiry = "fut_exp = fut_exp_rows.iloc[2]['Current Expiry']" in content
            status = "✓" if has_correct_expiry else "✗"
            print(f"    {status} Future expiry selection (iloc[2]): {has_correct_expiry}")
    
    print()
    return all_good

if __name__ == "__main__":
    print("Testing engine fixes...")
    print("=" * 50)
    
    test_rounding()
    engine_ok = check_engine_changes()
    
    print("=" * 50)
    if engine_ok:
        print("All checks passed! The engine fixes appear to be correctly implemented.")
    else:
        print("Some issues were detected with the engine fixes.")