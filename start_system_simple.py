"""
Simple AlgoTest Startup Script
=============================
Starts backend and frontend components with proper error handling
"""

import subprocess
import sys
import os
import time
import requests

def start_backend():
    """Start the backend API server"""
    print("🚀 Starting Backend API Server...")
    
    try:
        # Change to backend directory
        backend_dir = os.path.join(os.getcwd(), "backend")
        if not os.path.exists(backend_dir):
            print("❌ Backend directory not found")
            return None
            
        # Start backend server
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "0.0.0.0", "--port", "8000"
        ], cwd=backend_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        print("   Backend process started (PID: {})".format(process.pid))
        
        # Wait and check if it's responding
        print("   Waiting for backend to initialize...")
        for i in range(15):
            try:
                time.sleep(1)
                response = requests.get("http://localhost:8000/health", timeout=1)
                if response.status_code == 200:
                    print("✅ Backend API Server is running!")
                    print("   API URL: http://localhost:8000")
                    print("   Docs: http://localhost:8000/docs")
                    return process
            except:
                continue
                
        print("⚠ Backend started but not responding yet")
        return process
        
    except Exception as e:
        print("❌ Failed to start backend: {}".format(e))
        return None

def start_dashboard():
    """Start the strategy dashboard"""
    print("📊 Starting Strategy Dashboard...")
    
    try:
        # Start dashboard
        process = subprocess.Popen([
            sys.executable, "-m", "streamlit", "run", "strategy_dashboard.py",
            "--server.port", "8501"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        print("   Dashboard process started (PID: {})".format(process.pid))
        print("   Dashboard URL: http://localhost:8501")
        return process
        
    except Exception as e:
        print("❌ Failed to start dashboard: {}".format(e))
        return None

def check_system_status():
    """Check if all components are running"""
    print("\n🏥 Checking System Status...")
    
    checks = [
        ("Backend API", "http://localhost:8000/health"),
        ("API Docs", "http://localhost:8000/docs"),
        ("Strategy Dashboard", "http://localhost:8501")
    ]
    
    all_running = True
    for service, url in checks:
        try:
            if "docs" in url or url == "http://localhost:8501":
                # For docs and dashboard, check if port is listening
                import socket
                port = 8000 if "8000" in url else 8501
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = sock.connect_ex(('localhost', port))
                sock.close()
                if result == 0:
                    print("✅ {}: Running".format(service))
                else:
                    print("❌ {}: Not responding".format(service))
                    all_running = False
            else:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    print("✅ {}: Running".format(service))
                else:
                    print("❌ {}: Status {}".format(service, response.status_code))
                    all_running = False
        except Exception as e:
            print("❌ {}: Error - {}".format(service, str(e)[:50]))
            all_running = False
    
    return all_running

def main():
    print("🎯 ALGOTEST SYSTEM STARTUP")
    print("=" * 40)
    
    processes = []
    
    # Start backend
    backend_process = start_backend()
    if backend_process:
        processes.append(("Backend", backend_process))
        time.sleep(3)  # Give backend time to fully start
    
    # Start dashboard
    dashboard_process = start_dashboard()
    if dashboard_process:
        processes.append(("Dashboard", dashboard_process))
    
    # Wait a moment for processes to initialize
    print("\n⏳ Waiting for services to initialize...")
    time.sleep(5)
    
    # Check system status
    system_ok = check_system_status()
    
    print("\n" + "=" * 50)
    if system_ok:
        print("🎉 ALGOTEST SYSTEM IS FULLY OPERATIONAL!")
        print("\n✅ Access your services at:")
        print("   Backend API: http://localhost:8000")
        print("   API Documentation: http://localhost:8000/docs")
        print("   Strategy Dashboard: http://localhost:8501")
        print("\n💡 Ready to run strategy analyses and generate reports!")
    else:
        print("⚠ SYSTEM PARTIALLY OPERATIONAL")
        print("Some components may still be starting up...")
    
    print("\nPress Ctrl+C to stop all services")
    
    try:
        # Keep the script running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down services...")
        
        # Terminate all processes
        for name, process in processes:
            try:
                if process.poll() is None:  # Process still running
                    print("   Stopping {}...".format(name))
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    print("   ✅ {} stopped".format(name))
            except Exception as e:
                print("   ❌ Error stopping {}: {}".format(name, e))
        
        print("✅ All services stopped")

if __name__ == "__main__":
    main()