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
import threading
import time
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
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
async def enqueue_optimization(request: Dict[str, Any]):
    """Validate and enqueue the run. Returns a Celery job_id."""
    base_payload = _prepared_payload(request.get("base_payload") or {})
    param_specs = request.get("param_specs") or []
    method = (request.get("method") or "exhaustive").lower()
    sample_n = request.get("sample_n")
    objective_name = request.get("objective") or "total_pnl"
    algorithm = request.get("algorithm")
    seed = request.get("seed")
    # Parallelism: user override from UI. Capped between 1 and cpu_count to
    # prevent thrashing. Unset → runner falls back to OPTIMIZE_PARALLELISM
    # env / cpu//2 default.
    parallelism_raw = request.get("parallelism")
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
    }
    task = run_optimize_job.apply_async(args=[spec], queue="optimize")
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


# ── Background ZIP builder ───────────────────────────────────────────────────
# Building 100+ XLSX tradesheets takes minutes — far longer than a browser HTTP
# timeout (~60s).  The endpoint kicks off a background build, returns 202 with
# progress, and the frontend polls until the ZIP is ready on disk.  Subsequent
# downloads stream from disk instantly.

_ZIP_CACHE_DIR = "/data/cache/optim_zips"
_ZIP_BUILDER_VERSION = "v2"
_zip_build_state: Dict[str, Dict[str, Any]] = {}
_zip_build_lock = threading.Lock()


def _zip_cache_path(job_id: str) -> str:
    os.makedirs(_ZIP_CACHE_DIR, exist_ok=True)
    return os.path.join(_ZIP_CACHE_DIR, f"{job_id}.{_ZIP_BUILDER_VERSION}.zip")


def _build_one_xlsx(args: tuple) -> tuple:
    """Worker function for ProcessPoolExecutor.

    Returns (label_safe, xlsx_bytes) or (label_safe, None) on failure.
    Top-level so it's picklable.
    """
    fpath, label_safe, combo_summary, combo_label, from_date, to_date = args
    try:
        import pandas as _pd
        from services.optimizer.excel_builder import build_combo_xlsx as _build
        trades_df = _pd.read_csv(fpath, dtype=str)
        xlsx_bytes = _build(
            trades_df, combo_summary,
            combo_label=combo_label,
            from_date=from_date, to_date=to_date,
        )
        return (label_safe, xlsx_bytes, None)
    except Exception as exc:
        return (label_safe, None, str(exc))


def _build_zip_blocking(job_id: str) -> None:
    """Build the ZIP file on disk.  Updates progress state as it goes."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import pandas as pd

    state = _zip_build_state[job_id]
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

        files = sorted(os.listdir(trades_dir))
        csv_files = [f for f in files if f.endswith(".csv")]
        _root_files = {"summary.csv", "run_config.csv"}

        combo_csvs = [f for f in csv_files if f not in _root_files]
        root_csvs  = [f for f in csv_files if f in _root_files]

        state["total"] = len(combo_csvs)
        state["done"]  = 0

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
            ))

        # Write directly to disk — atomic move at end so partial files never
        # appear cached.
        out_path = _zip_cache_path(job_id)
        tmp_path = out_path + ".building"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        # Tune workers: 4-6 is a sweet spot for CPU-bound XLSX builds without
        # starving the host (Granian + 2 worker containers also need CPU).
        max_workers = min(6, max(2, (os.cpu_count() or 4) - 1))

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
            # Root CSVs first — these are tiny and don't need parallel build.
            for fname in root_csvs:
                zf.write(os.path.join(trades_dir, fname), fname)

            if build_args:
                with ProcessPoolExecutor(max_workers=max_workers) as pool:
                    futures = {pool.submit(_build_one_xlsx, a): a[1] for a in build_args}
                    for fut in as_completed(futures):
                        label_safe = futures[fut]
                        try:
                            ls, xlsx_bytes, err = fut.result()
                        except Exception as exc:
                            xlsx_bytes, err = None, str(exc)
                        if xlsx_bytes is not None:
                            zf.writestr(f"tradesheets/{label_safe}.xlsx", xlsx_bytes)
                        else:
                            logger.warning("[ZIP] XLSX failed for %s: %s — using CSV",
                                           label_safe, err)
                            csv_path = os.path.join(trades_dir, f"{label_safe}.csv")
                            if os.path.isfile(csv_path):
                                zf.write(csv_path, f"tradesheets/{label_safe}.csv")
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
async def download_tradesheets_zip(job_id: str):
    """
    Returns the tradesheets ZIP, building it on first request.

      • 200 + ZIP file       — when the ZIP is ready on disk
      • 202 + progress JSON  — while a background build is in progress
      • 4xx                  — job not found / not complete / no trades
    """
    # Disk-cache shortcut: if the ZIP already exists, serve it directly even
    # when Redis meta has expired (TTL) or been wiped (e.g., compose down).
    # The presence of the file itself is proof the job ran to completion.
    cache_path = _zip_cache_path(job_id)
    if os.path.isfile(cache_path):
        filename = f"optimize_{job_id[:8]}_tradesheets.zip"
        return FileResponse(
            cache_path,
            media_type="application/zip",
            filename=filename,
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

    # Kick off / report background build
    with _zip_build_lock:
        state = _zip_build_state.get(job_id)
        if state is None or state.get("status") in ("error",):
            state = {
                "status": "building",
                "done": 0,
                "total": 0,
                "started_at": time.time(),
                "error": None,
            }
            _zip_build_state[job_id] = state
            thread = threading.Thread(
                target=_build_zip_blocking, args=(job_id,), daemon=True,
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


@router.delete("/optimize/jobs/{job_id}")
async def cancel_optimize_job(job_id: str):
    """Revoke the Celery task and drop result data."""
    try:
        celery_app.control.revoke(job_id, terminate=True)
    except Exception as exc:
        logger.warning("Could not revoke %s: %s", job_id, exc)
    result_store.delete_job(job_id)
    result_store.delete_job_trades(job_id)
    return {"status": "deleted", "job_id": job_id}
