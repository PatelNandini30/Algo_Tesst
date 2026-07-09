from fastapi import APIRouter, HTTPException, Response, Header, UploadFile, File, Request
from typing import Dict, Any, List, Optional, Tuple
# Import generic multi-leg engine
# NOTE: keep FastAPI imports at top for readability
from engines.generic_algotest_engine import run_algotest_backtest, _apply_slippage, _calculate_fo_charges
from services.algotest_job import execute_algotest_job, _normalize_request, _resolve_effective_request
from services.backtest_cache import get_backtest_cache as _get_result_cache
from services.index_metadata import validate_index_payload
from worker.tasks import run_algotest_job, warm_backtest_cache_task
from worker.celery import celery_app
import sys
import os
import threading
import pandas as pd
import numpy as np
import traceback
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import asyncio
import logging
from datetime import datetime
from uuid import uuid4

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


_db_max_date_cache: dict = {}  # symbol -> ISO date string


def _get_db_max_date(symbol: str) -> str | None:
    """Return the latest date present in option_data for the given symbol.

    Result cached per symbol per process to avoid hitting Postgres on every
    request. Falls back to None if the lookup fails — callers should treat
    that as "no clamp".
    """
    if not symbol:
        return None
    key = str(symbol).upper()
    if key in _db_max_date_cache:
        return _db_max_date_cache[key]
    try:
        from sqlalchemy import text
        from database import engine
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT MAX(date)::text FROM option_data WHERE symbol = :s"),
                {"s": key},
            ).first()
        max_date = row[0] if row and row[0] else None
        _db_max_date_cache[key] = max_date
        if max_date:
            logger.info("[NORMALIZE] DB max date for %s cached as %s", key, max_date)
        return max_date
    except Exception as exc:
        logger.warning("[NORMALIZE] DB max-date lookup failed for %s: %s", key, exc)
        return None


def _normalize_payload_dates(payload: dict) -> dict:
    normalized = dict(payload or {})
    from_date = _normalize_date(normalized.get("date_from") or normalized.get("from_date"))
    to_date = _normalize_date(normalized.get("date_to") or normalized.get("to_date"))
    if from_date:
        normalized["from_date"] = from_date
        normalized["date_from"] = from_date
    if to_date:
        # Clamp to_date to the actual DB max for the symbol so requests past the
        # data ceiling don't trigger a slow FULL DB LOAD. Cache is per-symbol per-process.
        symbol = normalized.get("index") or normalized.get("symbol") or "NIFTY"
        db_max = _get_db_max_date(symbol)
        if db_max and to_date > db_max:
            logger.info(
                "[NORMALIZE] clamping to_date %s → %s (DB ceiling for %s)",
                to_date, db_max, symbol,
            )
            to_date = db_max
        normalized["to_date"] = to_date
        normalized["date_to"] = to_date
    return normalized

# Thread pool for async tasks (I/O/cache warming) and process pool for CPU-heavy backtests
_backtest_executor = ThreadPoolExecutor(max_workers=3)
_BACKTEST_PROCESS_WORKERS = min(4, max(1, os.cpu_count() or 1))
_backtest_process_executor = ProcessPoolExecutor(max_workers=_BACKTEST_PROCESS_WORKERS)

# Add the parent directory to the path to import engines
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _validate_lazy_leg_no_cycles(leg_config: dict, depth: int = 0) -> None:
    """Guard lazy-leg chaining depth before dispatching a backtest."""
    if depth > 3:
        raise ValueError("Lazy leg chaining depth exceeds maximum of 3.")
    for reentry_key in ('reEntryOnSL', 'reEntryOnTarget'):
        reentry = leg_config.get(reentry_key) or {}
        if not isinstance(reentry, dict):
            continue
        if str(reentry.get('mode', '') or '').upper().replace(' ', '_') != 'LAZY_LEG':
            continue
        child = reentry.get('lazyLegConfig')
        if child:
            _validate_lazy_leg_no_cycles(child, depth + 1)


