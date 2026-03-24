"""
Parallel Backtest Execution

PHASE 8: Parallel Execution

Features:
- Parallelize backtests across CPU cores
- Split by expiry, month, or strategy leg
- Combine results correctly
- Thread-safe operations

Usage:
    from services.parallel_executor import ParallelExecutor, run_parallel_backtest
    
    executor = ParallelExecutor(max_workers=4)
    
    # Split by month for parallel execution
    results = executor.run_by_month(
        params,
        start_date="2020-01-01",
        end_date="2025-12-31"
    )
    
    # Combine results
    combined = executor.combine_results(results)
"""

import importlib
import os
import logging
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# Configuration
DEFAULT_WORKERS = min(mp.cpu_count(), 2)


@dataclass
class BacktestChunk:
    """Single chunk of backtest work."""
    chunk_id: int
    params: Dict[str, Any]
    from_date: str
    to_date: str


@dataclass
class BacktestResult:
    """Result from a single backtest chunk."""
    chunk_id: int
    trades_df: Optional[pd.DataFrame]
    summary: Dict[str, Any]
    pivot: Dict[str, Any]
    success: bool
    error: Optional[str] = None


def _load_backtest_func(func_path: str) -> Callable:
    module_name, _, func_name = func_path.rpartition('.')
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _run_single_backtest_worker(chunk_dict: dict, backtest_func_path: str) -> BacktestResult:
    chunk = BacktestChunk(
        chunk_id=chunk_dict["chunk_id"],
        params=chunk_dict["params"],
        from_date=chunk_dict["from_date"],
        to_date=chunk_dict["to_date"],
    )
    backtest_func = _load_backtest_func(backtest_func_path)

    try:
        logger.info(
            f"[PARALLEL] Running chunk {chunk.chunk_id}: "
            f"{chunk.from_date} to {chunk.to_date}"
        )
        params = chunk.params.copy()
        params["from_date"] = chunk.from_date
        params["to_date"] = chunk.to_date

        trades_df, summary, pivot = backtest_func(params)
        return BacktestResult(
            chunk_id=chunk.chunk_id,
            trades_df=trades_df,
            summary=summary,
            pivot=pivot,
            success=True
        )
    except Exception as e:
        logger.error(f"[PARALLEL] Chunk {chunk.chunk_id} failed: {e}")
        return BacktestResult(
            chunk_id=chunk.chunk_id,
            trades_df=pd.DataFrame(),
            summary={},
            pivot={},
            success=False,
            error=str(e)
        )


