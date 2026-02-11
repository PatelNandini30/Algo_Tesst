"""
Performance monitoring and metrics collection.
"""
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import defaultdict
import threading


class MetricsCollector:
    """Collects and aggregates execution metrics."""
    
    def __init__(self):
        self._metrics = defaultdict(lambda: {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "total_execution_time_ms": 0,
            "min_execution_time_ms": float('inf'),
            "max_execution_time_ms": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "last_execution": None,
            "error_count": 0,
            "errors": []
        })
        self._lock = threading.Lock()
        self._start_time = datetime.utcnow()
    
    def record_execution(self, strategy_name: str, execution_time_ms: int, 
                        success: bool, cached: bool = False, error: str = None):
        """
        Record a strategy execution.
        
        Args:
            strategy_name: Name of the strategy
            execution_time_ms: Execution time in milliseconds
            success: Whether execution was successful
            cached: Whether result was from cache
            error: Error message if failed
        """
        with self._lock:
            metrics = self._metrics[strategy_name]
            
            metrics["total_executions"] += 1
            metrics["last_execution"] = datetime.utcnow().isoformat()
            
            if success:
                metrics["successful_executions"] += 1
            else:
                metrics["failed_executions"] += 1
                metrics["error_count"] += 1
                if error:
                    # Keep last 10 errors
                    metrics["errors"].append({
                        "timestamp": datetime.utcnow().isoformat(),
                        "error": error
                    })
                    if len(metrics["errors"]) > 10:
                        metrics["errors"] = metrics["errors"][-10:]
            
            if cached:
                metrics["cache_hits"] += 1
            else:
                metrics["cache_misses"] += 1
            
            # Update execution time stats
            metrics["total_execution_time_ms"] += execution_time_ms
            metrics["min_execution_time_ms"] = min(
                metrics["min_execution_time_ms"], 
                execution_time_ms
            )
            metrics["max_execution_time_ms"] = max(
                metrics["max_execution_time_ms"], 
                execution_time_ms
            )
    
    def get_strategy_metrics(self, strategy_name: str) -> Dict[str, Any]:
        """Get metrics for a specific strategy."""
        with self._lock:
            if strategy_name not in self._metrics:
                return {}
            
            metrics = self._metrics[strategy_name].copy()
            
            # Calculate averages
            if metrics["total_executions"] > 0:
                metrics["avg_execution_time_ms"] = round(
                    metrics["total_execution_time_ms"] / metrics["total_executions"],
                    2
                )
                metrics["success_rate"] = round(
                    (metrics["successful_executions"] / metrics["total_executions"]) * 100,
                    2
                )
                
                total_cache_requests = metrics["cache_hits"] + metrics["cache_misses"]
                if total_cache_requests > 0:
                    metrics["cache_hit_rate"] = round(
                        (metrics["cache_hits"] / total_cache_requests) * 100,
                        2
                    )
                else:
                    metrics["cache_hit_rate"] = 0.0
            else:
                metrics["avg_execution_time_ms"] = 0
                metrics["success_rate"] = 0.0
                metrics["cache_hit_rate"] = 0.0
            
            # Handle infinity for min time
            if metrics["min_execution_time_ms"] == float('inf'):
                metrics["min_execution_time_ms"] = 0
            
            return metrics
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get aggregated metrics for all strategies."""
        with self._lock:
            all_metrics = {}
            total_executions = 0
            total_successful = 0
            total_failed = 0
            total_cache_hits = 0
            total_cache_misses = 0
            
            for strategy_name in self._metrics:
                strategy_metrics = self.get_strategy_metrics(strategy_name)
                all_metrics[strategy_name] = strategy_metrics
                
                total_executions += strategy_metrics["total_executions"]
                total_successful += strategy_metrics["successful_executions"]
                total_failed += strategy_metrics["failed_executions"]
                total_cache_hits += strategy_metrics["cache_hits"]
                total_cache_misses += strategy_metrics["cache_misses"]
            
            # Calculate overall statistics
            uptime_seconds = (datetime.utcnow() - self._start_time).total_seconds()
            
            overall_success_rate = 0.0
            if total_executions > 0:
                overall_success_rate = round(
                    (total_successful / total_executions) * 100,
                    2
                )
            
            total_cache_requests = total_cache_hits + total_cache_misses
            overall_cache_hit_rate = 0.0
            if total_cache_requests > 0:
                overall_cache_hit_rate = round(
                    (total_cache_hits / total_cache_requests) * 100,
                    2
                )
            
            return {
                "uptime_seconds": round(uptime_seconds, 2),
                "uptime_hours": round(uptime_seconds / 3600, 2),
                "start_time": self._start_time.isoformat(),
                "current_time": datetime.utcnow().isoformat(),
                "overall": {
                    "total_executions": total_executions,
                    "successful_executions": total_successful,
                    "failed_executions": total_failed,
                    "success_rate": overall_success_rate,
                    "cache_hits": total_cache_hits,
                    "cache_misses": total_cache_misses,
                    "cache_hit_rate": overall_cache_hit_rate
                },
                "by_strategy": all_metrics
            }
    
    def reset_metrics(self, strategy_name: str = None):
        """
        Reset metrics for a strategy or all strategies.
        
        Args:
            strategy_name: Strategy to reset (None = reset all)
        """
        with self._lock:
            if strategy_name:
                if strategy_name in self._metrics:
                    del self._metrics[strategy_name]
            else:
                self._metrics.clear()
                self._start_time = datetime.utcnow()


# Global metrics collector instance
_metrics_collector: MetricsCollector = None


def get_metrics_collector() -> MetricsCollector:
    """Get or create global metrics collector instance."""
    global _metrics_collector
    
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    
    return _metrics_collector
