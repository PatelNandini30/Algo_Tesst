from fastapi import APIRouter, HTTPException, Response, Header, UploadFile, File
from typing import Dict, Any, List, Optional, Tuple
# Import generic multi-leg engine
# NOTE: keep FastAPI imports at top for readability
from engines.generic_algotest_engine import run_algotest_backtest, _apply_slippage, _calculate_fo_charges
from services.algotest_job import execute_algotest_job
from services.backtest_cache import get_backtest_cache as _get_result_cache
from worker.tasks import run_algotest_job
from worker.celery import celery_app
import sys
import os
import pandas as pd
import numpy as np
import traceback
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
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
_BACKTEST_PROCESS_WORKERS = min(4, max(1, os.cpu_count() or 1))
_backtest_process_executor = ProcessPoolExecutor(max_workers=_BACKTEST_PROCESS_WORKERS)

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


def _run_algotest_job_process(payload: dict) -> dict:
    """Helper executed inside the ProcessPoolExecutor."""
    return execute_algotest_job(payload)


@router.post("/algotest")
async def run_algotest_backtest_endpoint(request: dict):
    """
    Legacy synchronous endpoint kept for backwards compatibility.
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _backtest_process_executor,
        _run_algotest_job_process,
        request,
    )
    return result


@router.post("/algotest/jobs")
async def queue_algotest_job(request: dict):
    """
    Enqueue an AlgoTest backtest to run asynchronously via Celery.
    """
    payload = dict(request or {})
    task = run_algotest_job.apply_async(args=[payload])
    return {"status": "queued", "job_id": task.id}


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
