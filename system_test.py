import subprocess
import time
import requests
import sys

def test_backend():
    """Test if backend is running and responding"""
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        if response.status_code == 200:
            print("✓ Backend is running and healthy")
            return True
        else:
            print(f"✗ Backend returned status code: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("✗ Backend is not running or not accessible")
        return False
    except Exception as e:
        print(f"✗ Error testing backend: {e}")
        return False

def test_frontend():
    """Test if frontend is running"""
    try:
        response = requests.get("http://localhost:5173", timeout=5)
        if response.status_code == 200:
            print("✓ Frontend is running")
            return True
        else:
            print(f"✗ Frontend returned status code: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("✗ Frontend is not running or not accessible")
        return False
    except Exception as e:
        print(f"✗ Error testing frontend: {e}")
        return False

def start_backend():
    """Start the backend server"""
    print("Starting backend server...")
    try:
        # Start backend in background
        backend_process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "main:app", 
            "--host", "0.0.0.0", "--port", "8000", "--reload"
        ], cwd="e:/Algo_Test_Software/backend")
        
        # Wait a moment for server to start
        time.sleep(3)
        
        if test_backend():
            print("Backend started successfully!")
            return backend_process
        else:
            print("Failed to start backend")
            backend_process.terminate()
            return None
    except Exception as e:
        print(f"Error starting backend: {e}")
        return None

def start_frontend():
    """Start the frontend server"""
    print("Starting frontend server...")
    try:
        # Start frontend in background
        frontend_process = subprocess.Popen([
            "npm", "run", "dev"
        ], cwd="e:/Algo_Test_Software/frontend")
        
        # Wait a moment for server to start
        time.sleep(5)
        
        if test_frontend():
            print("Frontend started successfully!")
            return frontend_process
        else:
            print("Failed to start frontend")
            frontend_process.terminate()
            return None
    except Exception as e:
        print(f"Error starting frontend: {e}")
        return None

def main():
    print("=== AlgoTest Dynamic Strategy Builder - System Test ===\n")
    
    # Test current status
    print("1. Testing current system status:")
    backend_running = test_backend()
    frontend_running = test_frontend()
    
    processes = []
    
    # Start backend if not running
    if not backend_running:
        print("\n2. Starting backend server...")
        backend_process = start_backend()
        if backend_process:
            processes.append(backend_process)
    else:
        print("\n2. Backend is already running")
    
    # Start frontend if not running
    if not frontend_running:
        print("\n3. Starting frontend server...")
        frontend_process = start_frontend()
        if frontend_process:
            processes.append(frontend_process)
    else:
        print("\n3. Frontend is already running")
    
    # Final status check
    print("\n4. Final system status:")
    test_backend()
    test_frontend()
    
    if processes:
        print(f"\n✓ System is running with {len(processes)} processes")
        print("Frontend: http://localhost:5173")
        print("Backend: http://localhost:8000")
        print("Backend API Docs: http://localhost:8000/docs")
        print("\nPress Ctrl+C to stop all servers")
        
        try:
            # Keep processes running
            for process in processes:
                process.wait()
        except KeyboardInterrupt:
            print("\n\nShutting down servers...")
            for process in processes:
                process.terminate()
                process.wait()
            print("Servers stopped.")
    else:
        print("\n✓ System is already running and accessible!")

if __name__ == "__main__":
    main()