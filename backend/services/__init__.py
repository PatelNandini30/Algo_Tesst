"""
Services module for optimized data loading and caching.
"""

from .data_loader import (
    HighPerformanceLoader, get_loader, reset_loader, pl,
    bulk_load, bulk_clear, is_bulk_loaded,
    get_bulk_option_price, get_bulk_spot_price, get_bulk_strikes_for_date,
    get_bulk_expiry_dates, get_bulk_spot_df, get_bulk_options_df
)
from .data_memory_cache import DataMemoryCache, get_memory_cache, clear_memory_cache, get_cache_stats
from .backtest_cache import BacktestCache, get_backtest_cache, clear_backtest_cache
from .parallel_executor import ParallelExecutor, run_parallel_backtest

__all__ = [
    'HighPerformanceLoader',
    'get_loader',
    'reset_loader',
    'pl',
    'bulk_load',
    'bulk_clear',
    'is_bulk_loaded',
    'get_bulk_option_price',
    'get_bulk_spot_price',
    'get_bulk_strikes_for_date',
    'get_bulk_expiry_dates',
    'get_bulk_spot_df',
    'get_bulk_options_df',
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
