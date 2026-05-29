"""Shared helper for running AlgoTest backtests with caching/logging."""
import logging
import traceback
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict

import numpy as np
import pandas as pd
from sqlalchemy.exc import OperationalError

from engines.generic_algotest_engine import run_algotest_backtest, get_expiry_dates
from base import bulk_load_options, bulk_clear_options
from database import reset_engine
from services.backtest_cache import get_backtest_cache
from services.index_metadata import normalize_index, validate_index_payload
# ── FAST_LOOKUP imports ──────────────────────────────────────────────────────
from services.fast_lookup import build_fast_lookup, clear_fast_lookup
from services.data_loader import get_bulk_options_df, get_bulk_spot_df
# ────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# Maximum years to load at once before chunking a single-threaded run.
# Keep this bounded so long backtests do not accumulate a huge working set.
_BULK_LOAD_CHUNK_YEARS = int(os.environ.get("BULK_LOAD_CHUNK_YEARS", "3"))
_FAST_LOOKUP_MIN_YEARS = float(os.environ.get("FAST_LOOKUP_MIN_YEARS", "2.0"))
_FAST_LOOKUP_MODE = os.environ.get("FAST_LOOKUP_MODE", "auto").strip().lower()


def _get_backtest_worker_count() -> int:
    """
    Return the number of process workers for one backtest.

    Process-level parallelism duplicates the option/spot working set in each
    child process.  That can make long backtests slower on typical desktops and
    can starve the UI, so keep it opt-in.
    """
    return max(1, int(os.environ.get("BACKTEST_WORKERS", "1")))


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
    payload['index'] = normalize_index(payload.get('index', 'NIFTY'))
    payload['from_date'] = _normalize_cache_date(payload.get('date_from') or payload.get('from_date'))
    payload['to_date'] = _normalize_cache_date(payload.get('date_to') or payload.get('to_date'))
    payload['date_from'] = payload['from_date']
    payload['date_to'] = payload['to_date']
    return payload


