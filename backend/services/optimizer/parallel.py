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


def _live_available_mb() -> Optional[int]:
    """Current host MemAvailable in MB, or None if unreadable."""
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


def _cgroup_available_mb() -> Optional[int]:
    """Free RAM inside THIS container's cgroup (memory.max - memory.current) in
    MB, or None if unreadable. The fork-width clamp must honour this: MemAvailable
    reports the whole box, but forks are killed by the container's cgroup OOM when
    the sum of their working sets exceeds the container limit — even while the box
    still shows GBs free. Bounding by the smaller of the two is what prevents the
    'signal 9 (SIGKILL) -> batch chunk lost' the wide multi-index sweep hit."""
    limit = _cgroup_mem_limit_mb()
    if not limit:
        return None
    try:
        with open("/sys/fs/cgroup/memory.current") as fh:
            current = int(fh.read().strip()) // (1024 * 1024)
    except Exception:
        return None
    return max(0, limit - current)


def _is_multi_index_payload(payload: Optional[Dict[str, Any]]) -> bool:
    """Whether one optimization needs more than one resident symbol cache."""
    p = payload or {}
    base = str(p.get("index") or p.get("symbol") or "NIFTY").strip().upper()
    symbols = {base}
    for leg in list(p.get("legs") or []) + list(p.get("midcap_legs") or []):
        if isinstance(leg, dict):
            sym = str(leg.get("index") or "").strip().upper()
            if sym:
                symbols.add(sym)
    return len(symbols) > 1


