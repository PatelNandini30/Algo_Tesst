"""
Optimization API.

Endpoints
---------
POST   /api/optimize/preview      → validate + estimate combinations (no run)
POST   /api/optimize/jobs         → enqueue an optimization run
GET    /api/optimize/jobs/{id}    → status + meta
GET    /api/optimize/jobs/{id}/results
                                  → paginated/sorted result rows
GET    /api/optimize/objectives   → list available ranking metrics
DELETE /api/optimize/jobs/{id}    → cancel + delete results
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import threading
import time
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from services.optimizer import result_store
from services.optimizer.objective import list_objectives, resolve_objective
from services.optimizer.param_expander import count_combinations
from services.optimizer.runner import OptimizationError, validate_request
from worker.celery import celery_app
from worker.tasks import run_optimize_job

# Reuse backtest payload normalization (dates, etc.)
from routers.backtest import _normalize_payload_dates
from services.algotest_job import _normalize_request, _resolve_effective_request
from services.index_metadata import validate_index_payload

logger = logging.getLogger(__name__)

router = APIRouter()


def _prepared_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _resolve_effective_request(_normalize_request(_normalize_payload_dates(raw)))


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client and client.host else "unknown"


@router.get("/optimize/objectives")
async def get_objectives():
    """List the metrics the UI can offer as the ranking objective."""
    return {"objectives": list_objectives()}


@router.get("/optimize/system-info")
async def get_system_info():
    """
    Return system info the UI needs for the worker-count picker:
      * cpu_count — physical worker cap
      * default_parallelism — what the runner would pick if user doesn't set one
    """
    cpu = os.cpu_count() or 1
    default = min(cpu, max(1, cpu // 2))
    return {"cpu_count": cpu, "default_parallelism": default}


@router.post("/optimize/preview")
async def preview_optimization(request: Dict[str, Any]):
    """
    Validate a request and return combinations count + estimated runtime.
    Does NOT enqueue a job.
    """
    base_payload = _prepared_payload(request.get("base_payload") or {})
    param_specs = request.get("param_specs") or []
    method = (request.get("method") or "exhaustive").lower()
    sample_n = request.get("sample_n")

    try:
        validate_index_payload(base_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        total = validate_request(base_payload, param_specs, method, sample_n)
    except OptimizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    grid_size = count_combinations(param_specs)
    # 250 ms per combo observed at P=1 on 2-year NIFTY; divide by actual parallelism.
    _parallelism = max(1, int(os.environ.get("OPTIMIZE_PARALLELISM", "1") or "1"))
    est_seconds = int(total * 0.25 / _parallelism)
    return {
        "grid_size": grid_size,
        "planned_runs": total,
        "estimated_seconds": est_seconds,
        "method": method,
    }


@router.post("/optimize/jobs")
async def enqueue_optimization(request: Request):
    """Validate and enqueue the run. Returns a Celery job_id."""
    body = await request.json()
    origin_ip = _client_ip(request)
    base_payload = _prepared_payload(body.get("base_payload") or {})
    param_specs = body.get("param_specs") or []
    method = (body.get("method") or "exhaustive").lower()
    sample_n = body.get("sample_n")
    objective_name = body.get("objective") or "total_pnl"
    algorithm = body.get("algorithm")
    seed = body.get("seed")
    # Parallelism: user override from UI. Capped between 1 and cpu_count to
    # prevent thrashing. Unset → runner falls back to OPTIMIZE_PARALLELISM
    # env / cpu//2 default.
    parallelism_raw = body.get("parallelism")
    parallelism: Optional[int] = None
    if parallelism_raw is not None:
        try:
            parallelism = max(1, min(int(parallelism_raw), os.cpu_count() or 8))
        except (TypeError, ValueError):
            parallelism = None

    try:
        validate_index_payload(base_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        total = validate_request(base_payload, param_specs, method, sample_n)
    except OptimizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        resolve_objective(objective_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    spec = {
        "base_payload": base_payload,
        "param_specs": param_specs,
        "method": method,
        "sample_n": sample_n,
        "objective": objective_name,
        "algorithm": algorithm,
        "seed": seed,
        "parallelism": parallelism,
        "zip_naming": body.get("zip_naming") or None,
        "client_ip": origin_ip,
    }
    task = run_optimize_job.apply_async(args=[spec], queue="optimize")
    logger.info("[OPTIM] queued job %s from ip=%s objective=%s method=%s", task.id[:8], origin_ip, objective_name, method)
    return {
        "status": "queued",
        "job_id": task.id,
        "total_combos": total,
        "objective": objective_name,
        "method": method,
    }


@router.get("/optimize/jobs/{job_id}")
async def get_optimize_job(job_id: str):
    """Return progress meta + Celery state."""
    meta = result_store.get_meta(job_id)
    celery_state: Optional[str] = None
    try:
        celery_state = celery_app.AsyncResult(job_id).state
    except Exception:
        celery_state = None

    if not meta:
        # The Celery task may still be pending in the queue
        if celery_state in (None, "PENDING"):
            return {"status": "queued"}
        return {"status": "unknown", "celery_state": celery_state}

    status = meta.get("status") or "running"
    return {
        "status": status,
        "celery_state": celery_state,
        "meta": meta,
    }


@router.get("/optimize/jobs/{job_id}/results")
async def get_optimize_results(
    job_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2000),
    sort_by: Optional[str] = None,
    order: str = Query("desc", regex="^(asc|desc)$"),
):
    """Paginated, sortable result rows. `sort_by` is a summary-dict key."""
    result = result_store.get_results(
        job_id,
        offset=offset,
        limit=limit,
        sort_key=sort_by,
        descending=(order == "desc"),
    )
    meta = result_store.get_meta(job_id)
    return {
        "job_id": job_id,
        "offset": offset,
        "limit": limit,
        "sort_by": sort_by,
        "order": order,
        "total": result["total"],
        "rows": result["rows"],
        "meta": meta,
    }


# ── On-demand MAE/MFE compute for individual tradesheet downloads ────────────
# Optim runs skip MAE/MFE per combo (OPTIMIZE_SKIP_MAE_MFE=1) for speed.
# When a user downloads a single combo's tradesheet we compute MAE/MFE here
# and cache it back to the CSV so the second download is instant.

_ohlc_pandas_cache: Dict[str, "tuple[Any, List[str]]"] = {}
_ohlc_pandas_lock = threading.Lock()


def _get_ohlc_pandas_for_index(index_str: str):
    """
    Lazy-load OHLC feather into a pandas DataFrame (cached per-process).
    Returns (ohlc_pd, trading_days). Same dtypes as the worker's preload:
    datetime64[ms] dates, category strings, float32 prices, int32 strike_r.
    """
    key = index_str.upper()
    with _ohlc_pandas_lock:
        if key in _ohlc_pandas_cache:
            return _ohlc_pandas_cache[key]
        import pyarrow as _pa
        import pyarrow.ipc as _pa_ipc
        import pyarrow.compute as _pc
        import pandas as _pd
        from services import rust_fast_path as _rfp
        _pa.set_cpu_count(1)
        _pa.set_io_thread_count(1)
        feather = _rfp._cache_root() / f"arrow-v2:bulk:{key}:full" / "options.feather"
        if not feather.exists():
            raise FileNotFoundError(f"OHLC feather missing: {feather}")
        _t0 = time.time()
        needed = ["Symbol", "Date", "ExpiryDate", "StrikePrice", "OptionType", "High", "Low"]
        reader = _pa_ipc.open_file(str(feather))
        avail = set(reader.schema.names)
        sel = [c for c in needed if c in avail]
        tbl = reader.read_all().select(sel)
        if "OptionType" in avail:
            mask = _pc.is_in(tbl.column("OptionType"), value_set=_pa.array(["CE", "PE"]))
            tbl = tbl.filter(mask)
        ohlc_pd = tbl.to_pandas(date_as_object=False)
        for c in ("Symbol", "OptionType"):
            if c in ohlc_pd.columns and ohlc_pd[c].dtype == object:
                ohlc_pd[c] = ohlc_pd[c].astype("category")
        for c in ("High", "Low"):
            if c in ohlc_pd.columns:
                ohlc_pd[c] = ohlc_pd[c].astype("float32")
        if "StrikePrice" in ohlc_pd.columns:
            ohlc_pd["strike_r"] = ohlc_pd["StrikePrice"].round(0).astype("int32")
            ohlc_pd = ohlc_pd.drop(columns=["StrikePrice"])
        trading_days = sorted({d.strftime("%Y-%m-%d")
                               for d in _pd.to_datetime(ohlc_pd["Date"]).dt.date.unique()})
        logger.info(
            "[OPTIM_DL] OHLC pandas cached for %s: %d rows, %d trading days in %.2fs",
            key, len(ohlc_pd), len(trading_days), time.time() - _t0,
        )
        _ohlc_pandas_cache[key] = (ohlc_pd, trading_days)
        return _ohlc_pandas_cache[key]


def _enrich_tradesheet_with_mae_mfe(csv_path: str, index_str: str) -> bool:
    """
    Read the combo tradesheet CSV, compute MAE/MFE and Lowest NAV per trade,
    write back to disk. Returns True if enrichment was applied.

    Idempotent: skips if MAE/MFE columns already populated (non-zero sum).
    """
    import pandas as _pd
    try:
        trades_df = _pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        logger.warning("[OPTIM_DL] CSV read failed for %s: %s", csv_path, exc)
        return False
    if trades_df.empty or "MAE" not in trades_df.columns or "MFE" not in trades_df.columns:
        return False
    # Skip if already enriched (any non-zero MAE/MFE means populated)
    mae_num = _pd.to_numeric(trades_df["MAE"], errors="coerce").fillna(0.0)
    mfe_num = _pd.to_numeric(trades_df["MFE"], errors="coerce").fillna(0.0)
    if (mae_num.abs().sum() + mfe_num.abs().sum()) > 0.0001:
        return False  # already has values, no need to recompute
    try:
        ohlc_pd, trading_days = _get_ohlc_pandas_for_index(index_str)
    except Exception as exc:
        logger.warning("[OPTIM_DL] OHLC load failed: %s", exc)
        return False

    # _compute_mae_mfe_batch reads from runner._RUST_CONTEXT["ohlc_df_pandas"].
    # Install the cached pandas DataFrame and trading_days so it uses our path.
    from services.optimizer import runner as _r
    from services.optimizer.runner import _compute_mae_mfe_batch, _compute_live_dd_from_mae
    _prev_ctx = _r._RUST_CONTEXT
    _r._RUST_CONTEXT = {"ohlc_df_pandas": ohlc_pd, "trading_days": trading_days}
    try:
        # CSV dates are DD-MM-YYYY (per algotest_job tradesheet format).
        # Convert back to datetime for the compute, restore string format on save.
        for col in ("Entry Date", "Exit Date"):
            if col in trades_df.columns:
                trades_df[col] = _pd.to_datetime(trades_df[col], format="%d-%m-%Y", errors="coerce")
        # Numeric columns the compute reads
        for col in ("Strike", "Entry Price", "Entry Spot", "Exit Price", "Exit Spot",
                    "Cumulative", "Peak", "DD", "%DD", "Net P&L"):
            if col in trades_df.columns:
                trades_df[col] = _pd.to_numeric(trades_df[col], errors="coerce")
        trades_df = _compute_mae_mfe_batch(trades_df, index_str.upper(), trading_days)

        # Lowest NAV needs an aggregated DataFrame (one row per trade with Cumulative).
        # Build it from parent rows (first occurrence of each Trade id).
        if "Trade" in trades_df.columns:
            parent_rows = trades_df.drop_duplicates(subset=["Trade"], keep="first")
            aggregated = parent_rows[["Trade"]].copy()
            for col in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                if col in parent_rows.columns:
                    aggregated[col] = parent_rows[col].values
            trades_df = _compute_live_dd_from_mae(trades_df, aggregated)

        # Restore date format for CSV
        for col in ("Entry Date", "Exit Date"):
            if col in trades_df.columns:
                trades_df[col] = trades_df[col].dt.strftime("%d-%m-%Y")

        trades_df.to_csv(csv_path, index=False)
        logger.info("[OPTIM_DL] enriched MAE/MFE for %s", os.path.basename(csv_path))
        return True
    except Exception as exc:
        logger.warning("[OPTIM_DL] MAE/MFE enrich failed for %s: %s", csv_path, exc)
        return False
    finally:
        _r._RUST_CONTEXT = _prev_ctx


# ── Background ZIP builder ───────────────────────────────────────────────────
# Building 100+ XLSX tradesheets takes minutes — far longer than a browser HTTP
# timeout (~60s).  The endpoint kicks off a background build, returns 202 with
# progress, and the frontend polls until the ZIP is ready on disk.  Subsequent
# downloads stream from disk instantly.

_UNSAFE_ZIP_RE = re.compile(r'[/\\:*?"<>|]')

def _safe_zip_name(name: str) -> str:
    """Sanitize a user-provided name for use in ZIP folder/filename paths."""
    return _UNSAFE_ZIP_RE.sub('_', str(name or '').strip())
_zip_build_state: Dict[str, Dict[str, Any]] = {}
_zip_build_lock = threading.Lock()


def _zip_cache_path(job_id: str, patchwise: bool = False) -> str:
    return result_store.zip_cache_path(job_id, patchwise)


def _build_one_xlsx(args: tuple) -> tuple:
    """Worker function for ProcessPoolExecutor.

    Returns (label_safe, xlsx_bytes, None) or (label_safe, None, err_str) on failure.
    Top-level so it's picklable.
    """
    (fpath, label_safe, combo_summary, combo_label, from_date, to_date,
     midcap_legs, midcap_sa, midcap_sym, filter_name, patchwise, filter_segments) = args
    try:
        import pandas as _pd
        from services.optimizer.excel_builder import build_combo_xlsx as _build
        trades_df = _pd.read_csv(fpath, dtype=str)
        xlsx_bytes = _build(
            trades_df, combo_summary,
            combo_label=combo_label,
            from_date=from_date, to_date=to_date,
            midcap_legs=midcap_legs,
            midcap_spot_adjustment=midcap_sa,
            midcap_symbol=midcap_sym,
            filter_name=filter_name,
            patchwise=patchwise,
            filter_segments=filter_segments,
        )
        return (label_safe, xlsx_bytes, None)
    except Exception as exc:
        return (label_safe, None, str(exc))


def _build_zip_blocking(job_id: str, patchwise: bool = False) -> None:
    """Build the ZIP file on disk.  Updates progress state as it goes."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import pandas as pd

    state_key = f"{job_id}:pw" if patchwise else job_id
    state = _zip_build_state[state_key]
    try:
        meta = result_store.get_meta(job_id) or {}
        trades_dir = result_store.get_trades_dir(job_id)
        all_results = result_store.get_all_results(job_id)

        summary_by_label: Dict[str, Any] = {}
        for row in all_results:
            cls = row.get("combo_label_safe") or ""
            if cls:
                summary_by_label[cls] = row

        base_payload = meta.get("base_payload") or {}
        from_date = base_payload.get("from_date") or base_payload.get("date_from") or ""
        to_date   = base_payload.get("to_date")   or base_payload.get("date_to")   or ""

        # Named folder structure when a filter was active during the run.
        zip_naming = meta.get("zip_naming") or {}
        if zip_naming:
            _l1 = _safe_zip_name(zip_naming.get("level1", "")) or "tradesheets"
            _l2 = _safe_zip_name(zip_naming.get("level2", ""))
            _l3 = _safe_zip_name(zip_naming.get("level3", ""))
            tradesheets_root = "/".join(p for p in [_l1, _l2, _l3] if p)
        else:
            tradesheets_root = "tradesheets"

        files = sorted(os.listdir(trades_dir))
        csv_files = [f for f in files if f.endswith(".csv")]
        _root_files = {"summary.csv", "run_config.csv"}

        combo_csvs = [f for f in csv_files if f not in _root_files]
        root_csvs  = [f for f in csv_files if f in _root_files]

        state["total"] = len(combo_csvs)
        state["done"]  = 0

        # Enrich MAE/MFE on disk before building XLSXs.
        # OPTIMIZE_SKIP_MAE_MFE=1 leaves zeros in stored CSVs for speed;
        # we compute them here in this thread (OHLC feather is cached after the
        # first call so all combos share one load).  The ProcessPoolExecutor
        # subprocesses below then read already-enriched CSVs.
        index_str = (base_payload.get("index") or "NIFTY").upper()
        state["phase"] = "enriching"
        for i, fname in enumerate(combo_csvs):
            try:
                _enrich_tradesheet_with_mae_mfe(
                    os.path.join(trades_dir, fname), index_str
                )
            except Exception as _e:
                logger.warning("[ZIP] MAE/MFE enrich skipped for %s: %s", fname, _e)
            state["done"] = i + 1
        state["done"] = 0
        state["phase"] = "building"

        # Midcap cross-index overlay config (from the run's base payload). When
        # present, each combo XLSX gets the same Midcap/Combined columns + summary
        # as the verified backtest, computed via the same native engine.
        _midcap_legs = base_payload.get("midcap_legs") or None
        _midcap_sa   = base_payload.get("midcap_spot_adjustment") or None
        _midcap_sym  = (
            (_midcap_legs[0].get("symbol") if (_midcap_legs and isinstance(_midcap_legs[0], dict)) else None)
            or "NIFTYMIDCAP100"
        )

        _filter_name = (zip_naming.get("level1") or "") if zip_naming else ""
        # Filter segment windows (from the run's base payload) — patch-wise sheet
        # resets the equity at each segment START instead of guessing via gaps.
        _filter_segments = base_payload.get("filter_segments") or None

        build_args = []
        for fname in combo_csvs:
            label_safe = fname[:-4]
            row = summary_by_label.get(label_safe, {})
            build_args.append((
                os.path.join(trades_dir, fname),
                label_safe,
                row.get("summary") or {},
                row.get("combo_label") or label_safe,
                from_date, to_date,
                _midcap_legs, _midcap_sa, _midcap_sym,
                _filter_name,
                patchwise,
                _filter_segments,
            ))

        # Write directly to disk — atomic move at end so partial files never
        # appear cached.
        out_path = _zip_cache_path(job_id, patchwise)
        tmp_path = out_path + ".building"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        # Tune workers: 4-6 is a sweet spot for CPU-bound XLSX builds without
        # starving the host (Granian + 2 worker containers also need CPU).
        max_workers = min(6, max(2, (os.cpu_count() or 4) - 1))

        # Fork-safety: SQLAlchemy's connection pool is not fork-safe. Forked
        # subprocesses inherit the parent's open TCP connections and corrupt them
        # (PGRES_TUPLES_OK errors). Disposing connections in each child immediately
        # after fork forces them to open fresh connections. The parent pool is
        # completely unaffected — initializer only runs in child processes.
        def _child_init():
            try:
                from database import get_engine as _ge
                _ge().dispose()
            except Exception:
                pass

        # When a job swept spot_adjustment_enabled (both true and false), route
        # each combo into Adjustment/ or No_Adjustment/ subfolders inside the zip.
        # Use filenames directly — Redis results may be deduplicated (multiple
        # identical NoAdjustment runs collapse to one row), so label_safe from
        # the filename is the only reliable source.
        _has_no_adj = any("NoAdjustment" in f for f in combo_csvs)
        _has_adj    = any("NoAdjustment" not in f for f in combo_csvs)
        _use_adj_folders = _has_no_adj and _has_adj

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
            # Root CSVs first — these are tiny and don't need parallel build.
            # The master summary.csv must follow the user-selected download method:
            # patchwise download → patchwise-recomputed summary (matches each combo's
            # patchwise XLSX Summary); overall download → stored (overall) summary.
            for fname in root_csvs:
                if patchwise and fname == "summary.csv":
                    continue  # regenerated patchwise below
                zf.write(os.path.join(trades_dir, fname), fname)

            if patchwise:
                try:
                    from services.optimizer.excel_builder import (
                        compute_xlsx_summary_metrics as _cmetrics,
                    )
                    _flat_rows = []
                    for fname in combo_csvs:
                        _ls = fname[:-4]
                        _row = summary_by_label.get(_ls, {})
                        try:
                            _cdf = pd.read_csv(os.path.join(trades_dir, fname), dtype=str)
                            _pw_metrics = _cmetrics(
                                _cdf, _row.get("summary") or {},
                                midcap_legs=_midcap_legs,
                                midcap_spot_adjustment=_midcap_sa,
                                midcap_symbol=_midcap_sym,
                                patchwise=True,
                                filter_segments=_filter_segments,
                            )
                            _flat = {
                                "combo_id": _row.get("combo_id"),
                                "combo_label": _row.get("combo_label") or _ls,
                            }
                            _flat.update({**(_row.get("summary") or {}), **_pw_metrics})
                            _flat_rows.append(_flat)
                        except Exception as _se:
                            logger.warning("[ZIP] patchwise summary skipped for %s: %s", _ls, _se)
                    if _flat_rows:
                        zf.writestr("summary.csv", pd.DataFrame(_flat_rows).to_csv(index=False))
                except Exception as _pwe:
                    logger.warning("[ZIP] patchwise summary.csv regen failed: %s", _pwe)
                    # Fall back to the stored (overall) summary so the ZIP isn't missing it.
                    _stored_summary = os.path.join(trades_dir, "summary.csv")
                    if os.path.isfile(_stored_summary):
                        zf.write(_stored_summary, "summary.csv")

            if build_args:
                # Track no-adjustment labels already written (strip numeric prefix)
                # so that identical no-adj runs with different swept pct values
                # are deduplicated to one file per unique strategy.
                _no_adj_seen: set = set()

                with ProcessPoolExecutor(max_workers=max_workers, initializer=_child_init) as pool:
                    futures = {pool.submit(_build_one_xlsx, a): a[1] for a in build_args}
                    for fut in as_completed(futures):
                        label_safe = futures[fut]
                        try:
                            ls, xlsx_bytes, err = fut.result()
                        except Exception as exc:
                            xlsx_bytes, err = None, str(exc)
                        if _use_adj_folders:
                            _subfolder = "No_Adjustment" if "NoAdjustment" in label_safe else "Adjustment"
                            if _subfolder == "No_Adjustment":
                                _base = label_safe.split("_", 1)[1] if "_" in label_safe else label_safe
                                if _base in _no_adj_seen:
                                    state["done"] += 1
                                    continue
                                _no_adj_seen.add(_base)
                            arc_base = f"{tradesheets_root}/{_subfolder}/{label_safe}"
                        else:
                            arc_base = f"{tradesheets_root}/{label_safe}"
                        if xlsx_bytes is not None:
                            zf.writestr(f"{arc_base}.xlsx", xlsx_bytes)
                        else:
                            logger.warning("[ZIP] XLSX failed for %s: %s — using CSV",
                                           label_safe, err)
                            csv_path = os.path.join(trades_dir, f"{label_safe}.csv")
                            if os.path.isfile(csv_path):
                                zf.write(csv_path, f"{arc_base}.csv")
                        state["done"] += 1

        os.replace(tmp_path, out_path)
        state["status"] = "ready"
        state["finished_at"] = time.time()
        logger.info("[ZIP] Built %s for job %s in %.1fs",
                    out_path, job_id, time.time() - state["started_at"])
    except Exception as exc:
        logger.exception("[ZIP] Build failed for job %s", job_id)
        state["status"] = "error"
        state["error"] = str(exc)


