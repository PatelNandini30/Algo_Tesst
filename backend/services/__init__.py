"""
Services module for optimized data loading and caching.
"""

from .data_loader import HighPerformanceLoader, get_loader, reset_loader, pl
from .data_memory_cache import DataMemoryCache, get_memory_cache, clear_memory_cache, get_cache_stats
from .backtest_cache import BacktestCache, get_backtest_cache, clear_backtest_cache
from .parallel_executor import ParallelExecutor, run_parallel_backtest

__all__ = [
    'HighPerformanceLoader',
    'get_loader',
    'reset_loader',
    'pl',
    'DataMemoryCache',
    'get_memory_cache',
    'clear_memory_cache',
    'get_cache_stats',
    'BacktestCache',
    'get_backtest_cache',
    'clear_backtest_cache',
    'ParallelExecutor',
    'run_parallel_backtest'
]
