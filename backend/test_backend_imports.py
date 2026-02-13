import sys
import os

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print("=== Backend Import Test ===")

# Test 1: Basic imports
try:
    from fastapi import FastAPI
    print("✓ FastAPI imported successfully")
except Exception as e:
    print(f"✗ FastAPI import failed: {e}")

# Test 2: Strategy types import
try:
    from strategies.strategy_types import InstrumentType, OptionType, PositionType
    print("✓ Strategy types imported successfully")
    print(f"  - InstrumentType.OPTION = {InstrumentType.OPTION}")
    print(f"  - OptionType.CE = {OptionType.CE}")
    print(f"  - PositionType.BUY = {PositionType.BUY}")
except Exception as e:
    print(f"✗ Strategy types import failed: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Router import
try:
    from routers import backtest
    print("✓ Backtest router imported successfully")
except Exception as e:
    print(f"✗ Backtest router import failed: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Main app import
try:
    from main import app
    print("✓ Main app imported successfully")
except Exception as e:
    print(f"✗ Main app import failed: {e}")
    import traceback
    traceback.print_exc()

print("=== Import Test Complete ===")