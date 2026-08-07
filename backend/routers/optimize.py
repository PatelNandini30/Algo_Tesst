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

from services import node_registry
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


@router.get("/system/nodes")
async def get_lan_nodes(request: Request):
    """
    Live LAN remote-worker nodes (see services/node_registry.py, remote-worker/)
    for the frontend's "Core:" picker. "Local (this box)" is always available
    and is NOT included here — it's the implicit default when node_id is unset.

    Each node is flagged `stale` when its code fingerprint (services/code_version.py)
    differs from THIS box's — i.e. it's running a mismatched image. The UI greys
    those out and job submission refuses to route to them, so a remote running
    outdated engine/optimizer code can never silently produce wrong or crashing
    results.
    """
    caller_ip = _client_ip(request)
    try:
        from services.code_version import compute_code_version
        my_version = compute_code_version()
    except Exception:
        my_version = ""
    nodes = node_registry.list_nodes()
    for node in nodes:
        node["is_you"] = node.get("ip") == caller_ip
        nv = node.get("version") or ""
        # Stale only when we can actually compare (both versions known) and they
        # differ. Unknown versions (old worker that never reported one) are left
        # non-stale so the guard fails open rather than blocking everything.
        node["stale"] = bool(my_version and nv and nv != my_version)
    nodes.sort(key=lambda n: (not n.get("is_you"), n.get("ip") or ""))
    return {"nodes": nodes, "server_version": my_version}


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
    from services.maintenance import is_maintenance
    if is_maintenance():
        raise HTTPException(status_code=503, detail="System is under maintenance — optimizations are temporarily disabled. Please try again shortly.")
    body = await request.json()
    origin_ip = _client_ip(request)
    base_payload = _prepared_payload(body.get("base_payload") or {})
    param_specs = body.get("param_specs") or []
    method = (body.get("method") or "exhaustive").lower()
    sample_n = body.get("sample_n")
    objective_name = body.get("objective") or "total_pnl"
    algorithm = body.get("algorithm")
    seed = body.get("seed")
    # Optional LAN remote-worker routing: if the UI's "Core:" picker selected a
    # registered remote node, run this job on that node's dedicated queue
    # instead of the local "optimize" queue. Unset (default) is unchanged
    # local behavior. See services/node_registry.py.
    node_id = body.get("node_id") or None
    node_cpu_cap = os.cpu_count() or 8
    if node_id:
        # Staleness guard: refuse to route to a remote worker running a different
        # code version than this box (mismatched image) — it would silently
        # produce wrong or crashing results. See services/code_version.py.
        if node_registry.is_stale(node_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Worker {node_id} is running an outdated version and can't "
                    "run this job. Update that PC's remote-worker image, or pick "
                    "a different worker / Local."
                ),
            )
        node = node_registry.get_node(node_id)
        if node and node.get("cpu_count"):
            node_cpu_cap = int(node["cpu_count"])
    # Parallelism: user override from UI. Capped between 1 and the target
    # node's own core count (local box's os.cpu_count() when no node is
    # selected) to prevent thrashing. Unset → runner falls back to
    # OPTIMIZE_PARALLELISM env / cpu//2 default.
    parallelism_raw = body.get("parallelism")
    parallelism: Optional[int] = None
    if parallelism_raw is not None:
        try:
            parallelism = max(1, min(int(parallelism_raw), node_cpu_cap))
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
        "node_id": node_id,
        # User opt-in (Optimize panel "auto-download" toggle) — see
        # services/optimizer/runner.py init_job() for why this gates adoption
        # into any browser's AutoDownloadQueue instead of every job.
        "auto_download": bool(body.get("auto_download")),
    }
    logger.info(
        "[OPTIM] enqueue resolved download_mode=%s index=%s date=%s..%s auto_download=%s",
        (base_payload.get("download_mode") or "patchwise"),
        base_payload.get("index") or base_payload.get("symbol") or "NIFTY",
        base_payload.get("from_date") or base_payload.get("date_from") or "",
        base_payload.get("to_date") or base_payload.get("date_to") or "",
        bool(body.get("auto_download")),
    )
    queue_name = f"optimize@{node_id}" if node_id else "optimize"
    task = run_optimize_job.apply_async(args=[spec], queue=queue_name)
    if node_id:
        node_registry.record_job_node(task.id, node_id)
    logger.info(
        "[OPTIM] queued job %s from ip=%s objective=%s method=%s queue=%s",
        task.id[:8], origin_ip, objective_name, method, queue_name,
    )
    return {
        "status": "queued",
        "job_id": task.id,
        "total_combos": total,
        "objective": objective_name,
        "method": method,
        "queue": queue_name,
    }


