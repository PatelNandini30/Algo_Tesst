"""
Fix all req_leg attribute accesses to dictionary accesses in backtest.py
"""
import re

# Read the file
with open('backend/routers/backtest.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace all req_leg.attribute with req_leg["attribute"] or req_leg.get("attribute")
replacements = [
    (r'req_leg\.instrument', 'req_leg["instrument"]'),
    (r'req_leg\.option_type', 'req_leg.get("option_type")'),
    (r'req_leg\.position', 'req_leg["position"]'),
    (r'req_leg\.expiry_type', 'req_leg["expiry_type"]'),
    (r'req_leg\.strike_selection', 'req_leg["strike_selection"]'),
    (r'req_leg\.entry_condition', 'req_leg["entry_condition"]'),
    (r'req_leg\.exit_condition', 'req_leg["exit_condition"]'),
    (r'req_leg\.leg_number', 'req_leg["leg_number"]'),
    (r'req_leg\.lots', 'req_leg["lots"]'),
    (r"getattr\(req_leg, 'quantity', req_leg\.get\('quantity', 1\)\)", "req_leg.get('quantity', 1)"),
]

for pattern, replacement in replacements:
    content = re.sub(pattern, replacement, content)

# Write back
with open('backend/routers/backtest.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed all req_leg attribute accesses")
