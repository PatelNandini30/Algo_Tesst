from fastapi import APIRouter, HTTPException, Response, Header, UploadFile, File
from typing import Dict, Any, List, Optional, Tuple
# Import generic multi-leg engine
# NOTE: keep FastAPI imports at top for readability
from engines.generic_algotest_engine import run_algotest_backtest
from services.algotest_job import execute_algotest_job
from services.redis_cache import redis_cache as shared_redis_cache
from worker.tasks import run_algotest_job
from worker.celery import celery_app
import sys
import os
import pandas as pd
import numpy as np
import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _normalize_date(value: Any) -> str:
    if not value:
        return ''
    value = str(value).strip()
    for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(value, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return value

# Thread pool for async tasks (I/O/cache warming) and process pool for CPU-heavy backtests
_backtest_executor = ThreadPoolExecutor(max_workers=3)

# Simple in-memory cache for backtest results
class BacktestCache:
    def __init__(self, max_size=100, ttl_seconds=3600):
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._max_size = max_size
        self._ttl = ttl_seconds
    
    def _make_key(self, params: dict) -> str:
        """Hash the full params dict so strategy definitions aren't ignored."""
        try:
            key_str = json.dumps(params, sort_keys=True, default=str)
        except Exception:
            key_str = repr(params)
        return hashlib.sha256(key_str.encode()).hexdigest()
    
    def get(self, params: dict) -> Optional[dict]:
        key = self._make_key(params)
        with self._lock:
            if key in self._cache:
                result, timestamp = self._cache[key]
                if time.time() - timestamp < self._ttl:
                    return result
                else:
                    del self._cache[key]
        return None
    
    def set(self, params: dict, result: dict):
        key = self._make_key(params)
        with self._lock:
            if len(self._cache) >= self._max_size:
                # Remove oldest entry
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]
            self._cache[key] = (result, time.time())

# Global cache instance
backtest_cache = BacktestCache(max_size=50, ttl_seconds=1800)  # 30 min cache

# Add the parent directory to the path to import engines
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import strategy functions dynamically to avoid circular imports
import importlib

# Import strategy types for dynamic backtest
# First try direct import
try:
    from strategies.strategy_types import (
        InstrumentType, OptionType, PositionType, ExpiryType,
        StrikeSelectionType, StrategyDefinition, Leg, StrikeSelection,
        EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
        ReEntryMode
    )
    IMPORT_SUCCESS = True
except ImportError as e:
    print(f"Direct import failed: {e}")
    # Fallback for direct execution
    try:
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'strategies')
        if strategies_dir not in sys.path:
            sys.path.insert(0, strategies_dir)
        from strategy_types import (
            InstrumentType, OptionType, PositionType, ExpiryType,
            StrikeSelectionType, StrategyDefinition, Leg, StrikeSelection,
            EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
            ReEntryMode
        )
        IMPORT_SUCCESS = True
        print("Fallback import successful")
    except ImportError as e2:
        print(f"Fallback import also failed: {e2}")
        IMPORT_SUCCESS = False
        # Define minimal fallback classes for error handling
        class InstrumentType:
            OPTION = "Option"
            FUTURE = "Future"
            @classmethod
            def __call__(cls, value):
                return value
        
        class OptionType:
            CE = "CE"
            PE = "PE"
            @classmethod
            def __call__(cls, value):
                return value
                
        class PositionType:
            BUY = "Buy"
            SELL = "Sell"
            @classmethod
            def __call__(cls, value):
                return value

router = APIRouter()


@router.post("/clear-cache")
async def clear_cache():
    """Clear the backtest cache"""
    backtest_cache._cache.clear()
    return {"message": "Cache cleared"}


