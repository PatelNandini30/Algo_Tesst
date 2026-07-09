"""
Persist optimization results to Redis with optional Parquet overflow.

Key layout:

    optim:{job_id}:meta             — JSON {status, total, done, started_at,
                                              objective, method, eta_seconds, error}
    optim:{job_id}:results          — list of compact JSON rows, one per combo
    optim:{job_id}:parquet_path     — set if results were spilled to disk

TTL: 24 hours by default.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional

import redis

logger = logging.getLogger(__name__)

# Redis connection. Historically read from REDIS_HOST/REDIS_PORT (defaulting to
# the Docker service name "redis"), while celery/memory_gate use REDIS_URL. On a
# LAN remote worker (remote-worker/) the "redis" name doesn't resolve, so if
# REDIS_HOST isn't explicitly set we parse REDIS_URL instead — one source of
# truth, and no silent "[OPTIM_STORE] Redis unavailable" on remote nodes. Main
# box is unaffected: it sets REDIS_URL=redis://redis:6379/0, which parses to the
# same host/port the old default produced.
def _resolve_redis_params():
    if os.getenv("REDIS_HOST"):
        return (
            os.getenv("REDIS_HOST"),
            int(os.getenv("REDIS_PORT", "6379")),
            int(os.getenv("REDIS_DB", "0")),
            os.getenv("REDIS_PASSWORD", None),
        )
    url = os.getenv("REDIS_URL")
    if url:
        from urllib.parse import urlparse
        u = urlparse(url)
        db_path = (u.path or "").strip("/")
        return (
            u.hostname or "redis",
            u.port or 6379,
            int(db_path) if db_path.isdigit() else 0,
            u.password,
        )
    return "redis", 6379, 0, None


REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD = _resolve_redis_params()
OPTIM_TTL = int(os.getenv("OPTIMIZE_RESULT_TTL", "86400"))
OPTIM_SPILL_THRESHOLD = int(os.getenv("OPTIMIZE_PARQUET_SPILL_AT", "10000"))
OPTIM_PARQUET_DIR = os.getenv("OPTIMIZE_PARQUET_DIR", "/data/cache/optim_results")

# ── ZIP cache ────────────────────────────────────────────────────────────────
# Shared by runner.py (pre-build) and routers/optimize.py (download) so they
# always agree on the filename. Bump ZIP_BUILDER_VERSION when the XLSX format
# changes and old ZIPs must be invalidated.
ZIP_CACHE_DIR = os.getenv("OPTIMIZE_ZIP_DIR", "/data/cache/optim_zips")
ZIP_BUILDER_VERSION = "v16"


def zip_cache_path(job_id: str, patchwise: bool = False) -> str:
    """Canonical path for a job's pre-built ZIP file."""
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    ver = f"{ZIP_BUILDER_VERSION}-pw" if patchwise else ZIP_BUILDER_VERSION
    return os.path.join(ZIP_CACHE_DIR, f"{job_id}.{ver}.zip")


def wow_mom_cache_path(job_id: str, patchwise: bool = False) -> str:
    """Canonical path for a job's pre-built WOW/MOM XLSX file."""
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    suffix = "-pw" if patchwise else ""
    return os.path.join(ZIP_CACHE_DIR, f"{job_id}.{ZIP_BUILDER_VERSION}-wm{suffix}.xlsx")


def patchwise_summary_cache_path(job_id: str) -> str:
    """Canonical path for a job's pre-built patchwise summary JSON file."""
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    return os.path.join(ZIP_CACHE_DIR, f"{job_id}.{ZIP_BUILDER_VERSION}-pw-summary.json")


_client: Optional[redis.Redis] = None


def _redis() -> Optional[redis.Redis]:
    global _client
    if _client is not None:
        return _client
    try:
        c = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        c.ping()
        _client = c
        return c
    except Exception as e:
        logger.warning("[OPTIM_STORE] Redis unavailable: %s", e)
        return None


def _meta_key(job_id: str) -> str:
    return f"optim:{job_id}:meta"


def _results_key(job_id: str) -> str:
    return f"optim:{job_id}:results"


def _parquet_key(job_id: str) -> str:
    return f"optim:{job_id}:parquet_path"


