"""
Comprehensive API Test Suite
============================
Tests all AlgoTest API endpoints to ensure they function correctly
"""

import requests
import json
import time
from datetime import datetime

class APITestSuite:
    """Test suite for all AlgoTest APIs"""
    
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.test_results = []
    
    def test_endpoint(self, method, endpoint, description, expected_status=200, payload=None):
        """Test a single API endpoint"""
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()
        
        try:
            if method == "GET":
                response = requests.get(url, timeout=10)
            elif method == "POST":
                response = requests.post(url, json=payload, timeout=30)
            
            response_time = time.time() - start_time
            status_check = response.status_code == expected_status
            response_data = response.json() if response.content else {}
            
            result = {
                "endpoint": endpoint,
                "method": method,
                "description": description,
                "status": "PASS" if status_check else "FAIL",
                "status_code": response.status_code,
                "expected": expected_status,
                "response_time": round(response_time, 3),
                "response_size": len(response.content),
                "data_sample": str(response_data)[:200] + "..." if len(str(response_data)) > 200 else str(response_data)
            }
            
            self.test_results.append(result)
            return status_check
            
        except Exception as e:
            result = {
                "endpoint": endpoint,
                "method": method,
                "description": description,
                "status": "ERROR",
                "error": str(e)[:100],
                "response_time": round(time.time() - start_time, 3)
            }
            self.test_results.append(result)
            return False
    
    def run_all_tests(self):
        """Run comprehensive API test suite"""
        print("🧪 Running Comprehensive API Test Suite")
        print("=" * 50)
        print(f"Testing against: {self.base_url}")
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Test groups
        test_groups = [
            # Basic Health Checks
            ("Basic Health", [
                ("GET", "/", "Root endpoint", 200),
                ("GET", "/health", "Health check", 200),
            ]),
            
            # Strategy Information
            ("Strategy Information", [
                ("GET", "/api/strategies", "List all strategies", 200),
                ("GET", "/api/data/dates?index=NIFTY", "Get date range for NIFTY", 200),
            ]),
            
            # Expiry Data
            ("Expiry Data", [
                ("GET", "/api/expiry?index=NIFTY&type=weekly", "Weekly expiry dates", 200),
                ("GET", "/api/expiry?index=NIFTY&type=monthly", "Monthly expiry dates", 200),
                ("GET", "/api/expiry?index=BANKNIFTY&type=weekly", "BANKNIFTY weekly expiry", 200),
            ]),
            
            # Backtest Endpoints (if available)
            ("Backtest Functionality", [
                ("POST", "/api/backtest", "Backtest execution", 200, {
                    "strategy_version": "v1",
                    "from_date": "2019-01-01",
                    "to_date": "2019-01-31",
                    "index": "NIFTY",
                    "call_sell_position": 0.0,
                    "expiry_window": "weekly_expiry"
                }),
            ])
        ]
        
        # Run all tests
        total_tests = 0
        passed_tests = 0
        
        for group_name, tests in test_groups:
            print(f"📋 {group_name}")
            print("-" * 30)
            
            group_passed = 0
            for method, endpoint, description, expected_status, *args in tests:
                payload = args[0] if args else None
                result = self.test_endpoint(method, endpoint, description, expected_status, payload)
                total_tests += 1
                if result:
                    group_passed += 1
                    passed_tests += 1
                
                # Print immediate result
                latest_result = self.test_results[-1]
                status_icon = "✓" if latest_result["status"] == "PASS" else "✗" if latest_result["status"] == "FAIL" else "⚠"
                print(f"  {status_icon} {description} ({latest_result['response_time']}s)")
            
            print(f"  Group Result: {group_passed}/{len(tests)} passed")
            print()
        
        # Generate report
        self.generate_report(total_tests, passed_tests)
        
        return passed_tests == total_tests
    
    def generate_report(self, total_tests, passed_tests):
        """Generate comprehensive test report"""
        print("=" * 60)
        print("📊 API TEST SUITE REPORT")
        print("=" * 60)
        print(f"Test Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Target: {self.base_url}")
        print()
        
        # Summary
        print("SUMMARY:")
        print("-" * 20)
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {total_tests - passed_tests}")
        print(f"Success Rate: {passed_tests/total_tests*100:.1f}%")
        print()
        
        # Detailed results by status
        passed_results = [r for r in self.test_results if r["status"] == "PASS"]
        failed_results = [r for r in self.test_results if r["status"] == "FAIL"]
        error_results = [r for r in self.test_results if r["status"] == "ERROR"]
        
        if passed_results:
            print("✅ PASSED TESTS:")
            print("-" * 20)
            for result in passed_results:
                print(f"  ✓ {result['description']}")
                print(f"    Endpoint: {result['method']} {result['endpoint']}")
                print(f"    Response Time: {result['response_time']}s")
                print()
        
        if failed_results:
            print("❌ FAILED TESTS:")
            print("-" * 20)
            for result in failed_results:
                print(f"  ✗ {result['description']}")
                print(f"    Expected: {result['expected']}, Got: {result['status_code']}")
                print(f"    Endpoint: {result['method']} {result['endpoint']}")
                print()
        
        if error_results:
            print("⚠ ERROR TESTS:")
            print("-" * 20)
            for result in error_results:
                print(f"  ⚠ {result['description']}")
                print(f"    Error: {result['error']}")
                print(f"    Endpoint: {result['method']} {result['endpoint']}")
                print()
        
        # Performance summary
        response_times = [r['response_time'] for r in self.test_results if 'response_time' in r]
        if response_times:
            avg_response_time = sum(response_times) / len(response_times)
            max_response_time = max(response_times)
            print("⏱ PERFORMANCE METRICS:")
            print("-" * 20)
            print(f"Average Response Time: {avg_response_time:.3f}s")
            print(f"Maximum Response Time: {max_response_time:.3f}s")
            print()
        
        # Overall assessment
        print("OVERALL ASSESSMENT:")
        print("-" * 20)
        if passed_tests == total_tests:
            print("🎉 ALL API ENDPOINTS ARE WORKING CORRECTLY!")
            print("✅ AlgoTest API system is fully operational")
        elif passed_tests > total_tests * 0.8:
            print("⚠ MOST API ENDPOINTS ARE WORKING")
            print("🔧 Some endpoints may need attention")
        else:
            print("❌ MULTIPLE API ENDPOINTS ARE NOT WORKING")
            print("🔧 Significant issues need to be addressed")
        
        print()

def main():
    # Test against local server
    tester = APITestSuite("http://localhost:8000")
    
    try:
        success = tester.run_all_tests()
        return success
    except Exception as e:
        print(f"❌ Test suite failed with error: {e}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)