def _validate_lazy_legs_payload(payload: dict) -> None:
    """Validate all lazy-leg configs in a submitted AlgoTest payload."""
    for leg in payload.get('legs', []) or []:
        if isinstance(leg, dict):
            _validate_lazy_leg_no_cycles(leg)

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


def _backtest_queue_depth() -> int:
    try:
        import redis
        client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        return int(client.llen("backtests") or 0)
    except Exception:
        return 0


def _queue_depth(queue_name: str) -> int:
    try:
        import redis
        client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        return int(client.llen(queue_name) or 0)
    except Exception:
        return 0


def _real_backtest_active() -> bool:
    """True if an actual run_algotest_job (a user's 'Run Backtest' click) is
    currently executing on either backtest worker — NOT a warm_backtest_cache_task.
    Both backtest workers run at concurrency=1, so this is a reliable "is the
    single slot taken by real work" check. Fails open (False) on any inspect
    error so a broker hiccup never silently blocks warming."""
    try:
        active = celery_app.control.inspect(timeout=2).active() or {}
        for _host, tasks in active.items():
            for t in tasks:
                if t.get("name") == "worker.tasks.run_algotest_job":
                    return True
        return False
    except Exception:
        return False


def _date_span_days(from_date: Any, to_date: Any) -> int:
    try:
        start = pd.to_datetime(_normalize_date(from_date))
        end = pd.to_datetime(_normalize_date(to_date))
        return max(0, int((end - start).days))
    except Exception:
        return 999999


def _backtest_queue_for_payload(payload: dict) -> str:
    max_fast_days = int(os.environ.get("BACKTEST_FAST_QUEUE_MAX_DAYS", "550"))
    span_days = _date_span_days(payload.get("from_date"), payload.get("to_date"))
    return "backtests_fast" if span_days <= max_fast_days else "backtests"


def _normalize_recalc_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == '':
            return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(numeric):
        return None
    return numeric


