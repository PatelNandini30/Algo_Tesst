import sys
import os

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Backend directory: {backend_dir}")
print(f"Current sys.path: {sys.path[:3]}")

try:
    print("Attempting to import from strategies.strategy_types...")
    from strategies.strategy_types import InstrumentType
    print("✓ SUCCESS: Imported InstrumentType directly")
    
    # Test creating an enum value
    test_value = InstrumentType.OPTION
    print(f"✓ SUCCESS: Created InstrumentType.OPTION = {test_value}")
    
except ImportError as e:
    print(f"✗ ImportError: {e}")
    
    # Try fallback import
    try:
        strategies_dir = os.path.join(backend_dir, 'strategies')
        print(f"Trying fallback import from: {strategies_dir}")
        if strategies_dir not in sys.path:
            sys.path.insert(0, strategies_dir)
        
        from strategy_types import InstrumentType
        print("✓ SUCCESS: Imported InstrumentType via fallback")
        
    except Exception as e2:
        print(f"✗ Fallback failed: {e2}")
        
except Exception as e:
    print(f"✗ Other error: {e}")
    import traceback
    traceback.print_exc()