@router.get("/optimize/jobs/{job_id}/tradesheets.zip")
async def download_tradesheets_zip(job_id: str, patchwise: bool = Query(False)):
    """
    Returns the tradesheets ZIP, building it on first request.

      • 200 + ZIP file       — when the ZIP is ready on disk
      • 202 + progress JSON  — while a background build is in progress
      • 4xx                  — job not found / not complete / no trades

    Pass ?patchwise=true to get a ZIP where the combined chain resets to 100
    at each FILTER_END boundary (separate on-disk cache from the default build).
    """
    # Disk-cache shortcut: if the ZIP already exists, serve it directly even
    # when Redis meta has expired (TTL) or been wiped (e.g., compose down).
    cache_path = _zip_cache_path(job_id, patchwise)
    if os.path.isfile(cache_path):
        _meta_for_name = result_store.get_meta(job_id) or {}
        _zip_naming = _meta_for_name.get("zip_naming") or {}
        _level1 = _safe_zip_name(_zip_naming.get("level1", "")) if _zip_naming else ""
        _pw_suffix = "_patchwise" if patchwise else ""
        filename = f"{_level1}{_pw_suffix}.zip" if _level1 else f"optimize_{job_id[:8]}_tradesheets{_pw_suffix}.zip"
        return FileResponse(
            cache_path,
            media_type="application/zip",
            filename=filename,
            headers={"X-Filename": filename},
        )

    meta = result_store.get_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")
    if meta.get("status") != "success":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not complete (status: {meta.get('status')})",
        )

    trades_dir = result_store.get_trades_dir(job_id)
    if not os.path.isdir(trades_dir):
        raise HTTPException(
            status_code=404,
            detail="No tradesheets found. The job may have produced 0 trades.",
        )

    # Separate state key for patchwise so both modes can build concurrently
    state_key = f"{job_id}:pw" if patchwise else job_id
    with _zip_build_lock:
        state = _zip_build_state.get(state_key)
        if state is None or state.get("status") in ("error",):
            state = {
                "status": "building",
                "done": 0,
                "total": 0,
                "started_at": time.time(),
                "error": None,
            }
            _zip_build_state[state_key] = state
            thread = threading.Thread(
                target=_build_zip_blocking, args=(job_id, patchwise), daemon=True,
            )
            thread.start()

    elapsed = time.time() - state.get("started_at", time.time())
    return JSONResponse(
        status_code=202,
        content={
            "status": state.get("status", "building"),
            "done": state.get("done", 0),
            "total": state.get("total", 0),
            "elapsed_seconds": round(elapsed, 1),
            "error": state.get("error"),
            "message": "ZIP is being built — retry shortly to download.",
        },
    )


