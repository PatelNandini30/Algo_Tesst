"""
Fix f-string syntax by extracting variables before f-string usage
"""

# Read the file
with open('backend/routers/backtest.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Process line by line
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Check if this is a problematic f-string line
    if 'detail=f' in line and 'req_leg[' in line:
        # Extract the indentation
        indent = len(line) - len(line.lstrip())
        indent_str = ' ' * indent
        
        # Determine what value we're trying to access
        if '"instrument"' in line or '\\"instrument\\"' in line or "'instrument'" in line:
            var_name = 'instrument_val'
            key = 'instrument'
        elif '"option_type"' in line or '\\"option_type\\"' in line or "'option_type'" in line:
            var_name = 'option_type_val'
            key = 'option_type'
        elif '"position"' in line or '\\"position\\"' in line or "'position'" in line:
            var_name = 'position_val'
            key = 'position'
        elif '"expiry_type"' in line or '\\"expiry_type\\"' in line or "'expiry_type'" in line:
            var_name = 'expiry_type_val'
            key = 'expiry_type'
        else:
            # Can't determine, keep original
            new_lines.append(line)
            i += 1
            continue
        
        # Insert variable extraction before the raise statement
        new_lines.append(f'{indent_str}{var_name} = req_leg.get("{key}", "unknown")\n')
        
        # Replace the f-string to use the variable
        if 'Invalid instrument type' in line:
            new_line = f'{indent_str}raise HTTPException(status_code=400, detail=f"Invalid instrument type: {{{var_name}}}")\n'
        elif 'Invalid option type' in line:
            new_line = f'{indent_str}raise HTTPException(status_code=400, detail=f"Invalid option type: {{{var_name}}}")\n'
        elif 'Invalid position type' in line:
            new_line = f'{indent_str}raise HTTPException(status_code=400, detail=f"Invalid position type: {{{var_name}}}")\n'
        elif 'Invalid expiry type' in line:
            new_line = f'{indent_str}raise HTTPException(status_code=400, detail=f"Invalid expiry type: {{{var_name}}}")\n'
        else:
            new_line = line
        
        new_lines.append(new_line)
    else:
        new_lines.append(line)
    
    i += 1

# Write back
with open('backend/routers/backtest.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✓ Fixed all f-string syntax errors by extracting variables")
