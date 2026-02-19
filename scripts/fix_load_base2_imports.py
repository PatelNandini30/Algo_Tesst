"""
Script to comment out load_base2 imports and usage across all engine files
"""

import os
import re

# Files that need to be fixed
files_to_fix = [
    'backend/engines/v1_ce_fut.py',
    'backend/engines/v2_pe_fut.py',
    'backend/engines/v3_strike_breach.py',
    'backend/engines/v4_strangle.py',
    'backend/engines/v5_protected.py',
    'backend/engines/v6_inverse_strangle.py',
    'backend/engines/v7_premium.py',
    'backend/engines/v8_ce_pe_fut.py',
    'backend/engines/v8_hsl.py',
    'backend/engines/v9_counter.py',
    'backend/engines/generic_multi_leg.py',
    'backend/algotest_engine.py',
]

def fix_file(filepath):
    """Fix load_base2 imports and usage in a file"""
    if not os.path.exists(filepath):
        print(f"⚠️  File not found: {filepath}")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    changes_made = False
    
    # Fix 1: Comment out load_base2 in import statements
    # Pattern: load_base2, or , load_base2
    content = re.sub(
        r'(\s+)load_base2,',
        r'\1# load_base2,  # Disabled - base2 filter not used',
        content
    )
    
    # Fix 2: Comment out base2 = load_base2() lines
    content = re.sub(
        r'^(\s*)base2\s*=\s*load_base2\(\)',
        r'\1# base2 = load_base2()  # Disabled - base2 filter not used',
        content,
        flags=re.MULTILINE
    )
    
    # Fix 3: Comment out base2 filter logic blocks
    # This is more complex - we'll look for common patterns
    
    # Pattern: for _, row in base2.iterrows():
    content = re.sub(
        r'^(\s*)for\s+_,\s+row\s+in\s+base2\.iterrows\(\):',
        r'\1# for _, row in base2.iterrows():  # Disabled - base2 filter not used',
        content,
        flags=re.MULTILINE
    )
    
    # Pattern: mask = (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    content = re.sub(
        r'^(\s*)mask\s*=\s*\(spot_df\[.Date.\]\s*>=\s*row\[.Start.\]\).*row\[.End.\]\)',
        r'\1# mask = (spot_df[\'Date\'] >= row[\'Start\']) & (spot_df[\'Date\'] <= row[\'End\'])  # Disabled',
        content,
        flags=re.MULTILINE
    )
    
    if content != original_content:
        changes_made = True
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Fixed: {filepath}")
    else:
        print(f"  No changes needed: {filepath}")
    
    return changes_made

def main():
    print("=" * 70)
    print("Fixing load_base2 imports and usage")
    print("=" * 70)
    print()
    
    total_fixed = 0
    for filepath in files_to_fix:
        if fix_file(filepath):
            total_fixed += 1
    
    print()
    print("=" * 70)
    print(f"Fixed {total_fixed} files")
    print("=" * 70)
    print()
    print("Note: Some files may need manual review for base2 filter logic")
    print("The base2 filter is now disabled across all engines")

if __name__ == '__main__':
    main()
