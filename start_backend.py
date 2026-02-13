"""
Startup script for backend server with proper import handling
"""
import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"Project root added to path: {project_root}")

# Now try to import and run the FastAPI app
try:
    from backend.main import app
    import uvicorn
    
    print("Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
except Exception as e:
    print(f"Error starting server: {e}")
    import traceback
    traceback.print_exc()