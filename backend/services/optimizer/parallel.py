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


def _cgroup_cpu_quota() -> Optional[float]:
    """Effective CPU quota for this container (cgroup v2 `cpu.max`), or None."""
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max":
            return float(quota) / float(period)
    except Exception:
        pass
    return None


def _cgroup_mem_limit_mb() -> Optional[int]:
    """Container memory limit in MB (cgroup v2 `memory.max`), or None."""
    try:
        with open("/sys/fs/cgroup/memory.max") as fh:
            raw = fh.read().strip()
        if raw != "max":
            return int(raw) // (1024 * 1024)
    except Exception:
        pass
    return None


def get_parallelism() -> int:
    """How many child processes to spawn — DYNAMICALLY clamped to what this box
    can sustain, so a too-high OPTIMIZE_PARALLELISM can never thrash the machine.

    Why: on 2026-07-04, P=16 forked workers contending for memory bandwidth on
    the shared ~2.6 GB Rust cache inflated per-combo time ~47x (205s vs 4.4s
    solo) on this 16 GB box. The configured value is now a CEILING; the
    effective value is min(configured, cpu_cap, mem_cap):

      cpu_cap = container CPU quota // RUST_SIM_THREADS  (each worker runs its
                own rayon pool of that many threads — oversubscription stalls
                everyone on memory bandwidth, it does not add speed)
      mem_cap = (container mem limit − shared cache/overhead reserve)
                // per-worker private pages  (fork-CoW keeps the cache shared;
                only private pages multiply per worker)

    Tunables: OPTIMIZE_WORKER_PRIVATE_MB (default 700),
    OPTIMIZE_MEM_RESERVE_MB (default 4500 ≈ Rust cache + parent + OHLC).
    Pure orchestration — no calculation logic involved.
    """
    explicit = os.environ.get("OPTIMIZE_PARALLELISM")
    if explicit and explicit.isdigit():
        requested = max(1, int(explicit))
    else:
        requested = min(max(1, (os.cpu_count() or 2) // 2), 4)

    # CPU clamp: quota (fractional cpus) divided by per-worker rayon threads.
    try:
        sim_threads = max(1, int(os.environ.get("RUST_SIM_THREADS", "1")))
    except (TypeError, ValueError):
        sim_threads = 1
    cpus = _cgroup_cpu_quota() or float(os.cpu_count() or 2)
    cpu_cap = max(1, int(cpus // sim_threads))

    # Memory clamp: only per-worker PRIVATE pages multiply (cache is CoW-shared).
    try:
        private_mb = max(100, int(os.environ.get("OPTIMIZE_WORKER_PRIVATE_MB", "700")))
    except (TypeError, ValueError):
        private_mb = 700
    try:
        reserve_mb = max(0, int(os.environ.get("OPTIMIZE_MEM_RESERVE_MB", "4500")))
    except (TypeError, ValueError):
        reserve_mb = 4500
    mem_limit = _cgroup_mem_limit_mb()
    mem_cap = max(1, (mem_limit - reserve_mb) // private_mb) if mem_limit else requested

    effective = max(1, min(requested, cpu_cap, mem_cap))
    if effective < requested:
        logger.info(
            "[OPTIM_PARALLEL] parallelism clamped %d -> %d (cpu_cap=%d @ %.1f cpus/"
            "%d threads, mem_cap=%d @ limit=%sMB reserve=%dMB private=%dMB)",
            requested, effective, cpu_cap, cpus, sim_threads,
            mem_cap, mem_limit, reserve_mb, private_mb,
        )
    return effective


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
            # MAE/MFE must never be silently skipped — retry the preload a few
            # times (transient I/O/timing is the common cause of a first-try
            # miss) before treating it as a real failure. Only after retries
            # are exhausted do we fall back to OPTIMIZE_SKIP_MAE_MFE, and that
            # fallback is now logged at ERROR (not swallowed) so a zeroed
            # tradesheet is never a silent surprise.
            _MAE_PRELOAD_ATTEMPTS = 3
            _mae_last_err = None
            for _mae_attempt in range(1, _MAE_PRELOAD_ATTEMPTS + 1):
                if _rctx is None or "ohlc_df_pandas" in _rctx:
                    break
                _sym_upper = str(
                    base_payload.get("index") or base_payload.get("symbol") or "NIFTY"
                ).upper()
                _feather_path = (
                    _rfp_w._cache_root()
                    / f"arrow-v2:bulk:{_sym_upper}:full"
                    / "options.feather"
                )
                if not _feather_path.exists():
                    _mae_last_err = FileNotFoundError(f"OHLC feather missing: {_feather_path}")
                    if _mae_attempt < _MAE_PRELOAD_ATTEMPTS:
                        time.sleep(1.0)
                    continue
                try:
                    _t0 = time.perf_counter()
                    # Only the columns _compute_mae_mfe_batch needs — skips
                    # Open/Close/Volume and any other heavy fields in the feather.
                    _needed = ["Symbol", "Date", "ExpiryDate", "StrikePrice",
                               "OptionType", "High", "Low", "SettledPrice"]
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
                    for _col in ("High", "Low", "SettledPrice"):
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
                except Exception as _load_exc:
                    _mae_last_err = _load_exc
                    if _mae_attempt < _MAE_PRELOAD_ATTEMPTS:
                        logger.warning(
                            "[OPTIM_PARALLEL] job=%s: OHLC preload attempt %d/%d failed (%s) — retrying",
                            job_id, _mae_attempt, _MAE_PRELOAD_ATTEMPTS, _load_exc,
                        )
                        time.sleep(1.0)
            if _rctx is not None and "ohlc_df_pandas" not in _rctx:
                logger.error(
                    "[OPTIM_PARALLEL] job=%s: OHLC preload for MAE/MFE FAILED after %d attempts "
                    "(last error: %s) — MAE/MFE will be 0 for combos run by this worker until "
                    "the underlying data/cache issue is fixed",
                    job_id, _MAE_PRELOAD_ATTEMPTS, _mae_last_err,
                )
                os.environ["OPTIMIZE_SKIP_MAE_MFE"] = "1"
        except Exception as _ohlc_err:
            logger.error(
                "[OPTIM_PARALLEL] job=%s: OHLC pyarrow load crashed unexpectedly (%s) — MAE/MFE will be 0",
                job_id, _ohlc_err,
            )
            os.environ["OPTIMIZE_SKIP_MAE_MFE"] = "1"

        # Signal that this worker has started processing combos.
        try:
            result_store.update_progress(job_id, done=0, phase="running")
        except Exception:
            pass

        _from_date = base_payload.get("from_date") or base_payload.get("date_from") or ""
        _to_date = base_payload.get("to_date") or base_payload.get("date_to") or ""
        _index_str = str(base_payload.get("index") or base_payload.get("symbol") or "NIFTY").upper()

        # Midcap cross-index overlay config (from the run's base payload). Passed
        # into the per-combo XLSX so its Summary mirrors the verified backtest /
        # master summary (Combined Live DD etc.) instead of NIFTY-only stats.
        _mc_legs = base_payload.get("midcap_legs") or None
        _mc_sa = base_payload.get("midcap_spot_adjustment") or None
        _mc_sym = (
            (_mc_legs[0].get("symbol") if (_mc_legs and isinstance(_mc_legs[0], dict)) else None)
            or "NIFTYMIDCAP100"
        )
        _filter_segments = base_payload.get("filter_segments") or None
        # filter_name (zip_naming level1) gates the "Patch wise" sheet in
        # build_combo_xlsx — same source the finalization/download uses.
        try:
            _zip_naming = (result_store.get_meta(job_id) or {}).get("zip_naming") or {}
            _filter_name = (_zip_naming.get("level1") or "") if _zip_naming else ""
        except Exception:
            _filter_name = ""

        # Inline-finalization helpers: compute the patchwise summary + WOW/MOM data
        # per combo (in parallel) so the sequential finalization is instant.
        from services.optimizer.excel_builder import (
            compute_xlsx_summary_metrics as _cmetrics,
            build_cleaned_for_combo as _bcc,
        )
        from services.optimizer.wow_mom import _wm_from_cleaned
        _INLINE_FINALIZE = os.environ.get("OPTIMIZE_INLINE_FINALIZE", "1").strip().lower() in ("1", "true", "yes")

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
                # Inline finalization (from the in-memory trades_df, across the
                # parallel workers) so the sequential finalization has nothing to
                # recompute — the master summary is already corrected, and the
                # patchwise summary + WOW/MOM data are pre-computed and stored:
                #   • overall metrics merged into flat_summary  (= old Step 0)
                #   • patchwise metrics                          → row["summary_pw"]
                #   • WOW/MOM wm (overall + patchwise)           → row["wm_*"]
                _summary_pw = None
                _wm_over = _wm_pw = None
                _has_mc = False
                if _INLINE_FINALIZE and not (hasattr(trades_df, "empty") and trades_df.empty):
                    # Overall metrics (incl. avg_final_mae) merged into the stored
                    # summary. Kept in its own try so a patchwise failure below can
                    # never strip avg_final_mae from the overall master summary.
                    try:
                        _over = _cmetrics(
                            trades_df, flat_summary, midcap_legs=_mc_legs,
                            midcap_spot_adjustment=_mc_sa, midcap_symbol=_mc_sym,
                            patchwise=False, filter_segments=_filter_segments,
                        )
                        flat_summary = {**flat_summary, **_over}
                    except Exception as _inl_exc:
                        logger.warning("[OPTIM] inline overall finalize failed for combo %d: %s", starting_combo_id + i, _inl_exc)
                    # Patchwise metrics (row["summary_pw"]) — served directly by the
                    # master-summary endpoint when download_mode=patchwise.
                    try:
                        _summary_pw = _cmetrics(
                            trades_df, flat_summary, midcap_legs=_mc_legs,
                            midcap_spot_adjustment=_mc_sa, midcap_symbol=_mc_sym,
                            patchwise=True, filter_segments=_filter_segments,
                        )
                    except Exception as _inl_exc:
                        logger.warning("[OPTIM] inline patchwise finalize failed for combo %d: %s", starting_combo_id + i, _inl_exc)
                        _summary_pw = None
                    # WOW/MOM cleaned data (overall + patchwise).
                    try:
                        _cl_o, _has_mc = _bcc(trades_df, _mc_legs, _mc_sa, _mc_sym,
                                              patchwise=False, filter_segments=_filter_segments)
                        _cl_p, _ = _bcc(trades_df, _mc_legs, _mc_sa, _mc_sym,
                                        patchwise=True, filter_segments=_filter_segments)
                        _wm_over = _wm_from_cleaned(_cl_o, _has_mc)
                        _wm_pw = _wm_from_cleaned(_cl_p, _has_mc)
                    except Exception as _inl_exc:
                        logger.warning("[OPTIM] inline WOW/MOM finalize failed for combo %d: %s", starting_combo_id + i, _inl_exc)
                        _wm_over = _wm_pw = None
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
                    "summary_pw": _summary_pw,
                    "wm_overall": _wm_over,
                    "wm_pw": _wm_pw,
                    "has_midcap": bool(_has_mc),
                    "inline_finalized": _summary_pw is not None,
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
                    _tdays = (_runner_mod._RUST_CONTEXT or {}).get("trading_days") or []
                    result_store.write_combo_xlsx(
                        job_id,
                        combo_label_safe,
                        trades_df,
                        flat_summary,
                        combo_label=labels["combo_label"],
                        from_date=_from_date,
                        to_date=_to_date,
                        index_str=_index_str,
                        trading_days=_tdays,
                        midcap_legs=_mc_legs,
                        midcap_spot_adjustment=_mc_sa,
                        midcap_symbol=_mc_sym,
                        filter_segments=_filter_segments,
                    )
                    # Also build the PATCHWISE variant directly from the same
                    # in-memory trades_df — ONLY when inline finalization is on.
                    # Gated with _INLINE_FINALIZE because these extra per-combo
                    # MAE/MFE + build passes, run across many parallel workers,
                    # saturate RAM/CPU and inflate the whole sweep; with it off
                    # the patchwise ZIP is built once at finalization instead.
                    if _INLINE_FINALIZE:
                        result_store.write_combo_xlsx_patchwise(
                            job_id,
                            combo_label_safe,
                            trades_df,
                            flat_summary,
                            combo_label=labels["combo_label"],
                            from_date=_from_date,
                            to_date=_to_date,
                            index_str=_index_str,
                            trading_days=_tdays,
                            midcap_legs=_mc_legs,
                            midcap_spot_adjustment=_mc_sa,
                            midcap_symbol=_mc_sym,
                            filter_name=_filter_name,
                            filter_segments=_filter_segments,
                        )
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
        # The runner's dynamic split (solo_ceiling // live_optims) can legitimately
        # resolve to P=1 when several optims run concurrently (e.g. ceiling 2 with
        # 2 live → 2//2=1, or ceiling 3 with 2 live → 3//2=1). The runner has
        # already committed to this (parallel) path by the time the split is
        # computed (it must be, so concurrent optims register first), so we can't
        # bounce back to the sequential branch here. Instead, run the single chunk
        # IN-PROCESS via the exact same _worker_entrypoint the pool workers use —
        # so per-combo computation and Redis writes are byte-for-byte identical,
        # just without forking. Save/restore the caller's _RUST_CONTEXT because
        # _worker_entrypoint tears market data down in its finally; the caller's
        # finalization (ZIP MAE/MFE enrichment) needs trading_days to survive
        # exactly as it does after the normal parallel path.
        from services.optimizer import runner as _runner_mod
        _saved_ctx = _runner_mod._RUST_CONTEXT
        try:
            res = _worker_entrypoint(
                job_id,
                base_payload,
                combos,
                objective_name,
                1,
                prebuilt_feather_root=prebuilt_feather_root,
                prebuilt_rust_context=prebuilt_rust_context,
            )
        finally:
            _runner_mod._RUST_CONTEXT = _saved_ctx
        _done = int(res.get("done", 0))
        if progress_cb:
            try:
                progress_cb(_done)
            except Exception:
                pass
        return {"done": _done, "failures": int(res.get("failures", 0))}

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

    # Dispose the SQLAlchemy pool BEFORE forking so children don't inherit a
    # connection that is mid-read (causes psycopg2 "D without T" errors when a
    # child hits load_expiry via the Python fallback path). Disposing in the
    # parent before fork is safe — the pool lazily reopens connections as needed.
    # Do NOT call dispose() inside _worker_entrypoint (the child) — the pool
    # lock may be held by a dead parent thread, causing a futex deadlock.
    try:
        from database import get_engine as _get_db_engine
        _get_db_engine().dispose()
    except Exception:
        pass

    t0 = time.perf_counter()
    pool = ctx.Pool(processes=parallelism)

    # When Celery revokes this task it sends SIGTERM to this process.
    # The billiard fork children are in a separate process group and do NOT
    # receive that signal — they keep running their full combo chunk.
    # Install a SIGTERM handler that calls pool.terminate() so all children
    # are killed immediately when the task is cancelled.
    import signal as _signal
    _pool_ref = [pool]

    def _on_terminate(signum, frame):
        try:
            _pool_ref[0].terminate()
        except Exception:
            pass
        raise SystemExit(0)

    try:
        _old_sigterm = _signal.signal(_signal.SIGTERM, _on_terminate)
    except (OSError, ValueError):
        _old_sigterm = None  # not the main thread — signal() not allowed

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
    except BaseException:
        pool.terminate()
        pool.join()
        raise
    finally:
        _pool_ref[0] = None
        if _old_sigterm is not None:
            try:
                _signal.signal(_signal.SIGTERM, _old_sigterm)
            except (OSError, ValueError):
                pass
    logger.info(
        "[OPTIM_PARALLEL] %d combos in %.2fs across %d workers",
        total_done,
        time.perf_counter() - t0,
        len(chunks),
    )
    return {"done": total_done, "failures": total_failures}