@router.post("/optimize/jobs/{job_id}/resume")
async def resume_optimize_job(job_id: str):
    """Continue an interrupted sweep instead of recomputing it.

    Re-enqueues the ORIGINAL spec under the SAME job_id, with `resume` set. The
    runner then matches the expanded grid against the result rows already stored
    and dispatches only the combos still missing — the per-combo CSV/XLSX/wm
    files from the first run stay exactly where they are and are reused by the
    ZIP fast path.

    Reusing the job_id is what makes this cheap (no copying between trades dirs),
    and it is safe because the stored spec is replayed verbatim: same payload,
    same param_specs, same expansion order. A job whose combos are all present
    is not re-run — it is simply finalized.
    """
    meta = result_store.get_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown optimize job {job_id}")
    if meta.get("status") == "running":
        # A job killed mid-sweep (worker restart, cgroup OOM, crash) is left
        # frozen at status=running — which is PRECISELY when resume is wanted.
        # So refuse only if it is genuinely alive: still registered in the
        # live-optim set, or still heartbeating. Registry entries self-expire
        # (_ACTIVE_STALE_SEC) and the heartbeat stops the moment the process
        # dies, so a dead job clears this check on its own.
        alive = result_store.is_active_optim(job_id, meta.get("node_id"))
        if not alive:
            try:
                alive = (time.time() - float(meta.get("last_progress_at") or 0)) < 120
            except (TypeError, ValueError):
                alive = False
        if alive:
            raise HTTPException(
                status_code=409,
                detail="Job is still running — cancel it first or wait")
        logger.info("[OPTIM] resume: job %s is stale-running (no live worker) — allowing",
                    job_id[:8])

    base_payload = meta.get("base_payload") or {}
    param_specs = meta.get("param_specs") or []
    if not param_specs:
        raise HTTPException(
            status_code=400,
            detail="Job has no stored param_specs — cannot resume; resubmit it instead",
        )

    done_rows = len(result_store.get_all_results_raw(job_id))
    spec = {
        "base_payload": base_payload,
        "param_specs": param_specs,
        "method": meta.get("method") or "exhaustive",
        "sample_n": meta.get("sample_n"),
        "objective": meta.get("objective") or "total_pnl",
        "algorithm": meta.get("algorithm"),
        "seed": meta.get("seed"),
        "parallelism": meta.get("parallelism"),
        "zip_naming": meta.get("zip_naming") or None,
        "client_ip": meta.get("client_ip"),
        "node_id": meta.get("node_id"),
        "auto_download": bool(meta.get("auto_download")),
        "resume": True,
    }
    node_id = meta.get("node_id")
    queue_name = f"optimize@{node_id}" if node_id else "optimize"
    # Same task id as the original job so job_id, trades dir and result list all
    # continue to line up.
    run_optimize_job.apply_async(args=[spec], queue=queue_name, task_id=job_id)
    logger.info("[OPTIM] resume queued job %s (%d combos already done)",
                job_id[:8], done_rows)
    return {
        "status": "queued",
        "job_id": job_id,
        "already_done": done_rows,
        "total_combos": meta.get("total"),
        "queue": queue_name,
    }