def _wm_adj_display(raw: Optional[str]) -> str:
    """'NoAdjustment'→'No Adj', 'RiseBy1%'→'Rise 1%', 'FallsBy1%'→'Fall 1%',
    'RisesOrFallsBy1%'→'Rise or Fall 1%'."""
    import re as _re
    s = (raw or "").strip()
    if not s or s.lower().startswith("noadjust"):
        return "No Adj"
    m = _re.match(r"(RiseBy|FallsBy|RisesOrFallsBy)([\d.]+)%?", s)
    if m:
        word = {"RiseBy": "Rise", "FallsBy": "Fall", "RisesOrFallsBy": "Rise or Fall"}[m.group(1)]
        return f"{word} {m.group(2)}%"
    return s


def _wm_strike_display(cc: Dict[str, Any]) -> str:
    """Build 'CE ATM' / 'PE 1% ITM' / 'CE 3% OTM PE 0.5% ITM' from combo columns."""
    def fmt(lbl: Optional[str], cepe: str) -> Optional[str]:
        if not lbl or lbl == "-":
            return None
        return f"{cepe} {str(lbl).replace('_', ' ')}"
    parts = [p for p in (fmt(cc.get("call_strike_label"), "CE"),
                         fmt(cc.get("put_strike_label"), "PE")) if p]
    return " ".join(parts) if parts else "Strategy"