def _recalculate_trade_prices(
    trades: List[Dict[str, Any]],
    slippage_pct: float,
    charges_enabled: bool = False,
):
    """
    Re-price every leg row using raw prices.

    1. Applies slippage to raw entry/exit prices (existing behaviour).
    2. Optionally applies Zerodha F&O transaction charges as price adjustments:
       - SELL leg: effective_entry = entry - entry_charge_per_unit
                   effective_exit  = exit  + exit_charge_per_unit
       - BUY  leg: effective_entry = entry + entry_charge_per_unit
                   effective_exit  = exit  - exit_charge_per_unit
       Charges per unit = total_charges_₹ / qty.  This keeps Net P&L in
       the same "per-unit points" unit that the rest of the system uses,
       while correctly deducting the rupee cost of every brokerage component.
    """
    updated_rows: List[Dict[str, Any]] = []
    trade_totals:  Dict[Any, float] = {}
    trade_charges: Dict[Any, float] = {}   # total ₹ charges per trade_id

    for raw_row in trades:
        row = dict(raw_row)
        position  = str(row.get('B/S',  '') or '').upper().strip()
        raw_entry = _normalize_recalc_numeric(row.get('Raw Entry Price'))
        raw_exit  = _normalize_recalc_numeric(row.get('Raw Exit Price'))
        trade_id  = row.get('Trade')
        leg_type  = str(row.get('Type', '') or '').upper().strip()
        is_leg_row = (
            bool(position)
            and leg_type in {'CE', 'PE', 'FUT', 'CALL', 'PUT', 'C', 'P'}
        )

        if is_leg_row and raw_entry is not None and raw_exit is not None:
            # ── Step 1: apply slippage ────────────────────────────────────
            new_entry = _apply_slippage(raw_entry, position, 'entry', slippage_pct)
            new_exit  = _apply_slippage(raw_exit,  position, 'exit',  slippage_pct)

            # ── Step 2: apply transaction charges ────────────────────────
            charges_inr = 0.0
            if charges_enabled:
                qty_raw = _normalize_recalc_numeric(row.get('Qty'))
                qty     = float(qty_raw) if qty_raw and qty_raw > 0 else 1.0
                segment = 'FUTURE' if leg_type == 'FUT' else 'OPTION'
                ch = _calculate_fo_charges(new_entry, new_exit, qty, position, segment)

                epu = ch['entry_charge_per_unit']   # ₹ / qty
                xpu = ch['exit_charge_per_unit']    # ₹ / qty
                charges_inr = ch['total_charges_inr']

                # Adjust effective prices so P&L = (eff_entry - eff_exit) for SELL
                if position == 'SELL':
                    new_entry = round(new_entry - epu, 2)   # sell gets less
                    new_exit  = round(new_exit  + xpu, 2)   # buy-back costs more
                else:
                    new_entry = round(new_entry + epu, 2)   # buy costs more
                    new_exit  = round(new_exit  - xpu, 2)   # sell-to-close gets less

            # ── Step 3: P&L (per-unit points) ────────────────────────────
            if position == 'BUY':
                leg_pnl = new_exit - new_entry
            else:
                leg_pnl = new_entry - new_exit

            row['Entry Price'] = new_entry
            row['Exit Price']  = new_exit
            if charges_enabled:
                row['Charges'] = round(charges_inr, 2)

            if leg_type == 'FUT':
                row['FUT Entry Price'] = new_entry
                row['FUT Exit Price']  = new_exit
                row['FUT P&L'] = leg_pnl
                row['CE P&L']  = 0
                row['PE P&L']  = 0
            elif leg_type in {'CE', 'CALL', 'C'}:
                row['CE P&L']  = leg_pnl
                row['PE P&L']  = 0
                row['FUT P&L'] = row.get('FUT P&L', 0) or 0
            else:
                row['PE P&L']  = leg_pnl
                row['CE P&L']  = 0
                row['FUT P&L'] = row.get('FUT P&L', 0) or 0

            trade_totals[trade_id]  = trade_totals.get(trade_id, 0.0)  + float(leg_pnl)
            trade_charges[trade_id] = trade_charges.get(trade_id, 0.0) + charges_inr
        else:
            numeric_net = _normalize_recalc_numeric(row.get('Net P&L'))
            if trade_id is not None and numeric_net is not None:
                trade_totals.setdefault(trade_id, float(numeric_net))

        updated_rows.append(row)

    for row in updated_rows:
        trade_id = row.get('Trade')
        net_pnl  = trade_totals.get(trade_id)
        if net_pnl is None:
            continue
        row['Net P&L'] = round(float(net_pnl), 2)
        if charges_enabled:
            row['Total Charges'] = round(trade_charges.get(trade_id, 0.0), 2)
        entry_spot = _normalize_recalc_numeric(row.get('Entry Spot'))
        if entry_spot and entry_spot > 1000:
            row['% P&L'] = round((float(net_pnl) / entry_spot) * 100, 4)
        else:
            row['% P&L'] = 0.0

    return updated_rows


@router.post("/clear-cache")
async def clear_cache():
    """Clear the backtest cache"""
    cache = _get_result_cache()
    cache.clear_all()
    return {"message": "Cache cleared"}