def init_job(
    job_id: str,
    *,
    total: int,
    method: str,
    objective: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    r = _redis()
    if r is None:
        return
    meta = {
        "status": "running",
        "total": int(total),
        "done": 0,
        "started_at": time.time(),
        # Progress heartbeat — bumped on every combo + every finalization step.
        # The watchdog kills a job only when THIS stops advancing (a real hang),
        # never a job that is still making progress. See services/optimizer/watchdog.py.
        "last_progress_at": time.time(),
        "phase": "starting",
        "method": method,
        "objective": objective,
        "eta_seconds": None,
        "error": None,
    }
    if extra:
        meta.update(extra)
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))
    r.delete(_results_key(job_id))


def update_progress(
    job_id: str,
    *,
    done: int,
    total: Optional[int] = None,
    phase: Optional[str] = None,
) -> None:
    r = _redis()
    if r is None:
        return
    raw = r.get(_meta_key(job_id))
    if not raw:
        return
    meta = json.loads(raw)
    meta["done"] = int(done)
    meta["last_progress_at"] = time.time()
    if total is not None:
        meta["total"] = int(total)
    if phase is not None:
        meta["phase"] = phase
    started = meta.get("started_at") or time.time()
    elapsed = max(time.time() - started, 0.001)
    rate = done / elapsed if done > 0 else 0
    remaining = max(int(meta["total"]) - done, 0)
    meta["eta_seconds"] = int(remaining / rate) if rate > 0 else None
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))


def increment_done(job_id: str) -> None:
    """Atomically increment the running done counter in Redis.

    Called once per completed combo from any worker. The INCR is atomic;
    the meta update is best-effort (acceptable race for progress display).
    """
    r = _redis()
    if r is None:
        return
    counter_key = f"optim:{job_id}:done_counter"
    cnt = r.incr(counter_key)
    r.expire(counter_key, OPTIM_TTL)
    raw = r.get(_meta_key(job_id))
    if not raw:
        return
    meta = json.loads(raw)
    meta["done"] = int(cnt)
    meta["phase"] = "running"
    meta["last_progress_at"] = time.time()
    started = meta.get("started_at") or time.time()
    elapsed = max(time.time() - started, 0.001)
    rate = cnt / elapsed if cnt > 0 else 0
    remaining = max(int(meta.get("total", cnt)) - cnt, 0)
    meta["eta_seconds"] = int(remaining / rate) if rate > 0 else None
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))


def heartbeat(job_id: str, phase: Optional[str] = None) -> None:
    """Bump the job's progress heartbeat (and optionally its phase label).

    Called at every finalization step boundary (spill, summary, ZIP, WOW/MOM)
    so a job that is still moving through post-processing is NEVER seen as stuck
    by the watchdog — only a step that itself hangs longer than the stuck
    threshold freezes `last_progress_at`. Best-effort; never raises.
    """
    r = _redis()
    if r is None:
        return
    try:
        raw = r.get(_meta_key(job_id))
        if not raw:
            return
        meta = json.loads(raw)
        meta["last_progress_at"] = time.time()
        if phase is not None:
            meta["phase"] = phase
        r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))
    except Exception as exc:  # pragma: no cover - heartbeat must never break a run
        logger.debug("[OPTIM_STORE] heartbeat error for %s: %s", job_id, exc)


def append_result(job_id: str, row: Dict[str, Any]) -> None:
    r = _redis()
    if r is None:
        return
    r.rpush(_results_key(job_id), json.dumps(row, default=str))
    r.expire(_results_key(job_id), OPTIM_TTL)


def mark_complete(job_id: str, *, error: Optional[str] = None) -> None:
    r = _redis()
    if r is None:
        return
    raw = r.get(_meta_key(job_id))
    if not raw:
        return
    meta = json.loads(raw)
    meta["status"] = "failed" if error else "success"
    meta["error"] = error
    meta["finished_at"] = time.time()
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))


def get_meta(job_id: str) -> Optional[Dict[str, Any]]:
    r = _redis()
    if r is None:
        return None
    raw = r.get(_meta_key(job_id))
    if not raw:
        return None
    meta = json.loads(raw)
    # Overlay atomic running counter when job is still in progress.
    if meta.get("status") == "running":
        cnt_raw = r.get(f"optim:{job_id}:done_counter")
        if cnt_raw is not None:
            meta["done"] = int(cnt_raw)
    return meta


