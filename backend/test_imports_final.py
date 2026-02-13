import sys
import os

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print("Testing imports...")

try:
    from strategies.strategy_types import InstrumentType, Leg, StrategyDefinition
    print("✓ strategy_types imports successfully")
except Exception as e:
    print(f"✗ strategy_types import failed: {e}")

try:
    from strategies.generic_multi_leg_engine import run_generic_multi_leg
    print("✓ generic_multi_leg_engine imports successfully")
except Exception as e:
    print(f"✗ generic_multi_leg_engine import failed: {e}")

try:
    import routers.backtest
    print("✓ backtest router imports successfully")
except Exception as e:
    print(f"✗ backtest router import failed: {e}")

print("Import test complete!")