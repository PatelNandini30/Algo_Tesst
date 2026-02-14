"""
Fix f-string syntax errors with dictionary access
"""
import re

# Read the file
with open('backend/routers/backtest.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix f-strings with dictionary access - need to use different quote style or extract variable
# Pattern: f"...{req_leg["key"]}..." -> f"...{req_leg['key']}..."
content = re.sub(
    r'f"([^"]*)\{req_leg\["([^"]+)"\]\}([^"]*)"',
    r"f'\1{req_leg[\"\2\"]}\3'",
    content
)

# Alternative: for cases where single quotes are already used in the f-string
# We need to extract the variable first
patterns_to_fix = [
    (r'detail=f"Invalid instrument type: \{req_leg\["instrument"\]\}"', 
     'detail=f"Invalid instrument type: {req_leg.get(\'instrument\', \'unknown\')}"'),
    (r'detail=f"Invalid option type: \{req_leg\["option_type"\]\}"',
     'detail=f"Invalid option type: {req_leg.get(\'option_type\', \'unknown\')}"'),
    (r'detail=f"Invalid position type: \{req_leg\["position"\]\}"',
     'detail=f"Invalid position type: {req_leg.get(\'position\', \'unknown\')}"'),
    (r'detail=f"Invalid expiry type: \{req_leg\["expiry_type"\]\}"',
     'detail=f"Invalid expiry type: {req_leg.get(\'expiry_type\', \'unknown\')}"'),
]

for pattern, replacement in patterns_to_fix:
    content = re.sub(pattern, replacement, content)

# Write back
with open('backend/routers/backtest.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed f-string syntax errors")
