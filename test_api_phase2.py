"""
Test script for Phase 2 API features
Tests caching, async execution, and monitoring
"""
import requests
import time
import json
from datetime import datetime


BASE_URL = "http://localhost:8000"


def print_section(title):
    """Print section header"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_health():
    """Test health endpoint"""
    print_section("1. Health Check")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.status_code == 200


def test_list_strategies():
    """Test listing strategies"""
    print_section("2. List Available Strategies")
    response = requests.get(f"{BASE_URL}/api/v1/strategies")
    print(f"Status: {response.status_code}")
    
    strategies = response.json()
    print(f"Found {len(strategies)} strategies:")
    for strategy in strategies:
        print(f"  - {strategy['name']}: {strategy['description']}")
    
    return response.status_code == 200


def test_cache_health():
    """Test cache health"""
    print_section("3. Cache Health Check")
    response = requests.get(f"{BASE_URL}/api/v1/cache/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.status_code == 200


def test_sync_execution_no_cache():
    """Test synchronous execution without cache"""
    print_section("4. Synchronous Execution (No Cache)")
    
    payload = {
        "strategy_name": "call_sell_future_buy_t1",
        "parameters": {
            "spot_adjustment_type": 0,
            "spot_adjustment": 1.0,
            "call_sell_position": 0.0,
            "symbol": "NIFTY"
        },
        "use_cache": False,
        "async_execution": False
    }
    
    print(f"Executing strategy: {payload['strategy_name']}")
    start_time = time.time()
    
    response = requests.post(f"{BASE_URL}/api/v1/execute", json=payload)
    
    elapsed_time = (time.time() - start_time) * 1000
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"Execution ID: {result['execution_id']}")
        print(f"Status: {result['status']}")
        print(f"Cached: {result.get('cached', False)}")
        print(f"Duration: {result.get('duration_ms', 0)}ms")
        print(f"Total Time: {elapsed_time:.2f}ms")
        print(f"Row Count: {result.get('row_count', 0)}")
        return result['execution_id']
    else:
        print(f"Error: {response.text}")
        return None


def test_sync_execution_with_cache():
    """Test synchronous execution with cache"""
    print_section("5. Synchronous Execution (With Cache)")
    
    payload = {
        "strategy_name": "call_sell_future_buy_t1",
        "parameters": {
            "spot_adjustment_type": 0,
            "spot_adjustment": 1.0,
            "call_sell_position": 0.0,
            "symbol": "NIFTY"
        },
        "use_cache": True,
        "async_execution": False
    }
    
    print(f"Executing strategy: {payload['strategy_name']}")
    print("First execution (cache miss)...")
    start_time = time.time()
    
    response1 = requests.post(f"{BASE_URL}/api/v1/execute", json=payload)
    elapsed_time1 = (time.time() - start_time) * 1000
    
    if response1.status_code == 200:
        result1 = response1.json()
        print(f"  Cached: {result1.get('cached', False)}")
        print(f"  Duration: {result1.get('duration_ms', 0)}ms")
        print(f"  Total Time: {elapsed_time1:.2f}ms")
    
    print("\nSecond execution (cache hit)...")
    start_time = time.time()
    
    response2 = requests.post(f"{BASE_URL}/api/v1/execute", json=payload)
    elapsed_time2 = (time.time() - start_time) * 1000
    
    if response2.status_code == 200:
        result2 = response2.json()
        print(f"  Cached: {result2.get('cached', False)}")
        print(f"  Duration: {result2.get('duration_ms', 0)}ms")
        print(f"  Total Time: {elapsed_time2:.2f}ms")
        
        if result2.get('cached'):
            speedup = (elapsed_time1 / elapsed_time2) if elapsed_time2 > 0 else 0
            print(f"\n  Cache Speedup: {speedup:.1f}x faster")
    
    return response2.status_code == 200


def test_async_execution():
    """Test asynchronous execution"""
    print_section("6. Asynchronous Execution")
    
    payload = {
        "strategy_name": "call_sell_future_buy_t2",
        "parameters": {
            "spot_adjustment_type": 1,
            "spot_adjustment": 2.0,
            "call_sell_position": 5.0,
            "symbol": "NIFTY"
        },
        "use_cache": False,
        "async_execution": True
    }
    
    print(f"Submitting async job: {payload['strategy_name']}")
    response = requests.post(f"{BASE_URL}/api/v1/execute", json=payload)
    
    if response.status_code == 200:
        result = response.json()
        job_id = result.get('job_id')
        print(f"Job ID: {job_id}")
        print(f"Status: {result['status']}")
        
        if job_id:
            print("\nPolling job status...")
            max_attempts = 30
            attempt = 0
            
            while attempt < max_attempts:
                time.sleep(2)
                attempt += 1
                
                status_response = requests.get(f"{BASE_URL}/api/v1/jobs/{job_id}")
                if status_response.status_code == 200:
                    status = status_response.json()
                    print(f"  Attempt {attempt}: {status['status']}")
                    
                    if status['status'] in ['completed', 'failed']:
                        print(f"\nFinal Status: {status['status']}")
                        if status['status'] == 'completed':
                            result_data = status.get('result', {})
                            print(f"Row Count: {result_data.get('result', {}).get('row_count', 0)}")
                        return True
                else:
                    print(f"  Error checking status: {status_response.status_code}")
            
            print("\nTimeout waiting for job completion")
            return False
    else:
        print(f"Error: {response.text}")
        return False


def test_metrics():
    """Test metrics endpoints"""
    print_section("7. Performance Metrics")
    
    # Overall metrics
    print("Overall Metrics:")
    response = requests.get(f"{BASE_URL}/api/v1/metrics")
    if response.status_code == 200:
        metrics = response.json()
        print(f"  Uptime: {metrics.get('uptime_hours', 0):.2f} hours")
        
        overall = metrics.get('overall', {})
        print(f"  Total Executions: {overall.get('total_executions', 0)}")
        print(f"  Success Rate: {overall.get('success_rate', 0):.1f}%")
        print(f"  Cache Hit Rate: {overall.get('cache_hit_rate', 0):.1f}%")
        
        # Strategy-specific metrics
        print("\nBy Strategy:")
        by_strategy = metrics.get('by_strategy', {})
        for strategy_name, strategy_metrics in by_strategy.items():
            print(f"  {strategy_name}:")
            print(f"    Executions: {strategy_metrics.get('total_executions', 0)}")
            print(f"    Avg Time: {strategy_metrics.get('avg_execution_time_ms', 0):.2f}ms")
            print(f"    Success Rate: {strategy_metrics.get('success_rate', 0):.1f}%")
        
        # Cache metrics
        cache_metrics = metrics.get('cache', {})
        if 'total_cached_entries' in cache_metrics:
            print("\nCache:")
            print(f"  Cached Entries: {cache_metrics.get('total_cached_entries', 0)}")
            print(f"  Memory Used: {cache_metrics.get('memory_used_mb', 0):.2f} MB")
    
    return response.status_code == 200


def test_cache_stats():
    """Test cache statistics"""
    print_section("8. Cache Statistics")
    
    response = requests.get(f"{BASE_URL}/api/v1/cache/stats")
    if response.status_code == 200:
        stats = response.json()
        print(f"Total Cached Entries: {stats.get('total_cached_entries', 0)}")
        print(f"Total Cache Hits: {stats.get('total_cache_hits', 0)}")
        print(f"Memory Used: {stats.get('memory_used_mb', 0):.2f} MB")
        print(f"Memory Peak: {stats.get('memory_peak_mb', 0):.2f} MB")
        
        by_strategy = stats.get('by_strategy', {})
        if by_strategy:
            print("\nBy Strategy:")
            for strategy_name, strategy_stats in by_strategy.items():
                print(f"  {strategy_name}:")
                print(f"    Cached Entries: {strategy_stats.get('cached_entries', 0)}")
                print(f"    Total Hits: {strategy_stats.get('total_hits', 0)}")
    
    return response.status_code == 200


def test_cache_invalidation():
    """Test cache invalidation"""
    print_section("9. Cache Invalidation")
    
    # Invalidate specific strategy
    print("Invalidating cache for call_sell_future_buy_t1...")
    response = requests.post(
        f"{BASE_URL}/api/v1/cache/invalidate",
        params={"strategy_name": "call_sell_future_buy_t1"}
    )
    
    if response.status_code == 200:
        result = response.json()
        print(f"Status: {result['status']}")
        print(f"Deleted Entries: {result['deleted_entries']}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_execution_history():
    """Test execution history"""
    print_section("10. Execution History")
    
    response = requests.get(f"{BASE_URL}/api/v1/executions", params={"limit": 10})
    if response.status_code == 200:
        executions = response.json()
        print(f"Recent Executions: {len(executions)}")
        
        for i, execution in enumerate(executions[:5], 1):
            print(f"\n{i}. Execution #{execution['execution_id']}")
            print(f"   Strategy: {execution['strategy_name']}")
            print(f"   Status: {execution['status']}")
            print(f"   Duration: {execution.get('duration_ms', 0)}ms")
            print(f"   Rows: {execution.get('row_count', 0)}")
    
    return response.status_code == 200


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("  PHASE 2 API TEST SUITE")
    print("="*60)
    
    tests = [
        ("Health Check", test_health),
        ("List Strategies", test_list_strategies),
        ("Cache Health", test_cache_health),
        ("Sync Execution (No Cache)", test_sync_execution_no_cache),
        ("Sync Execution (With Cache)", test_sync_execution_with_cache),
        ("Async Execution", test_async_execution),
        ("Performance Metrics", test_metrics),
        ("Cache Statistics", test_cache_stats),
        ("Cache Invalidation", test_cache_invalidation),
        ("Execution History", test_execution_history),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\nError in {test_name}: {str(e)}")
            results.append((test_name, False))
    
    # Summary
    print_section("TEST SUMMARY")
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed ({(passed/total*100):.1f}%)")
    
    if passed == total:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")


if __name__ == "__main__":
    main()