@router.get("/optimize/jobs")
async def list_optimize_jobs(limit: int = Query(200, ge=1, le=500)):
    """List every known optimize job (any machine, any browser) with just
    enough info for a browser that did NOT enqueue a job to still discover and
    auto-download it by job_id — powers AutoDownloadQueue's system-wide poll
    (frontend/src/components/AutoDownloadQueue.jsx) so results follow the job,
    not the tab/PC that started it. Deliberately excludes bulky fields
    (results rows) — callers already have dedicated endpoints for those.
    """
    jobs = result_store.list_recent_jobs(limit=limit)
    return {
        "jobs": [
            {
                "job_id": j.get("job_id"),
                "status": j.get("status"),
                "done": j.get("done"),
                "total": j.get("total"),
                "objective": j.get("objective"),
                "method": j.get("method"),
                "sample_n": j.get("sample_n"),
                "algorithm": j.get("algorithm"),
                "started_at": j.get("started_at"),
                "zip_naming": j.get("zip_naming"),
                "base_payload": j.get("base_payload"),
                "param_specs": j.get("param_specs"),
                "auto_download": j.get("auto_download", False),
            }
            # Only surface jobs the user explicitly opted into auto-download
            # for (Optimize panel toggle) — this endpoint's sole consumer is
            # AutoDownloadQueue's cross-tab/cross-PC discovery poll, so
            # filtering here (not just client-side) is what actually enforces
            # "don't show this until I turn the toggle on".
            for j in jobs
            if j.get("auto_download")
        ]
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
        # The Celery task may still be pending in the queue, OR — the race this
        # branch must not misdiagnose — it may have just been picked up by a
        # worker: run_optimize_job calls self.update_state(state='PROCESSING')
        # BEFORE run_optimization()/init_job() has written the Redis meta a
        # moment later. A poll landing in that ~1-3s startup window must NOT
        # be reported as "unknown" (client code treats unknown as terminal —
        # "job was cancelled" — and permanently stops polling a job that is
        # actually just starting normally and about to succeed). Only report
        # "unknown" for states that mean the task is truly gone without ever
        # having written meta (revoked, or an odd finished-with-no-meta case).
        if celery_state in (None, "PENDING", "STARTED", "PROCESSING", "RETRY"):
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
    # Multi-index combos ("Group Index" column) already have correct per-leg-
    # symbol MAE/MFE from run_multi_index_feature — never run them through
    # this blanket index_str recompute. See [[multi-index-fut-mae-mfe-scale-bug]].
    if "Group Index" in trades_df.columns:
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
     midcap_legs, midcap_sa, midcap_sym, filter_name, patchwise, filter_segments,
     yearly, rules_sheet) = args
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
            # Without this WOW buckets by Expiry, and a YEARLY leg has ONE
            # expiry — the whole run collapses into that contract's ISO week
            # while MOM (by Exit Date) still spans every year.
            yearly=yearly,
            # Leg-wise "Rules" first sheet for this combo (identical to backtest).
            rules_sheet=rules_sheet,
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
        _is_yearly = str(base_payload.get("expiry_type") or "").upper() == "YEARLY"

        # Per-combo leg-wise "Rules" sheet — rebuild each combo's merged payload
        # from base_payload + that combo's swept values, then the SAME rows the
        # backtest download shows. Reconstructed here (rather than reusing the
        # run-time file) because the ZIP builds each combo xlsx fresh from the CSV.
        from services.optimizer.param_expander import apply_combo_for_optim as _apply_combo
        from services.optimizer.rules_sheet import build_rules_sheet as _brs
        build_args = []
        for fname in combo_csvs:
            label_safe = fname[:-4]
            row = summary_by_label.get(label_safe, {})
            try:
                _merged = _apply_combo(base_payload, row.get("combo") or {})
                _rs = _brs(_merged, _filter_name)
            except Exception:
                _rs = None
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
                _is_yearly,
                _rs,
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
            # ZIP is Excel-only — summary.csv/run_config.csv are intentionally
            # NOT included (the master summary is a separate Export-XLSX
            # download; these were leaking into every downloaded ZIP, unwanted).

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


