import sys
import os

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Backend directory: {backend_dir}")
print(f"Current working directory: {os.getcwd()}")
print(f"Python path: {sys.path[:3]}")

try:
    from strategies.strategy_types import InstrumentType, Leg, StrategyDefinition
    print("✓ SUCCESS: strategy_types imported")
    
    # Test creating a simple leg
    test_leg = Leg(
        leg_number=1,
        instrument=InstrumentType.OPTION,
        option_type="CE",
        position="Sell",
        lots=1,
        expiry_type="Weekly",
        strike_selection={"type": "ATM"},
        entry_condition={"type": "Days Before Expiry", "days_before_expiry": 5},
        exit_condition={"type": "Days Before Expiry", "days_before_expiry": 3}
    )
    print("✓ SUCCESS: Leg model created")
    
    print("All imports working correctly!")
    
except Exception as e:
    print(f"✗ ERROR: {e}")
    import traceback
    traceback.print_exc()