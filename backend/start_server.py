import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Add the parent directory to the path to import engines
sys.path.append(os.path.dirname(os.path.abspath('.')))

from main import app
import uvicorn

if __name__ == "__main__":
    print("Starting server...")
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    except Exception as e:
        print(f"Error starting server: {e}")
        import traceback
        traceback.print_exc()