def _download_base_for_job(job_id: str) -> str:
    """Base URL the browser should hit to download a job's on-disk artifacts
    (ZIP / WOW-MOM / per-combo tradesheets). Those files live on the disk of
    whichever worker RAN the job. For a job that ran on a LAN remote node, that's
    the remote PC's own API (remote-worker/'s remote-api container, port
    NODE_API_PORT) — the main box never has those files. Empty string = this box
    ran it (use same-origin relative URLs, unchanged behavior)."""
    try:
        node = node_registry.get_job_node(job_id)
        if node:
            port = os.environ.get("NODE_API_PORT", "8100")
            return f"http://{node}:{port}"
    except Exception:
        pass
    return ""


@router.get("/optimize/jobs/{job_id}/download-base")
async def get_download_base(job_id: str):
    """Where the frontend should fetch this job's file downloads from (its own
    remote node, or same-origin for local jobs). See _download_base_for_job."""
    return {"download_base": _download_base_for_job(job_id)}


@router.get("/optimize/jobs/{job_id}/tradesheets.zip")
async def download_tradesheets_zip(job_id: str, patchwise: bool = Query(True)):
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
        _pw_suffix = "_patchwise" if patchwise else "_overall"
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


@router.post("/optimize/jobs/{job_id}/summary.xlsx")
async def download_optimization_summary(job_id: str, request: Request):
    """Build the "Optimization Summary" workbook on the BACKEND.

    This replaces the ExcelJS builder that lived in the frontend
    (optimSummaryExport.js), so every .xlsx the product emits now comes from one
    place — openpyxl, server-side:
        tradesheet -> excel_builder.build_combo_xlsx
        WOW & MOM  -> wow_mom.write_merged_wow_mom
        this sheet -> summary_workbook.build_summary_workbook

    The caller POSTs {rows, rule_rows}: `rows` are the per-combo results it already
    holds and `rule_rows` is the Rules block it already derived from the sweep config.
    Re-deriving those here would mean a SECOND implementation of the sweep-label logic,
    which is the exact duplication this consolidation removes — so the split is
    deliberate: the caller supplies the data, this owns the workbook.
    """
    from services.optimizer.summary_workbook import build_summary_workbook

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="`rows` must be a non-empty list")
    rule_rows = payload.get("rule_rows") or []
    rules_sheet = payload.get("rules_sheet") or None
    try:
        xlsx = build_summary_workbook(rows, rule_rows, rules_sheet=rules_sheet)
    except Exception as exc:
        logger.exception("[OPTIM] summary workbook build failed for %s", job_id)
        raise HTTPException(status_code=500, detail=f"summary build failed: {exc}")
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="optimize_summary.xlsx"'},
    )


