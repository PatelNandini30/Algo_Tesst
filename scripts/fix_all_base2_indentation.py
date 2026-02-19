"""
Fix all base2 filter indentation issues in engine files
"""

import os
import re

# All engine files that might have the issue
files_to_fix = [
    'backend/engines/v2_pe_fut.py',
    'backend/engines/v3_strike_breach.py',
    'backend/engines/v4_strangle.py',
    'backend/engines/v5_protected.py',
    'backend/engines/v6_inverse_strangle.py',
    'backend/engines/v7_premium.py',
    'backend/engines/v8_ce_pe_fut.py',
    'backend/engines/v8_hsl.py',
    'backend/engines/v9_counter.py',
]

def fix_base2_filter_block(content):
    """Fix the base2 filter block indentation"""
    
    # Pattern to match the problematic block
    pattern = r'(    # base2 = load_base2\(\).*?\n)(    .*?Base2 Filter.*?\n)(    mask = pd\.Series\(False, index=spot_df\.index\)\n)(    # for _, row in base2\.iterrows\(\):.*?\n)(        mask \|= .*?\n)(    spot_df = spot_df\[mask\]\.reset_index\(drop=True\)\n)'
    
    replacement = r'\1\2    # mask = pd.Series(False, index=spot_df.index)\n    # for _, row in base2.iterrows():\n    #     mask |= (spot_df[\'Date\'] >= row[\'Start\']) & (spot_df[\'Date\'] <= row[\'End\'])\n    # spot_df = spot_df[mask].reset_index(drop=True)\n'
    
    content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    # Also handle simpler pattern without comments
    pattern2 = r'(    mask = pd\.Series\(False, index=spot_df\.index\)\n)(    # for _, row in base2\.iterrows\(\):.*?\n)(        mask \|= .*?\n)(    spot_df = spot_df\[mask\]\.reset_index\(drop=True\)\n)'
    
    replacement2 = r'    # mask = pd.Series(False, index=spot_df.index)\n    # for _, row in base2.iterrows():\n    #     mask |= (spot_df[\'Date\'] >= row[\'Start\']) & (spot_df[\'Date\'] <= row[\'End\'])\n    # spot_df = spot_df[mask].reset_index(drop=True)\n'
    
    content = re.sub(pattern2, replacement2, content, flags=re.DOTALL)
    
    return content

def fix_file(filepath):
    """Fix a single file"""
    if not os.path.exists(filepath):
        print(f"⚠️  File not found: {filepath}")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    
    # Fix the base2 filter block
    content = fix_base2_filter_block(content)
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Fixed: {filepath}")
        return True
    else:
        print(f"  No changes needed: {filepath}")
        return False

def main():
    print("=" * 70)
    print("Fixing base2 filter indentation issues")
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

if __name__ == '__main__':
    main()
