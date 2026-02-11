"""
Quick API Test Script
Tests the Phase 1 API endpoints
"""
import requests
import json
import time


BASE_URL = "http://localhost:8000"


def test_health():
    """Test health check endpoint"""
    print("\n=== Testing Health Check ===")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200


def test_list_strategies():
    """Test list strategies endpoint"""
    print("\n=== Testing List Strategies ===")
    response = requests.get(f"{BASE_URL}/api/v1/strategies")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Found {len(data)} strategies:")
    for strategy in data:
        print(f"  - {strategy['name']}: {strategy['description']}")
    return response.status_code == 200


def test_get_strategy_details():
    """Test get strategy details endpoint"""
    print("\n=== Testing Get Strategy Details ===")
    strategy_name = "call_sell_future_buy_weekly"
    response = requests.get(f"{BASE_URL}/api/v1/strategies/{strategy_name}")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Strategy: {data['name']}")
    print(f"Version: {data['version']}")
    print(f"Parameters:")
    for param in data['parameter_schema']:
        print(f"  - {param['name']} ({param['type']}): {param['description']}")
    return response.status_code == 200


def test_execute_strategy():
    """Test execute strategy endpoint"""
    print("\n=== Testing Execute Strategy ===")
    
    payload = {
        "strategy_name": "call_sell_future_buy_weekly",
        "parameters": {
            "spot_adjustment_type": 0,
            "spot_adjustment": 1.0,
            "call_sell_position": 0.0,
            "symbol": "NIFTY"
        },
        "user_id": "test_user"
    }
    
    print(f"Executing with parameters: {json.dumps(payload['parameters'], indent=2)}")
    
    start_time = time.time()
    response = requests.post(f"{BASE_URL}/api/v1/execute", json=payload)
    elapsed = time.time() - start_time
    
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Execution ID: {data['execution_id']}")
        print(f"Status: {data['status']}")
        print(f"Duration: {data['duration_ms']}ms")
        print(f"Row Count: {data['row_count']}")
        print(f"API Response Time: {elapsed:.2f}s")
        return data['execution_id']
    else:
        print(f"Error: {response.text}")
        return None


def test_get_execution_result(execution_id):
    """Test get execution result endpoint"""
    print(f"\n=== Testing Get Execution Result (ID: {execution_id}) ===")
    response = requests.get(f"{BASE_URL}/api/v1/executions/{execution_id}")
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Execution Status: {data['status']}")
        print(f"Row Count: {data['row_count']}")
        print(f"Metadata: {json.dumps(data['metadata'], indent=2)}")
        
        if data['data']:
            print(f"\nFirst 3 results:")
            for i, row in enumerate(data['data'][:3]):
                print(f"  Trade {i+1}:")
                print(f"    Entry: {row.get('Entry Date')} @ {row.get('Entry Spot')}")
                print(f"    Exit: {row.get('Exit Date')} @ {row.get('Exit Spot')}")
                print(f"    Net P&L: {row.get('Net P&L')}")
        
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_list_executions():
    """Test list executions endpoint"""
    print("\n=== Testing List Executions ===")
    response = requests.get(f"{BASE_URL}/api/v1/executions?limit=5")
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Found {len(data)} recent executions:")
        for exec_info in data:
            print(f"  - ID {exec_info['execution_id']}: {exec_info['strategy_name']} - {exec_info['status']}")
            print(f"    Started: {exec_info['started_at']}, Duration: {exec_info['duration_ms']}ms")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def run_all_tests():
    """Run all API tests"""
    print("=" * 60)
    print("Phase 1 API Test Suite")
    print("=" * 60)
    
    try:
        # Test 1: Health check
        if not test_health():
            print("\n❌ Health check failed!")
            return
        
        # Test 2: List strategies
        if not test_list_strategies():
            print("\n❌ List strategies failed!")
            return
        
        # Test 3: Get strategy details
        if not test_get_strategy_details():
            print("\n❌ Get strategy details failed!")
            return
        
        # Test 4: Execute strategy
        execution_id = test_execute_strategy()
        if not execution_id:
            print("\n❌ Execute strategy failed!")
            return
        
        # Test 5: Get execution result
        if not test_get_execution_result(execution_id):
            print("\n❌ Get execution result failed!")
            return
        
        # Test 6: List executions
        if not test_list_executions():
            print("\n❌ List executions failed!")
            return
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
    
    except requests.exceptions.ConnectionError:
        print("\n❌ Connection Error: Is the API server running?")
        print("Start the server with: python -m src.api.main")
    except Exception as e:
        print(f"\n❌ Test failed with error: {str(e)}")


if __name__ == "__main__":
    run_all_tests()