def list_recent_jobs(limit: int = 200) -> List[Dict[str, Any]]:
    """List every optimize job with live meta (any machine, any browser) so a
    browser that did NOT enqueue a job can still discover and auto-download it
    by job_id — see routers/optimize.py GET /optimize/jobs and
    frontend/src/components/AutoDownloadQueue.jsx's system-wide poll.

    Cheap: SCANs the `optim:*:meta` keyspace (bounded by OPTIM_TTL — jobs
    expire out of Redis on their own) rather than maintaining a separate index.
    Returns newest-first, capped at `limit`.
    """
    r = _redis()
    if r is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for key in r.scan_iter(match="optim:*:meta", count=200):
            job_id = key.split(":")[1] if isinstance(key, str) else key.decode().split(":")[1]
            try:
                meta = get_meta(job_id)
            except Exception:
                continue
            if not meta:
                continue
            out.append({"job_id": job_id, **meta})
    except Exception as exc:
        logger.warning("[OPTIM_STORE] list_recent_jobs scan failed: %s", exc)
        return []
    out.sort(key=lambda m: m.get("started_at") or 0, reverse=True)
    return out[:limit]


def _combo_fingerprint(row: Dict[str, Any]) -> str:
    """Stable hash of the result row's combo_label + summary PnL.

    Deduplication key: two rows are duplicates when they have the same
    combo_label (same human-readable strategy) AND identical total_pnl
    (same engine outcome).  Using both fields ensures:
      - Parameters swept with no engine effect (e.g. strike_type in
        pct_of_atm mode) are collapsed — same label, same result.
      - Different parameter values that happen to share a legacy label
        due to abs() rounding are NOT collapsed — same label, different result.
    """
    label = row.get("combo_label") or str(row.get("combo_id"))
    pnl = row.get("summary", {}).get("total_pnl", None)
    return f"{label}|{pnl}"


