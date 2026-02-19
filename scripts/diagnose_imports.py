"""
Diagnostic script to identify import issues
"""
import sys
import os

print("=== Import Diagnostic ===")
print(f"Python version: {sys.version}")
print(f"Current working directory: {os.getcwd()}")
print(f"Python path: {sys.path}")

print("\n=== Testing imports step by step ===")

# Test 1: Basic imports
try:
    import pandas as pd
    print("✓ pandas imported successfully")
except Exception as e:
    print(f"✗ pandas import failed: {e}")

try:
    import numpy as np
    print("✓ numpy imported successfully")
except Exception as e:
    print(f"✗ numpy import failed: {e}")

# Test 2: Backend directory structure
backend_dir = os.path.join(os.getcwd(), "backend")
print(f"\nBackend directory exists: {os.path.exists(backend_dir)}")

if os.path.exists(backend_dir):
    print("Backend directory contents:")
    for item in os.listdir(backend_dir):
        print(f"  {item}")

# Test 3: Engine files
engines_dir = os.path.join(backend_dir, "engines")
if os.path.exists(engines_dir):
    print("\nEngine files:")
    for item in os.listdir(engines_dir):
        if item.endswith('.py') and not item.startswith('__'):
            print(f"  {item}")

# Test 4: Try importing specific engine
try:
    print("\nTesting v4_strangle import...")
    sys.path.insert(0, engines_dir)
    import v4_strangle
    print("✓ v4_strangle imported successfully")
    import inspect
    functions = [name for name, obj in inspect.getmembers(v4_strangle) if inspect.isfunction(obj) and name.startswith('run_v4')]
    print(f"Available run_v4 functions: {functions}")
except Exception as e:
    print(f"✗ v4_strangle import failed: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Try importing strategy_engine
try:
    print("\nTesting strategy_engine import...")
    sys.path.insert(0, backend_dir)
    import strategy_engine
    print("✓ strategy_engine imported successfully")
except Exception as e:
    print(f"✗ strategy_engine import failed: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Diagnostic complete ===")