@router.post("/warm-cache")
async def warm_cache(request: dict):
    """
    Pre-load bulk data in the Celery backtest worker before the next run.

    The worker is the process that executes backtests, so warming only the
    FastAPI process gives a false readiness signal.
    """
    request = _normalize_payload_dates(request)
    symbol = request.get('index', request.get('symbol', 'NIFTY'))
    from_date = request.get('from_date', request.get('date_from'))
    to_date = request.get('to_date', request.get('date_to'))
    
    if not from_date or not to_date:
        return {"status": "error", "message": "Missing from_date or to_date"}

    warm_timeout = float(os.environ.get("WARM_CACHE_WAIT_SECONDS", "90"))
    warm_payload = {"index": symbol, "from_date": from_date, "to_date": to_date}
    queue_name = _backtest_queue_for_payload(warm_payload)

    # Debounced date-picker edits (StrategyBuilder fires one of these per
    # keystroke-pause) were each enqueuing a brand-new warm_backtest_cache_task
    # onto the SAME queue real backtests use, so a burst of edits from a few
    # users could pile up ahead of an actual run_algotest_job and make it look
    # "stuck in queue". Collapse concurrent warm requests for the same symbol
    # into the one already in flight instead of enqueuing a duplicate.
    task = None
    try:
        from services.optimizer.result_store import _redis as _get_redis
        _r = _get_redis()
    except Exception:
        _r = None
    inflight_key = f"warm_inflight:{symbol}"
    if _r is not None:
        try:
            existing_id = _r.get(inflight_key)
            if existing_id:
                existing_state = celery_app.AsyncResult(existing_id).state
                if existing_state in ("PENDING", "STARTED", "RETRY"):
                    task = celery_app.AsyncResult(existing_id)
        except Exception:
            pass
    # A real "Run Backtest" click is the only thing that should ever occupy
    # the single-concurrency backtest slot — don't let a background warm-up
    # queue up behind/ahead of it. If nothing is already warming (no in-flight
    # task to reuse) and a genuine backtest is running right now, skip warming
    # entirely rather than competing for the slot.
    if task is None and _real_backtest_active():
        return {
            "status": "skipped",
            "queue": queue_name,
            "message": "A backtest is currently running — skipping background cache warm.",
        }

    if task is None:
        task = warm_backtest_cache_task.apply_async(
            args=[warm_payload],
            queue=queue_name,
        )
        if _r is not None:
            try:
                _r.set(inflight_key, task.id, ex=int(warm_timeout) + 30)
            except Exception:
                pass

    try:
        result = await asyncio.to_thread(task.get, timeout=warm_timeout, propagate=False)
    except Exception as exc:
        logger.warning("Worker cache warm did not finish within %.1fs: %s", warm_timeout, exc)
        return {
            "status": "warming",
            "job_id": task.id,
            "queue": queue_name,
            "queue_depth": _queue_depth(queue_name),
            "message": f"Worker cache warm queued for {symbol} {from_date} to {to_date}",
        }

    if isinstance(result, dict) and result.get("status") == "ready":
        return {"status": "ready", "job_id": task.id, "queue": queue_name, **result}
    if isinstance(result, dict):
        return {"status": result.get("status", "error"), "job_id": task.id, "queue": queue_name, **result}
    return {"status": "error", "job_id": task.id, "queue": queue_name, "message": str(result)}