def _dedupe_by_label(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = _combo_fingerprint(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def get_results(
    job_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
    sort_key: Optional[str] = None,
    descending: bool = True,
) -> Dict[str, Any]:
    """Return {"rows": [...], "total": <unique_count>} for the results page."""
    r = _redis()
    if r is None:
        return {"rows": [], "total": 0}
    raw = r.lrange(_results_key(job_id), 0, -1)
    rows = _dedupe_by_label([json.loads(x) for x in raw])
    total = len(rows)
    if sort_key:
        def _k(row):
            try:
                return float(row.get("summary", {}).get(sort_key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        rows.sort(key=_k, reverse=descending)
    return {"rows": rows[offset : offset + limit], "total": total}


def get_all_results(job_id: str) -> List[Dict[str, Any]]:
    r = _redis()
    if r is None:
        return []
    raw = r.lrange(_results_key(job_id), 0, -1)
    return _dedupe_by_label([json.loads(x) for x in raw])


def get_combo_by_id(job_id: str, combo_id: int) -> Optional[Dict[str, Any]]:
    """
    Return the result row for a specific combo_id (1-indexed integer).
    combo_id is stored as insertion order (done + 1), so row is at index combo_id - 1.
    Falls back to a full scan if the index miss (e.g. due to sorted view).
    """
    r = _redis()
    if r is None:
        return None
    # Fast path: combo_id is 1-indexed insertion order
    raw = r.lindex(_results_key(job_id), combo_id - 1)
    if raw:
        row = json.loads(raw)
        if row.get("combo_id") == combo_id:
            return row
    # Slow fallback: linear scan (handles edge cases)
    all_raw = r.lrange(_results_key(job_id), 0, -1)
    for item in all_raw:
        row = json.loads(item)
        if row.get("combo_id") == combo_id:
            return row
    return None


def maybe_spill_to_parquet(job_id: str) -> Optional[str]:
    """If results > OPTIM_SPILL_THRESHOLD, write Parquet and return path."""
    r = _redis()
    if r is None:
        return None
    n = r.llen(_results_key(job_id))
    if n < OPTIM_SPILL_THRESHOLD:
        return None
    try:
        import pandas as pd

        rows = get_all_results(job_id)
        flat = []
        for row in rows:
            flat_row = {**row.get("combo", {}), **(row.get("summary") or {})}
            flat_row["combo_label"] = row.get("combo_label")
            flat.append(flat_row)
        df = pd.DataFrame(flat)
        os.makedirs(OPTIM_PARQUET_DIR, exist_ok=True)
        path = os.path.join(OPTIM_PARQUET_DIR, f"{job_id}.parquet")
        df.to_parquet(path, index=False)
        r.setex(_parquet_key(job_id), OPTIM_TTL, path)
        return path
    except Exception as e:
        logger.warning("[OPTIM_STORE] parquet spill failed: %s", e)
        return None


def update_result_summaries(
    job_id: str,
    corrected_by_label: Dict[str, Any],
) -> None:
    """Merge corrected metrics into stored result rows, keyed by combo_label_safe."""
    r = _redis()
    if r is None or not corrected_by_label:
        return
    try:
        raw_list = r.lrange(_results_key(job_id), 0, -1)
        if not raw_list:
            return
        updated = []
        for raw in raw_list:
            row = json.loads(raw)
            label = row.get("combo_label_safe", "")
            if label in corrected_by_label:
                row["summary"] = {**(row.get("summary") or {}), **corrected_by_label[label]}
            updated.append(json.dumps(row, default=str))
        pipe = r.pipeline()
        pipe.delete(_results_key(job_id))
        for item in updated:
            pipe.rpush(_results_key(job_id), item)
        pipe.expire(_results_key(job_id), OPTIM_TTL)
        pipe.execute()
        logger.info("[OPTIM_STORE] Updated summaries for %d combos in job %s", len(corrected_by_label), job_id[:8])
    except Exception as e:
        logger.warning("[OPTIM_STORE] update_result_summaries failed: %s", e)


def delete_job(job_id: str) -> None:
    r = _redis()
    if r is None:
        return
    r.delete(_meta_key(job_id), _results_key(job_id), _parquet_key(job_id))


# ── On-disk tradesheet storage ───────────────────────────────────────────────

OPTIM_TRADES_DIR = os.getenv("OPTIMIZE_TRADES_DIR", "/data/cache/optim_trades")


def get_trades_dir(job_id: str) -> str:
    return os.path.join(OPTIM_TRADES_DIR, job_id)


def write_combo_tradesheet(
    job_id: str,
    combo_label_safe: str,
    trades_df: Any,
) -> None:
    """Write a single combo's tradesheet DataFrame to CSV on disk."""
    if trades_df is None:
        return
    try:
        if hasattr(trades_df, "empty") and trades_df.empty:
            return
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, f"{combo_label_safe}.csv")
        trades_df.to_csv(path, index=False)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] tradesheet write failed (%s): %s", combo_label_safe, exc)


def write_combo_xlsx(
    job_id: str,
    combo_label_safe: str,
    trades_df: Any,
    summary: Dict[str, Any],
    combo_label: str = "",
    from_date: str = "",
    to_date: str = "",
    index_str: str = "",
    trading_days: Optional[List] = None,
    midcap_legs=None,
    midcap_spot_adjustment=None,
    midcap_symbol: str = "NIFTYMIDCAP100",
    filter_segments=None,
) -> None:
    """Write a single combo's XLSX tradesheet to disk (called per-combo during execution).

    Always enriches MAE/MFE before building the XLSX regardless of
    OPTIMIZE_SKIP_MAE_MFE — that flag only controls the ranking metrics,
    not the download tradesheet.
    """
    if trades_df is None:
        return
    try:
        if hasattr(trades_df, "empty") and trades_df.empty:
            return

        # Enrich MAE/MFE for the download tradesheet.  The optimizer skips this
        # during combo execution (OPTIMIZE_SKIP_MAE_MFE=1) for speed, so we do
        # it here using the feather that's already on disk.
        if index_str and trading_days:
            try:
                from services.optimizer.runner import (
                    _compute_mae_mfe_batch,
                    _compute_live_dd_from_mae,
                )
                import pandas as _pd
                enriched = _compute_mae_mfe_batch(trades_df, index_str, trading_days)
                if "Trade" in enriched.columns:
                    _pr = enriched.drop_duplicates(subset=["Trade"], keep="first")
                    _agg = _pr[["Trade"]].copy()
                    for _c in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                        if _c in _pr.columns:
                            _agg[_c] = _pr[_c].values
                    enriched = _compute_live_dd_from_mae(enriched, _agg)
                trades_df = enriched
            except Exception as _mae_exc:
                logger.warning("[OPTIM_STORE] MAE/MFE enrich FAILED (%s): %s", combo_label_safe, _mae_exc)

        from services.optimizer.excel_builder import build_combo_xlsx
        xlsx_bytes = build_combo_xlsx(
            trades_df,
            summary,
            combo_label=combo_label,
            from_date=from_date,
            to_date=to_date,
            midcap_legs=midcap_legs,
            midcap_spot_adjustment=midcap_spot_adjustment,
            midcap_symbol=midcap_symbol,
            filter_segments=filter_segments,
        )
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, f"{combo_label_safe}.xlsx")
        with open(path, "wb") as fh:
            fh.write(xlsx_bytes)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] xlsx write failed (%s): %s", combo_label_safe, exc)


