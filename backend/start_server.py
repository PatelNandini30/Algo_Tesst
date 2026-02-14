"""
AlgoTest Backend Server Startup Script
Starts the FastAPI server with proper configuration
"""
import sys
import os
import uvicorn

# Add backend directory to Python path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Add parent directory to Python path for relative imports
parent_dir = os.path.dirname(backend_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

print("="*80)
print("  ALGOTEST BACKEND SERVER")
print("="*80)
print(f"Backend directory: {backend_dir}")
print(f"Parent directory: {parent_dir}")
print("\nStarting server...")
print("  API URL: http://localhost:8000")
print("  API Docs: http://localhost:8000/docs")
print("  Health Check: http://localhost:8000/health")
print("\nPress CTRL+C to stop the server")
print("="*80 + "\n")

from main import app

if __name__ == "__main__":
    try:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            log_level="info",
            reload=False,
            access_log=True
        )
    except KeyboardInterrupt:
        print("\n\nServer stopped by user")
    except Exception as e:
        print(f"\nError starting server: {e}")
        import traceback
        traceback.print_exc()