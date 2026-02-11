import subprocess
import sys
import os
import time

def run_backend():
    """Start the backend server"""
    print("Starting backend server...")
    try:
        # Change to backend directory
        os.chdir("backend")
        
        # Run uvicorn server
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "main:app", 
            "--host", "0.0.0.0", "--port", "8000", "--reload"
        ])
        
        print("Backend server started on http://localhost:8000")
        return process
    except Exception as e:
        print(f"Error starting backend: {e}")
        return None

def run_frontend():
    """Start the frontend server"""
    print("Starting frontend server...")
    try:
        # Change to frontend directory
        os.chdir("../frontend")
        
        # Install dependencies if needed
        print("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], check=True)
        
        # Run frontend server
        process = subprocess.Popen(["npm", "run", "dev"])
        
        print("Frontend server started on http://localhost:3000")
        return process
    except Exception as e:
        print(f"Error starting frontend: {e}")
        return None

if __name__ == "__main__":
    print("Starting AlgoTest Clone servers...")
    
    # Start backend
    backend_process = run_backend()
    if backend_process is None:
        print("Failed to start backend server")
        sys.exit(1)
    
    # Wait a moment before starting frontend
    time.sleep(2)
    
    # Start frontend
    frontend_process = run_frontend()
    if frontend_process is None:
        print("Failed to start frontend server")
        backend_process.terminate()
        sys.exit(1)
    
    print("\nBoth servers are running!")
    print("Backend: http://localhost:8000")
    print("Frontend: http://localhost:3000")
    print("Press Ctrl+C to stop both servers")
    
    try:
        # Keep the script running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping servers...")
        backend_process.terminate()
        frontend_process.terminate()
        print("Servers stopped.")