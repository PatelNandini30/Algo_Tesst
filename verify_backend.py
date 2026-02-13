"""
Simple verification script to test if backend components work
"""
import sys
import os

# Add backend to path
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print("=== Backend Component Verification ===")
print(f"Backend directory: {backend_dir}")

# Test 1: Strategy Engine
try:
    from strategy_engine import StrategyDefinition, Leg, InstrumentType
    print("✓ strategy_engine imports working")
except Exception as e:
    print(f"✗ strategy_engine import failed: {e}")

# Test 2: Basic dataclasses
try:
    strategy = StrategyDefinition(
        name="Test Strategy",
        legs=[
            Leg(instrument_type=InstrumentType.OPTION, symbol="NIFTY")
        ]
    )
    print("✓ StrategyDefinition instantiation working")
except Exception as e:
    print(f"✗ StrategyDefinition instantiation failed: {e}")

# Test 3: Check what run_v4 functions are available
try:
    import engines.v4_strangle as v4
    import inspect
    functions = [name for name, obj in inspect.getmembers(v4) if inspect.isfunction(obj) and name.startswith('run_v4')]
    print(f"✓ Available v4 functions: {functions}")
except Exception as e:
    print(f"✗ v4_strangle inspection failed: {e}")

print("\n=== Verification Complete ===")