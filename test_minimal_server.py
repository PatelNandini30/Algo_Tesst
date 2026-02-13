"""
Minimal working FastAPI server for testing
"""
import sys
import os

# Add backend directory to path
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Backend directory: {backend_dir}")
print(f"Python path: {sys.path[:3]}...")  # Show first 3 paths

# Simple FastAPI app without complex imports
from fastapi import FastAPI

app = FastAPI(title="Test API", version="1.0.0")

@app.get("/")
def read_root():
    return {"message": "Test API is running"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "backend_dir": backend_dir}

@app.get("/test-imports")
def test_imports():
    """Test if we can import the key components"""
    results = {}
    
    # Test strategy engine import
    try:
        from strategy_engine import StrategyDefinition
        results["strategy_engine"] = "✓ Imported successfully"
    except Exception as e:
        results["strategy_engine"] = f"✗ Failed: {str(e)}"
    
    # Test backtest router import
    try:
        from routers import backtest
        results["backtest_router"] = "✓ Imported successfully"
    except Exception as e:
        results["backtest_router"] = f"✗ Failed: {str(e)}"
    
    return results

if __name__ == "__main__":
    import uvicorn
    print("Starting minimal test server...")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")