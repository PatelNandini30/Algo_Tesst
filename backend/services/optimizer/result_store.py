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

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
OPTIM_TTL = int(os.getenv("OPTIMIZE_RESULT_TTL", "86400"))
OPTIM_SPILL_THRESHOLD = int(os.getenv("OPTIMIZE_PARQUET_SPILL_AT", "10000"))
OPTIM_PARQUET_DIR = os.getenv("OPTIMIZE_PARQUET_DIR", "/data/cache/optim_results")


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
    return json.loads(raw) if raw else None


def get_results(
    job_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
    sort_key: Optional[str] = None,
    descending: bool = True,
) -> List[Dict[str, Any]]:
    r = _redis()
    if r is None:
        return []
    raw = r.lrange(_results_key(job_id), 0, -1)
    rows = [json.loads(x) for x in raw]
    if sort_key:
        def _k(row):
            try:
                return float(row.get("summary", {}).get(sort_key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        rows.sort(key=_k, reverse=descending)
    return rows[offset : offset + limit]


def get_all_results(job_id: str) -> List[Dict[str, Any]]:
    r = _redis()
    if r is None:
        return []
    raw = r.lrange(_results_key(job_id), 0, -1)
    return [json.loads(x) for x in raw]


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


def delete_job_trades(job_id: str) -> None:
    """Remove the on-disk tradesheet directory for a job."""
    import shutil

    dirpath = get_trades_dir(job_id)
    if os.path.isdir(dirpath):
        try:
            shutil.rmtree(dirpath, ignore_errors=True)
        except Exception as exc:
            logger.warning("[OPTIM_STORE] trade dir delete failed: %s", exc)