@router.get("/optimize/jobs/{job_id}/wow_mom.xlsx")
async def download_wow_mom(job_id: str, patchwise: bool = Query(True)):
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
    from services.optimizer.wow_mom import (
        write_merged_wow_mom, variant_labels, adj_label_from_combo_label,
    )

    meta = result_store.get_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")
    if meta.get("status") != "success":
        raise HTTPException(status_code=400, detail=f"Job is not complete (status: {meta.get('status')})")

    zip_naming = meta.get("zip_naming") or {}
    lvl1 = _safe_zip_name(zip_naming.get("level1", "")) if zip_naming else ""
    _pw_suffix = "_patchwise" if patchwise else "_overall"
    fname_out = f"{lvl1}{_pw_suffix}_WOW_MOM.xlsx" if lvl1 else f"optimize_{job_id[:8]}{_pw_suffix}_WOW_MOM.xlsx"

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
    _wm_yearly = str(base_payload.get("expiry_type") or "").upper() == "YEARLY"

    label_by_safe: Dict[str, str] = {}
    cols_by_safe: Dict[str, Dict[str, Any]] = {}
    combo_by_safe: Dict[str, Dict[str, Any]] = {}
    # RAW (un-deduped) — see result_store.get_all_results_raw. A deduped read
    # drops rows whose (combo_label, total_pnl) matched an earlier combo, but
    # their tradesheet CSVs are still on disk, so those combos lost their
    # metadata and fell into a second, misaligned column group.
    for row in result_store.get_all_results_raw(job_id):
        cls = row.get("combo_label_safe")
        if cls:
            label_by_safe[cls] = row.get("combo_label") or cls
            cols_by_safe[cls] = row.get("combo_columns") or {}
            combo_by_safe[cls] = row.get("combo") or {}
    variant_by_safe = variant_labels(combo_by_safe)

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
        # Strategy-level knob off → fall back to the PER-LEG adjustment, which
        # label_combo writes into combo_label but not into combo_columns.
        if adj_label == "No Adj":
            adj_label = (adj_label_from_combo_label(label_by_safe.get(label_safe, ""))
                         or adj_label)
        row_key = "|".join([strike_disp, str(cc.get("expiry") or ""), str(cc.get("shifting") or "")])
        combos.append({
            "title": f"{strike_disp} | {adj_label}" if cc else label_by_safe.get(label_safe, label_safe),
            "cleaned": cleaned,
            "has_midcap": has_midcap,
            # Key on the DISPLAY label, never the raw value: "NoAdjustment" and
            # "No Adj" mean the same thing and must share one column.
            "adj_key": adj_label,
            "adj_label": adj_label,
            "row_key": row_key,
            "row_sort": _wm_strike_sort(cc),
            "variant_label": variant_by_safe.get(label_safe, ""),
            "yearly": _wm_yearly,
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


@router.get("/optimize/jobs/{job_id}/combo/{combo_id}/tradesheet.xlsx")
async def download_combo_tradesheet_xlsx(
    job_id: str, combo_id: int, patchwise: bool = Query(False)
):
    """
    Return the styled XLSX tradesheet for a single combination — the EXACT same
    workbook that goes into the ZIP (built by excel_builder.build_combo_xlsx,
    openpyxl). Serves the pre-built on-disk file when present (byte-identical to
    the ZIP entry); otherwise builds it on-demand via the same code path the
    sweep uses (result_store.write_combo_xlsx), caches it to disk, and serves it.

    ?patchwise=true serves the per-patch-reset variant (patchwise/ subdir),
    matching the patchwise ZIP; default false = overall.
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
    # Discard per-combo XLSX built by an older builder before deciding whether to
    # serve from disk — otherwise a formatting change never reaches the user.
    result_store.ensure_xlsx_version(job_id)
    if patchwise:
        xlsx_path = os.path.join(trades_dir, "patchwise", f"{combo_label_safe}.xlsx")
    else:
        xlsx_path = os.path.join(trades_dir, f"{combo_label_safe}.xlsx")
    # Path-traversal guard (combo_label_safe is sanitized at write time, re-check).
    if not os.path.abspath(xlsx_path).startswith(os.path.abspath(trades_dir)):
        raise HTTPException(status_code=400, detail="Invalid combo label")

    # Build on-demand if the pre-built file isn't on disk (e.g. cache evicted or
    # this combo wasn't inline-finalized). Uses the SAME builder the ZIP uses so
    # the output is identical to the ZIP entry.
    if not os.path.isfile(xlsx_path):
        csv_path = os.path.join(trades_dir, f"{combo_label_safe}.csv")
        if not os.path.isfile(csv_path):
            raise HTTPException(status_code=404, detail="Tradesheet file not found on disk")

        def _build() -> None:
            import pandas as _pd
            base_payload = meta.get("base_payload") or {}
            index_str = base_payload.get("index") or "NIFTY"
            # Enrich MAE/MFE into the CSV first (idempotent), same as the CSV endpoint.
            try:
                _enrich_tradesheet_with_mae_mfe(csv_path, index_str)
            except Exception as _exc:
                logger.warning("[OPTIM_DL] xlsx enrich skipped for combo %d: %s", combo_id, _exc)
            tdf = _pd.read_csv(csv_path)
            # Coerce dtypes the builder expects (CSV is all strings on read).
            for _c in ("Strike", "Entry Price", "Entry Spot", "Exit Price", "Exit Spot",
                       "Cumulative", "Peak", "DD", "%DD", "Net P&L", "% P&L", "MAE", "MFE"):
                if _c in tdf.columns:
                    tdf[_c] = _pd.to_numeric(tdf[_c], errors="coerce")
            midcap_legs = base_payload.get("midcap_legs") or None
            midcap_sa   = base_payload.get("midcap_spot_adjustment") or None
            midcap_sym  = (
                (midcap_legs[0].get("symbol") if (midcap_legs and isinstance(midcap_legs[0], dict)) else None)
                or "NIFTYMIDCAP100"
            )
            filter_segments = base_payload.get("filter_segments") or None
            from_date = base_payload.get("date_from") or base_payload.get("from_date") or ""
            to_date   = base_payload.get("date_to") or base_payload.get("to_date") or ""
            combo_label = row.get("combo_label") or combo_label_safe
            # trading_days for the internal MAE/MFE enrich (already enriched above,
            # but pass so the builder's own enrich is a no-op / consistent).
            try:
                _ohlc_pd, trading_days = _get_ohlc_pandas_for_index(index_str)
            except Exception:
                trading_days = None
            # Leg-wise "Rules" first sheet for this combo — rebuild the combo's
            # merged payload and render the SAME rows the backtest download shows.
            try:
                from services.optimizer.param_expander import apply_combo_for_optim as _apply
                from services.optimizer.rules_sheet import build_rules_sheet as _brs
                _merged = _apply(base_payload, row.get("combo") or {})
                _dl_filter_name = (meta.get("zip_naming") or {}).get("level1") or ""
                _dl_rules_sheet = _brs(_merged, _dl_filter_name)
            except Exception:
                _dl_rules_sheet = None
            _dl_yearly = str(base_payload.get("expiry_type") or "").upper() == "YEARLY"
            if patchwise:
                zip_naming = meta.get("zip_naming") or {}
                filter_name = (zip_naming.get("level1") or "") if zip_naming else ""
                result_store.write_combo_xlsx_patchwise(
                    job_id, combo_label_safe, tdf, row.get("summary") or {},
                    combo_label=combo_label, from_date=from_date, to_date=to_date,
                    index_str=index_str, trading_days=trading_days,
                    midcap_legs=midcap_legs, midcap_spot_adjustment=midcap_sa,
                    midcap_symbol=midcap_sym, filter_name=filter_name,
                    filter_segments=filter_segments,
                    yearly=_dl_yearly,
                    rules_sheet=_dl_rules_sheet,
                )
            else:
                result_store.write_combo_xlsx(
                    job_id, combo_label_safe, tdf, row.get("summary") or {},
                    combo_label=combo_label, from_date=from_date, to_date=to_date,
                    index_str=index_str, trading_days=trading_days,
                    midcap_legs=midcap_legs, midcap_spot_adjustment=midcap_sa,
                    midcap_symbol=midcap_sym, filter_segments=filter_segments,
                    yearly=_dl_yearly,
                    rules_sheet=_dl_rules_sheet,
                )

        await asyncio.get_event_loop().run_in_executor(None, _build)

    if not os.path.isfile(xlsx_path):
        raise HTTPException(status_code=500, detail="Failed to build tradesheet XLSX")

    _pw = "_patchwise" if patchwise else ""
    filename = f"combo_{combo_id}_{combo_label_safe[:60]}{_pw}.xlsx"
    return FileResponse(
        xlsx_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        headers={"X-Filename": filename},
    )


@router.get("/optimize/jobs/{job_id}/summary")
async def get_optim_summary(job_id: str, patchwise: bool = Query(True)):
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

    # Patchwise metrics are computed inline in the worker (row["summary_pw"]) and
    # stored per combo, so the master summary can be served straight from Redis —
    # no disk/cache dependency, no silent fallback to the OVERALL summary. We do
    # NOT serve the old one-shot patchwise-summary JSON cache: it was written once
    # and never invalidated, so a stale/overall copy could survive across runs.
    # A CSV recompute is used only as a last resort for combos that somehow lack
    # an inline patchwise summary (e.g. inline finalize was disabled/failed).
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
        import pandas as pd
        from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _cmetrics
        out = []
        for r in all_results:
            _stored = r.get("summary") or {}
            # Fast path: patchwise metrics already computed inline in the worker.
            _inline_pw = r.get("summary_pw")
            if _inline_pw is not None:
                out.append({"combo_id": r.get("combo_id"), "summary": {**_stored, **_inline_pw}})
                continue
            # Fallback: recompute patchwise metrics from the on-disk tradesheet.
            _ls = r.get("combo_label_safe") or ""
            _csv = os.path.join(trades_dir, f"{_ls}.csv")
            if not _ls or not os.path.isfile(_csv):
                logger.warning(
                    "[OPTIM_DL] no inline patchwise summary and no CSV for %s; "
                    "serving OVERALL summary for this combo", _ls or "<unlabeled>",
                )
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
async def cancel_optimize_job(job_id: str, only_if_active: bool = Query(False)):
    """Revoke the Celery task and drop result data.

    only_if_active=true is for the frontend's "abandon on tab-close/navigate"
    cleanup (OptimizationResults.jsx's pagehide handler) — it must NOT wipe a
    job's results if the job actually already finished by the time this
    fires. The frontend's own "skip if already succeeded" check runs against
    React state that only updates on the next ~1.5s poll tick, so there's a
    real window where the job is done server-side but the client doesn't
    know it yet — closing the tab in that window used to delete a job's
    trade files (including what WOW/MOM is built from) right after it
    finished. Checking status here, server-side, is authoritative and has no
    polling lag, so that race can't happen. The explicit user-facing
    "delete this job" button does not set this flag — it always deletes,
    same as before.
    """
    if only_if_active:
        _meta = result_store.get_meta(job_id)
        if _meta and _meta.get("status") in ("success", "failed"):
            return {"status": "kept", "job_id": job_id, "reason": f"job already {_meta.get('status')}"}
    try:
        celery_app.control.revoke(job_id, terminate=True)
    except Exception as exc:
        logger.warning("Could not revoke %s: %s", job_id, exc)
    # Free the memory-gate reservation immediately so a queued job can start
    # (the TTL would also reclaim it, but cancellation should free it at once).
    try:
        from services import memory_gate, node_registry
        _node = node_registry.get_job_node(job_id)
        memory_gate.release(job_id, node_id=_node)
        # ...and drop it from the LIVE-OPTIM registry in the same breath. Releasing
        # only the reservation left the cancelled job counted as a live claimant
        # until the 5-minute stale sweep, so every RUNNING sweep kept dividing the
        # box by a job that no longer existed and stayed pinned at a narrower fork
        # width for minutes after the cancel (observed: P=1 with 4.1 GB free).
        result_store.unregister_active_optim(job_id, _node)
    except Exception as exc:
        logger.debug("memory_gate release on cancel failed: %s", exc)
    result_store.delete_job(job_id)
    result_store.delete_job_trades(job_id)
    return {"status": "deleted", "job_id": job_id}
