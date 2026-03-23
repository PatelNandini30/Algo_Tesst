"""Shared helper for running AlgoTest backtests with caching/logging."""
import traceback
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict

import pandas as pd
from sqlalchemy.exc import OperationalError

from engines.generic_algotest_engine import run_algotest_backtest, get_expiry_dates
from base import bulk_load_options
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


def _run_backtest_chunk(args: tuple) -> list:
    """Run backtest for a subset of expiry dates. Must be top-level for pickling."""
    params, chunk_dates = args
    from base import bulk_load_options
    from engines.generic_algotest_engine import run_algotest_backtest
    
    index = params.get('index', 'NIFTY')
    from_date = params.get('from_date')
    to_date = params.get('to_date')
    
    try:
        bulk_load_options(index, from_date, to_date)
        chunk_params = dict(params)
        chunk_params['_expiry_chunk'] = chunk_dates
        df, _, _ = run_algotest_backtest(chunk_params)
        return df.to_dict('records') if df is not None and not df.empty else []
    except Exception:
        return []


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
                return {**cached, 'cached': True}
    except Exception:
        use_cache = False

    try:
        try:
            bulk_load_options(index, from_date, to_date)
        except OperationalError:
            traceback.print_exc()
            reset_engine()
            return {
                "status": "error",
                "message": "PostgreSQL connection lost while loading option data."
            }
        
        n_workers = int(os.environ.get("BACKTEST_WORKERS", "1"))
        expiry_type = payload.get('expiry_type', 'WEEKLY')
        
        expiry_df = get_expiry_dates(index, expiry_type.lower(), from_date, to_date)
        
        all_trades = []
        
        if n_workers > 1 and expiry_df is not None and not expiry_df.empty and len(expiry_df) >= n_workers * 2:
            expiry_dates = expiry_df['Current Expiry'].dt.strftime('%Y-%m-%d').tolist()
            chunk_size = max(1, len(expiry_dates) // n_workers)
            
            chunks = []
            for i in range(n_workers):
                start = i * chunk_size
                end = start + chunk_size if i < n_workers - 1 else len(expiry_dates)
                chunks.append((dict(payload), expiry_dates[start:end]))
            
            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                results = list(executor.map(_run_backtest_chunk, chunks))
                for chunk_trades in results:
                    if chunk_trades:
                        all_trades.extend(chunk_trades)
        else:
            trades_df, summary, pivot = run_algotest_backtest(payload)
            all_trades = trades_df.to_dict('records') if trades_df is not None and not trades_df.empty else []

        # Re-compute summary and pivot from the collected trades
        # so the frontend receives full analytics, not just raw trades.
        result_summary = {}
        result_pivot = {"headers": [], "rows": []}
        if all_trades:
            try:
                import pandas as pd
                from base import compute_analytics, build_pivot
                trades_df = pd.DataFrame(all_trades)
                # Restore datetime columns for analytics
                for col in ['Entry Date', 'Exit Date']:
                    if col in trades_df.columns:
                        trades_df[col] = pd.to_datetime(
                            trades_df[col], dayfirst=True, errors='coerce'
                        )
                trades_df, result_summary = compute_analytics(trades_df)
                result_pivot = build_pivot(trades_df, "Future Expiry")
                all_trades = _convert_numpy(_format_dates(trades_df.to_dict('records')))
            except Exception:
                pass
        else:
            all_trades = _convert_numpy(_format_dates(all_trades))

        result_payload = {
            'status': 'success',
            'trades': all_trades,
            'summary': _convert_numpy(result_summary),
            'pivot': _convert_numpy(result_pivot),
            'cached': False,
        }

        if use_cache and redis_cache and cache_key:
            redis_cache.set(cache_key, result_payload)

        return result_payload
    except OperationalError:
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