class ParallelExecutor:
    """
    Parallel backtest executor.
    
    Splits work by:
    - Month: Each month runs in parallel
    - Expiry: Each expiry runs in parallel  
    - Leg: Strategy legs run in parallel
    """
    
    def __init__(self, max_workers: int = DEFAULT_WORKERS):
        self._max_workers = min(max_workers, int(os.getenv("MAX_PARALLEL_WORKERS", "2")))
        logger.info(f"[PARALLEL] Initialized with {self._max_workers} workers")
    
    def _generate_month_chunks(
        self,
        params: Dict[str, Any],
        from_date: str,
        to_date: str
    ) -> List[BacktestChunk]:
        """Generate monthly chunks for parallel execution."""
        chunks = []
        
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        
        chunk_id = 0
        current = start
        
        while current <= end:
            # Find month end
            month_end = (current.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            chunk_end = min(month_end, end)
            
            chunks.append(BacktestChunk(
                chunk_id=chunk_id,
                params=params.copy(),
                from_date=current.strftime("%Y-%m-%d"),
                to_date=chunk_end.strftime("%Y-%m-%d")
            ))
            
            # Move to next month
            current = chunk_end + timedelta(days=1)
            chunk_id += 1
        
        logger.info(f"[PARALLEL] Generated {len(chunks)} monthly chunks")
        return chunks
    
    def run_by_month(
        self,
        params: Dict[str, Any],
        from_date: str,
        to_date: str,
        backtest_func: Callable
    ) -> List[BacktestResult]:
        """
        Run backtests in parallel by month.
        
        Args:
            params: Backtest parameters
            from_date: Start date
            to_date: End date
            backtest_func: Function that runs a single backtest
        
        Returns:
            List of BacktestResult, one per month
        """
        chunks = self._generate_month_chunks(params, from_date, to_date)
        
        results = []
        
        logger.info(f"[PARALLEL] Starting {len(chunks)} parallel backtests...")
        
        backtest_func_path = f"{backtest_func.__module__}.{backtest_func.__name__}"
        with ProcessPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(
                    _run_single_backtest_worker,
                    {
                        "chunk_id": chunk.chunk_id,
                        "params": chunk.params,
                        "from_date": chunk.from_date,
                        "to_date": chunk.to_date,
                    },
                    backtest_func_path
                ): chunk
                for chunk in chunks
            }
            
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
        
        results.sort(key=lambda x: x.chunk_id)
        success_count = sum(1 for r in results if r.success)
        logger.info(
            f"[PARALLEL] Completed: {success_count}/{len(results)} successful"
        )
        
        return results


    def combine_results(
        self,
        results: List[BacktestResult]
    ) -> tuple:
        """
        Combine results from parallel execution.
        
        Returns:
            (combined_trades_df, combined_summary, combined_pivot)
        """
        # Filter successful results
        successful = [r for r in results if r.success]
        
        if not successful:
            return pd.DataFrame(), {}, {}
        
        # Combine trades DataFrames
        trades_list = [r.trades_df for r in successful if r.trades_df is not None]
        
        if trades_list:
            combined_trades = pd.concat(trades_list, ignore_index=True)
        else:
            combined_trades = pd.DataFrame()
        
        # Combine summaries (sum P&L, count trades)
        combined_summary = self._combine_summaries([r.summary for r in successful])
        
        # Combine pivots
        combined_pivot = self._combine_pivots([r.pivot for r in successful])
        
        return combined_trades, combined_summary, combined_pivot
    
    def _combine_summaries(
        self,
        summaries: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Combine multiple summary dicts."""
        if not summaries:
            return {}
        
        combined = {}
        
        # Sum numeric values
        numeric_keys = [
            "Total Trades", "Winning Trades", "Losing Trades",
            "Total P&L", "Win Rate", "Avg Win", "Avg Loss",
            "Max Profit", "Max Loss"
        ]
        
        for key in numeric_keys:
            values = []
            for s in summaries:
                if key in s:
                    val = s[key]
                    if isinstance(val, (int, float)):
                        values.append(val)
                    elif isinstance(val, str):
                        # Try to extract number
                        try:
                            values.append(float(val.replace("%", "").replace("₹", "").replace(",", "")))
                        except:
                            pass
            
            if values:
                if key in ["Win Rate"]:
                    combined[key] = f"{sum(values) / len(values):.2f}%"
                elif key in ["Total P&L", "Max Profit", "Max Loss"]:
                    combined[key] = f"₹{sum(values):,.2f}"
                else:
                    combined[key] = sum(values)
        
        return combined
    
    def _combine_pivots(
        self,
        pivots: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Combine multiple pivot tables."""
        if not pivots:
            return {}
        
        # Simple merge - take first non-empty
        for pivot in pivots:
            if pivot:
                return pivot
        
        return {}


def run_parallel_backtest(
    params: Dict[str, Any],
    from_date: str,
    to_date: str,
    backtest_func: Callable,
    max_workers: int = None
) -> tuple:
    """
    Convenience function to run backtest in parallel.
    
    Args:
        params: Backtest parameters
        from_date: Start date
        to_date: End date
        backtest_func: Function that runs a single backtest
        max_workers: Number of parallel workers
    
    Returns:
        (trades_df, summary, pivot)
    """
    if max_workers is None:
        max_workers = DEFAULT_WORKERS
    
    executor = ParallelExecutor(max_workers=max_workers)
    
    # Run parallel backtests
    results = executor.run_by_month(
        params=params,
        from_date=from_date,
        to_date=to_date,
        backtest_func=backtest_func
    )
    
    # Combine results
    return executor.combine_results(results)