def write_combo_xlsx_patchwise(
    job_id: str,
    combo_label_safe: str,
    trades_df: Any,
    summary: Dict[str, Any],
    combo_label: str = "",
    from_date: str = "",
    to_date: str = "",
    index_str: str = "",
    trading_days: Optional[List] = None,
    midcap_legs=None,
    midcap_spot_adjustment=None,
    midcap_symbol: str = "NIFTYMIDCAP100",
    filter_name: str = "",
    filter_segments=None,
) -> None:
    """Write a combo's PATCHWISE XLSX tradesheet directly from trades_df during
    the run — same builder the finalization/download uses, just fed the in-memory
    trades instead of re-reading the CSV. Written to a `patchwise/` subdir so the
    overall fast-path (which globs top-level *.xlsx) never picks it up. This lets
    the patchwise ZIP be assembled by just zipping pre-built files (seconds)
    instead of rebuilding every combo's XLSX from CSV at download time."""
    if trades_df is None:
        return
    try:
        if hasattr(trades_df, "empty") and trades_df.empty:
            return
        # Enrich MAE/MFE identically to the overall write (same code path).
        if index_str and trading_days:
            try:
                from services.optimizer.runner import (
                    _compute_mae_mfe_batch,
                    _compute_live_dd_from_mae,
                )
                enriched = _compute_mae_mfe_batch(trades_df, index_str, trading_days)
                if "Trade" in enriched.columns:
                    _pr = enriched.drop_duplicates(subset=["Trade"], keep="first")
                    _agg = _pr[["Trade"]].copy()
                    for _c in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                        if _c in _pr.columns:
                            _agg[_c] = _pr[_c].values
                    enriched = _compute_live_dd_from_mae(enriched, _agg)
                trades_df = enriched
            except Exception as _mae_exc:
                logger.warning("[OPTIM_STORE] pw MAE/MFE enrich FAILED (%s): %s", combo_label_safe, _mae_exc)

        from services.optimizer.excel_builder import build_combo_xlsx
        xlsx_bytes = build_combo_xlsx(
            trades_df,
            summary,
            combo_label=combo_label,
            from_date=from_date,
            to_date=to_date,
            midcap_legs=midcap_legs,
            midcap_spot_adjustment=midcap_spot_adjustment,
            midcap_symbol=midcap_symbol,
            filter_name=filter_name,
            patchwise=True,
            filter_segments=filter_segments,
        )
        dirpath = os.path.join(get_trades_dir(job_id), "patchwise")
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, f"{combo_label_safe}.xlsx")
        with open(path, "wb") as fh:
            fh.write(xlsx_bytes)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] pw xlsx write failed (%s): %s", combo_label_safe, exc)


def write_summary_csv(job_id: str, rows: List[Dict[str, Any]]) -> None:
    """Write master summary CSV (one row per combo) to disk."""
    if not rows:
        return
    try:
        import pandas as pd

        flat = []
        for row in rows:
            flat_row = {
                "combo_id": row.get("combo_id"),
                "combo_label": row.get("combo_label"),
            }
            flat_row.update(row.get("summary") or {})
            flat.append(flat_row)
        df = pd.DataFrame(flat)
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        df.to_csv(os.path.join(dirpath, "summary.csv"), index=False)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] summary CSV write failed: %s", exc)


