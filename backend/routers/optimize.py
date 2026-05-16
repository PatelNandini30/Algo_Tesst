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

import io
import logging
import os
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

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
    rows = result_store.get_results(
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
        "rows": rows,
        "meta": meta,
    }


@router.get("/optimize/jobs/{job_id}/tradesheets.zip")
async def download_tradesheets_zip(job_id: str):
    """
    Stream a ZIP containing:
      - summary.csv         — one row per combo with all metrics
      - tradesheets/*.csv   — per-combo tradesheet (one file per combo)

    Only available after the job has status='success'.
    Tradesheets are written to disk during the optimize run; this endpoint
    just zips and returns them — no backtest re-run needed.
    """
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

    files = sorted(os.listdir(trades_dir))
    csv_files = [f for f in files if f.endswith(".csv")]
    if not csv_files:
        raise HTTPException(status_code=404, detail="No tradesheet files found.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in csv_files:
            fpath = os.path.join(trades_dir, fname)
            arcname = "summary.csv" if fname == "summary.csv" else f"tradesheets/{fname}"
            zf.write(fpath, arcname)
    buf.seek(0)

    filename = f"optimize_{job_id[:8]}_tradesheets.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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
