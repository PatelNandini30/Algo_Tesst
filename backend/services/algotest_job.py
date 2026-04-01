"""Shared helper for running AlgoTest backtests with caching/logging."""
import traceback
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict

import numpy as np
import orjson
import pandas as pd
from sqlalchemy.exc import OperationalError

from engines.generic_algotest_engine import run_algotest_backtest, get_expiry_dates
from base import bulk_load_options
from database import reset_engine
from services.backtest_cache import get_backtest_cache


# Maximum years to load at once; keeps chunked bulk loads under ~1.2GB.
_BULK_LOAD_CHUNK_YEARS = int(os.environ.get("BULK_LOAD_CHUNK_YEARS", "3"))


def _date_chunks(from_date: str, to_date: str, chunk_years: int):
    """
    Split a date range into chunks of at most chunk_years years.
    Yields (chunk_from, chunk_to) string pairs.
    """
    import pandas as _pd

    start = _pd.to_datetime(from_date)
    end = _pd.to_datetime(to_date)
    current = start
    while current <= end:
        chunk_end = min(end, current + _pd.DateOffset(years=chunk_years) - _pd.Timedelta(days=1))
        yield current.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')
        current = chunk_end + _pd.Timedelta(days=1)


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


def _reindex_trades(trades: list):
    """
    Reassign unique trade/index numbers so chunked results don't reuse 'Trade' values.
    """
    trade_counter = 0
    for row in trades:
        leg_val = row.get('Leg') or row.get('leg')
        try:
            leg_num = int(leg_val)
        except (TypeError, ValueError):
            leg_num = None

        # Start a new trade when we see leg #1 (or when leg info is missing)
        if leg_num == 1 or leg_num is None:
            trade_counter += 1

        row['Trade'] = trade_counter
        row['Index'] = trade_counter

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
    use_cache = False  # DEBUG: Disabled cache to trace issues
    cache_key = None

    try:
        redis_cache = get_backtest_cache()
        # Cache disabled for debugging
        if False and redis_cache.is_available():
            use_cache = True
            cache_key = redis_cache.generate_key(symbol=index, from_date=from_date, to_date=to_date, strategy_config=payload)
            cached = redis_cache.get(cache_key)
            if cached:
                sanitized = {k: v for k, v in cached.items() if k != 'trades_df'}
                return {**sanitized, 'cached': True}
    except Exception:
        use_cache = False

    try:
        # If STR filter is enabled, shrink the load range to only the dates covered
        # by active segments — avoids loading the full 18-year history when only
        # a portion is needed.
        effective_from = from_date
        effective_to = to_date
        super_trend_config = str(payload.get('super_trend_config', 'None'))
        if super_trend_config in ('5x1', '5x2'):
            try:
                from base import load_super_trend_dates, get_super_trend_segments
                load_super_trend_dates()
                segments = get_super_trend_segments(super_trend_config)
                if segments:
                    user_from = pd.to_datetime(from_date)
                    user_to = pd.to_datetime(to_date)
                    seg_dates = []
                    for seg in segments:
                        seg_start = pd.to_datetime(seg.get('start') or seg.get('Start'))
                        seg_end = pd.to_datetime(seg.get('end') or seg.get('End'))
                        if seg_end >= user_from and seg_start <= user_to:
                            seg_dates.append(seg_start)
                            seg_dates.append(seg_end)
                    if seg_dates:
                        effective_from = max(min(seg_dates), user_from).strftime('%Y-%m-%d')
                        effective_to = min(max(seg_dates), user_to).strftime('%Y-%m-%d')
                    print(f"[STR FILTER] Segments: {len(segments)}, Effective range: {effective_from} → {effective_to}")
            except Exception as e:
                print(f"[STR FILTER] Error: {e}")
                pass  # fall back to full range on any error
        
        # Update payload with effective date range for the engine
        payload['from_date'] = effective_from
        payload['to_date'] = effective_to

        n_workers = int(os.environ.get("BACKTEST_WORKERS", "1"))
        expiry_type = payload.get('expiry_type', 'WEEKLY')

        print(f"[DATE RANGE] User: {from_date} → {to_date}, Effective: {effective_from} → {effective_to}")
        expiry_df = get_expiry_dates(index, expiry_type.lower(), effective_from, effective_to)
        
        print(f"[DEBUG] expiry_df: {type(expiry_df)}, len={len(expiry_df) if expiry_df is not None else 'None'}")
        if expiry_df is not None and not expiry_df.empty:
            print(f"[DEBUG] First expiry: {expiry_df.iloc[0]['Current Expiry']}")
            print(f"[DEBUG] Last expiry: {expiry_df.iloc[-1]['Current Expiry']}")

        all_trades = []

        if n_workers > 1 and expiry_df is not None and not expiry_df.empty and len(expiry_df) >= n_workers * 2:
            bulk_load_options(index, effective_from, effective_to)
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
            engine_summary = None
            engine_pivot = None
            if engine_summary is None:
                engine_summary = {}
            if engine_pivot is None:
                engine_pivot = {"headers": [], "rows": []}
        else:
            from_dt = pd.to_datetime(effective_from)
            to_dt = pd.to_datetime(effective_to)
            span_years = (to_dt - from_dt).days / 365.25
            if span_years <= _BULK_LOAD_CHUNK_YEARS:
                bulk_load_options(index, effective_from, effective_to)
                print(f"[DEBUG] Calling run_algotest_backtest with from={effective_from}, to={effective_to}")
                trades_df, engine_summary, engine_pivot = run_algotest_backtest(payload)
                print(f"[DEBUG] Backtest returned: type={type(trades_df)}, len={len(trades_df) if trades_df is not None else 'None'}, empty={trades_df.empty if trades_df is not None else 'N/A'}")
                all_trades = trades_df.to_dict('records') if trades_df is not None and not trades_df.empty else []
                print(f"[DEBUG] Single chunk: trades_df={type(trades_df)}, len={len(trades_df) if trades_df is not None else 'None'}")
                all_trades = trades_df.to_dict('records') if trades_df is not None and not trades_df.empty else []
                print(f"[DEBUG] all_trades from single chunk: {len(all_trades)}")
                if engine_summary is None:
                    engine_summary = {}
                if engine_pivot is None:
                    engine_pivot = {"headers": [], "rows": []}
            else:
                all_chunk_trades = []
                engine_summary = None
                engine_pivot = None
                for chunk_from, chunk_to in _date_chunks(effective_from, effective_to, _BULK_LOAD_CHUNK_YEARS):
                    try:
                        bulk_load_options(index, chunk_from, chunk_to)
                        chunk_payload = dict(payload)
                        chunk_payload['from_date'] = chunk_from
                        chunk_payload['to_date'] = chunk_to
                        c_df, c_summary, c_pivot = run_algotest_backtest(chunk_payload)
                        chunk_count = len(c_df) if c_df is not None and not c_df.empty else 0
                        print(f"[DEBUG] chunk {chunk_from}→{chunk_to}: c_df type={type(c_df)}, count={chunk_count}")
                        if c_df is not None and not c_df.empty:
                            print(f"[DEBUG] c_df columns: {list(c_df.columns)[:5]}")
                            print(f"[DEBUG] c_df first row: {c_df.iloc[0].to_dict() if len(c_df) > 0 else 'empty'}")
                        if chunk_count > 0:
                            all_chunk_trades.extend(c_df.to_dict('records'))
                            if c_summary:
                                engine_summary = c_summary
                            if c_pivot:
                                engine_pivot = c_pivot
                        expiry_type_used = chunk_payload.get('expiry_type', 'WEEKLY')
                        print(f"[CHUNK] {chunk_from} → {chunk_to}: {chunk_count} trades (expiry_type={expiry_type_used})")
                    except Exception as chunk_err:
                        print(f"[CHUNK ERROR] {chunk_from} → {chunk_to}: {chunk_err}")
                        traceback.print_exc()
                        continue
                all_trades = all_chunk_trades
                print(f"[DEBUG] Total all_chunk_trades collected: {len(all_chunk_trades)}")
                if not all_trades:
                    engine_summary = None
                    engine_pivot = None

        # Reindex trades so multi-chunk runs produce unique trade numbers
        if all_trades:
            _reindex_trades(all_trades)

        # Re-compute summary and pivot from the collected trades
        # so the frontend receives full analytics, not just raw trades.
        result_summary = {}
        result_pivot = {"headers": [], "rows": []}
        
        # Always recompute analytics from combined trades when we have multiple chunks
        # (engine_summary only reflects the last chunk's trades)
        if all_trades and len(all_trades) > 0:
            try:
                from base import compute_analytics, build_pivot
                trades_df = pd.DataFrame(all_trades)

                # Restore datetime columns before aggregation
                for col in ['Entry Date', 'Exit Date']:
                    if col in trades_df.columns:
                        trades_df[col] = pd.to_datetime(
                            trades_df[col], dayfirst=True, errors='coerce'
                        )

                # Skip aggregation - trades are already properly indexed
                # The _reindex_trades call above ensures unique Trade IDs
                trades_aggregated = trades_df

                if 'Net P&L' in trades_aggregated.columns:
                    print(f"[DEBUG] Net P&L sample: "
                          f"{trades_aggregated['Net P&L'].head().tolist()}")

                trades_aggregated, result_summary = compute_analytics(trades_aggregated)
                print(f"[DEBUG] Result summary: {result_summary}")
                result_pivot = build_pivot(trades_aggregated, "Exit Date")

                all_trades = _convert_numpy(
                    _format_dates(trades_df.to_dict('records'))
                )

            except Exception as e:
                print(f"[ERROR] compute_analytics failed: {e}")
                traceback.print_exc()
                result_summary = {}
                try:
                    trades_df = pd.DataFrame(all_trades)
                    if ('Trade' in trades_df.columns and
                            trades_df['Trade'].nunique() < len(trades_df)):
                        fallback_df = trades_df.groupby(
                            'Trade', sort=False
                        ).agg({'Net P&L': 'sum'}).reset_index()
                    else:
                        fallback_df = trades_df

                    pnl_col = ('Net P&L' if 'Net P&L' in fallback_df.columns
                               else 'net_pnl')
                    if pnl_col in fallback_df.columns:
                        pnl_vals = pd.to_numeric(
                            fallback_df[pnl_col], errors='coerce'
                        ).fillna(0)
                        wins = pnl_vals[pnl_vals > 0]
                        losses = pnl_vals[pnl_vals < 0]
                        win_count = len(wins)
                        loss_count = len(losses)
                        count = len(pnl_vals)
                        
                        avg_win = round(wins.mean(), 2) if win_count > 0 else 0
                        avg_loss = round(losses.mean(), 2) if loss_count > 0 else 0
                        expectancy = (avg_win * win_count / count - avg_loss * loss_count / count) if count > 0 else 0
                        reward_to_risk = abs(avg_win / avg_loss) if avg_loss != 0 else 0
                        
                        cumulative = pnl_vals.cumsum()
                        peak = cumulative.cummax()
                        dd = np.where(peak > cumulative, cumulative - peak, 0)
                        max_dd_pts = round(abs(dd.min()), 2) if len(dd) > 0 else 0
                        
                        initial_capital = 100000.0
                        final_capital = initial_capital + pnl_vals.sum()
                        if len(fallback_df) > 1 and 'Entry Date' in fallback_df.columns:
                            dates = pd.to_datetime(fallback_df['Entry Date'], dayfirst=True, errors='coerce')
                            if dates.notna().any():
                                n_years = (dates.max() - dates.min()).days / 365.25
                                n_years = max(n_years, 0.01)
                                cagr_options = round(100 * ((final_capital / initial_capital) ** (1 / n_years) - 1), 2) if initial_capital > 0 else 0
                            else:
                                cagr_options = 0
                        else:
                            cagr_options = 0
                        
                        car_mdd = round(cagr_options / abs(max_dd_pts) * 100, 2) if max_dd_pts > 0 else 0
                        
                        result_summary = {
                            'total_pnl': round(pnl_vals.sum(), 2),
                            'count': count,
                            'win_pct': round(100 * win_count / count, 2) if count > 0 else 0,
                            'loss_pct': round(100 * loss_count / count, 2) if count > 0 else 0,
                            'avg_win': avg_win,
                            'avg_loss': avg_loss,
                            'max_win': round(wins.max(), 2) if win_count > 0 else 0,
                            'max_loss': round(losses.min(), 2) if loss_count > 0 else 0,
                            'avg_profit_per_trade': round(pnl_vals.mean(), 2),
                            'expectancy': round(expectancy, 2),
                            'reward_to_risk': round(reward_to_risk, 2),
                            'cagr_options': cagr_options,
                            'max_dd_pts': max_dd_pts,
                            'car_mdd': car_mdd,
                            'recovery_factor': round(pnl_vals.sum() / max_dd_pts, 2) if max_dd_pts > 0 else 0,
                            'max_win_streak': 0,
                            'max_loss_streak': 0,
                            'mdd_duration_days': 0,
                            'mdd_start_date': '',
                            'mdd_end_date': '',
                            'mdd_trade_number': None,
                            'cagr_spot': 0,
                            'spot_change': 0,
                            'profit_factor': round(wins.sum() / abs(losses.sum()), 2) if losses.sum() != 0 else 0,
                        }
                        print(f"[DEBUG] Fallback summary: {result_summary}")
                except Exception as fallback_error:
                    print(f"[ERROR] Fallback summary failed: {fallback_error}")
        else:
            all_trades = _convert_numpy(_format_dates(all_trades))

        import json

        def _make_json_safe(obj):
            """Convert result to JSON-safe structure using orjson (handles numpy natively)."""
            try:
                import pandas as _pd
                import numpy as np

                if isinstance(obj, _pd.DataFrame):
                    obj = obj.to_dict('records')
                elif isinstance(obj, _pd.Series):
                    obj = obj.to_dict()
                elif isinstance(obj, dict):
                    new_obj = {}
                    for k, v in obj.items():
                        if isinstance(v, (np.integer, np.floating)):
                            new_obj[k] = float(v) if isinstance(v, np.floating) else int(v)
                        elif isinstance(v, dict):
                            new_obj[k] = _make_json_safe(v)
                        else:
                            new_obj[k] = v
                    return new_obj

                # orjson serialises numpy int/float/bool, datetime, UUID natively
                # Round-trip through JSON to get a plain Python structure
                return orjson.loads(orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS))
            except Exception:
                # Fallback: convert to string representation
                return str(obj)

        print(f"[DEBUG] Before JSON safe: result_summary={result_summary}")
        print(f"[DEBUG] result_summary types: {[(k, type(v)) for k, v in result_summary.items()]}")
        
        result_payload = {
            'status': 'success',
            'trades': _make_json_safe(all_trades),
            'summary': _make_json_safe(result_summary),
            'pivot': _make_json_safe(result_pivot),
            'cached': False,
        }
        
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"[SUMMARY_DEBUG] result_summary has {len(result_summary)} keys: {list(result_summary.keys())}")
        logger.warning(f"[SUMMARY_DEBUG] total_pnl value: {result_summary.get('total_pnl')}")
        logger.warning(f"[SUMMARY_DEBUG] cagr_options value: {result_summary.get('cagr_options')}")
        
        print(f"[DEBUG] After JSON safe: payload.summary={result_payload.get('summary')}")

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