@router.post("/backtest/warm-cache")
async def warm_cache_legacy(request: dict):
    return await warm_cache(request)


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
        
        print(f"[CSV UPLOAD] filename: {file.filename}, content length: {len(csv_content)}")
        print(f"[CSV UPLOAD] first 200 chars: {csv_content[:200]}")
        
        # Parse CSV
        try:
            segments = parse_filter_csv(csv_content)
            print(f"[CSV UPLOAD] parsed segments: {len(segments)}")
        except Exception as parse_err:
            print(f"[CSV UPLOAD] parse error: {parse_err}")
            import traceback
            traceback.print_exc()
            segments = []
        
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
            filters[config_key] = {
                "label": label,
                "count": len(serialized_segments),
                "segments": serialized_segments,
                "preview": serialized_segments[:5],
                "range": summary_range,
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


def _run_algotest_job_process(payload: dict) -> dict:
    """Helper executed inside the ProcessPoolExecutor."""
    return execute_algotest_job(payload)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client and client.host else "unknown"


@router.post("/algotest")
async def run_algotest_backtest_endpoint(request: Request):
    """
    Legacy synchronous endpoint kept for backwards compatibility.
    """
    from services.maintenance import is_maintenance
    if is_maintenance():
        raise HTTPException(status_code=503, detail="System is under maintenance — backtests are temporarily disabled. Please try again shortly.")
    body = await request.json()
    try:
        _validate_lazy_legs_payload(body or {})
        validate_index_payload(body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _backtest_process_executor,
        _run_algotest_job_process,
        body,
    )
    return result


@router.post("/algotest/jobs")
async def queue_algotest_job(request: Request):
    """
    Enqueue an AlgoTest backtest to run asynchronously via Celery.
    """
    from services.maintenance import is_maintenance
    if is_maintenance():
        raise HTTPException(status_code=503, detail="System is under maintenance — backtests are temporarily disabled. Please try again shortly.")
    body = await request.json()
    origin_ip = _client_ip(request)
    payload = _resolve_effective_request(_normalize_request(_normalize_payload_dates(body)))
    try:
        _validate_lazy_legs_payload(payload)
        validate_index_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cache = _get_result_cache()
    if cache.is_available() and not payload.get("no_cache"):
        try:
            cache_key = cache.generate_key(
                symbol=payload.get("index") or payload.get("symbol") or "NIFTY",
                from_date=payload.get("from_date"),
                to_date=payload.get("to_date"),
                strategy_config=payload,
            )
            cached = cache.get(cache_key)
            if cached:
                job_id = str(uuid4())
                cached_result = {k: v for k, v in cached.items() if k != "trades_df"}
                cached_result = {**cached_result, "status": "success", "cached": True}
                celery_app.backend.store_result(job_id, cached_result, state="SUCCESS")
                return {"status": "completed", "job_id": job_id, "cached": True}
        except Exception as exc:
            logger.warning("Cache short-circuit failed, falling back to queue: %s", exc)

    # Optional LAN remote-worker routing: if the UI's "Core:" picker selected a
    # registered remote node, run this job on that node's dedicated queue
    # instead of the local backtests/backtests_fast queue. Unset (default) is
    # unchanged local behavior. See services/node_registry.py.
    node_id = body.get("node_id") or None
    if node_id:
        # Staleness guard: don't route to a remote worker running a different
        # code version than this box (mismatched image). See code_version.py.
        from services import node_registry as _nr
        if _nr.is_stale(node_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Worker {node_id} is running an outdated version and can't "
                    "run this job. Update that PC's remote-worker image, or pick "
                    "a different worker / Local."
                ),
            )
        base_queue = _backtest_queue_for_payload(payload)
        kind = "backtests_fast" if base_queue == "backtests_fast" else "backtests"
        queue_name = f"{kind}@{node_id}"
    else:
        queue_name = _backtest_queue_for_payload(payload)
    queue_depth = _queue_depth(queue_name)
    payload["_client_ip"] = origin_ip
    payload["node_id"] = node_id
    task = run_algotest_job.apply_async(args=[payload], queue=queue_name)
    if node_id:
        from services import node_registry
        node_registry.record_job_node(task.id, node_id)
    logger.info("[BACKTEST] queued job %s from ip=%s queue=%s node=%s", task.id[:8], origin_ip, queue_name, node_id or "local")
    return {"status": "queued", "job_id": task.id, "queue": queue_name, "queue_depth": queue_depth}


