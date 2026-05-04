"""
Startup script for backend server with proper import handling
"""
import sys
import os
import subprocess

# Add project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Project root: {project_root}")

try:
    print("Starting FastAPI server with granian...")
    subprocess.run(
        [sys.executable, "-m", "granian",
         "--interface", "asgi",
         "--host", "0.0.0.0",
         "--port", "8000",
         "--loop", "uvloop",
         "backend.main:app"],
        cwd=project_root,
    )
except Exception as e:
    print(f"Error starting server: {e}")
    import traceback
    traceback.print_exc()