@router.post("/warm-cache")
async def warm_cache(request: dict):
    """
    Pre-load bulk data in background - makes actual backtest run faster.
    Returns immediately while data loads in background thread.
    """
    from base import bulk_load_options
    
    symbol = request.get('index', request.get('symbol', 'NIFTY'))
    from_date = request.get('from_date', request.get('date_from'))
    to_date = request.get('to_date', request.get('date_to'))
    
    if not from_date or not to_date:
        return {"status": "error", "message": "Missing from_date or to_date"}
    
    def _load():
        try:
            bulk_load_options(symbol, from_date, to_date)
            return {"status": "warmed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    _backtest_executor.submit(_load)
    
    return {"status": "warming", "message": f"Pre-loading {symbol} {from_date} to {to_date}"}


@router.post("/upload-filter-csv")
async def upload_filter_csv(file: UploadFile = File(...)):
    """
    Upload and parse a CSV file for filter segments.
    Returns parsed segments (start_date, end_date) for use in backtest.
    
    Supports:
    - Column formats: start_date/end_date OR entry_date/exit_date
    - Date formats: All common formats (dd-mm-yyyy, mm/dd/yyyy, yyyy-mm-dd, etc.)
    """
    try:
        import sys, os
        _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _base_dir not in sys.path:
            sys.path.insert(0, _base_dir)
        from base import parse_filter_csv
        
        # Read file content
        content = await file.read()
        csv_content = content.decode('utf-8')
        
        # Parse CSV
        segments = parse_filter_csv(csv_content)
        
        if not segments:
            return {
                "success": False,
                "message": "No valid date ranges found in CSV. Please check the format.",
                "segments": []
            }
        
        # Convert dates to strings for JSON response
        segments_str = [
            {
                "start": seg["start"].strftime("%Y-%m-%d") if seg["start"] else None,
                "end": seg["end"].strftime("%Y-%m-%d") if seg["end"] else None
            }
            for seg in segments
        ]
        
        return {
            "success": True,
            "message": f"Loaded {len(segments)} filter segments",
            "segments": segments_str,
            "count": len(segments)
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error parsing CSV: {str(e)}",
            "segments": []
        }


@router.get("/filter-segments")
async def get_filter_segments():
    """
    Get available filter segment metadata for each built-in filter.
    Returns count, range, preview rows and the serialized segments for STR 5x1, 5x2 and base2.
    """
    try:
        import sys, os
        _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _base_dir not in sys.path:
            sys.path.insert(0, _base_dir)
        from base import (
            get_filter_segments as base_get_filter_segments,
            load_super_trend_dates,
        )

        # Ensure STR segments are loaded into memory so counts/range are accurate
        load_super_trend_dates()

        filter_configs = [
            ("5x1", "STR 5,1"),
            ("5x2", "STR 5,2"),
            ("base2", "base2"),
        ]

        filters = {}

        def _serialize_segments(segments):
            serialized = []
            for seg in segments:
                start = seg.get("start")
                end = seg.get("end")
                if not start or not end:
                    continue
                try:
                    start_iso = start.strftime("%Y-%m-%d")
                except Exception:
                    start_iso = str(start)
                try:
                    end_iso = end.strftime("%Y-%m-%d")
                except Exception:
                    end_iso = str(end)
                serialized.append({"start": start_iso, "end": end_iso})
            return serialized

        def _range_from_segments(serialized):
            if not serialized:
                return None
            starts = [s["start"] for s in serialized]
            ends = [s["end"] for s in serialized]
            return {
                "from": min(starts),
                "to": max(ends),
            }

        for config_key, label in filter_configs:
            segments = base_get_filter_segments(config_key)
            serialized_segments = _serialize_segments(segments)
            summary_range = _range_from_segments(serialized_segments)
            display_range = None
            if config_key == "base2":
                display_range = "Full DB date range (engine resolves)"

            filters[config_key] = {
                "label": label,
                "count": len(serialized_segments),
                "segments": serialized_segments,
                "preview": serialized_segments[:5],
                "range": summary_range,
                "display_range": display_range,
            }

        return {"success": True, "filters": filters}

    except Exception as e:
        import traceback
        return {
            "success": False,
            "message": str(e),
            "filters": {},
            "traceback": traceback.format_exc(),
        }


@router.get("/backtest/str-segments")
@router.get("/str-segments")
async def get_str_segments():
    """
    Return all STR segments for both configs (5x1 and 5x2).
    Used by frontend to display the segment preview table on page load.
    Format: {"5x1": [{"start": "DD-MM-YYYY", "end": "DD-MM-YYYY"}, ...], "5x2": [...]}
    """
    try:
        import sys, os
        _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _base_dir not in sys.path:
            sys.path.insert(0, _base_dir)
        from base import load_super_trend_dates, get_super_trend_segments
        load_super_trend_dates()
        result = {}
        for cfg in ("5x1", "5x2"):
            segs = get_super_trend_segments(cfg)
            result[cfg] = [
                {
                    "start": s["start"].strftime("%d-%m-%Y"),
                    "end":   s["end"].strftime("%d-%m-%Y"),
                }
                for s in segs
            ]
        return result
    except Exception as e:
        import traceback
        return {"5x1": [], "5x2": [], "error": str(e), "traceback": traceback.format_exc()}




@router.get("/export/trades")
async def export_trades(strategy_id: str):
    """
    Export trade sheet as CSV
    """
    # This is a placeholder - in a real implementation, you would retrieve
    # the trade data based on strategy_id and return it as CSV
    content = "Trade Date,Strategy Name,Leg Type,Strike,Entry Premium,Exit Premium,Quantity,P&L,Running Equity\n"
    content += "2023-01-01,Sample Strategy,CE SELL,18000,200.5,180.2,1,-20.3,10000\n"
    
    response = Response(content=content)
    response.headers["Content-Disposition"] = f"attachment; filename=trade_sheet_{strategy_id}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


@router.get("/export/summary")
async def export_summary(strategy_id: str):
    """
    Export summary as CSV
    """
    # This is a placeholder - in a real implementation, you would retrieve
    # the summary data based on strategy_id and return it as CSV
    content = "Metric,Value\n"
    content += "Total P&L,5000.00\n"
    content += "CAGR,15.25\n"
    content += "Max Drawdown,-12.34\n"
    content += "CAR/MDD,1.24\n"
    content += "Win Rate,65.43\n"
    content += "Total Trades,156\n"
    
    response = Response(content=content)
    response.headers["Content-Disposition"] = f"attachment; filename=summary_{strategy_id}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


@router.post("/algotest")
async def run_algotest_backtest_endpoint(request: dict):
    """
    Legacy synchronous endpoint kept for backwards compatibility.
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_backtest_executor, execute_algotest_job, request)
    return result


@router.post("/algotest/jobs")
async def queue_algotest_job(request: dict):
    """
    Enqueue an AlgoTest backtest to run asynchronously via Celery.
    """
    payload = dict(request or {})
    task = run_algotest_job.apply_async(args=[payload])
    return {"status": "queued", "job_id": task.id}


@router.get("/algotest/jobs/{job_id}")
async def get_algotest_job_status(job_id: str):
    """
    Check status/result of an async AlgoTest backtest job.
    """
    task = celery_app.AsyncResult(job_id)
    info = None
    try:
        state = task.state
        info = task.result if state == "SUCCESS" else task.info
    except ValueError as exc:
        logger.warning("Malformed Celery metadata for job %s: %s", job_id, exc)
        state = "FAILURE"
        info = {"error": "Task metadata corrupted"}
    if state == "PENDING":
        return {"status": "queued"}
    if state in {"STARTED", "PROCESSING", "RETRY"}:
        return {"status": "running", "meta": info or {"status": "Running..."}} 
    if state == "SUCCESS":
        result_payload = info or {}
        if result_payload.get("status") == "error":
            return {"status": "failed", "error": result_payload.get("message", "Backtest failed")}
        return {"status": "completed", "result": result_payload}
    if state == "FAILURE":
        error = None
        if isinstance(info, dict):
            error = info.get("message") or info.get("error") or str(info)
        else:
            error = str(info)
        return {"status": "failed", "error": error}
    return {"status": state.lower(), "meta": info}