@router.post("/backtest/recalculate-slippage")
async def recalculate_slippage(request: dict):
    trades = request.get('trades') or []
    if not isinstance(trades, list) or not trades:
        raise HTTPException(status_code=400, detail="No trades provided")

    try:
        slippage_pct = float(request.get('slippage_pct', 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid slippage_pct")

    charges_enabled = bool(request.get('charges_enabled', False))

    recalculated_rows = _recalculate_trade_prices(
        trades, slippage_pct, charges_enabled=charges_enabled
    )
    trades_df = pd.DataFrame(recalculated_rows)

    if trades_df.empty:
        return {
            'trades': [],
            'summary': {},
            'pivot': {"headers": [], "rows": []},
            'meta': {'slippage_pct': slippage_pct, 'charges_enabled': charges_enabled},
        }

    for col in ['Entry Date', 'Exit Date', 'Leg Exit Date', 'Expiry']:
        if col in trades_df.columns:
            trades_df[col] = pd.to_datetime(trades_df[col], dayfirst=True, errors='coerce')

    from base import compute_analytics, build_pivot

    trades_df, result_summary = compute_analytics(trades_df)
    result_pivot = build_pivot(trades_df, 'Exit Date')

    for col in ['Entry Date', 'Exit Date', 'Leg Exit Date', 'Expiry']:
        if col in trades_df.columns:
            trades_df[col] = trades_df[col].apply(
                lambda v: v.strftime('%d-%m-%Y') if hasattr(v, 'strftime') and not pd.isna(v) else None
            )

    result_trades = []
    for row in trades_df.to_dict('records'):
        for key in ('Cumulative', 'Peak', 'DD', '%DD'):
            value = row.get(key)
            if value is not None:
                try:
                    numeric = float(value)
                    if np.isnan(numeric):
                        row[key] = None
                except (TypeError, ValueError):
                    row[key] = None
        result_trades.append(row)

    return {
        'trades': result_trades,
        'summary': result_summary,
        'pivot': result_pivot,
        'meta': {'slippage_pct': slippage_pct, 'charges_enabled': charges_enabled},
    }


# ── Midcap cross-index overlay (additive; never runs the engine) ──────────────
# Bound concurrency so a burst of overlay requests can't spike memory. Each call
# is O(rows) over a small projection + a sub-MB index lookup, so a small cap is
# plenty. Lives in the API process; loads only the tiny INDEX_OHLC cache.
_MIDCAP_OVERLAY_SEM = threading.Semaphore(
    int(os.getenv("MIDCAP_OVERLAY_MAX_CONCURRENCY", "4") or "4")
)


@router.post("/midcap-overlay")
def midcap_overlay(request: dict):
    """Price Midcap100 overlay leg(s) on a finished NIFTY backtest.

    Body: {
      rows: [{trade_id, reentry_index?, entry_date, exit_date, nifty_pnl, nifty_pnl_pct}],
      midcap_legs: [{midcap_mode, cost_pct_per_month, position, lots, symbol}],
      midcap_spot_adjustment: {enabled, direction, pct, units} | null,
      symbol?: str
    }
    Returns {results: [...], summary: {...}, available: bool}.
    """
    rows = request.get('rows') or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="rows must be a list")
    midcap_legs = request.get('midcap_legs') or []
    if not isinstance(midcap_legs, list) or not midcap_legs:
        raise HTTPException(status_code=400, detail="No midcap_legs provided")
    midcap_sa = request.get('midcap_spot_adjustment') or None
    symbol = request.get('symbol') or (midcap_legs[0].get('symbol') if midcap_legs else None) or 'NIFTYMIDCAP100'

    if not _MIDCAP_OVERLAY_SEM.acquire(timeout=30):
        raise HTTPException(status_code=503, detail="midcap overlay busy, retry shortly")
    try:
        from services import index_ohlc_store, rust_fast_path
        # Rust-only: the Midcap math runs exclusively in the native engine.
        # No Python fallback — if the native path is unavailable we surface an
        # error so it's fixed (rebuilt), never silently computed in Python.
        index_ohlc_store.ensure_index_ohlc_loaded(symbol)
        if not (rust_fast_path.index_ohlc_is_loaded() and rust_fast_path.compute_midcap_legs_available()):
            raise HTTPException(
                status_code=503,
                detail="Rust native midcap engine unavailable (Rust-only mode) — rebuild the native extension.",
            )
        result = rust_fast_path.compute_midcap_legs(rows, midcap_legs, midcap_sa, symbol)
        if result is None:
            raise HTTPException(status_code=500, detail="Rust midcap computation failed.")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logging.exception("midcap-overlay failed")
        raise HTTPException(status_code=500, detail=f"midcap overlay error: {exc}")
    finally:
        _MIDCAP_OVERLAY_SEM.release()


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
        return {
            "status": "queued",
            "queue_depth": _queue_depth("backtests") + _queue_depth("backtests_fast"),
        }
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


@router.delete("/algotest/jobs/{job_id}")
async def cancel_algotest_job(job_id: str):
    """Cancel a queued/running AlgoTest backtest: revoke the Celery task and free
    its memory-gate reservation immediately so a queued job can start at once
    (the reservation TTL would also reclaim it, but cancel should free it now)."""
    try:
        celery_app.control.revoke(job_id, terminate=True)
    except Exception as exc:
        logger.warning("Could not revoke backtest %s: %s", job_id, exc)
    try:
        from services import memory_gate, node_registry
        memory_gate.release(job_id, node_id=node_registry.get_job_node(job_id))
    except Exception as exc:
        logger.debug("memory_gate release on cancel failed: %s", exc)
    return {"status": "cancelled", "job_id": job_id}
