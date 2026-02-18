"""
Final fix for all base2 filter indentation issues
"""

import os

files_to_fix = [
    'backend/engines/v3_strike_breach.py',
    'backend/engines/v4_strangle.py',
    'backend/engines/v5_protected.py',
    'backend/engines/v6_inverse_strangle.py',
    'backend/engines/v7_premium.py',
    'backend/engines/v8_ce_pe_fut.py',
    'backend/engines/v8_hsl.py',
    'backend/engines/v9_counter.py',
]

def fix_file(filepath):
    """Fix base2 filter block in a file"""
    if not os.path.exists(filepath):
        print(f"⚠️  File not found: {filepath}")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    fixed_lines = []
    i = 0
    changes_made = False
    
    while i < len(lines):
        line = lines[i]
        
        # Check if this is the problematic pattern
        if '# for _, row in base2.iterrows():' in line and line.strip().startswith('#'):
            # This is the commented for loop
            # Check if next line has wrong indentation
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if 'mask |=' in next_line and next_line.startswith('        '):
                    # Wrong indentation found - comment it out properly
                    fixed_lines.append(line)  # Keep the for loop comment
                    fixed_lines.append('    #     ' + next_line.strip() + '\n')  # Fix indentation
                    changes_made = True
                    i += 2
                    continue
        
        # Check for mask = pd.Series line that should be commented
        if 'mask = pd.Series(False, index=spot_df.index)' in line and not line.strip().startswith('#'):
            # Check if previous line mentions base2 filter
            if i > 0 and ('base2' in lines[i-1].lower() or 'filter' in lines[i-1].lower()):
                fixed_lines.append('    # ' + line.strip() + '\n')
                changes_made = True
                i += 1
                continue
        
        # Check for spot_df = spot_df[mask] that should be commented
        if 'spot_df = spot_df[mask]' in line and not line.strip().startswith('#'):
            # Check if we're in a base2 filter block
            if i > 2 and any('base2' in lines[j].lower() for j in range(max(0, i-5), i)):
                fixed_lines.append('    # ' + line.strip() + '\n')
                changes_made = True
                i += 1
                continue
        
        fixed_lines.append(line)
        i += 1
    
    if changes_made:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(fixed_lines)
        print(f"✓ Fixed: {filepath}")
        return True
    else:
        print(f"  No changes needed: {filepath}")
        return False

def main():
    print("=" * 70)
    print("Final fix for base2 filter indentation")
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
