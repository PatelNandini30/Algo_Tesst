"""
Process-pool parallelism for the optimizer runner.

Each child process:
  1. Inherits the parent's Rust AHashMap cache via Linux copy-on-write fork.
     Read-only pages are physically shared — no per-child reload, no memory
     duplication.  After fork, inherited Redis/_db sockets are reset to avoid
     cross-process socket sharing (the original forkserver deadlock source).
  2. Iterates its assigned combo batch using the shared Rust cache.
  3. Writes per-combo results directly to Redis via result_store.
  4. Returns lightweight progress counts to the parent.

We use the **fork** start method.  The old forkserver approach required each
child to call load_cache_from_root() independently, rebuilding ~1680 MB of
AHashMaps (Close/Open/High/Low × 8.4M rows).  With P=2 that is 3360 MB of
duplicated heap memory in a 4 GB container → OOM kill (exitcode 255).

With fork, the AHashMap pages are copy-on-write shared.  Children only
fault-in private pages for their own working memory (~300 MB each), leaving
~2.4 GB headroom in the 4 GB container for P=2.

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

        # Re-add /app to sys.path in case it was dropped (harmless on fork).
        for candidate in ("/app", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))):
            if candidate and candidate not in sys.path:
                sys.path.insert(0, candidate)

        # After fork: reset inherited Redis client so each child gets its own
        # connection.  The Rust AHashMap (read-only, CoW-inherited) is kept.
        # Do NOT call engine.dispose() here — SQLAlchemy's pool lock may have
        # been held by a parent thread that no longer exists in the child,
        # causing a futex deadlock.  Children use the Rust cache exclusively
        # (no DB queries), so the inherited DB connections are harmless.
        try:
            from services.optimizer import result_store as _rs_mod
            _rs_mod._client = None  # force fresh Redis connection per child
        except Exception:
            pass

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

        # Preserve fork-inherited ohlc_df_pandas across set_rust_context().
        # Parent built it in _prepare_market_data; child inherited it via CoW
        # (zero I/O). set_rust_context() below replaces _RUST_CONTEXT with the
        # pickled context that was stripped of ohlc_df_pandas (to avoid
        # pickling 111 MB through the pool pipe). We re-attach the inherited
        # one after — same physical pages, no copy.
        from services.optimizer import runner as _runner_mod
        _inherited_ohlc_pandas = None
        if _runner_mod._RUST_CONTEXT is not None:
            _inherited_ohlc_pandas = _runner_mod._RUST_CONTEXT.get("ohlc_df_pandas")

        if prebuilt_feather_root:
            # With fork, the parent's Rust AHashMap is inherited via CoW —
            # check if it's already live before doing any disk I/O.
            from services import rust_fast_path as _rfp
            _cache_inherited = _rfp.is_available() and _rfp._loaded_cache_key is not None
            if _cache_inherited:
                loaded = True
                logger.info("[OPTIM_PARALLEL] Rust cache inherited via fork — skipping reload")
            else:
                # forkserver / fresh process: mmap from disk (fallback path).
                from services.rust_fast_path import load_cache_from_root
                loaded = load_cache_from_root(prebuilt_feather_root)
            if not loaded:
                logger.warning(
                    "[OPTIM_PARALLEL] cache unavailable at %s — falling back to DB load",
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

        # Re-attach the CoW-inherited pandas OHLC (free, same physical pages).
        if _inherited_ohlc_pandas is not None and _runner_mod._RUST_CONTEXT is not None:
            _runner_mod._RUST_CONTEXT["ohlc_df_pandas"] = _inherited_ohlc_pandas
            logger.info(
                "[OPTIM_PARALLEL] OHLC pandas inherited via fork — skipping reload (%d rows)",
                len(_inherited_ohlc_pandas),
            )

        # Polars' Rayon pool was initialized in the parent (via pl.read_ipc during
        # feather preload). After fork the pool thread is dead — any subsequent
        # Polars op that schedules on Rayon (pl.read_ipc, .filter, .join on
        # multi-row data, etc.) deadlocks on futex_do_wait.
        #
        # Workaround: load the OHLC feather via pyarrow (no Rayon) into a pandas
        # DataFrame and hand it to runner via _RUST_CONTEXT["ohlc_df_pandas"].
        # _compute_mae_mfe_batch detects this key and runs the join in pandas,
        # bypassing Polars entirely in the child. Result: MAE/MFE in tradesheets
        # matches the regular backtest engine exactly.
        try:
            import pyarrow as _pa
            import pyarrow.ipc as _pa_ipc
            import pyarrow.compute as _pc
            # Single-thread pyarrow in forked children — the inherited Arrow
            # thread pool is dead post-fork, same root cause as Polars/Rayon.
            try:
                _pa.set_cpu_count(1)
                _pa.set_io_thread_count(1)
            except Exception:
                pass
            from services import rust_fast_path as _rfp_w
            from services.optimizer import runner as _runner_mod
            _rctx = _runner_mod._RUST_CONTEXT
            if _rctx is not None and "ohlc_df_pandas" not in _rctx:
                _sym_upper = str(
                    base_payload.get("index") or base_payload.get("symbol") or "NIFTY"
                ).upper()
                _feather_path = (
                    _rfp_w._cache_root()
                    / f"arrow-v2:bulk:{_sym_upper}:full"
                    / "options.feather"
                )
                if _feather_path.exists():
                    _t0 = time.perf_counter()
                    # Only the columns _compute_mae_mfe_batch needs — skips
                    # Open/Close/Volume and any other heavy fields in the feather.
                    _needed = ["Symbol", "Date", "ExpiryDate", "StrikePrice",
                               "OptionType", "High", "Low"]
                    _reader = _pa_ipc.open_file(str(_feather_path))
                    _avail = set(_reader.schema.names)
                    _sel = [c for c in _needed if c in _avail]
                    _pa_table = _reader.read_all().select(_sel)
                    # Filter to the run's date range in pyarrow (low-memory) BEFORE
                    # converting to pandas — avoids materializing rows we'll discard.
                    _days_sorted = sorted(_rctx.get("trading_days") or [])
                    import pandas as _pd_w
                    if _days_sorted and "Date" in _avail:
                        _from_dt = _pd_w.Timestamp(_days_sorted[0]).date()
                        _to_dt = _pd_w.Timestamp(_days_sorted[-1]).date()
                        _mask = _pc.and_(
                            _pc.greater_equal(_pa_table.column("Date"), _pa.scalar(_from_dt)),
                            _pc.less_equal(_pa_table.column("Date"), _pa.scalar(_to_dt)),
                        )
                        _pa_table = _pa_table.filter(_mask)
                    # Drop FUTIDX/spot rows in pyarrow before to_pandas — they have
                    # null StrikePrice/OptionType and would break the int32 strike_r cast.
                    # MAE/MFE only needs option (CE/PE) rows.
                    if "OptionType" in _avail:
                        _opt_mask = _pc.is_in(
                            _pa_table.column("OptionType"),
                            value_set=_pa.array(["CE", "PE"]),
                        )
                        _pa_table = _pa_table.filter(_opt_mask)
                    # Convert with date_as_object=False → datetime64[ns] (8 B/row vs ~32 B object).
                    # Categorical + float32 casts happen below to cut memory further.
                    _ohlc_pd = _pa_table.to_pandas(date_as_object=False)
                    # Cast string columns to category (low-cardinality: NIFTY, CE/PE).
                    for _col in ("Symbol", "OptionType"):
                        if _col in _ohlc_pd.columns and _ohlc_pd[_col].dtype == object:
                            _ohlc_pd[_col] = _ohlc_pd[_col].astype("category")
                    # Cast prices to float32 — halves their memory vs float64,
                    # and MAE/MFE math doesn't need float64 precision.
                    for _col in ("High", "Low"):
                        if _col in _ohlc_pd.columns:
                            _ohlc_pd[_col] = _ohlc_pd[_col].astype("float32")
                    # Pre-compute strike_r once, drop StrikePrice — saves another ~15 MB.
                    if "StrikePrice" in _ohlc_pd.columns:
                        _ohlc_pd["strike_r"] = _ohlc_pd["StrikePrice"].round(0).astype("int32")
                        _ohlc_pd = _ohlc_pd.drop(columns=["StrikePrice"])
                    _rctx["ohlc_df_pandas"] = _ohlc_pd
                    _mem_mb = _ohlc_pd.memory_usage(deep=True).sum() / (1024 * 1024)
                    logger.info(
                        "[OPTIM_PARALLEL] OHLC pyarrow-loaded: %d rows, %.1f MB in %.2fs (fork-safe MAE/MFE)",
                        len(_ohlc_pd), _mem_mb, time.perf_counter() - _t0,
                    )
                else:
                    logger.warning(
                        "[OPTIM_PARALLEL] OHLC feather not found at %s — MAE/MFE will be 0",
                        _feather_path,
                    )
                    os.environ["OPTIMIZE_SKIP_MAE_MFE"] = "1"
        except Exception as _ohlc_err:
            logger.warning(
                "[OPTIM_PARALLEL] OHLC pyarrow load failed: %s — MAE/MFE will be 0",
                _ohlc_err,
            )
            os.environ["OPTIMIZE_SKIP_MAE_MFE"] = "1"

        # Signal that this worker has started processing combos.
        try:
            result_store.update_progress(job_id, done=0, phase="running")
        except Exception:
            pass

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
                result_store.increment_done(job_id)
                _skip_ts = os.environ.get("OPTIMIZE_SKIP_TRADESHEETS", "0").strip().lower() in ("1", "true", "yes")
                if not _skip_ts and not trades_df.empty:
                    result_store.write_combo_tradesheet(job_id, combo_label_safe, trades_df)
                done += 1
                logger.info(
                    "[OPTIM] combo %d done | %s | trades=%d pnl=%.0f obj=%.4f | %.0fms",
                    _combo_id,
                    labels["combo_label"],
                    int(flat_summary.get("count", 0) or 0),
                    float(flat_summary.get("total_pnl", 0) or 0),
                    float(row["objective_value"] or 0),
                    elapsed_ms,
                )
            except Exception as exc:
                failures += 1
                logger.warning("[OPTIM_PARALLEL] combo %d failed: %s", starting_combo_id + i, exc)
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

    ctx = get_context("fork")

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