def _resolve_effective_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve the effective date window for a request without changing trade logic.
    STR filters can narrow the range; this helper centralizes that so cache keys
    and queued jobs use the same request shape.
    """
    resolved = dict(payload or {})
    from_date = resolved.get('from_date')
    to_date = resolved.get('to_date')

    effective_from = from_date
    effective_to = to_date
    super_trend_config = str(resolved.get('super_trend_config', 'None'))
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
                logger.info("[STR FILTER] Segments=%s Effective range=%s -> %s", len(segments), effective_from, effective_to)
        except Exception as exc:
            logger.warning("[STR FILTER] Error: %s", exc)

    resolved['from_date'] = effective_from
    resolved['to_date'] = effective_to
    resolved['_effective_from'] = effective_from
    resolved['_effective_to'] = effective_to
    return resolved


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
                    trade[key] = value.strftime('%d-%m-%Y')
                elif isinstance(value, str) and 'T' in value:
                    try:
                        trade[key] = pd.to_datetime(value).strftime('%d-%m-%Y')
                    except Exception:
                        pass
        return trades
    except Exception:
        return trades


def _reindex_trades(trades: list):
    """
    Reassign unique trade/index numbers so chunked results don't reuse 'Trade' values.

    Uses the engine-assigned Trade ID (set before this function runs) to detect
    trade boundaries, then maps them to sequential integers 1, 2, 3...

    The old approach (increment on Leg==1) breaks for re-entry trades that only
    re-enter a single non-first leg (e.g. a FUT roll with Leg=2 and no Leg=1),
    causing those rows to be merged under the previous trade's number.
    """
    if not trades:
        return trades

    # Sort by original engine Trade ID then Leg so legs are in predictable order.
    try:
        trades.sort(key=lambda r: (
            int(str(r.get('Trade', 0) or 0)),
            int(str(r.get('Leg',  0) or 0)),
        ))
    except Exception:
        pass  # if sorting fails keep original order

    trade_counter = 0
    prev_orig_id = None

    for row in trades:
        try:
            orig_id = int(str(row.get('Trade', 0) or 0))
        except (TypeError, ValueError):
            orig_id = None

        # New sequential trade whenever the engine-assigned ID changes
        if orig_id != prev_orig_id:
            trade_counter += 1
            prev_orig_id = orig_id

        row['Trade'] = trade_counter
        row['Index'] = trade_counter

    return trades


def _normalize_cache_date(value: Any) -> str:
    if not value:
        return ""
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return pd.to_datetime(text, format=fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    try:
        return pd.to_datetime(text, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return text


def _filter_df_date_range(df, from_date: str, to_date: str):
    if df is None or df.is_empty() or not from_date or not to_date:
        return df
    try:
        from services.data_loader import pl
        from_value = pd.to_datetime(from_date, dayfirst=True).date()
        to_value = pd.to_datetime(to_date, dayfirst=True).date()
        if "Date" not in df.columns:
            return df
        return df.filter(
            (pl.col("Date").cast(pl.Date) >= from_value) &
            (pl.col("Date").cast(pl.Date) <= to_value)
        )
    except Exception as exc:
        logger.warning("[FAST_LOOKUP] Date-range filter skipped: %s", exc)
        return df


def _strategy_needs_native_scan(payload: Dict[str, Any]) -> bool:
    if payload.get("spot_adjustment_enabled"):
        return True
    if payload.get("overall_sl_value") is not None or payload.get("overall_target_value") is not None:
        return True
    for leg in payload.get("legs", []) or []:
        if not isinstance(leg, dict):
            continue
        for key in ("targetProfit", "stopLoss", "trailSL", "reEntryOnSL", "reEntryOnTarget", "simpleMomentum"):
            if leg.get(key):
                return True
    return False


def _should_build_fast_lookup(payload: Dict[str, Any], from_date: str, to_date: str) -> bool:
    if _FAST_LOOKUP_MODE in ("0", "off", "false", "no"):
        return False
    if _FAST_LOOKUP_MODE in ("1", "on", "true", "yes", "always"):
        return True
    if _strategy_needs_native_scan(payload):
        return True
    try:
        span_years = (pd.to_datetime(to_date) - pd.to_datetime(from_date)).days / 365.25
        return span_years >= _FAST_LOOKUP_MIN_YEARS
    except Exception:
        return False


def _build_fast_lookup_from_bulk(index: str = None, from_date: str = None, to_date: str = None) -> None:
    """Build the O(1) lookup dict from whatever is in the bulk DFs right now."""
    try:
        options_df = get_bulk_options_df()
        spot_df = get_bulk_spot_df()
        cache_key = None
        if index and from_date and to_date and os.environ.get("FAST_LOOKUP_RANGE_FILTER", "0").strip().lower() in ("1", "true", "yes", "on"):
            normalized_from = _normalize_cache_date(from_date)
            normalized_to = _normalize_cache_date(to_date)
            options_df = _filter_df_date_range(options_df, normalized_from, normalized_to)
            spot_df = _filter_df_date_range(spot_df, normalized_from, normalized_to)
            cache_key = f"bulk:{str(index).upper()}:{normalized_from}:{normalized_to}"
        build_fast_lookup(options_df, spot_df, cache_key_override=cache_key)
        from base_fast_patch import apply_fast_lookup_patches
        apply_fast_lookup_patches()
    except Exception as exc:
        logger.warning("[FAST_LOOKUP] Build failed (non-fatal): %s", exc)


def _try_rust_engine(payload, index, effective_from, effective_to):
    """
    Slice 11 — opt-in Rust orchestrator path.

    Returns (trades_df, summary, pivot) or (None, None, None) when the Rust
    path can't handle the payload (caller falls back to the Python engine).

    Activation: set ENGINE_BACKEND=rust. Default unset / 'python' uses the
    Python engine.

    Status: Rust engine is fully parity-shipped with Python for the supported
    strategy surface (verified by tests/test_engine_rust_pipeline.py).  Supported:
    all strike modes, per-leg SL/Target/TrailSL, SL-with-Buffer, overall SL/Target,
    re-entry (RE_ASAP, RE_ASAP_REV, LAZY_LEG, RE_MOMENTUM, RE_MOMENTUM_REV),
    spot adjustment, buffer strike, STR/filter gating in all filter_entry_modes
    ('dte'/'fixed'/'min_days'), rollover + no_rollover, NEXT_WEEKLY/NEXT_MONTHLY,
    FUTURES (incl. SL/Target/TrailSL/re-entry), and FUTURES + NEXT_WEEKLY mixed.

    Only edge cases that still return None and fall back to Python: data-missing
    runtime failures (strike unresolvable, spot data missing for re-anchor),
    custom strike_selection.type values not in the supported set, and re-entry
    modes outside the 5 supported modes above.
    """
    from base import compute_analytics, build_pivot, get_spot_price_from_db, get_trading_calendar
    from engines.generic_algotest_engine import get_lot_size
    from services.engine_rust import run_rust_engine_pipeline, priced_to_tradesheet_records

    # When filter segments extend beyond effective_to, load data through the
    # latest segment end so the last rollover window can be priced correctly.
    # Without this, trading_days / expiries / spots stop at effective_to and
    # the rollover clipping has no valid last-trading-day to clamp to.
    _data_to = effective_to
    try:
        _custom_segs = payload.get("filter_segments") or []
        if _custom_segs and isinstance(_custom_segs, list):
            _seg_ends = [
                pd.Timestamp(s["end"]).strftime("%Y-%m-%d")
                for s in _custom_segs
                if isinstance(s, dict) and s.get("end")
            ]
            if _seg_ends:
                _max_seg_end = max(_seg_ends)
                if _max_seg_end > _data_to:
                    _data_to = _max_seg_end
    except Exception:
        pass

    _trading_cal_df = get_trading_calendar(effective_from, _data_to)
    days = pd.to_datetime(
        _trading_cal_df["date"]
    ).sort_values().dt.strftime("%Y-%m-%d").tolist()
    if not days:
        return (None, None, None)

    expiries_df = get_expiry_dates(
        index,
        payload.get("expiry_type", "weekly"),
        effective_from,
        _data_to,
    )
    if expiries_df is None or expiries_df.empty:
        return (None, None, None)
    col = "Current Expiry" if "Current Expiry" in expiries_df.columns else expiries_df.columns[0]
    expiries = (
        pd.to_datetime(expiries_df[col])
        .sort_values()
        .dt.strftime("%Y-%m-%d")
        .unique()
        .tolist()
    )

    spots = {}
    for d in days:
        v = get_spot_price_from_db(d, index)
        if v is not None:
            spots[d] = float(v)
    if not spots:
        return (None, None, None)

    lot_size = get_lot_size(index, days[0])
    priced = run_rust_engine_pipeline(
        payload,
        expiry_dates=expiries,
        trading_days=days,
        lot_size=int(lot_size),
        spot_by_date=spots,
        square_off_mode=payload.get("square_off_mode", "partial"),
    )
    if priced is None:
        return (None, None, None)
    if not priced:
        return (pd.DataFrame(), {}, {"headers": [], "rows": []})

    records = priced_to_tradesheet_records(priced, payload, int(lot_size))

    # Compute MAE/MFE per leg using the same DB/feather range query the Python engine uses.
    if os.environ.get("BACKTEST_INCLUDE_MAE_MFE", "1").strip().lower() not in ("0", "false", "no", "off"):
        try:
            from engines.generic_algotest_engine import _calculate_leg_mae_mfe
            for rec in records:
                opt_type = rec.get("Type", "")
                if opt_type not in ("CE", "PE"):
                    continue
                leg = {
                    'option_type': opt_type,
                    'strike': rec.get("Strike"),
                    'expiry': rec.get("Expiry"),
                }
                mae_val, mfe_val = _calculate_leg_mae_mfe(
                    index=str(index).upper(),
                    entry_date=rec.get("Entry Date"),
                    exit_date=rec.get("Exit Date"),
                    leg=leg,
                    entry_price=rec.get("Entry Price"),
                    position=rec.get("B/S", "SELL"),
                    entry_spot=rec.get("Entry Spot"),
                    trading_calendar_df=_trading_cal_df,
                )
                if mae_val is not None:
                    rec["MAE"] = mae_val
                if mfe_val is not None:
                    rec["MFE"] = mfe_val
        except Exception as _mae_exc:
            logger.warning("[MAE/MFE] Rust path computation failed (non-fatal): %s", _mae_exc)

    trades_df = pd.DataFrame(records)
    for c in ("Entry Date", "Exit Date"):
        if c in trades_df.columns:
            trades_df[c] = pd.to_datetime(trades_df[c], errors="coerce")

    # Mirror engines/generic_algotest_engine.py:5256-5310 — aggregate per-leg
    # rows into per-trade rows BEFORE running compute_analytics. Per-leg
    # parent rows hold trade-total Net P&L (slice 6 convention); aggregating
    # would double-count. So we sum CE/PE/FUT P&L per leg (each correctly
    # per-leg) and recompute trade-level Net P&L = CE+PE+FUT.
    aggregated = trades_df.groupby("Trade", as_index=False).agg({
        "Entry Date": "first",
        "Exit Date": "first",
        "Entry Spot": "first",
        "Exit Spot": "first",
        "Spot P&L": "first",
        "CE P&L": "sum",
        "PE P&L": "sum",
        "FUT P&L": "sum",
        "Exit Reason": "first",
    })
    aggregated["Net P&L"] = aggregated["CE P&L"] + aggregated["PE P&L"] + aggregated["FUT P&L"]
    entry_spot_series = aggregated["Entry Spot"].replace(0, float("nan"))
    aggregated["% P&L"] = (aggregated["Net P&L"] / entry_spot_series * 100.0).round(2).fillna(0)

    # Pre-compute Cumulative/Peak/DD/%DD using the same compound formula the
    # Python engine uses (engines/generic_algotest_engine.py:4716). compute_analytics
    # detects this pre-built series via its `has_series_b` gate (initial value
    # in [90, 110]) and uses it instead of building its own additive series.
    # Without this, summary CAGR diverges because compute_analytics seeds
    # initial_capital from entry_spot (~21500) instead of the base-100 series.
    aggregated = aggregated.sort_values("Entry Date").reset_index(drop=True)
    cumulative = 100.0
    peak = 100.0
    cum_series = []
    peak_series = []
    dd_series = []
    pct_dd_series = []
    for _, r in aggregated.iterrows():
        es = float(r["Entry Spot"]) if r["Entry Spot"] else 0.0
        npl = float(r["Net P&L"]) if r["Net P&L"] else 0.0
        pct_precise = (npl / es * 100.0) if es != 0 else 0.0
        cumulative = cumulative * (1.0 + pct_precise / 100.0)
        peak = max(cumulative, peak)
        dd = cumulative - peak
        pct_dd = (dd / peak) if peak != 0 else 0.0
        cum_series.append(cumulative)
        peak_series.append(peak)
        dd_series.append(dd)
        pct_dd_series.append(pct_dd)
    aggregated["Cumulative"] = cum_series
    aggregated["Peak"] = peak_series
    aggregated["DD"] = dd_series
    aggregated["%DD"] = pct_dd_series

    # IMPORTANT — Python engine quirk match.
    # The Python engine returns Entry/Exit Date as DD-MM-YYYY strings, then
    # `compute_analytics` does `series.min()` / `series.max()` on them. With
    # string-typed columns those calls do LEXICOGRAPHIC compare, so a date like
    # "28-02-2024" compares greater than "27-03-2024". That makes Python's
    # n_years (used in CAGR/CAR-MDD) consistently slightly wrong — but it is
    # the engine's de-facto behavior and the user's "exact copy" requirement
    # means we must reproduce it. So we serialize dates back to DD-MM-YYYY
    # strings before handing to compute_analytics.
    _df_for_analytics = aggregated.copy()
    for c in ("Entry Date", "Exit Date"):
        if c in _df_for_analytics.columns:
            _df_for_analytics[c] = pd.to_datetime(_df_for_analytics[c]).dt.strftime("%d-%m-%Y")
    _df_for_analytics, summary = compute_analytics(_df_for_analytics)
    # Pull compute_analytics's added analytics columns back onto the canonical
    # Timestamp-typed aggregated dataframe so downstream rendering keeps proper
    # dtypes.
    for col in ("Cumulative", "Peak", "DD", "%DD"):
        if col in _df_for_analytics.columns:
            aggregated[col] = _df_for_analytics[col].values

    # Propagate per-trade Cumulative/Peak/DD/%DD onto the per-leg trades_df —
    # Python convention: parent row (lowest Leg index per Trade) holds the
    # trade-level value, other leg rows leave it None. Re-entry/lazy rows also
    # get None. See engines/generic_algotest_engine.py:5107 — `row_cumulative`
    # is only assigned for the parent leg.
    trade_to_cum = {
        str(r["Trade"]): {
            "Cumulative": r.get("Cumulative"),
            "Peak": r.get("Peak"),
            "DD": r.get("DD"),
            "%DD": r.get("%DD"),
            "Spot P&L": r.get("Spot P&L"),
        }
        for _, r in aggregated.iterrows()
    }
    parent_leg_seen = set()
    cum, peak, dd, pdd, spot_pl = [], [], [], [], []
    for _, row in trades_df.iterrows():
        tid = str(row["Trade"])
        if tid in parent_leg_seen:
            cum.append(None); peak.append(None); dd.append(None); pdd.append(None)
            spot_pl.append(None)
            continue
        parent_leg_seen.add(tid)
        v = trade_to_cum.get(tid, {})
        cum.append(v.get("Cumulative"))
        peak.append(v.get("Peak"))
        dd.append(v.get("DD"))
        pdd.append(v.get("%DD"))
        spot_pl.append(v.get("Spot P&L"))
    trades_df["Cumulative"] = cum
    trades_df["Peak"] = peak
    trades_df["DD"] = dd
    trades_df["%DD"] = pdd
    # Spot P&L is a trade-level quantity. Write only on the parent (first-leg)
    # row to match Net P&L convention; per-leg sums then give the trade total
    # without double-counting.
    trades_df["Spot P&L"] = spot_pl

    # Sort by Entry Date so cascade mini-trades (which have NEW higher
    # trade_ids appended at the end by engine_rust._sa_reentry_specs) appear
    # interleaved chronologically with the original trades.  Without this,
    # the cascade re-entries pile up at the bottom of the tradesheet and
    # users miss them when reading top-to-bottom.
    if "Entry Date" in trades_df.columns and not trades_df.empty:
        trades_df = trades_df.sort_values(
            by=["Entry Date", "Trade", "Leg"],
            kind="stable",
        ).reset_index(drop=True)

    pivot = build_pivot(aggregated, "Exit Date")
    return (trades_df, summary, pivot)


def _safe_clear_fast_lookup() -> None:
    try:
        clear_fast_lookup(clear_native=False)
    except Exception:
        pass


def _run_backtest_chunk(args: tuple) -> list:
    """Run backtest for a subset of expiry dates. Must be top-level for pickling."""
    params, chunk_dates = args
    from base import bulk_load_options, bulk_clear_options
    from engines.generic_algotest_engine import run_algotest_backtest
    
    index = params.get('index', 'NIFTY')
    from_date = params.get('from_date')
    to_date = params.get('to_date')
    chunk_from = min(chunk_dates) if chunk_dates else from_date
    chunk_to = max(chunk_dates) if chunk_dates else to_date
    
    try:
        bulk_load_options(index, chunk_from, chunk_to)
        if _should_build_fast_lookup(params, chunk_from, chunk_to):
            _build_fast_lookup_from_bulk(index, chunk_from, chunk_to)
        chunk_params = dict(params)
        chunk_params['from_date'] = chunk_from
        chunk_params['to_date'] = chunk_to
        chunk_params['_expiry_chunk'] = chunk_dates
        df, _, _ = run_algotest_backtest(chunk_params)
        return df.to_dict('records') if df is not None and not df.empty else []
    except Exception:
        return []
    finally:
        try:
            _safe_clear_fast_lookup()
        except Exception:
            pass
        try:
            bulk_clear_options()
        except Exception:
            pass


def execute_algotest_job(request: Dict[str, Any]) -> Dict[str, Any]:
    job_t0 = time.perf_counter()
    payload = _normalize_request(request)
    validate_index_payload(payload)
    logger.debug("[SERVICE] entry_dte=%s exit_dte=%s", payload.get('entry_dte'), payload.get('exit_dte'))
    payload = _resolve_effective_request(payload)
    index = payload['index']
    from_date = payload.get('from_date')
    to_date = payload.get('to_date')

    redis_cache = None
    use_cache = (
        os.environ.get("BACKTEST_CACHE_ENABLED", "1").strip().lower() not in ("0", "false", "no")
        and not payload.get("no_cache")
    )
    cache_key = None

    try:
        # Drop any stale bulk state left behind by a previous task in the same worker.
        _safe_clear_fast_lookup()
        bulk_clear_options()

        redis_cache = get_backtest_cache()
        if use_cache and redis_cache.is_available():
            use_cache = True
            cache_key = redis_cache.generate_key(symbol=index, from_date=from_date, to_date=to_date, strategy_config=payload)
            cached = redis_cache.get(cache_key)
            if cached:
                sanitized = {k: v for k, v in cached.items() if k != 'trades_df'}
                return {**sanitized, 'cached': True}
    except Exception:
        use_cache = False

    try:
        effective_from = payload.get('_effective_from', from_date)
        effective_to = payload.get('_effective_to', to_date)
        n_workers = _get_backtest_worker_count()
        expiry_type = payload.get('expiry_type', 'WEEKLY')

        logger.debug("[DATE RANGE] User=%s -> %s Effective=%s -> %s", request.get('from_date') or request.get('date_from'), request.get('to_date') or request.get('date_to'), effective_from, effective_to)
        expiry_df = get_expiry_dates(index, expiry_type.lower(), effective_from, effective_to)
        
        logger.debug("[DEBUG] expiry_df type=%s len=%s", type(expiry_df), len(expiry_df) if expiry_df is not None else 'None')
        if expiry_df is not None and not expiry_df.empty:
            logger.debug("[DEBUG] First expiry=%s", expiry_df.iloc[0]['Current Expiry'])
            logger.debug("[DEBUG] Last expiry=%s", expiry_df.iloc[-1]['Current Expiry'])

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
            try:
                bulk_clear_options()
            except Exception:
                pass
            engine_summary = None
            engine_pivot = None
            if engine_summary is None:
                engine_summary = {}
            if engine_pivot is None:
                engine_pivot = {"headers": [], "rows": []}
        else:
            # Unified Rust engine path — all durations, no Python fallback.
            # Feather covers 2019-2026 (~275 MB, already in memory) so no
            # chunking is needed regardless of date range.
            stage_t = time.perf_counter()
            bulk_load_options(index, effective_from, effective_to)
            logger.info("[JOB_PERF] bulk_load_options %.2fs", time.perf_counter() - stage_t)
            stage_t = time.perf_counter()
            _build_fast_lookup_from_bulk(index, effective_from, effective_to)
            logger.info("[JOB_PERF] fast_lookup %.2fs", time.perf_counter() - stage_t)
            stage_t = time.perf_counter()
            trades_df, engine_summary, engine_pivot = _try_rust_engine(
                payload, index, effective_from, effective_to
            )
            logger.info("[JOB_PERF] run_algotest_backtest %.2fs", time.perf_counter() - stage_t)
            all_trades = trades_df.to_dict('records') if trades_df is not None and not trades_df.empty else []
            if engine_summary is None:
                engine_summary = {}
            if engine_pivot is None:
                engine_pivot = {"headers": [], "rows": []}
            if all_trades:
                try:
                    stage_t = time.perf_counter()
                    from base import compute_analytics, build_pivot
                    trades_agg = pd.DataFrame(all_trades)
                    for col in ['Entry Date', 'Exit Date']:
                        if col in trades_agg.columns:
                            trades_agg[col] = pd.to_datetime(trades_agg[col], dayfirst=True, errors='coerce')
                    _cum_cols = ['Cumulative', 'Peak', 'DD', '%DD']
                    _saved_agg = {col: trades_agg[col].copy() for col in _cum_cols if col in trades_agg.columns}
                    trades_agg, result_summary = compute_analytics(trades_agg)
                    result_pivot = build_pivot(trades_agg, "Exit Date")
                    for col, saved in _saved_agg.items():
                        trades_agg[col] = saved
                    all_trades = _convert_numpy(_format_dates(trades_agg.to_dict('records')))
                    engine_pivot = result_pivot
                    logger.info("[JOB_PERF] first analytics %.2fs", time.perf_counter() - stage_t)
                except Exception as e:
                    logger.warning("[WARN] compute_analytics failed: %s", e)
            try:
                bulk_clear_options()
            except Exception:
                pass

        # Reindex trades so multi-chunk runs produce unique trade numbers
        if all_trades:
            stage_t = time.perf_counter()
            _reindex_trades(all_trades)
            logger.info("[JOB_PERF] reindex %.2fs", time.perf_counter() - stage_t)

        # Re-compute summary and pivot from the collected trades
        # so the frontend receives full analytics, not just raw trades.
        result_summary = {}
        result_pivot = {"headers": [], "rows": []}
        
        # Always recompute analytics from combined trades when we have multiple chunks
        # (engine_summary only reflects the last chunk's trades)
        if all_trades and len(all_trades) > 0:
            try:
                stage_t = time.perf_counter()
                from base import compute_analytics, build_pivot
                trades_df = pd.DataFrame(all_trades)

                # Restore datetime columns before aggregation
                for col in ['Entry Date', 'Exit Date']:
                    if col in trades_df.columns:
                        trades_df[col] = pd.to_datetime(
                            trades_df[col], dayfirst=True, errors='coerce'
                        )

                trades_aggregated = trades_df

                if 'Net P&L' in trades_aggregated.columns:
                    logger.debug("[DEBUG] Net P&L sample=%s", trades_aggregated['Net P&L'].head().tolist())

                # Pass trade-level (first-leg) data to compute_analytics and
                # build_pivot.  The Python engine writes Net P&L = TRADE_TOTAL on
                # the first-leg row and per-leg partial on Leg 2+.  Both functions
                # use Net P&L directly (compute_analytics sums by Trade groupby;
                # build_pivot cumsum-s the column) — feeding them per-leg data
                # double-counts: Leg1(total) + Leg2(partial) != correct trade P&L.
                # Extracting first-leg rows gives one row per trade with the correct
                # combined P&L.  For single-leg strategies every row is a first-leg
                # row so the mask is a no-op and behaviour is identical to before.
                if 'Trade' in trades_aggregated.columns:
                    _leg_sort_pre = ['Entry Date']
                    if 'Leg' in trades_aggregated.columns:
                        _leg_sort_pre.append('Leg')
                    trades_aggregated = trades_aggregated.sort_values(
                        _leg_sort_pre, kind='stable'
                    ).reset_index(drop=True)
                    _fm_pre = trades_aggregated.groupby('Trade', sort=False).cumcount() == 0
                    _analytics_df = trades_aggregated[_fm_pre].copy()
                else:
                    _analytics_df = trades_aggregated.copy()

                _, result_summary = compute_analytics(_analytics_df)
                logger.debug("[DEBUG] Result summary=%s", result_summary)
                result_pivot = build_pivot(_analytics_df, "Exit Date")

                # Recompute cumulative/peak/DD/%DD from scratch using first-leg rows.
                # Each chunk's engine call resets cumulative to 100, so we must
                # recompute globally across the combined dataset.
                # First-leg rows carry the total trade P&L (not per-leg partial),
                # so the compound formula gives the correct global series.
                # 'Spot P&L' is a trade-level quantity (same Entry/Exit Spot on
                # every leg of a trade) and is also kept first-leg-only here so
                # downstream column sums match the trade-level total.
                _cum_cols = ['Cumulative', 'Peak', 'DD', '%DD', 'Spot P&L']
                if ('Trade' in trades_aggregated.columns
                        and 'Net P&L' in trades_aggregated.columns
                        and 'Entry Spot' in trades_aggregated.columns
                        and 'Entry Date' in trades_aggregated.columns):
                    # Re-sort by (Entry Date, Leg) with stable sort so Leg 1 always
                    # precedes Leg 2+ within the same trade date. compute_analytics uses
                    # pandas quicksort (unstable), which can put Leg 2 before Leg 1 for
                    # same-date legs — causing cumcount()==0 to pick the wrong leg and
                    # leaving Leg 1 with no cumulative (blank in the tradesheet).
                    _leg_sort_cols = ['Entry Date']
                    if 'Leg' in trades_aggregated.columns:
                        _leg_sort_cols.append('Leg')
                    trades_aggregated = trades_aggregated.sort_values(
                        _leg_sort_cols, kind='stable'
                    ).reset_index(drop=True)

                    _first_mask = trades_aggregated.groupby('Trade', sort=False).cumcount() == 0
                    _leg1 = trades_aggregated[_first_mask][
                        ['Trade', 'Net P&L', 'Entry Spot', 'Entry Date']
                    ].copy().sort_values('Entry Date').reset_index(drop=True)

                    _pnl_s  = pd.to_numeric(_leg1['Net P&L'],   errors='coerce').fillna(0.0)
                    _spot_s = pd.to_numeric(_leg1['Entry Spot'], errors='coerce').replace(0.0, np.nan)
                    _pct_s  = (_pnl_s / _spot_s * 100.0).fillna(0.0)

                    _cum, _pk = 100.0, 100.0
                    _cum_map, _pk_map, _dd_map, _pct_dd_map = {}, {}, {}, {}
                    for _i, (_, _r) in enumerate(_leg1.iterrows()):
                        _p   = float(_pct_s.iloc[_i])
                        _cum = _cum * (1.0 + _p / 100.0)
                        _pk  = max(_pk, _cum)
                        _dd  = _cum - _pk
                        _pd  = (_dd / _pk * 100.0) if _pk != 0.0 else 0.0
                        _tid = _r['Trade']
                        _cum_map[_tid] = round(_cum, 6)
                        _pk_map[_tid]  = round(_pk,  6)
                        _dd_map[_tid]  = round(_dd,  6)
                        _pct_dd_map[_tid] = round(_pd, 6)

                    trades_aggregated['Cumulative'] = trades_aggregated['Trade'].map(_cum_map)
                    trades_aggregated['Peak']       = trades_aggregated['Trade'].map(_pk_map)
                    trades_aggregated['DD']         = trades_aggregated['Trade'].map(_dd_map)
                    trades_aggregated['%DD']        = trades_aggregated['Trade'].map(_pct_dd_map)

                    # Clear cumulative from non-first-leg rows (matches engine output style)
                    _non_first = trades_aggregated.groupby('Trade', sort=False).cumcount() != 0
                    trades_aggregated.loc[_non_first, _cum_cols] = None

                all_trades = _convert_numpy(
                    _format_dates(trades_aggregated.to_dict('records'))
                )
                logger.info("[JOB_PERF] final analytics %.2fs", time.perf_counter() - stage_t)

            except Exception as e:
                logger.error("[ERROR] compute_analytics failed: %s", e)
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
                        logger.debug("[DEBUG] Fallback summary=%s", result_summary)
                except Exception as fallback_error:
                    logger.error("[ERROR] Fallback summary failed: %s", fallback_error)
        else:
            all_trades = _convert_numpy(_format_dates(all_trades))

        # Ensure missing Cumulative/Peak/DD/%DD on Leg 2+ rows are explicit None (→ JSON null).
        # float NaN from pandas survives _convert_numpy and causes parseFloat(NaN) in JS,
        # which the ?? 100.0 fallback cannot catch (NaN is not null/undefined).
        for row in all_trades:
            for k in ('Cumulative', 'Peak', 'DD', '%DD'):
                v = row.get(k)
                if v is not None:
                    try:
                        f = float(v)
                        if f != f:  # NaN check: NaN is the only float where f != f
                            row[k] = None
                    except (TypeError, ValueError):
                        row[k] = None

        import json

        def _make_json_safe(obj):
            """Convert result to JSON-safe structure using orjson (handles numpy natively)."""
            try:
                import pandas as _pd
                import numpy as np
                import orjson

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

        logger.debug("[DEBUG] Before JSON safe result_summary=%s", result_summary)
        logger.debug("[DEBUG] result_summary types=%s", [(k, type(v)) for k, v in result_summary.items()])
        
        stage_t = time.perf_counter()
        result_payload = {
            'status': 'success',
            'trades': _make_json_safe(all_trades),
            'summary': _make_json_safe(result_summary),
            'pivot': _make_json_safe(result_pivot),
            'meta': _make_json_safe({
                'slippage_pct': payload.get('slippage_pct', 0),
                'index': payload.get('index', 'NIFTY'),
                'from_date': payload.get('from_date'),
                'to_date': payload.get('to_date'),
                'buffer_strike_enabled': payload.get('buffer_strike_enabled', False),
                'buffer_strike_value': payload.get('buffer_strike_value', 0.5),
                'buffer_strike_unit': payload.get('buffer_strike_unit', 'percent'),
                'buffer_strike_apply_to': payload.get('buffer_strike_apply_to', 'both'),
                'buffer_position_above': payload.get('buffer_position_above', True),
                'buffer_position_below': payload.get('buffer_position_below', True),
                'spot_adjustment_enabled': bool(payload.get('spot_adjustment_enabled', False)),
                'date_range': f"{effective_from} to {effective_to}",
            }),
            'cached': False,
        }
        logger.info("[JOB_PERF] json_safe_payload %.2fs", time.perf_counter() - stage_t)
        
        logger.debug("[DEBUG] After JSON safe payload.summary=%s", result_payload.get('summary'))

        if use_cache and redis_cache and cache_key:
            stage_t = time.perf_counter()
            redis_cache.set(cache_key, result_payload)
            logger.info("[JOB_PERF] redis_set %.2fs", time.perf_counter() - stage_t)

        try:
            _safe_clear_fast_lookup()
        except Exception:
            pass
        try:
            bulk_clear_options()
        except Exception:
            pass
        logger.info("[JOB_PERF] total %.2fs", time.perf_counter() - job_t0)
        return result_payload
    except OperationalError:
        traceback.print_exc()
        reset_engine()
        try:
            _safe_clear_fast_lookup()
        except Exception:
            pass
        try:
            bulk_clear_options()
        except Exception:
            pass
        return {
            'status': 'error',
            'message': 'PostgreSQL connection dropped while running the backtest.'
        }
    except Exception as err:
        traceback.print_exc()
        try:
            _safe_clear_fast_lookup()
        except Exception:
            pass
        try:
            bulk_clear_options()
        except Exception:
            pass
        return {
            'status': 'error',
            'message': str(err)
        }