def cap_parallelism_for_live_ram(
    requested: int,
    live_optims: int = 1,
    private_mb_override: int = 0,
) -> int:
    """Clamp requested workers against current MemAvailable.

    This keeps the optimizer dynamic: the same combo can safely run with a
    higher P when the machine is idle, but it will automatically drop P when
    the host is already memory-tight.
    """
    try:
        req = max(1, int(requested))
    except Exception:
        req = 1
    try:
        live = max(1, int(live_optims))
    except Exception:
        live = 1
    # THRASH BRAKE. MemAvailable counts reclaimable page cache as free, so the
    # box can report GBs "available" while actively swapping — and a wider fork
    # makes that worse (more concurrent working sets, more page-cache churn
    # against the mmap'd feather). PSI measures the thing we actually care about:
    # the % of wall time everything is STALLED waiting on memory. Nonzero swap
    # USAGE is fine and expected (cold pages, si/so≈0); sustained stall is not.
    # Halving on real pressure is what keeps this box usable as a DESKTOP.
    try:
        _psi_max = float(os.environ.get("OPTIMIZE_MEM_PRESSURE_MAX_PCT", "8"))
    except (TypeError, ValueError):
        _psi_max = 8.0
    try:
        with open("/proc/pressure/memory") as _fh:
            for _line in _fh:
                if _line.startswith("full"):
                    _avg10 = float(_line.split("avg10=")[1].split()[0])
                    if _avg10 > _psi_max:
                        _throttled = max(1, req // 2)
                        if _throttled < req:
                            logger.warning(
                                "[OPTIM_PARALLEL] memory pressure %.1f%% > %.1f%% — "
                                "halving fork width %d -> %d (box is thrashing)",
                                _avg10, _psi_max, req, _throttled,
                            )
                        req = _throttled
                    break
    except (OSError, ValueError, IndexError):
        pass                      # PSI unavailable -> behave exactly as before

    avail = _live_available_mb()
    if avail is None:
        return req
    # Bound by the SMALLER of whole-box MemAvailable and this container's own
    # cgroup headroom. Forks are reaped by the container's cgroup OOM (signal 9)
    # when their combined working set exceeds the container limit, no matter how
    # much RAM the host still reports — so sizing against the host alone lets a
    # near-full container fan out into an instant OOM.
    cg_avail = _cgroup_available_mb()
    if cg_avail is not None:
        avail = min(avail, cg_avail)
    try:
        floor_mb = max(0, int(os.environ.get("HEAVY_GATE_LIVE_RAM_FLOOR_MB", "1500")))
    except (TypeError, ValueError):
        floor_mb = 1500
    try:
        private_mb = max(
            100,
            int(private_mb_override or os.environ.get("OPTIMIZE_WORKER_PRIVATE_MB", "700")),
        )
    except (TypeError, ValueError):
        private_mb = 700
    # Use the same headroom floor as the gate, but scale the private-worker
    # estimate by the number of live optims so concurrent jobs self-throttle.
    usable = max(0, avail - floor_mb)
    ram_cap = max(1, usable // max(1, private_mb * live))
    effective = max(1, min(req, ram_cap))
    if effective < req:
        logger.info(
            "[OPTIM_PARALLEL] live-RAM parallelism clamped %d -> %d "
            "(avail=%dMB live_optims=%d floor=%d private=%d)",
            req, effective, avail, live, floor_mb, private_mb,
        )
    return effective


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


# ── Per-combo phase profiler (OPTIM_PROFILE=1) ──────────────────────────────
# Measured externally, the per-combo path accounts for only ~1.08s of a ~2.43s
# wall slot per worker (engine ~925ms + post-engine ~150ms, most of it the two
# Rust workbook builds). ~1.35s was unaccounted for, and naming a culprit
# without measuring is what produced three wrong diagnoses before this. These
# probes time every phase INCLUDING the persistence calls, so the gap has
# nowhere left to hide. Off unless OPTIM_PROFILE=1 — a perf_counter pair per
# phase is cheap, but this stays opt-in so production runs are untouched.
_PROFILE = os.environ.get("OPTIM_PROFILE", "0").strip().lower() in ("1", "true", "yes")
_PROFILE_EVERY = int(os.environ.get("OPTIM_PROFILE_EVERY", "100"))
_prof_acc: Dict[str, float] = {}
_prof_n = 0


def _pm(bucket: str, t0: float) -> float:
    """Accumulate elapsed since t0 into `bucket`; returns a fresh timestamp."""
    if _PROFILE:
        _prof_acc[bucket] = _prof_acc.get(bucket, 0.0) + (time.perf_counter() - t0)
    return time.perf_counter()


def _pflush(combo_id: int, final: bool = False) -> None:
    """Log the accumulated phase breakdown every _PROFILE_EVERY combos.

    `final=True` forces a report at batch end: the counter is PER-PROCESS, and a
    96-combo sweep across 12 forked children gives each only ~8 combos, so a
    threshold-only flush printed nothing at all (observed).
    """
    global _prof_n
    if not _PROFILE:
        return
    if not final:
        _prof_n += 1
    if not _prof_n:
        return
    if not final and _prof_n % _PROFILE_EVERY:
        return
    tot = sum(_prof_acc.values()) or 1e-9
    parts = " ".join(
        f"{k}={v / _prof_n * 1000:.0f}ms/{100 * v / tot:.0f}%"
        for k, v in sorted(_prof_acc.items(), key=lambda kv: -kv[1])
    )
    logger.info("[OPTIM_PROFILE] pid=%d n=%d avg_total=%.0fms | %s",
                os.getpid(), _prof_n, tot / _prof_n * 1000, parts)


def _rust_loop_mode_safe() -> str:
    """OPTIMIZE_RUST_LOOP mode, resilient in forked workers (default "0" on any error)."""
    try:
        from services.optimizer.rust_combo_loop import rust_loop_mode
        return rust_loop_mode()
    except Exception:
        return "0"

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
        # YEARLY: every trade shares one December Expiry, so WOW's normal
        # bucket-by-Expiry would drop the whole year into ISO week 52. There the
        # roll segment IS the week, so WOW keys on Exit Date instead. Read from
        # the run's base payload — combos never vary expiry_type.
        _is_yearly = str(base_payload.get("expiry_type") or "").upper() == "YEARLY"
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
        # Which per-combo workbook variants this job's download_mode will ask for.
        try:
            from services.optimizer.runner import _download_mode_flags as _dmf
            _want_patchwise, _want_overall = _dmf(base_payload)
        except Exception:
            _want_patchwise, _want_overall = True, False

        def _inline_wm(trades_df, has_mc, tag, combo_id):
            """Per-combo WOW/MOM data for the variant this job will download.

            Computed here, in the parallel workers, from the in-memory trades_df.
            _prebuild_wow_mom has always had a fast path keyed on these values,
            but they were only produced under OPTIMIZE_INLINE_FINALIZE — which is
            "0" — so EVERY combo fell to the slow path there: re-read the CSV and
            rebuild the cleaned rows (measured: 44s for 180 combos, 3m39s for
            2160). Deliberately NOT under that flag: the flag was turned off
            because inline finalization computed BOTH variants plus extra summary
            passes and inflated the sweep; this computes only the variant
            download_mode actually asks for, so it replaces the finalize work
            rather than adding to it. Summary math is untouched.

            Returns (wm_overall, wm_patchwise, has_midcap). has_midcap must come
            back out: _prebuild_wow_mom's fast path reads it from the stored row,
            so leaving it False here would silently drop the midcap columns that
            the slow path used to derive for itself.
            """
            if hasattr(trades_df, "empty") and trades_df.empty:
                return None, None, has_mc
            wm_over = wm_pw = None
            found_mc = has_mc
            try:
                if _want_overall:
                    _cl_o, found_mc = _bcc(trades_df, _mc_legs, _mc_sa, _mc_sym,
                                           patchwise=False, filter_segments=_filter_segments)
                    wm_over = _wm_from_cleaned(_cl_o, found_mc, yearly=_is_yearly)
                if _want_patchwise:
                    _cl_p, found_mc = _bcc(trades_df, _mc_legs, _mc_sa, _mc_sym,
                                           patchwise=True, filter_segments=_filter_segments)
                    wm_pw = _wm_from_cleaned(_cl_p, found_mc, yearly=_is_yearly)
            except Exception as _exc:
                logger.warning("[OPTIM] %s WOW/MOM data failed for combo %d: %s",
                               tag, combo_id, _exc)
                return None, None, has_mc
            return wm_over, wm_pw, bool(found_mc)

        # Tradesheet skip decision — computed ONCE per worker, not per combo.
        # Dynamic on the job's own total combo count (not a per-worker chunk
        # size, which would vary): OPTIMIZE_SKIP_TRADESHEETS_ABOVE_COMBOS lets
        # large sweeps skip the expensive per-combo CSV/XLSX write (profiled at
        # ~300ms/combo, and a real source of disk-I/O contention at high fork
        # widths) while small sweeps keep full tradesheets. 0 (default) means
        # "no threshold" — falls back to the static OPTIMIZE_SKIP_TRADESHEETS
        # flag only.
        _skip_ts_static = os.environ.get("OPTIMIZE_SKIP_TRADESHEETS", "0").strip().lower() in ("1", "true", "yes")
        _skip_ts_threshold = int(os.environ.get("OPTIMIZE_SKIP_TRADESHEETS_ABOVE_COMBOS", "0") or "0")
        _skip_ts_job_total = None
        if _skip_ts_threshold > 0:
            try:
                _skip_ts_job_total = (result_store.get_meta(job_id) or {}).get("total")
            except Exception:
                _skip_ts_job_total = None
        _skip_ts = _skip_ts_static or (
            _skip_ts_threshold > 0
            and _skip_ts_job_total is not None
            and _skip_ts_job_total > _skip_ts_threshold
        )
        # WOW/MOM skip — SAME threshold as tradesheets (user rule: above the
        # limit a sweep produces neither the tradesheets ZIP nor WOW/MOM, only
        # the Summary). Skips the per-combo WOW/MOM compute + on-disk store; the
        # merged workbook + download are refused in routers/optimize.py on the
        # same total, so nothing tries to read what we never wrote. NOT tied to
        # the static OPTIMIZE_SKIP_TRADESHEETS flag (that is tradesheet-only).
        _skip_wm = (
            _skip_ts_threshold > 0
            and _skip_ts_job_total is not None
            and _skip_ts_job_total > _skip_ts_threshold
        )

        done = 0
        failures = 0
        first_error = None
        for i, combo in enumerate(chunk):
            try:
                # A resumed sweep dispatches only the combos it still owes, so
                # position in the chunk no longer identifies the combo. The
                # runner stamps the ORIGINAL 1-based index here; without it a
                # resume would renumber survivors and their combo_label_safe
                # would collide with files the first run already wrote (combo 1
                # of the resume overwriting combo 1 of the original). Popped
                # before apply_combo_for_optim so it is never read as a payload
                # path — same contract as __optim_callback__.
                _p = time.perf_counter()
                _orig_id = combo.pop("__combo_id__", None) if isinstance(combo, dict) else None
                merged = apply_combo_for_optim(base_payload, combo)
                t_combo = time.perf_counter()
                _p = _pm("setup", _p)
                trades_df, summary = _run_single_backtest(merged)
                _p = _pm("engine", _p)
                elapsed_ms = round((time.perf_counter() - t_combo) * 1000.0, 2)
                _rust_mode = _rust_loop_mode_safe()
                if _rust_mode == "1":
                    # Rust-authoritative summary — NO Python fallback (Rust-bounded rule).
                    # Hard-fail on any shape the Rust batch does not own yet.
                    from services.optimizer.rust_combo_loop import (
                        require_rust_supported, rust_authoritative_summary,
                    )
                    require_rust_supported(merged)
                    flat_summary, _summary_pw = rust_authoritative_summary(
                        trades_df, summary, merged, _mc_legs, _mc_sa, _mc_sym, _filter_segments)
                    _has_mc = bool(flat_summary.get("has_midcap"))
                    # _has_mc already came from the Rust summary — keep it as the
                    # authority and only let the cleaned build confirm it.
                    _wm_over = _wm_pw = None
                    if not _skip_wm:
                        _wm_over, _wm_pw, _has_mc = _inline_wm(
                            trades_df, _has_mc, "rust-mode", starting_combo_id + i)
                else:
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
                    # WOW/MOM data, computed OUTSIDE the _INLINE_FINALIZE gate (see
                    # _inline_wm) so the finalize pass never rebuilds it from CSV.
                    # Skipped above the combo threshold — the download is refused
                    # there, so this data would never be read (stays None from above).
                    if not _skip_wm:
                        _wm_over, _wm_pw, _has_mc = _inline_wm(trades_df, _has_mc, "inline",
                                                      starting_combo_id + i)
                _p = _pm("metrics", _p)
                labels = label_combo(merged)
                _combo_id = _orig_id if _orig_id is not None else starting_combo_id + i
                combo_label_safe = f"{_combo_id}_{safe_filename(labels['combo_label'])}"
                # RUST SHADOW (OPTIMIZE_RUST_LOOP=shadow): recompute the summary with the
                # ported Rust engine and diff it vs the Python summary above. Additive,
                # read-only, default-off — proves the port on every real combo. Never
                # affects output (Python stays authoritative in shadow mode).
                try:
                    from services.optimizer.rust_combo_loop import run_shadow_summary_check
                    run_shadow_summary_check(
                        _combo_id, labels.get("combo_label") or combo_label_safe, merged,
                        trades_df, summary, flat_summary, _summary_pw,
                        midcap_legs=_mc_legs, midcap_spot_adjustment=_mc_sa,
                        midcap_symbol=_mc_sym, filter_segments=_filter_segments,
                    )
                except Exception:
                    pass
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
                        # Leg-wise view (additive) — one block per EXISTING leg, so a
                        # 2-leg sweep emits no L3 columns. See combo_labeler.
                        "leg_cols": labels.get("leg_cols") or [],
                        "overall_adjustment": labels.get("overall_adjustment") or "",
                        "midcap_leg": labels.get("midcap_leg") or "",
                        "midcap_adj": labels.get("midcap_adj") or "",
                    },
                    "summary": flat_summary,
                    "summary_pw": _summary_pw,
                    # WOW/MOM data goes to DISK, not into this row: it is 13.5 KB
                    # of a 16.0 KB row (84.5%), which at 60k combos is ~985 MB of
                    # Redis against a 500 MB maxmemory. `wm_on_disk` tells the
                    # finalizer to load it from there instead of rebuilding from
                    # CSV, so the fast path survives without the memory cost.
                    "wm_on_disk": (False if _skip_wm else result_store.write_combo_wm(
                        job_id, combo_label_safe, _wm_over, _wm_pw)),
                    "has_midcap": bool(_has_mc),
                    "inline_finalized": _summary_pw is not None,
                    "objective_value": obj.extract(flat_summary),
                    "trade_count": int(flat_summary.get("count", 0) or 0),
                    "elapsed_ms": elapsed_ms,
                }
                if flat_summary.get("count", 0) == 0:
                    failures += 1
                _p = _pm("row+wm_write", _p)
                result_store.append_result(job_id, row)
                result_store.increment_done(job_id)
                _p = _pm("redis_write", _p)
                if not _skip_ts and not trades_df.empty:
                    result_store.write_combo_tradesheet(job_id, combo_label_safe, trades_df)
                    _p = _pm("csv_write", _p)
                    _tdays = (_runner_mod._RUST_CONTEXT or {}).get("trading_days") or []
                    # Leg-wise "Rules" first sheet — identical to the backtest
                    # (build_rules_sheet is the Python mirror of the JS buildRulesSheet;
                    # _write_rules_sheet renders it). merged = this combo's payload.
                    from services.optimizer.rules_sheet import build_rules_sheet as _brs
                    _rules_sheet = _brs(merged, _filter_name)
                    _p = _pm("rules_sheet", _p)
                    # Build the variant(s) this job will actually DOWNLOAD.
                    # Previously the overall variant was written unconditionally
                    # and the patchwise one only under _INLINE_FINALIZE — so a
                    # download_mode=patchwise job (the default) built 2160 overall
                    # workbooks nobody wanted, finalize deleted every one of them,
                    # and then rebuilt 2160 patchwise ones from CSV. Same count of
                    # builds as before, just the right variant, so the ZIP fast
                    # path can assemble from them instead of rebuilding.
                    if _want_overall:
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
                            filter_name=_filter_name,
                            filter_segments=_filter_segments,
                            yearly=_is_yearly,
                            rules_sheet=_rules_sheet,
                        )
                    if _want_patchwise:
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
                            yearly=_is_yearly,
                            rules_sheet=_rules_sheet,
                        )
                _p = _pm("xlsx_write", _p)
                _pflush(_combo_id)
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
                # Keep the FIRST combo error so a sweep where every combo fails can
                # report WHY instead of just "0 done". Without this the reason lived
                # only in the worker log and the job still reported success.
                if not first_error:
                    first_error = str(exc)
                logger.warning("[OPTIM_PARALLEL] combo %d failed: %s", starting_combo_id + i, exc)
        _pflush(0, final=True)
        return {"done": done, "failures": failures, "error": first_error}
    except Exception as exc:
        logger.error("[OPTIM_PARALLEL] worker crashed: %s\n%s", exc, traceback.format_exc())
        return {"done": 0, "failures": len(chunk), "error": str(exc)}
    finally:
        try:
            _teardown_market_data()
        except Exception:
            pass