def write_run_config(
    job_id: str,
    method: str,
    objective: str,
    param_specs: list,
    base_payload: dict,
    *,
    sample_n: int | None = None,
    algorithm: str | None = None,
    total_combos: int | None = None,
) -> None:
    """Write run_config.csv to the job's trades directory."""
    try:
        import csv
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, "run_config.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            # Run-level metadata block
            w.writerow(["# Run Configuration"])
            w.writerow(["Method", method])
            w.writerow(["Objective", objective])
            w.writerow(["Total Combinations", total_combos or ""])
            if sample_n is not None:
                w.writerow(["Sample N", sample_n])
            if algorithm:
                w.writerow(["Algorithm", algorithm])
            from_date = base_payload.get("from_date") or base_payload.get("date_from", "")
            to_date = base_payload.get("to_date") or base_payload.get("date_to", "")
            w.writerow(["From Date", from_date])
            w.writerow(["To Date", to_date])
            w.writerow(["Symbol", base_payload.get("symbol", "")])
            w.writerow([])
            # Parameter sweep specs
            w.writerow(["# Parameter Specs"])
            w.writerow(["Parameter", "Min", "Max", "Step", "Values", "Type"])
            for spec in param_specs:
                ptype = spec.get("type", "range")
                w.writerow([
                    spec.get("path", spec.get("label", "")),
                    spec.get("min", ""),
                    spec.get("max", ""),
                    spec.get("step", ""),
                    "|".join(str(v) for v in spec.get("values", [])) if spec.get("values") else "",
                    ptype,
                ])
    except Exception as exc:
        logger.warning("[OPTIM_STORE] run_config write failed: %s", exc)


def delete_job_trades(job_id: str) -> None:
    """Remove the on-disk tradesheet directory for a job."""
    import shutil

    dirpath = get_trades_dir(job_id)
    if os.path.isdir(dirpath):
        try:
            shutil.rmtree(dirpath, ignore_errors=True)
        except Exception as exc:
            logger.warning("[OPTIM_STORE] trade dir delete failed: %s", exc)


# ── Live-optim registry (drives DYNAMIC worker/memory allocation) ──────────
# How many optimize jobs are CURRENTLY in their compute phase, box-wide. The
# parallel runner divides the "solo" (full-box) parallelism by this count, so:
#   1 optim live  -> full box  (fast single run)
#   2 optims live -> half each (both fit, no OOM, no CPU thrash)
# Stored as a Redis hash {job_id: heartbeat_epoch}; entries older than
# _ACTIVE_STALE_SEC (a crashed/killed job that never deregistered) are pruned
# on read so they can't wedge the divisor high forever.
_ACTIVE_KEY = "algotest:optim:active"
_ACTIVE_STALE_SEC = 3 * 60 * 60  # 3h — longer than any real optim compute phase


def register_active_optim(job_id: str) -> int:
    """Mark this job as computing NOW and return the live optim count (incl.
    self). Best-effort: if Redis is unavailable, returns 1 so the caller falls
    back to full solo parallelism (safe — a single job never over-allocates)."""
    r = _redis()
    if r is None:
        return 1
    try:
        now = time.time()
        r.hset(_ACTIVE_KEY, job_id, now)
        # Prune stale entries (crashed jobs) before counting.
        stale = []
        for jid, ts in (r.hgetall(_ACTIVE_KEY) or {}).items():
            try:
                if now - float(ts) > _ACTIVE_STALE_SEC:
                    stale.append(jid)
            except (TypeError, ValueError):
                stale.append(jid)
        if stale:
            r.hdel(_ACTIVE_KEY, *stale)
        return max(1, r.hlen(_ACTIVE_KEY))
    except Exception as exc:
        logger.warning("[OPTIM_STORE] register_active_optim failed: %s", exc)
        return 1


def unregister_active_optim(job_id: str) -> None:
    """Remove this job from the live set once its compute phase ends."""
    r = _redis()
    if r is None:
        return
    try:
        r.hdel(_ACTIVE_KEY, job_id)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] unregister_active_optim failed: %s", exc)


def active_optim_count() -> int:
    """Current live optim count (pruned), for logging/introspection."""
    r = _redis()
    if r is None:
        return 0
    try:
        now = time.time()
        cnt = 0
        for _jid, ts in (r.hgetall(_ACTIVE_KEY) or {}).items():
            try:
                if now - float(ts) <= _ACTIVE_STALE_SEC:
                    cnt += 1
            except (TypeError, ValueError):
                pass
        return cnt
    except Exception:
        return 0
