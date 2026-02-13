import sys
import os

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print("=== Testing Import Chain ===")

try:
    print("1. Testing strategy_engine import...")
    from strategy_engine import InstrumentType, OptionType, PositionType
    print(f"   ✓ InstrumentType.OPTION = {InstrumentType.OPTION}")
    print(f"   ✓ OptionType.CE = {OptionType.CE}")
    print(f"   ✓ PositionType.BUY = {PositionType.BUY}")
except Exception as e:
    print(f"   ✗ strategy_engine import failed: {e}")
    import traceback
    traceback.print_exc()

try:
    print("\n2. Testing engines.generic_multi_leg import...")
    from engines.generic_multi_leg import *  # This should trigger the error
    print("   ✓ generic_multi_leg imported successfully")
except Exception as e:
    print(f"   ✗ engines.generic_multi_leg import failed: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Import Test Complete ===")