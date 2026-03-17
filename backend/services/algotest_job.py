"""Shared helper for running AlgoTest backtests with caching/logging."""
import traceback
from typing import Any, Dict

import pandas as pd
from sqlalchemy.exc import OperationalError

from engines.generic_algotest_engine import run_algotest_backtest
from base import bulk_clear_options, bulk_load_options
from database import reset_engine
from services.backtest_cache import get_backtest_cache


def _normalize_request(request: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(request or {})
    payload['index'] = payload.get('index', 'NIFTY')
    payload['from_date'] = payload.get('date_from') or payload.get('from_date')
    payload['to_date'] = payload.get('date_to') or payload.get('to_date')
    return payload


def _convert_numpy(obj: Any) -> Any:
    import numpy as np

    if obj is None:
        return None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dict):
        return {str(k): _convert_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_convert_numpy(item) for item in obj]
    if hasattr(obj, 'item'):
        try:
            return obj.item()
        except Exception:
            pass
    if hasattr(obj, 'tolist'):
        try:
            return obj.tolist()
        except Exception:
            pass
    return obj


def _format_dates(trades: Any) -> Any:
    try:
        for trade in trades:
            for key, value in list(trade.items()):
                if value is None:
                    continue
                if hasattr(value, 'strftime'):
                    trade[key] = value.strftime('%d/%m/%Y')
                elif isinstance(value, str) and 'T' in value:
                    try:
                        trade[key] = pd.to_datetime(value).strftime('%d/%m/%Y')
                    except Exception:
                        pass
        return trades
    except Exception:
        return trades


def execute_algotest_job(request: Dict[str, Any]) -> Dict[str, Any]:
    payload = _normalize_request(request)
    index = payload['index']
    from_date = payload.get('from_date')
    to_date = payload.get('to_date')

    redis_cache = None
    use_cache = False
    cache_key = None

    try:
        redis_cache = get_backtest_cache()
        if redis_cache.is_available():
            use_cache = True
            cache_key = redis_cache.generate_key(symbol=index, from_date=from_date, to_date=to_date, strategy_config=payload)
            cached = redis_cache.get(cache_key)
            if cached:
                print(f"⚡ REDIS CACHE HIT: {cache_key}")
                return {**cached, 'cached': True}
    except Exception as err:
        print(f"[CACHE] Redis unavailable: {err}")
        use_cache = False

    trades_df = None
    summary = {}
    pivot = {}
    result_payload = None

    try:
        try:
            bulk_load_options(index, from_date, to_date)
        except OperationalError as err:
            traceback.print_exc()
            reset_engine()
            return {
                "status": "error",
                "message": "PostgreSQL connection lost while loading option data."
            }
        print(f"   ✅ O(1) lookup dict ready")
        trades_df, summary, pivot = run_algotest_backtest(payload)
        trades_json = trades_df.to_dict('records') if trades_df is not None and not trades_df.empty else []
        trades_json = _convert_numpy(trades_json)
        trades_json = _format_dates(trades_json)
        summary = _convert_numpy(summary)
        pivot = _convert_numpy(pivot)

        result_payload = {
            'status': 'success',
            'trades': trades_json,
            'summary': summary,
            'pivot': pivot,
            'cached': False,
        }

        if use_cache and redis_cache and cache_key:
            redis_cache.set(cache_key, result_payload)
            print(f"💾 CACHED: {cache_key}")

        return result_payload
    except OperationalError as err:
        traceback.print_exc()
        reset_engine()
        return {
            'status': 'error',
            'message': 'PostgreSQL connection dropped while running the backtest.'
        }
    except Exception as err:
        traceback.print_exc()
        return {
            'status': 'error',
            'message': str(err)
        }
    finally:
        try:
            bulk_clear_options()
            print("[CLEANUP] Bulk data cleared from memory")
        except Exception as cleanup_err:
            print(f"[WARN] Failed to clear bulk data: {cleanup_err}")
