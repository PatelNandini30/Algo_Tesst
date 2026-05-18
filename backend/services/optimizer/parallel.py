"""
Process-pool parallelism for the optimizer runner.

Each child process:
  1. Imports the engine and loads market data once (mmap'd Rust feather +
     fresh DB engine — no inherited state).
  2. Iterates its assigned combo batch.
  3. Writes per-combo results directly to Redis via result_store.
  4. Returns lightweight progress counts to the parent.

We use the **spawn** start method (not fork). Fork-inheritance of the parent's
Rust feather cache, SQLAlchemy engine, and Redis connection pool caused
hard deadlocks after the first child's first DB query. Spawn pays a one-time
~1s Python init cost per child but is rock-solid.

The Rust feather is memory-mapped, so 4 spawn'd children all load() it and
share the OS page cache — no extra disk I/O after the first load.

We deliberately do NOT pass DataFrames or large objects between processes —
results stream to Redis, so the parent only collects `{done, failed}` ints.
"""
from __future__ import annotations

import logging
import os
import time
import traceback
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def get_parallelism() -> int:
    """How many child processes to spawn. Default: cpu_count // 2, capped at 4."""
    explicit = os.environ.get("OPTIMIZE_PARALLELISM")
    if explicit and explicit.isdigit():
        return max(1, int(explicit))
    n = max(1, (os.cpu_count() or 2) // 2)
    return min(n, 4)


def _chunk(combos: List[Dict[str, Any]], n_chunks: int) -> List[List[Dict[str, Any]]]:
    """Split into roughly equal contiguous chunks."""
    if n_chunks <= 1 or len(combos) <= n_chunks:
        return [combos]
    size = max(1, len(combos) // n_chunks)
    chunks: List[List[Dict[str, Any]]] = []
    for i in range(n_chunks):
        start = i * size
        end = start + size if i < n_chunks - 1 else len(combos)
        if start < len(combos):
            chunks.append(combos[start:end])
    return chunks


def _worker_entrypoint(
    job_id: str,
    base_payload: Dict[str, Any],
    chunk: List[Dict[str, Any]],
    objective_name: str,
    starting_combo_id: int,
    prebuilt_feather_root: Optional[str] = None,
    prebuilt_rust_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Child-process entry point. Loads data, iterates, persists.

    When `prebuilt_feather_root` is provided the parent has already built the
    Rust feather. Workers skip the expensive DB bulk-load entirely and just
    mmap the existing feather (OS page cache — effectively zero extra memory
    and ~2s startup vs ~30s + 500MB for a fresh DB load).

    Under the spawn start method this is a fresh Python interpreter; we need
    to (re-)set up sys.path so backend modules import cleanly.
    """
    try:
        import sys

        # Spawn'd children start with a near-empty sys.path. Re-add /app
        # (or whatever directory holds backend modules).
        for candidate in ("/app", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))):
            if candidate and candidate not in sys.path:
                sys.path.insert(0, candidate)

        from services.optimizer import result_store
        from services.optimizer.combo_labeler import label_combo, safe_filename
        from services.optimizer.metrics import compute_optim_metrics
        from services.optimizer.objective import resolve_objective
        from services.optimizer.param_expander import apply_combo_for_optim
        from services.optimizer.runner import (
            _payload_is_rust_compatible,
            _prepare_market_data,
            _run_single_backtest,
            _teardown_market_data,
            set_rust_context,
        )

        obj = resolve_objective(objective_name)

        if prebuilt_feather_root:
            # Fast path: parent pre-built the feather — just mmap it.
            # No DB bulk-load, no 500MB peak memory per worker.
            from services.rust_fast_path import load_cache_from_root
            loaded = load_cache_from_root(prebuilt_feather_root)
            if not loaded:
                logger.warning(
                    "[OPTIM_PARALLEL] mmap of pre-built feather failed at %s — falling back to DB load",
                    prebuilt_feather_root,
                )
                lean = _payload_is_rust_compatible(base_payload)
                meta = _prepare_market_data(base_payload, lean=lean)
                set_rust_context(meta.get("rust_context"))
            else:
                # Install parent's pre-computed rust_context (trading_days,
                # expiries, spots) — skips per-worker DB queries too.
                set_rust_context(prebuilt_rust_context)
        else:
            # Fallback: no pre-built feather — each worker does the full load.
            lean = _payload_is_rust_compatible(base_payload)
            meta = _prepare_market_data(base_payload, lean=lean)
            set_rust_context(meta.get("rust_context"))

        done = 0
        failures = 0
        for i, combo in enumerate(chunk):
            try:
                merged = apply_combo_for_optim(base_payload, combo)
                t_combo = time.perf_counter()
                trades_df, summary = _run_single_backtest(merged)
                elapsed_ms = round((time.perf_counter() - t_combo) * 1000.0, 2)
                optim_extra = compute_optim_metrics(trades_df, summary)
                flat_summary = {**summary, **optim_extra}
                labels = label_combo(merged)
                _combo_id = starting_combo_id + i
                combo_label_safe = f"{_combo_id}_{safe_filename(labels['combo_label'])}"
                row = {
                    "combo_id": _combo_id,
                    "combo": combo,
                    "combo_label": labels["combo_label"],
                    "combo_label_safe": combo_label_safe,
                    "combo_columns": {
                        "expiry": labels["expiry"],
                        "shifting": labels["shifting"],
                        "put_strike_label": labels["put_strike_label"],
                        "call_strike_label": labels["call_strike_label"],
                        "spot_adjustment": labels["spot_adjustment"],
                    },
                    "summary": flat_summary,
                    "objective_value": obj.extract(flat_summary),
                    "trade_count": int(flat_summary.get("count", 0) or 0),
                    "elapsed_ms": elapsed_ms,
                }
                if flat_summary.get("count", 0) == 0:
                    failures += 1
                result_store.append_result(job_id, row)
                if not trades_df.empty:
                    result_store.write_combo_tradesheet(job_id, combo_label_safe, trades_df)
                done += 1
            except Exception as exc:
                failures += 1
                logger.warning("[OPTIM_PARALLEL] combo failed: %s", exc)
        return {"done": done, "failures": failures}
    except Exception as exc:
        logger.error("[OPTIM_PARALLEL] worker crashed: %s\n%s", exc, traceback.format_exc())
        return {"done": 0, "failures": len(chunk), "error": str(exc)}
    finally:
        try:
            _teardown_market_data()
        except Exception:
            pass


def run_parallel(
    *,
    job_id: str,
    base_payload: Dict[str, Any],
    combos: List[Dict[str, Any]],
    objective_name: str,
    parallelism: int,
    progress_cb: Optional[callable] = None,
    prebuilt_feather_root: Optional[str] = None,
    prebuilt_rust_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """
    Execute the optimizer loop in `parallelism` worker processes.

    Returns aggregated `{done, failures}`. Per-combo results are already in
    Redis by the time this returns.
    """
    if parallelism <= 1:
        raise ValueError("Use the sequential path for parallelism <= 1")

    chunks = _chunk(combos, parallelism)
    total_done = 0
    total_failures = 0

    # Use billiard (Celery's vendored multiprocessing). Inside a Celery
    # worker, stdlib multiprocessing.spawn/forkserver/Pool all hit the
    # AuthenticationString pickling guard. Billiard knows about its own
    # spawning context and handles authkey serialization correctly.
    #
    # billiard.Pool with `forkserver` start method gives clean child
    # interpreters — no inherited Rust feather cache, no SQLAlchemy pool,
    # no Redis connection from the parent. Each child does its own
    # _prepare_market_data inside _worker_entrypoint.
    from billiard import get_context  # type: ignore

    ctx = get_context("forkserver")

    t0 = time.perf_counter()
    pool = ctx.Pool(processes=parallelism)
    try:
        offset = 1
        async_results = []
        for c in chunks:
            async_results.append(
                pool.apply_async(
                    _worker_entrypoint,
                    args=(job_id, base_payload, c, objective_name, offset),
                    kwds={
                        "prebuilt_feather_root": prebuilt_feather_root,
                        "prebuilt_rust_context": prebuilt_rust_context,
                    },
                )
            )
            offset += len(c)
        pool.close()

        for ar in async_results:
            res = ar.get()
            total_done += int(res.get("done", 0))
            total_failures += int(res.get("failures", 0))
            if progress_cb:
                try:
                    progress_cb(total_done)
                except Exception:
                    pass
        pool.join()
    except Exception:
        pool.terminate()
        pool.join()
        raise
    logger.info(
        "[OPTIM_PARALLEL] %d combos in %.2fs across %d workers",
        total_done,
        time.perf_counter() - t0,
        len(chunks),
    )
    return {"done": total_done, "failures": total_failures}