def _wm_strike_sort(cc: Dict[str, Any]) -> float:
    """Row ordering: ATM first, then ITM (ascending %), then OTM (ascending %)."""
    import re as _re
    lbl = cc.get("call_strike_label")
    if not lbl or lbl == "-":
        lbl = cc.get("put_strike_label")
    lbl = str(lbl or "").upper()
    if not lbl or lbl == "ATM" or lbl == "-":
        return 0.0
    m = _re.match(r"([\d.]+)%_(ITM|OTM)", lbl)
    if m:
        mag = float(m.group(1))
        return (1000.0 + mag) if m.group(2) == "ITM" else (2000.0 + mag)
    return 3000.0


@router.get("/optimize/jobs/{job_id}/wow_mom.xlsx")
async def download_wow_mom(job_id: str, patchwise: bool = Query(False)):
    """
    Merged WOW & MOM summary across ALL combos: two sheets ('WOW Summary' +
    'MOM Summary'), each stacking one block per combo (titled by combo label).
    Each block is computed from the same `cleaned` rows the per-combo tradesheet
    uses, so the blocks match the individual tradesheets exactly. Additive —
    nothing existing changes.
    """
    import pandas as pd
    from openpyxl import Workbook
    from services.optimizer.excel_builder import build_cleaned_for_combo
    from services.optimizer.wow_mom import write_merged_wow_mom

    meta = result_store.get_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")
    if meta.get("status") != "success":
        raise HTTPException(status_code=400, detail=f"Job is not complete (status: {meta.get('status')})")

    zip_naming = meta.get("zip_naming") or {}
    lvl1 = _safe_zip_name(zip_naming.get("level1", "")) if zip_naming else ""
    fname_out = f"{lvl1}_WOW_MOM.xlsx" if lvl1 else f"optimize_{job_id[:8]}_WOW_MOM.xlsx"

    # Serve from disk cache if already built — first click builds, every
    # subsequent click is instant.
    cache_path = result_store.wow_mom_cache_path(job_id, patchwise)
    if os.path.isfile(cache_path):
        return FileResponse(
            cache_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=fname_out,
            headers={"X-Filename": fname_out},
        )

    trades_dir = result_store.get_trades_dir(job_id)
    if not os.path.isdir(trades_dir):
        raise HTTPException(status_code=404, detail="No tradesheets found.")

    base_payload = meta.get("base_payload") or {}
    midcap_legs = base_payload.get("midcap_legs") or None
    midcap_sa   = base_payload.get("midcap_spot_adjustment") or None
    midcap_sym  = (
        (midcap_legs[0].get("symbol") if (midcap_legs and isinstance(midcap_legs[0], dict)) else None)
        or "NIFTYMIDCAP100"
    )
    filter_segments = base_payload.get("filter_segments") or None

    label_by_safe: Dict[str, str] = {}
    cols_by_safe: Dict[str, Dict[str, Any]] = {}
    for row in result_store.get_all_results(job_id):
        cls = row.get("combo_label_safe")
        if cls:
            label_by_safe[cls] = row.get("combo_label") or cls
            cols_by_safe[cls] = row.get("combo_columns") or {}

    files = sorted(os.listdir(trades_dir))
    combo_csvs = [f for f in files if f.endswith(".csv") and f not in ("summary.csv", "run_config.csv")]
    if not combo_csvs:
        raise HTTPException(status_code=404, detail="No combos found for this job.")

    combos: List[Dict[str, Any]] = []
    for fname in combo_csvs:
        label_safe = fname[:-4]
        try:
            tdf = pd.read_csv(os.path.join(trades_dir, fname))
        except Exception as exc:
            logger.warning("[WOW_MOM] read failed %s: %s", fname, exc)
            continue
        try:
            cleaned, has_midcap = build_cleaned_for_combo(
                tdf, midcap_legs, midcap_sa, midcap_sym,
                patchwise=patchwise,
                filter_segments=filter_segments,
            )
        except Exception as exc:
            logger.warning("[WOW_MOM] cleaned build failed %s: %s", fname, exc)
            continue
        cc = cols_by_safe.get(label_safe) or {}
        strike_disp = _wm_strike_display(cc)
        adj_label = _wm_adj_display(cc.get("spot_adjustment"))
        row_key = "|".join([strike_disp, str(cc.get("expiry") or ""), str(cc.get("shifting") or "")])
        combos.append({
            "title": f"{strike_disp} | {adj_label}" if cc else label_by_safe.get(label_safe, label_safe),
            "cleaned": cleaned,
            "has_midcap": has_midcap,
            "adj_key": str(cc.get("spot_adjustment") or adj_label),
            "adj_label": adj_label,
            "row_key": row_key,
            "row_sort": _wm_strike_sort(cc),
        })

    wb = Workbook()
    wb.remove(wb.active)
    if not write_merged_wow_mom(wb, combos):
        raise HTTPException(status_code=400, detail="No WOW/MOM data (combos produced 0 trades).")

    # Save to disk cache so subsequent clicks are instant.
    try:
        tmp_path = cache_path + ".building"
        wb.save(tmp_path)
        os.replace(tmp_path, cache_path)
    except Exception as _ce:
        logger.warning("[WOW_MOM] cache write failed for %s: %s", job_id[:8], _ce)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{fname_out}"',
            "X-Filename": fname_out,
        },
    )


