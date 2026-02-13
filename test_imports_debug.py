"""
Test script to diagnose import issues
"""
import sys
import os

print("Python path:")
for path in sys.path:
    print(f"  {path}")

print(f"\nCurrent working directory: {os.getcwd()}")

print("\nTesting imports...")

try:
    print("1. Testing backend.main import...")
    from backend.main import app
    print("   ✓ backend.main imported successfully")
except Exception as e:
    print(f"   ✗ backend.main import failed: {e}")

try:
    print("2. Testing strategy_engine import...")
    from backend.strategy_engine import StrategyDefinition
    print("   ✓ strategy_engine imported successfully")
except Exception as e:
    print(f"   ✗ strategy_engine import failed: {e}")

try:
    print("3. Testing routers.backtest import...")
    from backend.routers.backtest import router
    print("   ✓ routers.backtest imported successfully")
except Exception as e:
    print(f"   ✗ routers.backtest import failed: {e}")

print("\nTest completed.")