def split_width(
    solo_ceiling: int,
    parallelism: int,
    node_id: Optional[str] = None,
    base_payload: Optional[Dict[str, Any]] = None,
) -> int:
    """How many children this sweep may fork RIGHT NOW.

    Re-derived every batch from three live constraints, so the width tracks the
    box instead of a single sample taken at launch:
      • other optims running   -> share the ceiling with them
      • jobs BLOCKED on the memory gate -> share with them too, so a backtest
        waiting behind this sweep gets budget handed back within one batch
        instead of sitting there for the whole run
      • free RAM right now     -> cap_parallelism_for_live_ram

    solo_ceiling<=0 means the caller opted out of adaptive width.
    """
    if solo_ceiling <= 0:
        return max(1, parallelism)
    try:
        from services.optimizer import result_store as _rs
        live = max(1, _rs.active_optim_count(node_id))
    except Exception:
        return max(1, parallelism)      # registry unreadable -> fail safe, don't widen
    # Count BACKTEST waiters only. An optimize-kind waiter is ALREADY inside
    # active_optim_count(): worker/tasks.py registers a job in the live set
    # BEFORE it calls the gate, so counting it here as well made a single queued
    # sweep worth two claimants and narrowed the running job harder than the box
    # required (observed: live=2 + waiting=1 -> "live_optims=3" with only two
    # real jobs). Backtests are never in that registry, so they are counted here
    # and here only — which is exactly the yield this mechanism exists for.
    waiting = 0
    try:
        from services import memory_gate as _mg
        waiting = max(0, _mg.waiting_count("backtest", node_id=node_id))
    except Exception:
        waiting = 0                     # gate unreadable -> behave as before
    claimants = live + waiting
    by_optims = max(1, solo_ceiling // claimants)
    try:
        if _is_multi_index_payload(base_payload):
            # A multi-symbol worker dirties more CoW pages during symbol merge,
            # expiry synchronization and recovery reloads than a single-index
            # worker.  Size only those jobs with the conservative measurement;
            # existing single-index fork width is unchanged.
            # Measured on THIS box: a FUSED multi-index fork (NIFTY + MIDCPNIFTY,
            # both feathers + OHLC + the fused-simulate working set) does NOT peak
            # at a steady ~830 MB — heavy combos spike to ~1650 MB, and the clamp
            # reads the cgroup at a transient low point between batches, so 600 and
            # even 1000 let it pick P=4/8 which then overran the 9 GB container
            # cgroup → kernel SIGKILL (signal 9). 1800 MB reflects the real spike
            # (P≈2 at typical free RAM), which fits with headroom. Env-tunable:
            # lower it on a bigger box for more parallelism.
            private_mb = max(
                int(os.environ.get("OPTIMIZE_WORKER_PRIVATE_MB", "700")),
                int(os.environ.get("OPTIMIZE_MULTI_INDEX_WORKER_PRIVATE_MB", "1800")),
            )
            return max(1, cap_parallelism_for_live_ram(
                by_optims, claimants, private_mb_override=private_mb,
            ))
        return max(1, cap_parallelism_for_live_ram(by_optims, claimants))
    except Exception:
        return max(1, min(parallelism, by_optims))


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
    # Full-box width for a lone optim, and the node whose live-optim registry
    # decides the split. Supplied -> the pool RE-SIZES between batches as other
    # optims start/finish. Omitted (0/None) -> fixed `parallelism`, as before.
    solo_ceiling: int = 0,
    node_id: Optional[str] = None,
) -> Dict[str, int]:
    """
    Execute the optimizer loop in `parallelism` worker processes.

    Returns aggregated `{done, failures}`. Per-combo results are already in
    Redis by the time this returns.
    """
    # (A temporary guard here forced P=1 for sync_weekly_roll sweeps, because every
    # forked child deadlocked in that path. py-spy on a hung child traced it to
    # pl.scan_ipc(...).collect() in base._get_ohlc_range_from_feather — Polars' rayon
    # threads don't survive fork(). That fallback now uses pyarrow, which is
    # fork-safe, so the guard is gone and these sweeps fork like any other.)
    # P=1 is only TERMINAL when the caller opted out of adaptive width
    # (solo_ceiling<=0). With adaptive width on, a P=1 start must still fall
    # through to the batch loop below: P=1 usually means the box was momentarily
    # tight at launch (observed: MemAvailable 1459MB vs a 1500MB floor), and
    # returning here froze the sweep serial for its whole life even after RAM
    # freed — 2160 combos crawling at P=1 with 4.1GB available. The batch loop
    # runs p==1 batches in-process (no fork, same memory profile as this branch)
    # and re-measures at every batch boundary, so the job climbs 1 -> 3 -> 6.
    if parallelism <= 1 and solo_ceiling <= 0:
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

    total_done = 0
    total_failures = 0
    first_error = None      # first combo error, surfaced when the whole sweep fails

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

    # Merge any OVERLAY index (e.g. MIDCPNIFTY on a multi-index strategy) into the
    # Rust cache BEFORE forking, so the children share ONE copy copy-on-write.
    #
    # _overlay_legs_onto_base also calls ensure_symbol_merged(), but a merge done in
    # a CHILD allocates ~600 MB of PRIVATE pages per worker — fork-CoW can only share
    # what already exists at fork time. At P=6 that is ~3.5 GB of pure duplication,
    # which OOM-killed the pool (cgroup SIGKILL -> WorkerLostError). Merging here
    # makes the children's ensure_symbol_merged() a no-op via the inherited
    # _merged_symbols set, restoring this module's load-once/fork-share design.
    #
    # Only worth doing when the parent already holds the base cache: otherwise the
    # child's own load_cache() would REPLACE this merge (and clear _merged_symbols),
    # putting us back to a per-child merge.
    try:
        if base_payload.get("multi_index_mode"):
            from services import rust_fast_path as _rf_pre
            if _rf_pre.get_loaded_feather_root():
                _base_idx = str(base_payload.get("index") or "").strip().upper()
                _ovl: List[str] = []
                for _l in (base_payload.get("legs") or []):
                    _s = str(_l.get("index") or _base_idx).strip().upper()
                    if _s and _s != _base_idx and _s not in _ovl:
                        _ovl.append(_s)
                for _s in _ovl:
                    if _rf_pre.ensure_symbol_merged(_s):
                        logger.info(
                            "[OPTIM_PARALLEL] pre-fork merged %s into the Rust cache — "
                            "shared CoW across %d workers (avoids ~600 MB per child)",
                            _s, parallelism,
                        )
    except Exception as _pre_exc:  # never block the sweep on an optimisation
        logger.warning("[OPTIM_PARALLEL] pre-fork overlay merge skipped: %s", _pre_exc)

    t0 = time.perf_counter()

    # ── ADAPTIVE FORK WIDTH ──────────────────────────────────────────────────
    # Combos are dispatched in BATCHES and the pool is rebuilt at the CURRENT
    # split before each batch, instead of one up-front _chunk(combos, P) that
    # froze P for the whole sweep. That freeze is what OOM-killed the box: a job
    # that started alone forked 6 children and kept all 6 when a second optim
    # arrived and forked 3 more — 9 live children inside a cap sized for 6.
    #
    # Now the width tracks BOTH directions, per the batch boundary:
    #   • a second optim registers  -> live=2 -> this job drops 6 -> 3
    #   • that optim finishes       -> live=1 -> this job climbs 3 -> 6
    # Rebuilding (not just idling) the pool is what makes it OOM-safe: the
    # surplus children are terminated, so their private pages are actually
    # returned rather than merely going quiet.
    #
    # solo_ceiling<=0 means the caller opted out -> behave exactly as before.
    #
    # Width is re-derived EVERY batch from both live constraints:
    #   • other optims running  -> solo_ceiling // live_optims
    #   • free RAM right now    -> cap_parallelism_for_live_ram (the runner's own check)
    # Capping at the width the runner picked at START was wrong in the other
    # direction: that number is a single RAM sample taken before the sweep began,
    # so a job that launched while the box was busy stayed pinned at P=1 for hours
    # even after the RAM freed (observed: 3600 combos crawling at P=1 with 3.9 GB
    # free — enough for P=3). Re-measuring keeps it honest both ways: it still can
    # never exceed what RAM allows right now, so it cannot OOM.
    def _current_p() -> int:
        return split_width(solo_ceiling, parallelism, node_id, base_payload)

    # Combos per worker per batch. Bigger = fewer re-forks (less overhead) but
    # coarser reaction to another optim arriving; this is the knob to turn if
    # either side hurts.
    _batch_per_worker = max(1, int(os.environ.get("OPTIMIZE_BATCH_PER_WORKER", "40")))
    # Hang-breaker for a SIGKILLed pool child (see ar.get below). Sized well
    # above any real batch: 40 combos/worker at even 10s each is ~7min, so 1h
    # only trips when a child is genuinely gone.
    try:
        _batch_timeout = max(60, int(os.environ.get("OPTIMIZE_BATCH_TIMEOUT_SECONDS", "3600")))
    except (TypeError, ValueError):
        _batch_timeout = 3600

    # When Celery revokes this task it sends SIGTERM to this process.
    # The billiard fork children are in a separate process group and do NOT
    # receive that signal — they keep running their full combo chunk.
    # Install a SIGTERM handler that calls pool.terminate() so all children
    # are killed immediately when the task is cancelled.
    import signal as _signal
    _pool_ref = [None]

    def _on_terminate(signum, frame):
        try:
            if _pool_ref[0] is not None:
                _pool_ref[0].terminate()
        except Exception:
            pass
        raise SystemExit(0)

    try:
        _old_sigterm = _signal.signal(_signal.SIGTERM, _on_terminate)
    except (OSError, ValueError):
        _old_sigterm = None  # not the main thread — signal() not allowed

    remaining = list(combos)
    offset = 1
    _last_p = None
    _batches = 0
    try:
        while remaining:
            p = _current_p()
            if p != _last_p:
                logger.info(
                    "[OPTIM_PARALLEL] fork width -> P=%d (was %s, %d combos left)",
                    p, _last_p if _last_p is not None else "-", len(remaining),
                )
                # Re-state the memory reservation at the width we're ACTUALLY about
                # to fork. Dropping 6->3 hands ~2 GB back so the optim that caused
                # the drop can start instead of queueing behind memory we freed;
                # going 3->6 re-takes it once we're alone again. job_id == the
                # Celery task id the gate keyed the reservation under.
                if solo_ceiling > 0 and _last_p is not None:
                    try:
                        from services import memory_gate as _mg
                        _mg.resize(job_id, _mg.cost_for_job("optimize", base_payload,
                                                            p_override=p), node_id)
                    except Exception as _rz:
                        logger.debug("[OPTIM_PARALLEL] gate resize skipped: %s", _rz)
                _last_p = p
            batch, remaining = remaining[: p * _batch_per_worker], remaining[p * _batch_per_worker:]
            chunks = _chunk(batch, p)
            _batches += 1

            # Drop pooled DB connections IMMEDIATELY before every fork, not just
            # once before the loop. The parent keeps querying between batches
            # (expiry resolution, rust_context, finalization), which lazily
            # reopens pooled connections — and fork() hands the child both the
            # live socket and the pool's mutex, without the thread that would
            # release it. A child that then touches the DB parks in futex_do_wait
            # forever (seen: 6 children, 0/36 combos, CPU 0.04% on a 387-window
            # sweep; a short sweep does little post-dispose DB work and survived).
            # Cost is ~0: dispose only closes idle sockets, and the pool reopens
            # lazily on next use — no reconnect happens unless something queries.
            try:
                from database import get_engine as _ge
                _ge().dispose()
            except Exception:
                pass

            # NOTE: p==1 forks a single-child pool rather than running the batch
            # in-process. Running it in-process was tried and deadlocked the very
            # next batch: the engine initialises Rust/rayon thread state in the
            # PARENT, and children forked afterwards inherit that state without
            # the threads that own it, so they hang before their first combo
            # (observed: 40 combos done, then P=3 forked and produced nothing,
            # CPU 1%, all children sleeping). Every batch forking from a pristine
            # parent is exactly why P>=2 has always been safe. The extra child is
            # near-free — the caches are inherited copy-on-write.
            pool = ctx.Pool(processes=p)
            _pool_ref[0] = pool
            try:
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

                # BOUNDED wait. A pool child that is SIGKILLed (cgroup OOM) dies
                # without reporting, and an unbounded ar.get() then blocks the
                # sweep FOREVER: observed a job wedged at 160/648, parent asleep
                # at 0.06% CPU still holding 5.6 GB, its dead children left as
                # zombies because the blocked parent never reaped them. SIGKILL
                # is uncatchable, so the child cannot fail gracefully — the
                # parent has to give up on its own. This is a hang-breaker, not
                # a policy: the default is deliberately far longer than any real
                # batch, so it can only fire when something has actually died.
                for ar in async_results:
                    try:
                        res = ar.get(timeout=_batch_timeout)
                    except Exception as _lost:
                        # Includes billiard's TimeoutError and WorkerLostError.
                        # Surface it and keep going: the combos this chunk owned
                        # are simply missing, and a RESUME will pick them up
                        # because results are keyed by parameter values, not by
                        # position. Far better than wedging the whole sweep.
                        logger.error(
                            "[OPTIM_PARALLEL] batch chunk lost (%s: %s) — its combos "
                            "are unfinished; resume the job to fill them in",
                            type(_lost).__name__, _lost,
                        )
                        if not first_error:
                            first_error = f"worker lost: {type(_lost).__name__}: {_lost}"
                        total_failures += 1
                        continue
                    total_done += int(res.get("done", 0))
                    total_failures += int(res.get("failures", 0))
                    if not first_error:
                        first_error = res.get("error")
                    if progress_cb:
                        try:
                            progress_cb(total_done)
                        except Exception:
                            pass
                pool.join()
            finally:
                # Always reap this batch's children before re-sizing, so surplus
                # workers' memory is released rather than lingering into the next.
                try:
                    pool.terminate()
                    pool.join()
                except Exception:
                    pass
                _pool_ref[0] = None
    finally:
        if _old_sigterm is not None:
            try:
                _signal.signal(_signal.SIGTERM, _old_sigterm)
            except (OSError, ValueError):
                pass
    logger.info(
        "[OPTIM_PARALLEL] %d combos in %.2fs across %d batches (final P=%s)",
        total_done,
        time.perf_counter() - t0,
        _batches,
        _last_p,
    )
    return {"done": total_done, "failures": total_failures, "error": first_error}