@router.get("/optimize/jobs/{job_id}/combo/{combo_id}/tradesheet")
async def download_combo_tradesheet(job_id: str, combo_id: int):
    """
    Return the tradesheet CSV for a single combination, looked up by combo_id.
    The CSV is fetched on-demand — no re-run needed, file was written during the job.
    """
    meta = result_store.get_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")

    row = result_store.get_combo_by_id(job_id, combo_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Combo {combo_id} not found in job results")

    combo_label_safe = row.get("combo_label_safe") or ""
    if not combo_label_safe:
        raise HTTPException(status_code=404, detail="Tradesheet not available for this combo")

    trades_dir = result_store.get_trades_dir(job_id)
    csv_path = os.path.join(trades_dir, f"{combo_label_safe}.csv")
    # Validate path stays within trades_dir (prevent traversal)
    if not os.path.abspath(csv_path).startswith(os.path.abspath(trades_dir)):
        raise HTTPException(status_code=400, detail="Invalid combo label")
    if not os.path.isfile(csv_path):
        raise HTTPException(status_code=404, detail="Tradesheet file not found on disk")

    # On-demand MAE/MFE compute (cached to disk after first download).
    # Worker skips MAE/MFE during the optim run (OPTIMIZE_SKIP_MAE_MFE=1) so
    # combos finish in ~200ms each. The full MAE/MFE compute happens here
    # only for combos the user actually downloads — ~5s first time, instant
    # on subsequent downloads since we cache by writing back to the CSV.
    try:
        _index_str = (meta.get("base_payload", {}) or {}).get("index") or "NIFTY"
        await asyncio.get_event_loop().run_in_executor(
            None, _enrich_tradesheet_with_mae_mfe, csv_path, _index_str
        )
    except Exception as _exc:
        logger.warning("[OPTIM_DL] enrich step skipped for combo %d: %s", combo_id, _exc)

    filename = f"combo_{combo_id}_{combo_label_safe[:60]}.csv"

    def _iter_file():
        with open(csv_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/optimize/jobs/{job_id}/summary")
async def get_optim_summary(job_id: str, patchwise: bool = Query(False)):
    """
    Per-combo master-summary metrics for the user-selected download method.

      • overall  (default) → the stored (overall-DD) summaries, as shown in the table.
      • patchwise           → metrics RECOMPUTED with the equity chain reset per patch,
                              using the exact same compute_xlsx_summary_metrics that
                              builds the patchwise ZIP's summary.csv — so the "summary
                              excel of all optims" matches the patchwise tradesheets.

    The frontend gates this behind a ZIP download, so the on-disk combo CSVs are
    already MAE/MFE-enriched by the time a patchwise summary is requested.
    """
    meta = result_store.get_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")

    all_results = result_store.get_all_results(job_id)
    if not patchwise:
        return {"rows": [
            {"combo_id": r.get("combo_id"), "summary": r.get("summary") or {}}
            for r in all_results
        ]}

    # Serve pre-built patchwise summary from disk if available (worker builds it
    # right after sweep completes so this is normally instant).
    _pw_cache = result_store.patchwise_summary_cache_path(job_id)
    if os.path.isfile(_pw_cache):
        import json as _json
        try:
            with open(_pw_cache) as _fh:
                return _json.load(_fh)
        except Exception:
            pass  # fall through to recompute

    import pandas as pd
    from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _cmetrics

    base_payload = meta.get("base_payload") or {}
    _midcap_legs = base_payload.get("midcap_legs") or None
    _midcap_sa   = base_payload.get("midcap_spot_adjustment") or None
    _midcap_sym  = (
        (_midcap_legs[0].get("symbol") if (_midcap_legs and isinstance(_midcap_legs[0], dict)) else None)
        or "NIFTYMIDCAP100"
    )
    _filter_segments = base_payload.get("filter_segments") or None
    trades_dir = result_store.get_trades_dir(job_id)

    def _build():
        out = []
        for r in all_results:
            _ls = r.get("combo_label_safe") or ""
            _stored = r.get("summary") or {}
            _csv = os.path.join(trades_dir, f"{_ls}.csv")
            if not _ls or not os.path.isfile(_csv):
                out.append({"combo_id": r.get("combo_id"), "summary": _stored})
                continue
            try:
                _df = pd.read_csv(_csv, dtype=str)
                _m = _cmetrics(
                    _df, _stored,
                    midcap_legs=_midcap_legs, midcap_spot_adjustment=_midcap_sa,
                    midcap_symbol=_midcap_sym, patchwise=True,
                    filter_segments=_filter_segments,
                )
                out.append({"combo_id": r.get("combo_id"), "summary": {**_stored, **_m}})
            except Exception as _e:
                logger.warning("[OPTIM_DL] patchwise summary skipped for %s: %s", _ls, _e)
                out.append({"combo_id": r.get("combo_id"), "summary": _stored})
        return out

    rows = await asyncio.get_event_loop().run_in_executor(None, _build)
    return {"rows": rows}


@router.delete("/optimize/jobs/{job_id}")
async def cancel_optimize_job(job_id: str):
    """Revoke the Celery task and drop result data."""
    try:
        celery_app.control.revoke(job_id, terminate=True)
    except Exception as exc:
        logger.warning("Could not revoke %s: %s", job_id, exc)
    # Free the memory-gate reservation immediately so a queued job can start
    # (the TTL would also reclaim it, but cancellation should free it at once).
    try:
        from services import memory_gate
        memory_gate.release(job_id)
    except Exception as exc:
        logger.debug("memory_gate release on cancel failed: %s", exc)
    result_store.delete_job(job_id)
    result_store.delete_job_trades(job_id)
    return {"status": "deleted", "job_id": job_id}
