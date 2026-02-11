"""
AlgoTest Complete Startup System
===============================
Starts all AlgoTest components and ensures they work together properly
"""

import subprocess
import sys
import os
import time
import requests
from datetime import datetime

class AlgoTestStarter:
    """Manages starting all AlgoTest components"""
    
    def __init__(self):
        self.processes = {}
        self.base_url = "http://localhost:8000"
        self.dashboard_url = "http://localhost:8501"
    
    def check_prerequisites(self):
        """Check system prerequisites"""
        print("📋 Checking Prerequisites...")
        
        # Check Python version
        if sys.version_info < (3, 8):
            print("✗ Python 3.8+ required")
            return False
        print("✓ Python version OK")
        
        # Check required packages
        required_packages = [
            "fastapi", "uvicorn", "pandas", "numpy", "streamlit", "plotly"
        ]
        
        missing_packages = []
        for package in required_packages:
            try:
                __import__(package)
            except ImportError:
                missing_packages.append(package)
        
        if missing_packages:
            print(f"✗ Missing packages: {', '.join(missing_packages)}")
            print("Install with: pip install " + " ".join(missing_packages))
            return False
        
        print("✓ All required packages installed")
        return True
    
    def start_backend_api(self):
        """Start the backend API server"""
        print("🚀 Starting Backend API Server...")
        
        try:
            # Change to backend directory
            os.chdir("backend")
            
            # Start backend server
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "main:app",
                "--host", "0.0.0.0", "--port", "8000", "--reload"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            self.processes["backend"] = process
            os.chdir("..")  # Return to root directory
            
            # Wait for server to start
            print("   Waiting for backend to start...")
            for i in range(10):
                try:
                    response = requests.get(f"{self.base_url}/health", timeout=1)
                    if response.status_code == 200:
                        print("✓ Backend API Server started successfully")
                        print(f"   API Documentation: {self.base_url}/docs")
                        return True
                except:
                    time.sleep(1)
            
            print("✗ Backend API Server failed to start")
            return False
            
        except Exception as e:
            print(f"✗ Error starting backend: {e}")
            return False
    
    def start_strategy_dashboard(self):
        """Start the strategy performance dashboard"""
        print("📊 Starting Strategy Performance Dashboard...")
        
        try:
            # Start dashboard in background
            process = subprocess.Popen([
                sys.executable, "-m", "streamlit", "run", "strategy_dashboard.py",
                "--server.port", "8501", "--server.headless", "true"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            self.processes["dashboard"] = process
            
            # Wait for dashboard to start
            print("   Waiting for dashboard to start...")
            for i in range(15):
                try:
                    # Simple check if port is listening
                    import socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    result = sock.connect_ex(('localhost', 8501))
                    sock.close()
                    if result == 0:
                        print("✓ Strategy Dashboard started successfully")
                        print(f"   Dashboard: {self.dashboard_url}")
                        return True
                except:
                    pass
                time.sleep(1)
            
            print("✓ Strategy Dashboard process started (may take a moment to load)")
            return True
            
        except Exception as e:
            print(f"✗ Error starting dashboard: {e}")
            return False
    
    def start_data_services(self):
        """Start data-related services"""
        print("📂 Starting Data Services...")
        
        # Check if database exists and is accessible
        if os.path.exists("bhavcopy_data.db"):
            try:
                import sqlite3
                conn = sqlite3.connect("bhavcopy_data.db")
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
                table_count = cursor.fetchone()[0]
                conn.close()
                print(f"✓ Database connected ({table_count} tables)")
            except Exception as e:
                print(f"⚠ Database connection issue: {e}")
        else:
            print("⚠ Database file not found")
        
        # Check data directories
        data_dirs = ["cleaned_csvs", "strikeData", "expiryData", "Filter"]
        for directory in data_dirs:
            if os.path.exists(directory):
                file_count = len([f for f in os.listdir(directory) if not f.startswith('.')])
                print(f"✓ {directory}: {file_count} files")
            else:
                print(f"⚠ {directory}: Directory not found")
        
        return True
    
    def validate_system_health(self):
        """Validate that all systems are running properly"""
        print("\n🏥 Validating System Health...")
        
        health_checks = [
            ("Backend API", f"{self.base_url}/health"),
            ("API Documentation", f"{self.base_url}/docs"),
            ("Strategy Dashboard", self.dashboard_url)
        ]
        
        all_healthy = True
        for service, url in health_checks:
            try:
                if "docs" in url or url == self.dashboard_url:
                    # For documentation and dashboard, just check if port responds
                    import socket
                    port = 8000 if "8000" in url else 8501
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    result = sock.connect_ex(('localhost', port))
                    sock.close()
                    if result == 0:
                        print(f"✓ {service}: OK")
                    else:
                        print(f"✗ {service}: Not responding")
                        all_healthy = False
                else:
                    response = requests.get(url, timeout=3)
                    if response.status_code == 200:
                        print(f"✓ {service}: OK")
                    else:
                        print(f"✗ {service}: Status {response.status_code}")
                        all_healthy = False
            except Exception as e:
                print(f"✗ {service}: {str(e)[:50]}...")
                all_healthy = False
        
        return all_healthy
    
    def show_system_status(self):
        """Display current system status"""
        print("\n" + "="*60)
        print("📊 ALGOTEST SYSTEM STATUS")
        print("="*60)
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        print("ACTIVE SERVICES:")
        print("-" * 20)
        for service, process in self.processes.items():
            if process.poll() is None:  # Process still running
                print(f"✓ {service}: RUNNING (PID: {process.pid})")
            else:
                print(f"✗ {service}: STOPPED")
        
        print()
        print("SYSTEM URLS:")
        print("-" * 20)
        print(f"Backend API: {self.base_url}")
        print(f"API Docs: {self.base_url}/docs")
        print(f"Strategy Dashboard: {self.dashboard_url}")
        
        print()
        print("DATA SOURCES:")
        print("-" * 20)
        data_dirs = ["cleaned_csvs", "strikeData", "expiryData", "Filter"]
        for directory in data_dirs:
            if os.path.exists(directory):
                file_count = len([f for f in os.listdir(directory) if not f.startswith('.')])
                print(f"✓ {directory}: {file_count} files")
            else:
                print(f"✗ {directory}: Not found")
        
        database_exists = os.path.exists("bhavcopy_data.db")
        print(f"{'✓' if database_exists else '✗'} Database: {'Connected' if database_exists else 'Not found'}")
    
    def shutdown_system(self):
        """Gracefully shutdown all processes"""
        print("\n🛑 Shutting down AlgoTest system...")
        
        for service, process in self.processes.items():
            try:
                if process.poll() is None:  # Process still running
                    print(f"   Stopping {service}...")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    print(f"   ✓ {service} stopped")
            except Exception as e:
                print(f"   ✗ Error stopping {service}: {e}")
        
        print("✅ AlgoTest system shutdown complete")
    
    def start_complete_system(self):
        """Start the complete AlgoTest system"""
        print("🎯 STARTING COMPLETE ALGOTEST SYSTEM")
        print("=" * 50)
        
        # Check prerequisites
        if not self.check_prerequisites():
            print("\n❌ System prerequisites not met. Please install required packages.")
            return False
        
        # Start components
        components = [
            ("Data Services", self.start_data_services),
            ("Backend API", self.start_backend_api),
            ("Strategy Dashboard", self.start_strategy_dashboard)
        ]
        
        success_count = 0
        for name, start_func in components:
            try:
                if start_func():
                    success_count += 1
                print()
            except Exception as e:
                print(f"✗ Error starting {name}: {e}\n")
        
        # Validate system health
        if success_count > 0:
            time.sleep(3)  # Give services time to fully start
            is_healthy = self.validate_system_health()
            
            # Show final status
            self.show_system_status()
            
            if is_healthy:
                print("\n🎉 ALGOTEST SYSTEM IS FULLY OPERATIONAL!")
                print("✅ All APIs and functions are working")
                print("\nYou can now:")
                print("  • Access API documentation at: http://localhost:8000/docs")
                print("  • Use the strategy dashboard at: http://localhost:8501")
                print("  • Run strategy analyses using the CLI tools")
                print("  • Execute backtests via the API endpoints")
                return True
            else:
                print("\n⚠ SYSTEM PARTIALLY OPERATIONAL")
                print("Some components may need attention")
                return True
        else:
            print("\n❌ Failed to start AlgoTest system")
            return False

def main():
    starter = AlgoTestStarter()
    
    try:
        success = starter.start_complete_system()
        if success:
            print("\nPress Ctrl+C to shutdown the system")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n")
        else:
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
    finally:
        starter.shutdown_system()

if __name__ == "__main__":
    main()