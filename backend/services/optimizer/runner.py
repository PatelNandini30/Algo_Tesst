"""
Optimization runner.

Responsibilities
----------------
1. Validate the request (parameter specs + base payload + method).
2. Bulk-load market data ONCE (this is the expensive step).
3. Build the in-process fast_lookup ONCE.
4. Iterate combinations from the chosen sampler; for each combo:
       a. Apply combo overrides to the base payload.
       b. Run the Rust engine ONLY (`run_rust_engine_pipeline` via the rust
          fast path). No Python fallback — a combo Rust can't handle hard-fails
          and is recorded as a failure (see `_run_single_backtest`).
       c. Recompute analytics (`compute_analytics`) on the trades.
       d. Layer in the extra optimizer metrics (`compute_optim_metrics`).
       e. Persist combo + flattened summary to result_store.
       f. Update progress.
5. Mark job complete (success / failure).

We deliberately DO NOT use multiprocessing in this v1: the Celery worker is
already isolated per task, and parallel processes would each have to reload
the bulk options data (defeating the whole point). The Rust hot-path already
provides per-iteration acceleration. Phase 2 introduces a Rust optimizer
batch function for true parallelism.

The `OPTIMIZE_USE_RUST_BATCH` env var is checked but currently a no-op (Phase 2
placeholder).
"""
from __future__ import annotations

import bisect
import logging
import os
import time
import traceback
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from services.optimizer import result_store
from services.optimizer.combo_labeler import label_combo, safe_filename
from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.objective import resolve_objective
from services.optimizer.param_expander import apply_combo_for_optim, count_combinations
from services.optimizer.samplers import build_sampler

logger = logging.getLogger(__name__)


# ── Hard cap on total combinations the API will accept ──────────────────────
# A 5-param × 10-step grid is 100k combos — for safety we cap by default
# at 100k for Exhaustive, allowing the user to switch to Random/Smart for
# bigger spaces.
MAX_COMBOS = int(os.environ.get("OPTIMIZE_MAX_COMBOS", "100000"))


class OptimizationError(Exception):
    pass


def validate_request(
    base_payload: Dict[str, Any],
    param_specs: Sequence[Dict[str, Any]],
    method: str,
    sample_n: Optional[int] = None,
) -> int:
    """Return the planned total iteration count, or raise OptimizationError."""
    if not base_payload:
        raise OptimizationError("Missing base strategy payload")
    if not param_specs:
        raise OptimizationError("No parameters selected for optimization")

    # Validate that any spot_adjustment_pct values being swept are >= 0.1.
    # A value of 0 (or near-0) means "trigger on every tick" which creates
    # an effectively infinite cascade of bridge trades per trading day.
    for spec in param_specs:
        path = spec.get("path", "")
        if "spot_adjustment_pct" in path or path == "spot_adjustment.pct":
            from services.optimizer.param_expander import _expand_values
            try:
                vals = _expand_values(spec)
            except Exception:
                vals = []
            for v in vals:
                try:
                    if float(v) < 0.1:
                        raise OptimizationError(
                            f"spot_adjustment_pct value {v} is too small (minimum 0.1). "
                            "Values below 0.1% trigger on nearly every trading day and "
                            "cause extremely long runtimes."
                        )
                except (TypeError, ValueError):
                    pass

    grid_size = count_combinations(param_specs)
    if grid_size <= 0:
        raise OptimizationError("Empty parameter grid")

    method = (method or "exhaustive").lower()
    if method == "exhaustive":
        if grid_size > MAX_COMBOS:
            raise OptimizationError(
                f"Grid has {grid_size} combinations — exceeds cap of {MAX_COMBOS}. "
                "Narrow your ranges or switch to random / smart sampling."
            )
        return grid_size
    if method == "random":
        n = int(sample_n or 0)
        if n <= 0:
            raise OptimizationError("Random sampling requires sample_n > 0")
        return min(n, grid_size)
    if method == "smart":
        n = int(sample_n or 200)
        return n
    raise OptimizationError(f"Unknown method: {method!r}")


_RUST_BLOCKING_TOP_TRUTHY: Tuple[str, ...] = ()


def _payload_is_rust_compatible(payload: Dict[str, Any]) -> bool:
    """
    Mirror of `simulate.rs::check_strategy_blockers` + `extract_leg_cfgs`
    bloacker checks. Returns True when EVERY combo built from this payload
    will be served by the Rust fast path — in which case the optimizer can
    skip the ~500MB Python bulk_options dict per worker (lean memory mode).
    """
    for key in _RUST_BLOCKING_TOP_TRUTHY:
        if payload.get(key):
            return False

    # filter_entry_mode='fixed' and 'min_days': premium-based strike modes are
    # now supported via the Rust feather (Slice 10a — _compute_strike_for_leg_python
    # calls get_strikes_for_date when entry_date/expiry/index are provided).

    for leg in (payload.get("legs") or []):
        if not isinstance(leg, dict):
            continue
        segment = str(leg.get("segment") or "").upper()
        # FUTURES with SL/Target/re-entry still fall back to Python; without
        # those controls, _build_futures_specs handles them in the Rust path.
        if segment in ("FUTURES", "FUTURE"):
            for risk_key in ("stopLoss", "targetProfit", "trailSL", "reEntryOnSL", "reEntryOnTarget"):
                v = leg.get(risk_key)
                if v and (not isinstance(v, dict) or any(v.values())):
                    return False
        # simpleMomentum: unimplemented in the engine — both Python and Rust ignore it safely.
        # rollover_strike_mode='fixed': handled by _apply_fixed_rollover_strike (Slice 9b).
        for key in ("reEntryOnSL", "reEntryOnTarget"):
            cfg = leg.get(key)
            if isinstance(cfg, dict) and cfg:
                mode = str(cfg.get("mode") or "RE_ASAP").upper()
                if mode not in ("RE_ASAP", "RE_ASAP_REV", "LAZY_LEG", "RE_MOMENTUM", "RE_MOMENTUM_REV"):
                    return False
    return True


def _prepare_market_data(payload: Dict[str, Any], lean: bool = False) -> Dict[str, Any]:
    """
    Trigger the existing one-shot bulk-load + fast_lookup builders. Returns
    a small dict the runner can later pass back to clear state.

    When `lean=True`, the heavy Python bulk_options dict (~500 MB) is SKIPPED.
    Only the Rust feather (mmap'd, shared across workers via OS page cache)
    and the rust_context are populated. The optimizer's Rust fast path doesn't
    need the Python dict — combos that would need it (= Rust-incompatible
    features) must not reach this branch.
    """
    from base import bulk_load_options as _bulk_load
    from services.algotest_job import (
        _build_fast_lookup_from_bulk,
        _normalize_cache_date,
        _safe_clear_fast_lookup,
        _should_build_fast_lookup,
    )

    index = payload.get("index") or payload.get("symbol") or "NIFTY"
    from_date = _normalize_cache_date(
        payload.get("_effective_from") or payload.get("from_date") or payload.get("date_from")
    )
    to_date = _normalize_cache_date(
        payload.get("_effective_to") or payload.get("to_date") or payload.get("date_to")
    )
    logger.info("[OPTIM] _prepare_market_data: index=%r from_date=%r to_date=%r payload_keys=%s",
                index, from_date, to_date, sorted(payload.keys()))

    # Clear any stale state from a prior task in the same worker.
    _safe_clear_fast_lookup()
    try:
        from base import bulk_clear_options
        bulk_clear_options()
    except Exception:
        pass

    t0 = time.perf_counter()
    _bulk_load(index, from_date, to_date)
    logger.info("[OPTIM] bulk_load_options %.2fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    # fast_lookup loads the Rust feather into MarketCache. The Rust feather is
    # mmap'd so it costs the same regardless of worker count — one OS-level
    # copy shared by all forked children.
    if lean or _should_build_fast_lookup(payload, from_date, to_date):
        _build_fast_lookup_from_bulk(index, from_date, to_date)
    logger.info("[OPTIM] fast_lookup %.2fs", time.perf_counter() - t0)

    if lean:
        # Lean mode: now that the Rust feather is in MarketCache, drop the
        # Python in-memory option/spot data structures. The Rust fast path
        # reads from MarketCache (mmap'd, shared) so the Python dicts are
        # dead weight. This saves ~500MB-1GB per worker, letting parallelism
        # scale up to ~12 instead of 4 within the 3GB container limit.
        t0 = time.perf_counter()
        try:
            import base as _base
            for attr in (
                "_bulk_bhav_by_date",
                "_option_lookup_cache",
                "_future_lookup_cache",
                "_strike_chain_cache",
            ):
                cache = getattr(_base, attr, None)
                if isinstance(cache, dict):
                    cache.clear()
            for attr in ("_bulk_bhav_df", "_bulk_spot_df"):
                if hasattr(_base, attr):
                    setattr(_base, attr, None)
            import gc
            gc.collect()
            logger.info(
                "[OPTIM] lean memory — cleared Python option/spot dicts in %.2fs (Rust feather kept)",
                time.perf_counter() - t0,
            )
        except Exception as exc:
            logger.warning("[OPTIM] lean cleanup partial: %s", exc)

    # Pre-compute the per-worker Rust context ONCE — trading days, expiries,
    # spots, lot_size. The optimizer reuses these across every combo,
    # eliminating ~10ms of cached-lookup overhead per combo (6400 combos
    # × 10ms = 64 seconds saved per worker on the small grid).
    ctx: Dict[str, Any] = {
        "index": index,
        "from_date": from_date,
        "to_date": to_date,
        "rust_context": None,
    }
    try:
        import pandas as pd
        import base as _base_mod
        from base import get_expiry_dates
        from engines.generic_algotest_engine import get_lot_size

        t0 = time.perf_counter()

        # Derive trading days + spots directly from the in-memory spot_lookup_table
        # that was populated by the feather shortcut — avoids DB queries that fail
        # when bhavcopy SQLite table is absent (PostgreSQL-only setups).
        sym_upper = index.upper()
        spot_table: Dict[str, float] = {}
        if hasattr(_base_mod, "_spot_lookup_table") and _base_mod._spot_lookup_table:
            for (d, s), v in _base_mod._spot_lookup_table.items():
                if s == sym_upper and from_date <= d <= to_date:
                    spot_table[d] = float(v)

        if spot_table:
            days = sorted(spot_table.keys())
            spots = spot_table
        else:
            # Fallback: query DB — only reached when feather shortcut was skipped
            from base import get_trading_calendar, get_spot_price_from_db
            days = pd.to_datetime(
                get_trading_calendar(from_date, to_date)["date"]
            ).sort_values().dt.strftime("%Y-%m-%d").tolist()
            spots = {}
            for d in days:
                v = get_spot_price_from_db(d, index)
                if v is not None:
                    spots[d] = float(v)

        expiry_type = payload.get("expiry_type", "weekly")
        df_exp = get_expiry_dates(index, expiry_type, from_date, to_date)
        expiries = []
        if df_exp is not None and not df_exp.empty:
            col = "Current Expiry" if "Current Expiry" in df_exp.columns else df_exp.columns[0]
            expiries = (
                pd.to_datetime(df_exp[col])
                .sort_values()
                .dt.strftime("%Y-%m-%d")
                .unique()
                .tolist()
            )

        lot_size = int(get_lot_size(index, days[0])) if days else 0

        ctx["rust_context"] = {
            "trading_days": days,
            "expiries": expiries,
            "spots": spots,
            "lot_size": lot_size,
        }
        # Preload OHLC as a compact pandas DataFrame (pyarrow, float32, category).
        # Used by _compute_mae_mfe_batch for MAE/MFE in both sequential and
        # parallel paths. Workers inherit it via CoW fork at zero extra memory cost.
        # Polars ohlc_df is intentionally NOT loaded — for 2019-2026 it adds ~500 MB
        # persistent + 2 GB transient spike from pl.read_ipc. The pandas path
        # already exists in _compute_mae_mfe_batch and produces identical results.
        try:
            import pyarrow as _pa_p
            import pyarrow.ipc as _pa_ipc_p
            import pyarrow.compute as _pc_p
            import pandas as _pd_dt
            from services import rust_fast_path as _rf
            _fpath = _rf._cache_root() / f"arrow-v2:bulk:{sym_upper}:full" / "options.feather"
            if _fpath.exists():
                _t2 = time.perf_counter()
                _ohlc_from = _pd_dt.Timestamp(from_date).date()
                _ohlc_to = _pd_dt.Timestamp(to_date).date()
                _needed_p = ["Symbol", "Date", "ExpiryDate", "StrikePrice",
                             "OptionType", "High", "Low", "SettledPrice"]
                _reader_p = _pa_ipc_p.open_file(str(_fpath))
                _avail_p = set(_reader_p.schema.names)
                _sel_p = [c for c in _needed_p if c in _avail_p]
                _tbl_p = _reader_p.read_all().select(_sel_p)
                if "OptionType" in _avail_p:
                    _tbl_p = _tbl_p.filter(_pc_p.is_in(
                        _tbl_p.column("OptionType"),
                        value_set=_pa_p.array(["CE", "PE"]),
                    ))
                if "Date" in _avail_p:
                    _tbl_p = _tbl_p.filter(_pc_p.and_(
                        _pc_p.greater_equal(_tbl_p.column("Date"), _pa_p.scalar(_ohlc_from)),
                        _pc_p.less_equal(_tbl_p.column("Date"), _pa_p.scalar(_ohlc_to)),
                    ))
                _ohlc_pd_p = _tbl_p.to_pandas(date_as_object=False)
                for _col_p in ("Symbol", "OptionType"):
                    if _col_p in _ohlc_pd_p.columns and _ohlc_pd_p[_col_p].dtype == object:
                        _ohlc_pd_p[_col_p] = _ohlc_pd_p[_col_p].astype("category")
                for _col_p in ("High", "Low", "SettledPrice"):
                    if _col_p in _ohlc_pd_p.columns:
                        _ohlc_pd_p[_col_p] = _ohlc_pd_p[_col_p].astype("float32")
                if "StrikePrice" in _ohlc_pd_p.columns:
                    _ohlc_pd_p["strike_r"] = _ohlc_pd_p["StrikePrice"].round(0).astype("int32")
                    _ohlc_pd_p = _ohlc_pd_p.drop(columns=["StrikePrice"])
                ctx["rust_context"]["ohlc_df_pandas"] = _ohlc_pd_p
                _mem_mb_p = _ohlc_pd_p.memory_usage(deep=True).sum() / (1024 * 1024)
                logger.info(
                    "[OPTIM] preloaded OHLC pandas: %d rows, %.1f MB in %.2fs",
                    len(_ohlc_pd_p), _mem_mb_p, time.perf_counter() - _t2,
                )
        except Exception as _exc:
            logger.warning("[OPTIM] OHLC pandas preload skipped: %s", _exc)

        logger.info(
            "[OPTIM] rust_context preloaded in %.2fs (days=%d, expiries=%d, spots=%d, source=%s)",
            time.perf_counter() - t0,
            len(days),
            len(expiries),
            len(spots),
            "spot_lookup_table" if spot_table else "db_query",
        )
    except Exception as exc:
        logger.warning("[OPTIM] rust_context preload failed (combos will use python fallback): %s", exc)

    # Expose the on-disk feather root so the parallel path can hand it to
    # workers — they skip bulk_load entirely and just mmap this directory.
    try:
        from services.rust_fast_path import get_loaded_feather_root
        ctx["feather_root"] = get_loaded_feather_root()
    except Exception:
        ctx["feather_root"] = None

    return ctx


def _teardown_market_data() -> None:
    try:
        from services.algotest_job import _safe_clear_fast_lookup
        _safe_clear_fast_lookup()
    except Exception:
        pass
    try:
        from base import bulk_clear_options
        bulk_clear_options()
    except Exception:
        pass


def _prebuild_csv_zip(job_id: str, base_payload: dict) -> None:
    """
    Enrich combo CSVs with MAE/MFE (OHLC already in _RUST_CONTEXT), build full
    XLSX tradesheets (Trade Sheet + Summary), and pack everything into a ZIP at
    the standard cache path.

    Called right before mark_complete while market data is still hot so the
    first download is instant — no lazy build needed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    import zipfile as _zf
    import pandas as _pd
    from services.optimizer.excel_builder import build_combo_xlsx as _build_xlsx

    zip_dir = os.environ.get("OPTIMIZE_ZIP_DIR", "/data/cache/optim_zips")
    zip_path = os.path.join(zip_dir, f"{job_id}.v2.zip")
    if os.path.isfile(zip_path):
        return

    trades_dir = result_store.get_trades_dir(job_id)
    if not os.path.isdir(trades_dir):
        return

    ctx = _RUST_CONTEXT
    trading_days: List[str] = (ctx or {}).get("trading_days") or []
    index_str = str(
        base_payload.get("index") or base_payload.get("symbol") or "NIFTY"
    ).upper()
    from_date = base_payload.get("from_date") or base_payload.get("date_from") or ""
    to_date   = base_payload.get("to_date")   or base_payload.get("date_to")   or ""

    files = sorted(os.listdir(trades_dir))
    csv_files = [f for f in files if f.endswith(".csv")]
    root_files = {"summary.csv", "run_config.csv"}
    combo_files = [f for f in csv_files if f not in root_files]

    if not combo_files:
        return

    # ── Step 0: Compute corrected metrics (same formulas as XLSX Summary Sheet) ─
    # Enrich each CSV with MAE/MFE in-memory, then derive every stat via the same
    # path as excel_builder._write_summary_sheet, and push corrections to Redis +
    # rewrite summary.csv so the master summary matches each combo XLSX exactly.
    try:
        from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _compute_metrics
        # Midcap overlay config (additive). When present, the master summary
        # metrics are computed on the COMBINED P&L via the same native engine.
        _mc_legs = base_payload.get("midcap_legs") or None
        _mc_sa   = base_payload.get("midcap_spot_adjustment") or None
        _mc_sym  = (
            (_mc_legs[0].get("symbol") if (_mc_legs and isinstance(_mc_legs[0], dict)) else None)
            or "NIFTYMIDCAP100"
        )
        _all_results = result_store.get_all_results(job_id)
        _result_by_label = {
            r.get("combo_label_safe", ""): r
            for r in _all_results if r.get("combo_label_safe")
        }
        _corrected: dict = {}
        for _fname in combo_files:
            _label_safe = _fname[:-4]
            _csv_path = os.path.join(trades_dir, _fname)
            _stored_summary = (_result_by_label.get(_label_safe) or {}).get("summary") or {}
            try:
                _df = _pd.read_csv(_csv_path, dtype=str)
                if _df.empty:
                    continue
                # Enrich with MAE/MFE if the CSV still has zeros (fast-exec path)
                if "MAE" in _df.columns and trading_days:
                    _mae_s = _pd.to_numeric(_df["MAE"], errors="coerce").fillna(0.0)
                    _mfe_s = _pd.to_numeric(_df["MFE"], errors="coerce").fillna(0.0)
                    if (_mae_s.abs().sum() + _mfe_s.abs().sum()) <= 0.0001:
                        for _col in ("Entry Date", "Exit Date"):
                            if _col in _df.columns:
                                _df[_col] = _pd.to_datetime(_df[_col], format="%d-%m-%Y", errors="coerce")
                        for _col in ("Strike", "Entry Price", "Entry Spot", "Exit Price", "Exit Spot",
                                     "Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                            if _col in _df.columns:
                                _df[_col] = _pd.to_numeric(_df[_col], errors="coerce")
                        _df = _compute_mae_mfe_batch(_df, index_str, trading_days)
                        if "Trade" in _df.columns:
                            _pr = _df.drop_duplicates(subset=["Trade"], keep="first")
                            _agg = _pr[["Trade"]].copy()
                            for _col in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                                if _col in _pr.columns:
                                    _agg[_col] = _pr[_col].values
                            _df = _compute_live_dd_from_mae(_df, _agg)
                        for _col in ("Entry Date", "Exit Date"):
                            if _col in _df.columns and hasattr(_df[_col], "dt"):
                                _df[_col] = _df[_col].dt.strftime("%d-%m-%Y")
                _corrected[_label_safe] = _compute_metrics(
                    _df, _stored_summary,
                    midcap_legs=_mc_legs, midcap_spot_adjustment=_mc_sa, midcap_symbol=_mc_sym,
                )
            except Exception as _ce:
                logger.warning("[OPTIM] Metric correction skipped for %s: %s", _label_safe, _ce)

        if _corrected:
            result_store.update_result_summaries(job_id, _corrected)
            result_store.write_summary_csv(job_id, result_store.get_all_results(job_id))
            logger.info("[OPTIM] Corrected summary metrics for %d combos in job %s",
                        len(_corrected), job_id[:8])
    except Exception as _step0_exc:
        logger.warning("[OPTIM] Step 0 metric correction failed for job %s: %s", job_id[:8], _step0_exc)

    # ── Fast path: per-combo XLSX files already written during execution ──────
    xlsx_files_disk = sorted(f for f in files if f.endswith(".xlsx"))
    combo_labels_set = {f[:-4] for f in combo_files}
    xlsx_labels_set = {f[:-5] for f in xlsx_files_disk}
    if combo_labels_set and combo_labels_set.issubset(xlsx_labels_set):
        # All combos already have an XLSX on disk (built inline with MAE/MFE
        # from the in-memory trades_df). Skip Steps 1+2 entirely.
        os.makedirs(zip_dir, exist_ok=True)
        tmp_path = zip_path + ".building"
        try:
            with _zf.ZipFile(tmp_path, "w", _zf.ZIP_DEFLATED, compresslevel=3) as zf:
                for fname in csv_files:
                    if fname in root_files:
                        zf.write(os.path.join(trades_dir, fname), fname)
                for xlsx_fname in xlsx_files_disk:
                    label_safe = xlsx_fname[:-5]
                    if label_safe in combo_labels_set:
                        zf.write(
                            os.path.join(trades_dir, xlsx_fname),
                            f"tradesheets/{xlsx_fname}",
                        )
                for fname in combo_files:
                    label_safe = fname[:-4]
                    if label_safe not in xlsx_labels_set:
                        fpath = os.path.join(trades_dir, fname)
                        if os.path.isfile(fpath):
                            zf.write(fpath, f"tradesheets/{fname}")
            os.replace(tmp_path, zip_path)
            logger.info(
                "[OPTIM] Pre-built ZIP for job %s (fast-path, per-combo XLSX): %d xlsx",
                job_id[:8], len(xlsx_files_disk),
            )
        except Exception as _e:
            logger.warning("[OPTIM] ZIP fast-path failed for job %s: %s", job_id[:8], _e)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        return

    # ── Step 1: Enrich each combo CSV with MAE/MFE while OHLC is in _RUST_CONTEXT.
    for fname in combo_files:
        csv_path = os.path.join(trades_dir, fname)
        try:
            df = _pd.read_csv(csv_path, dtype=str)
            if df.empty or "MAE" not in df.columns:
                continue
            mae_s = _pd.to_numeric(df["MAE"], errors="coerce").fillna(0.0)
            mfe_s = _pd.to_numeric(df["MFE"], errors="coerce").fillna(0.0)
            if (mae_s.abs().sum() + mfe_s.abs().sum()) > 0.0001:
                continue  # already enriched
            for col in ("Entry Date", "Exit Date"):
                if col in df.columns:
                    df[col] = _pd.to_datetime(df[col], format="%d-%m-%Y", errors="coerce")
            for col in ("Strike", "Entry Price", "Entry Spot", "Exit Price", "Exit Spot",
                        "Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                if col in df.columns:
                    df[col] = _pd.to_numeric(df[col], errors="coerce")
            df = _compute_mae_mfe_batch(df, index_str, trading_days)
            if "Trade" in df.columns:
                parent_rows = df.drop_duplicates(subset=["Trade"], keep="first")
                aggregated = parent_rows[["Trade"]].copy()
                for col in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                    if col in parent_rows.columns:
                        aggregated[col] = parent_rows[col].values
                df = _compute_live_dd_from_mae(df, aggregated)
            for col in ("Entry Date", "Exit Date"):
                if col in df.columns and hasattr(df[col], "dt"):
                    df[col] = df[col].dt.strftime("%d-%m-%Y")
            df.to_csv(csv_path, index=False)
        except Exception as _e:
            logger.warning("[OPTIM] MAE/MFE pre-enrich skipped for %s: %s", fname, _e)

    # ── Step 2: Build full XLSX for every combo using enriched CSVs.
    all_results = result_store.get_all_results(job_id)
    summary_by_label = {
        r.get("combo_label_safe", ""): r for r in all_results if r.get("combo_label_safe")
    }

    def _make_xlsx(fname: str):
        label_safe = fname[:-4]
        csv_path = os.path.join(trades_dir, fname)
        row = summary_by_label.get(label_safe, {})
        try:
            df = _pd.read_csv(csv_path, dtype=str)
            xlsx_bytes = _build_xlsx(
                df,
                row.get("summary") or {},
                combo_label=row.get("combo_label") or label_safe,
                from_date=from_date,
                to_date=to_date,
            )
            return label_safe, xlsx_bytes, None
        except Exception as _e:
            return label_safe, None, str(_e)

    n_workers = min(4, max(1, (os.cpu_count() or 2) - 1))
    xlsx_results: dict = {}
    with ThreadPoolExecutor(max_workers=n_workers) as _ex:
        futs = {_ex.submit(_make_xlsx, f): f for f in combo_files}
        for fut in _as_completed(futs):
            ls, xlsx_bytes, err = fut.result()
            if xlsx_bytes is not None:
                xlsx_results[ls] = xlsx_bytes
            else:
                logger.warning("[OPTIM] XLSX build failed for %s: %s", ls, err)

    # ── Step 3: Pack into ZIP (XLSX primary, CSV fallback for any that failed).
    os.makedirs(zip_dir, exist_ok=True)
    tmp_path = zip_path + ".building"
    try:
        with _zf.ZipFile(tmp_path, "w", _zf.ZIP_DEFLATED, compresslevel=3) as zf:
            for fname in csv_files:
                if fname in root_files:
                    zf.write(os.path.join(trades_dir, fname), fname)
            for label_safe, xlsx_bytes in xlsx_results.items():
                zf.writestr(f"tradesheets/{label_safe}.xlsx", xlsx_bytes)
            for fname in combo_files:
                label_safe = fname[:-4]
                if label_safe not in xlsx_results:
                    fpath = os.path.join(trades_dir, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, f"tradesheets/{fname}")
        os.replace(tmp_path, zip_path)
        n_fallback = len(combo_files) - len(xlsx_results)
        logger.info(
            "[OPTIM] Pre-built ZIP for job %s: %d xlsx, %d csv fallback",
            job_id[:8], len(xlsx_results), n_fallback,
        )
    except Exception as _e:
        logger.warning("[OPTIM] ZIP pre-build failed for job %s: %s", job_id[:8], _e)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _warm_feather_page_cache(feather_root: str) -> None:
    """Sequential read of all feather/arrow files in feather_root.

    With fork, children inherit the parent's AHashMap via CoW and don't need
    to read the feather at all.  This warm-up is kept as a safety net for the
    fallback path (no pre-built cache) and ensures the feather pages are in
    the OS page cache if a child ever needs to fall back to load_cache_from_root.

    Parent reads ~575 MB sequentially in ~3-5 s on HDD; any child fallback
    then hits RAM instead of disk.
    """
    import glob
    try:
        files = (
            glob.glob(os.path.join(feather_root, "*.feather"))
            + glob.glob(os.path.join(feather_root, "*.arrow"))
        )
        if not files:
            return
        t0 = time.perf_counter()
        total_mb = 0.0
        for fpath in files:
            sz = os.path.getsize(fpath)
            with open(fpath, "rb") as f:
                while f.read(4 * 1024 * 1024):
                    pass
            total_mb += sz / (1024 * 1024)
        logger.info(
            "[OPTIM] page-cache warmed: %.1f MB in %.1fs (%d files)",
            total_mb, time.perf_counter() - t0, len(files),
        )
    except Exception as exc:
        logger.warning("[OPTIM] page-cache warm failed (non-fatal): %s", exc)


# Per-worker Rust context cache. _prepare_market_data sets this; combos read
# from it on every iteration so we avoid recomputing trading_days/expiries/spots
# per combo. Worker-local — never shared across processes.
_RUST_CONTEXT: Optional[Dict[str, Any]] = None


def set_rust_context(ctx: Optional[Dict[str, Any]]) -> None:
    global _RUST_CONTEXT
    _RUST_CONTEXT = ctx


def _is_bearish_leg(leg_type: str, leg_bs: str) -> bool:
    """CE SELL, PE BUY or FUT SELL — profits when market falls."""
    t  = leg_type.upper()
    bs = leg_bs.upper()
    return (
        (t in ("CE", "CALL") and bs == "SELL")
        or (t in ("PE", "PUT") and bs == "BUY")
        or (t == "FUT" and bs == "SELL")
    )


def _is_bullish_leg(leg_type: str, leg_bs: str) -> bool:
    """CE BUY, PE SELL or FUT BUY — profits when market rises."""
    t  = leg_type.upper()
    bs = leg_bs.upper()
    return (
        (t in ("CE", "CALL") and bs == "BUY")
        or (t in ("PE", "PUT") and bs == "SELL")
        or (t == "FUT" and bs == "BUY")
    )


def _calc_final_mae_for_trade(trade_legs: "pd.DataFrame") -> Optional[float]:
    """
    Compute finalMae for a group of trade legs.
    Mirrors buildTradeExcel.js calcTradeMae function exactly.

    Every leg (option or future) is classified by market direction:
      Bullish (CE BUY / PE SELL / FUT BUY):  adverse when market falls, favorable when rises.
      Bearish (CE SELL / PE BUY / FUT SELL): adverse when market rises, favorable when falls.

    Unified rule (single-leg, multi-leg, options and futures alike):
      Net MAE 1 = sum(bullish MAE) + sum(bearish MFE)
      Net MAE 2 = sum(bullish MFE) + sum(bearish MAE)
      Final MAE = min(Net MAE 1, Net MAE 2)

    When every leg shares one direction this collapses to "all MAE" vs
    "all MFE"; mixed directions cross automatically.

    Returns None when MAE/MFE are all zero (not yet computed).
    """
    def _sum(legs: "pd.DataFrame", col: str) -> Optional[float]:
        vals = pd.to_numeric(legs[col], errors="coerce")
        if vals.isna().any():
            return None
        return float(vals.sum())

    dir_types = {"CE", "PE", "CALL", "PUT", "FUT"}
    dir_legs = trade_legs[trade_legs["Type"].str.upper().isin(dir_types)]
    if dir_legs.empty:
        return None

    all_mae = _sum(dir_legs, "MAE")
    all_mfe = _sum(dir_legs, "MFE")
    if all_mae is None or all_mfe is None:
        return None
    if all_mae == 0.0 and all_mfe == 0.0:
        return None  # MAE/MFE columns not yet computed

    bullish_mask = dir_legs.apply(
        lambda r: _is_bullish_leg(str(r["Type"]), str(r["B/S"])), axis=1
    )
    bearish_mask = dir_legs.apply(
        lambda r: _is_bearish_leg(str(r["Type"]), str(r["B/S"])), axis=1
    )
    bullish_mae = _sum(dir_legs[bullish_mask], "MAE")
    bullish_mfe = _sum(dir_legs[bullish_mask], "MFE")
    bearish_mae = _sum(dir_legs[bearish_mask], "MAE")
    bearish_mfe = _sum(dir_legs[bearish_mask], "MFE")
    if any(v is None for v in (bullish_mae, bullish_mfe, bearish_mae, bearish_mfe)):
        return None

    net_mae1 = bullish_mae + bearish_mfe  # type: ignore[operator]
    net_mae2 = bullish_mfe + bearish_mae  # type: ignore[operator]
    return round(min(net_mae1, net_mae2) * 10000) / 10000


def _expiry_cands_str(expiry_str: str) -> List[str]:
    """Return [exact, +1day, -1day] ISO strings — mirrors engine._expiry_candidates."""
    try:
        import pandas as _pd
        ts = _pd.Timestamp(expiry_str)
        return [
            ts.strftime("%Y-%m-%d"),
            (ts + _pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            (ts - _pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        ]
    except Exception:
        return [expiry_str]


# Exit reasons that get the SL/SL-with-buffer adverse-side cap (mirrors
# _SL_CAP_REASONS in engines/generic_algotest_engine.py — keep in sync).
_SL_CAP_REASONS = {
    "STOP_LOSS",
    "SL_WITH_BUFFER",
    "SL_WITH_BUFFER_GAP",
    "STOP_LOSS_BUFFER",
    "STOP_LOSS_BUFFER_GAP",
}


def _compute_mae_mfe_batch(
    df: "pd.DataFrame",
    index_str: str,
    trading_days: List[str],
) -> "pd.DataFrame":
    """
    Compute MAE/MFE for all option leg rows in df using ONE Polars feather scan.

    Formula identical to _calculate_mae_mfe_from_extremes in
    engines/generic_algotest_engine.py — no calculation changes:
      SELL: mae=(entry-high)/spot*100, mfe=(entry-low)/spot*100
      BUY:  mae=(low-entry)/spot*100,  mfe=(high-entry)/spot*100

    Window: (next_trading_day_after(entry_date), exit_date] — same as Python
    engine's _calculate_leg_mae_mfe.
    """
    if df.empty or not trading_days:
        return df
    if os.environ.get("BACKTEST_INCLUDE_MAE_MFE", "1").strip().lower() in ("0", "false", "no", "off"):
        return df

    try:
        import polars as pl
        from services import rust_fast_path as _rf
    except ImportError:
        return df

    sym_upper = index_str.upper()
    try:
        feather = _rf._cache_root() / f"arrow-v2:bulk:{sym_upper}:full" / "options.feather"
        if not feather.exists():
            return df
    except Exception:
        return df

    td_sorted = sorted(set(trading_days))

    # Per-row: gather window info
    rows_data: List[Optional[Dict]] = []
    for pos in range(len(df)):
        row = df.iloc[pos]
        opt_type = str(row.get("Type") or "").upper()
        if opt_type not in ("CE", "PE"):
            rows_data.append(None)
            continue
        strike = float(row.get("Strike") or 0.0)
        entry_price = float(row.get("Entry Price") or 0.0)
        entry_spot = float(row.get("Entry Spot") or 0.0)
        position = str(row.get("B/S") or "SELL").upper()
        if strike <= 0 or entry_spot <= 0:
            rows_data.append(None)
            continue
        expiry_raw = row.get("Expiry")
        entry_dt = row.get("Entry Date")
        exit_dt = row.get("Exit Date")
        if not expiry_raw or entry_dt is None or exit_dt is None:
            rows_data.append(None)
            continue
        try:
            import pandas as _pd

            def _to_iso(v) -> str:
                # DD-MM-YYYY strings (Python engine output) are misread by
                # pd.Timestamp as MM-DD-YYYY.  Detect by position of dashes.
                if isinstance(v, str) and len(v) == 10 and v[2] == "-" and v[5] == "-":
                    return _pd.to_datetime(v, format="%d-%m-%Y").strftime("%Y-%m-%d")
                return _pd.Timestamp(v).strftime("%Y-%m-%d")

            entry_str = _to_iso(entry_dt)
            exit_str = _to_iso(exit_dt)
            expiry_str = _to_iso(expiry_raw)
        except Exception:
            rows_data.append(None)
            continue
        # MAE/MFE window: next trading day after entry up to exit (same as Python engine)
        idx = bisect.bisect_right(td_sorted, entry_str)
        win_start = td_sorted[idx] if idx < len(td_sorted) else None
        if win_start is None or win_start > exit_str:
            win_start = entry_str  # same-day trade fallback
        if win_start > exit_str:
            rows_data.append(None)
            continue
        _exit_reason = str(row.get("Exit Reason") or "").upper().strip()
        try:
            _exit_price = float(row.get("Exit Price"))
        except (TypeError, ValueError):
            _exit_price = None
        rows_data.append({
            "pos": pos, "opt_type": opt_type, "strike": strike,
            "expiry_str": expiry_str, "win_start": win_start, "win_end": exit_str,
            "entry_price": entry_price, "position": position, "entry_spot": entry_spot,
            "exit_reason": _exit_reason, "exit_price": _exit_price,
        })

    valid_rows = [r for r in rows_data if r is not None]
    if not valid_rows:
        return df

    scan_from_str = min(r["win_start"] for r in valid_rows)
    scan_to_str = max(r["win_end"] for r in valid_rows)
    try:
        import pandas as _pd2
        from_dt = _pd2.Timestamp(scan_from_str).date()
        to_dt = _pd2.Timestamp(scan_to_str).date()
    except Exception:
        return df

    # Build a small lookup DataFrame of the unique (expiry, option_type, strike_rounded)
    # combinations we need, then JOIN against the date-range-filtered feather.
    # This avoids building a Polars OR-filter tree that grows with n_unique_combos
    # (a 750-clause OR on 3.87M rows took ~12-15 s/combo for 7-year runs).
    # With a JOIN, the feather is filtered by date range only (cheap) and then
    # matched via hash-join — O(n_rows) regardless of combo count.
    import pandas as _pd3

    unique_combos: Dict[Tuple, None] = {}
    for r in valid_rows:
        unique_combos[(r["expiry_str"], r["opt_type"], r["strike"])] = None

    _ctx = _RUST_CONTEXT
    # Fork-safe path: parallel workers pre-load the OHLC feather via pyarrow
    # (no Polars), since pl.read_ipc and any rayon-using Polars op deadlocks on
    # the dead inherited Rayon worker after fork. When ohlc_df_pandas is set we
    # bypass Polars entirely and do filter + join in pandas.
    if _ctx is not None and "ohlc_df_pandas" in _ctx:
        try:
            _full_pd = _ctx["ohlc_df_pandas"]
            # Date columns are datetime64[ns]; convert from_dt/to_dt to Timestamp
            # for vectorised comparison (orders of magnitude faster than object cmp).
            _from_ts = _pd3.Timestamp(from_dt)
            _to_ts = _pd3.Timestamp(to_dt)
            _mask = (
                (_full_pd["Symbol"] == sym_upper)
                & (_full_pd["Date"] >= _from_ts)
                & (_full_pd["Date"] <= _to_ts)
            )
            _cols = ["ExpiryDate", "OptionType", "strike_r", "Date", "High", "Low"]
            if "SettledPrice" in _full_pd.columns:
                _cols = _cols + ["SettledPrice"]
            ohlc_pd = _full_pd.loc[_mask, _cols].copy()
            ohlc_pd["date_str"] = ohlc_pd["Date"].dt.strftime("%Y-%m-%d")
            ohlc_pd["expiry_str"] = ohlc_pd["ExpiryDate"].dt.strftime("%Y-%m-%d")
            # OptionType is Categorical — cast to str for tuple comparison & MultiIndex.
            if str(ohlc_pd["OptionType"].dtype) == "category":
                ohlc_pd["OptionType"] = ohlc_pd["OptionType"].astype(str)
            needed = {
                (cand, r["opt_type"], int(round(r["strike"])))
                for r in valid_rows
                for cand in _expiry_cands_str(r["expiry_str"])
            }
            ohlc_pd = ohlc_pd[
                [(e, t, s) in needed for e, t, s in zip(
                    ohlc_pd["expiry_str"], ohlc_pd["OptionType"], ohlc_pd["strike_r"]
                )]
            ]
            if ohlc_pd.empty:
                return df
            _val_cols = ["High", "Low"] + (["SettledPrice"] if "SettledPrice" in ohlc_pd.columns else [])
            ohlc_idx = ohlc_pd.set_index(
                ["expiry_str", "OptionType", "strike_r", "date_str"]
            )[_val_cols]
        except Exception as exc:
            logger.debug("[OPTIM] MAE/MFE pandas fast-path failed: %s", exc)
            return df
    else:
        try:
            lookup_rows = []
            seen_lookup: set = set()
            for (exp_str, opt, strike) in unique_combos:
                for cand_str in _expiry_cands_str(exp_str):
                    key = (cand_str, opt, int(round(strike)))
                    if key in seen_lookup:
                        continue
                    seen_lookup.add(key)
                    lookup_rows.append({
                        "ExpiryDate": _pd3.Timestamp(cand_str).date(),
                        "OptionType": opt,
                        "strike_r": int(round(strike)),
                    })
            lookup_df = pl.DataFrame(
                lookup_rows,
                schema={"ExpiryDate": pl.Date, "OptionType": pl.Utf8, "strike_r": pl.Int32},
            )

            date_filter = (
                (pl.col("Symbol") == sym_upper)
                & (pl.col("Date") >= from_dt)
                & (pl.col("Date") <= to_dt)
            )
            _sel = ["ExpiryDate", "OptionType", "StrikePrice", "Date", "High", "Low"]
            # Include SettledPrice when the source carries it — enables the
            # backtest's zero-high/low → settled-price substitution below.
            if _ctx and "ohlc_df" in _ctx:
                if "SettledPrice" in _ctx["ohlc_df"].columns:
                    _sel = _sel + ["SettledPrice"]
                date_filtered = _ctx["ohlc_df"].filter(date_filter).select(_sel)
            else:
                try:
                    _feather_has_settled = "SettledPrice" in pl.read_ipc_schema(str(feather))
                except Exception:
                    _feather_has_settled = False
                if _feather_has_settled:
                    _sel = _sel + ["SettledPrice"]
                date_filtered = pl.scan_ipc(str(feather)).filter(date_filter).select(_sel).collect()

            # Round strike to int for the join key (matches strike_r in lookup_df)
            date_filtered = date_filtered.with_columns(
                (pl.col("StrikePrice").round(0).cast(pl.Int32)).alias("strike_r")
            )
            ohlc_raw = (
                date_filtered
                .join(lookup_df, on=["ExpiryDate", "OptionType", "strike_r"], how="inner")
                .select(_sel)
            )
        except Exception as exc:
            logger.debug("[OPTIM] MAE/MFE feather join failed: %s", exc)
            return df

        if ohlc_raw.is_empty():
            return df

        # Convert to pandas MultiIndex for fast per-day lookup
        try:
            import pandas as _pd4
            ohlc_pd = ohlc_raw.to_pandas()
            ohlc_pd["date_str"] = _pd4.to_datetime(ohlc_pd["Date"]).dt.strftime("%Y-%m-%d")
            ohlc_pd["expiry_str"] = _pd4.to_datetime(ohlc_pd["ExpiryDate"]).dt.strftime("%Y-%m-%d")
            ohlc_pd["strike_r"] = ohlc_pd["StrikePrice"].round(0).astype(int)
            _val_cols = ["High", "Low"] + (["SettledPrice"] if "SettledPrice" in ohlc_pd.columns else [])
            ohlc_idx = ohlc_pd.set_index(["expiry_str", "OptionType", "strike_r", "date_str"])[_val_cols]
        except Exception as exc:
            logger.debug("[OPTIM] MAE/MFE ohlc index build failed: %s", exc)
            return df

    df = df.copy()
    mae_vals = list(df["MAE"]) if "MAE" in df.columns else [0.0] * len(df)
    mfe_vals = list(df["MFE"]) if "MFE" in df.columns else [0.0] * len(df)

    # Whether the per-day lookup carries SettledPrice. When present we apply the
    # backtest's zero-high/low → settled-price substitution (mirrors get_ohlc_range
    # in backend/native/src/lib.rs) so optim MAE/MFE match the backtest tradesheet.
    _has_settled = "SettledPrice" in getattr(ohlc_idx, "columns", [])

    for r in valid_rows:
        pos = r["pos"]
        exp = r["expiry_str"]
        opt = r["opt_type"]
        strike_r = int(round(r["strike"]))
        win_start = r["win_start"]
        win_end = r["win_end"]
        entry_price = r["entry_price"]
        entry_spot = r["entry_spot"]
        position = r["position"]

        # Days in the OHLC window
        lo = bisect.bisect_left(td_sorted, win_start)
        hi = bisect.bisect_right(td_sorted, win_end)
        window_days = td_sorted[lo:hi]

        exp_cands = _expiry_cands_str(exp)
        highs: List[float] = []
        lows: List[float] = []
        for d in window_days:
            for cand_exp in exp_cands:
                try:
                    row_ohlc = ohlc_idx.loc[(cand_exp, opt, strike_r, d)]
                    # loc returns Series (single match) or DataFrame (rare duplicates)
                    if isinstance(row_ohlc, pd.DataFrame):
                        row_ohlc = row_ohlc.iloc[0]
                    _high = float(row_ohlc["High"])
                    _low = float(row_ohlc["Low"])
                    # Per-value SettledPrice substitution — mirrors the backtest's
                    # get_ohlc_range (backend/native/src/lib.rs): when High/Low is 0
                    # (illiquid / expiry day with no intraday trades) fall back to
                    # that day's settled price, applied independently to High and Low.
                    # A zero with no usable settled price contributes nothing, exactly
                    # as the Rust path leaves max_high/min_low unchanged for that day.
                    _settled = None
                    if _has_settled:
                        try:
                            _sv = float(row_ohlc["SettledPrice"])
                            if _sv > 0.0:
                                _settled = _sv
                        except (KeyError, TypeError, ValueError):
                            _settled = None
                    if _high > 0.0:
                        highs.append(_high)
                    elif _settled is not None:
                        highs.append(_settled)
                    if _low > 0.0:
                        lows.append(_low)
                    elif _settled is not None:
                        lows.append(_settled)
                    break
                except (KeyError, IndexError, TypeError):
                    continue

        if not highs or not lows:
            continue

        max_high = max(highs)
        min_low = min(lows)

        # SL / SL-with-buffer adverse-side cap (mirrors _cap_adverse_extreme_for_sl
        # in generic_algotest_engine.py): on a realised stop-out the adverse extreme
        # cannot be worse than the stop fill. SELL caps the High; BUY floors the Low.
        # MFE side untouched; non-SL exits keep the raw window extremes.
        if r["exit_reason"] in _SL_CAP_REASONS and r["exit_price"] is not None and r["exit_price"] > 0:
            if position == "SELL":
                max_high = min(max_high, r["exit_price"])
            else:
                min_low = max(min_low, r["exit_price"])

        # Same formula as _calculate_mae_mfe_from_extremes in generic_algotest_engine.py
        if position == "SELL":
            mae = (entry_price - max_high) / entry_spot
            mfe = (entry_price - min_low) / entry_spot
        else:
            mae = (min_low - entry_price) / entry_spot
            mfe = (max_high - entry_price) / entry_spot

        mae_vals[pos] = round(mae * 100, 4)
        mfe_vals[pos] = round(mfe * 100, 4)

    df["MAE"] = mae_vals
    df["MFE"] = mfe_vals
    return df


def _compute_live_dd_from_mae(
    df: "pd.DataFrame",
    aggregated: "pd.DataFrame",
) -> "pd.DataFrame":
    """
    Add 'Lowest NAV During Trade' column to df using Final MAE per trade.

    Formula (mirrors buildTradeExcel.js):
        finalMae = min(netMae1, netMae2)  — from _calc_final_mae_for_trade
        lowestNav = prevCum * (1 + finalMae/100)
        (prevCum = Cumulative of the prior trade, starting at 100.0;
         the FIRST trade therefore uses prevCum = 100.0 too — revised
         research-verified rule, AW = AU_prev * (1 + AM%) for all trades)

    Only parent rows (first occurrence of each trade) carry the value;
    secondary leg rows get None — matching Python engine convention.
    """
    if df.empty or "MAE" not in df.columns or "MFE" not in df.columns:
        return df

    df = df.copy()
    prev_cum = 100.0
    first_trade = True
    trade_lowest_nav: Dict[str, Optional[float]] = {}

    # Process trades in the same sorted order used by cumulative computation.
    # Revised research-verified rule: every trade (incl. the first, where
    # prev_cum = 100.0) anchors the low to AS_n = AN_(n-1) * (1 + AR_n/100).
    for _, agg_row in aggregated.iterrows():
        tid = str(agg_row.get("Trade") or "")
        if not tid:
            continue
        trade_legs = df[df["Trade"] == tid]
        final_mae = _calc_final_mae_for_trade(trade_legs) if not trade_legs.empty else None
        cum = agg_row.get("Cumulative")
        if final_mae is not None:
            trade_lowest_nav[tid] = round(prev_cum * (1.0 + float(final_mae) / 100.0) * 100) / 100
        else:
            trade_lowest_nav[tid] = None
        first_trade = False
        if cum is not None:
            try:
                prev_cum = float(cum)
            except (TypeError, ValueError):
                pass

    # Assign values to parent rows only
    seen: set = set()
    lnav_vals: List[Optional[float]] = []
    for _, row in df.iterrows():
        tid = str(row.get("Trade") or "")
        if tid not in seen:
            seen.add(tid)
            lnav_vals.append(trade_lowest_nav.get(tid))
        else:
            lnav_vals.append(None)
    df["Lowest NAV During Trade"] = lnav_vals
    return df


def _run_single_backtest_rust_fast(payload: Dict[str, Any]) -> Optional[tuple[pd.DataFrame, Dict[str, Any]]]:
    """
    Fastest path — call run_rust_engine_pipeline directly with the pre-computed
    per-worker context (trading_days/expiries/spots/lot_size). No per-combo DB
    fetches, no expiry-list rebuild, no spot dict construction. Just feed the
    payload to Rust and pipe priced rows through the tradesheet converter.

    Returns None if the Rust path can't handle the payload — caller MUST
    fall back to Python.
    """
    ctx = _RUST_CONTEXT
    if ctx is None:
        return None

    # Note: T-0 entry (entry_dte=0) is now handled correctly in Rust via
    # the settlement-price post-processing in run_rust_engine_pipeline —
    # when entry_date == exit_date == expiry_date the exit_price is replaced
    # with NSE intrinsic settlement (max(0,spot-strike) for CE). No fallback needed.

    try:
        import pandas as pd
        from base import compute_analytics
        from services.engine_rust import (
            run_rust_engine_pipeline,
            priced_to_tradesheet_records,
        )

        priced = run_rust_engine_pipeline(
            payload,
            expiry_dates=ctx["expiries"],
            trading_days=ctx["trading_days"],
            lot_size=ctx["lot_size"],
            spot_by_date=ctx["spots"],
            square_off_mode=payload.get("square_off_mode", "partial"),
        )
        if priced is None:
            return None
        if not priced:
            return pd.DataFrame(), {}

        records = priced_to_tradesheet_records(priced, payload, ctx["lot_size"])
        df = pd.DataFrame(records)
        for c in ("Entry Date", "Exit Date"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")

        # ── Bridge-trade split ────────────────────────────────────────────────
        # Bridge sub-trades (spot-adj re-entry cycles within the same trade
        # window) share the parent's trade_id in the Rust priced rows.  Rust
        # puts aggregate net_pnl only in the first row for each trade_id, so
        # child rows show 0 / blank for Net P&L, Cumulative, Peak, DD, etc.
        # Fix: give each bridge cycle its own unique trade_id so it appears as
        # an independent trade — matching the regular Python backtest tradesheet.
        #
        # Two rows are part of the SAME trade (multi-leg) when they share both
        # trade_id AND entry_date.  Different entry_date → different time window
        # → bridge sub-trade → gets a new trade_id.
        if not df.empty:
            # Use (orig_trade_id, entry_date) as the dedup key so that ALL legs
            # within the same bridge cycle (same entry date) get the SAME new
            # trade_id.  The first entry_date seen for a given orig_trade_id is
            # the "parent" cycle and keeps the original ID; subsequent distinct
            # entry_dates are bridge cycles and each gets a fresh ID.
            _orig_entry: dict = {}   # orig_trade_id → first entry_date seen
            _key_to_tid: dict = {}   # (orig_trade_id, entry_date) → assigned trade_id
            _next_btid = (
                max((int(str(r.get("trade_id") or 0)) for r in priced), default=0) + 10000
            )
            _new_tids: list = []
            try:
                _ed_strs = df["Entry Date"].dt.strftime("%Y-%m-%d").fillna("").tolist()
            except AttributeError:
                _ed_strs = (
                    pd.to_datetime(df["Entry Date"], errors="coerce")
                    .dt.strftime("%Y-%m-%d").fillna("").tolist()
                )
            for _tid_raw, _ed in zip(df["Trade"].astype(str).tolist(), _ed_strs):
                if _tid_raw not in _orig_entry:
                    _orig_entry[_tid_raw] = _ed
                key = (_tid_raw, _ed)
                if key not in _key_to_tid:
                    if _ed == _orig_entry[_tid_raw]:
                        _key_to_tid[key] = _tid_raw   # original cycle → keep ID
                    else:
                        _key_to_tid[key] = str(_next_btid)  # bridge cycle → new ID
                        _next_btid += 1
                _new_tids.append(_key_to_tid[key])
            df = df.copy()
            df["Trade"] = _new_tids

        # Recompute Net P&L and % P&L from per-leg values.
        # Rust puts aggregate net_pnl in the parent row only; after splitting
        # bridge sub-trades into their own trade_ids each needs P&L from its
        # own CE/PE/FUT legs (already correct per priced_to_tradesheet_records).
        # Net P&L and % P&L are computed at TRADE level (sum of all legs) and
        # propagated to the first leg row only — matching the Python engine's
        # tradesheet format. Per-leg values in CE P&L / PE P&L stay intact.
        # (The trade-level aggregation happens in the groupby below; we just
        # need the per-leg CE/PE values to be present for that sum to work.)

        # Mirror Python engine's per-trade aggregation + DD-MM date quirk so
        # CAGR/CAR-MDD match exactly. See services/algotest_job._try_rust_engine.
        aggregated = df.groupby("Trade", as_index=False).agg({
            "Entry Date": "first",
            "Exit Date": "first",
            "Entry Spot": "first",
            "Exit Spot": "first",
            "Spot P&L": "first",
            "CE P&L": "sum",
            "PE P&L": "sum",
            "FUT P&L": "sum",
            "Exit Reason": "first",
        })
        aggregated["Net P&L"] = (aggregated["CE P&L"] + aggregated["PE P&L"] + aggregated["FUT P&L"]).round(4)
        es_series = aggregated["Entry Spot"].replace(0, float("nan"))
        aggregated["% P&L"] = (aggregated["Net P&L"] / es_series * 100.0).round(2).fillna(0)

        # Compound cumulative seeded at 100 — matches engine convention.
        aggregated = aggregated.sort_values("Entry Date").reset_index(drop=True)
        cumulative, peak = 100.0, 100.0
        cum, pk, dd, pdd = [], [], [], []
        for _, r in aggregated.iterrows():
            es = float(r["Entry Spot"]) if r["Entry Spot"] else 0.0
            npl = float(r["Net P&L"]) if r["Net P&L"] else 0.0
            pct = (npl / es * 100.0) if es != 0 else 0.0
            cumulative *= (1.0 + pct / 100.0)
            peak = max(cumulative, peak)
            cum.append(cumulative); pk.append(peak)
            dd.append(cumulative - peak)
            pdd.append(((cumulative - peak) / peak * 100) if peak != 0 else 0.0)
        aggregated["Cumulative"] = cum
        aggregated["Peak"] = pk
        aggregated["DD"] = dd
        aggregated["%DD"] = pdd

        # Pass Timestamp-typed dates directly to compute_analytics.
        # The regular backtest path (algotest_job._try_rust_engine) converts
        # dates to DD-MM-YYYY strings to reproduce Python engine's lexicographic
        # date-sort quirk for exact-copy parity. The optimizer does NOT need
        # that parity — it compares combos, so correct n_years is critical.
        # String formats trip pd.to_datetime(x, dayfirst=True): "2020-01-09"
        # becomes September 1 (YYYY-DD-MM), collapsing n_years to 0.01 and
        # blowing CAGR to 9e14. Timestamps pass through dayfirst cleanly.
        _agg2, summary = compute_analytics(aggregated)

        # Propagate trade-level Cumulative/Peak/DD/%DD onto the per-leg df.
        # Python convention: only the parent leg row of each trade carries these
        # values; extra leg rows get None. This matches the backtest tradesheet
        # format so exported CSVs look identical to regular backtest downloads.
        # Build a lookup: trade_id → trade-level aggregated values.
        # Net P&L on the first leg = CE+PE+FUT sum (trade total).
        # % P&L on the first leg   = (trade_net_pnl / entry_spot) * 100.
        # Subsequent legs get their own per-leg Net P&L and % P&L.
        # Cumulative/Peak/DD/%DD are first-leg only, null on others.
        # This mirrors generic_algotest_engine.py:5385-5426 exactly.
        trade_to_analytics = {
            str(r["Trade"]): {
                "Cumulative": r.get("Cumulative"),
                "Peak":       r.get("Peak"),
                "DD":         r.get("DD"),
                "%DD":        r.get("%DD"),
                "Net P&L":    r.get("Net P&L"),
                "% P&L":      r.get("% P&L"),
                "Spot P&L":   r.get("Spot P&L"),
            }
            for _, r in aggregated.iterrows()
        }
        # Net P&L, % P&L, and Spot P&L (trade-level totals) shown on first leg
        # only — null on subsequent legs — so summing the column gives per-trade
        # counts, not inflated double-counts. Cumulative/Peak/DD/%DD follow the
        # same rule.
        parent_seen: set = set()
        c_vals, pk_vals, dd_vals, pdd_vals = [], [], [], []
        net_vals, pct_vals, spot_vals = [], [], []
        for _, row in df.iterrows():
            tid = str(row["Trade"])
            if tid in parent_seen:
                c_vals.append(None); pk_vals.append(None)
                dd_vals.append(None); pdd_vals.append(None)
                net_vals.append(None); pct_vals.append(None)
                spot_vals.append(None)
                continue
            parent_seen.add(tid)
            v = trade_to_analytics.get(tid, {})
            c_vals.append(v.get("Cumulative"))
            pk_vals.append(v.get("Peak"))
            dd_vals.append(v.get("DD"))
            pdd_vals.append(v.get("%DD"))
            net_vals.append(v.get("Net P&L"))
            pct_vals.append(v.get("% P&L"))
            spot_vals.append(v.get("Spot P&L"))
        df["Cumulative"] = c_vals
        df["Peak"]       = pk_vals
        df["DD"]         = dd_vals
        df["%DD"]        = pdd_vals
        df["Net P&L"]    = net_vals
        df["% P&L"]      = pct_vals
        df["Spot P&L"]   = spot_vals

        # MAE/MFE and Live DD: skip during batch optimizer runs (OPTIMIZE_SKIP_MAE_MFE=1).
        # Saves ~1.5s/combo by skipping the per-combo Polars feather scan.
        # Live DD metrics fall back to booked %DD — accurate enough for ranking.
        # Full MAE/MFE is only needed for tradesheet downloads.
        _skip_mae = os.environ.get("OPTIMIZE_SKIP_MAE_MFE", "0").strip().lower() in ("1", "true", "yes")
        if not _skip_mae:
            _index_str = str(payload.get("index") or payload.get("symbol") or "NIFTY").upper()
            df = _compute_mae_mfe_batch(df, _index_str, ctx["trading_days"])
            df = _compute_live_dd_from_mae(df, aggregated)

        # Sort by Entry Date so cascade mini-trades (which get NEW trade_ids
        # appended at the end by engine_rust._sa_reentry_specs) interleave
        # chronologically with the original trades — the tradesheet reads in
        # natural order: orig trade → its cascade re-entries → next orig trade.
        # Preserve leg order within a trade by using stable sort on (Entry Date, Trade, Leg).
        if "Entry Date" in df.columns and not df.empty:
            df = df.sort_values(
                by=["Entry Date", "Trade", "Leg"],
                kind="stable",
            ).reset_index(drop=True)

        # Format dates as DD-MM-YYYY strings to match backtest tradesheet output.
        for c in ("Entry Date", "Exit Date", "Leg Exit Date"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%d-%m-%Y")

        return df, summary
    except Exception as exc:
        logger.info("[OPTIM] rust fast path failed (%s) — falling back to Python\n%s",
                    exc, traceback.format_exc())
        return None


def _run_single_backtest(payload: Dict[str, Any]) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Run engine + analytics for one combo. Assumes market data already loaded.

    Rust-only — no Python fallback. Runs the Rust direct fast path (uses the
    pre-computed worker context). If Rust punts (returns None), this RAISES so
    the caller records the combo as a failure rather than substituting Python
    results.

    Returns (trades_df, summary).
    """
    fast = _run_single_backtest_rust_fast(payload)
    if fast is not None:
        return fast

    # HARD-FAIL — no Python fallback (Rust-only rule). The Rust fast path
    # returned None, meaning run_rust_engine_pipeline punted this payload
    # (unsupported feature: premium-based strikes, bare non-rollover
    # weekly/monthly T-n, futures with SL/Target/re-entry, unsupported
    # re-entry / lazy-leg modes, or the Rust extension/cache not loaded).
    # Running the Python engine here would NOT apply the SCHEDULED_EXIT relabel
    # and could diverge from the verified Rust numbers, so we refuse. The caller
    # (sequential loop + parallel worker) catches this per-combo and counts it
    # as a failure.
    raise RuntimeError(
        "Rust engine cannot handle this combo and Python fallback is disabled "
        "(Rust-only). Unsupported feature, or Rust extension/cache not loaded."
    )


def run_optimization(
    job_id: str,
    *,
    base_payload: Dict[str, Any],
    param_specs: Sequence[Dict[str, Any]],
    method: str = "exhaustive",
    sample_n: Optional[int] = None,
    objective: str = "total_pnl",
    algorithm: Optional[str] = None,
    seed: Optional[int] = None,
    progress_every: int = 1,
    parallelism: Optional[int] = None,
    zip_naming: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Top-level entry point invoked by the Celery task.

    Persists progress + per-combo results to Redis. Returns a small summary
    dict (NOT the full result list — clients fetch paginated results).

    When `OPTIMIZE_PARALLELISM` > 1 the iteration is fanned out across child
    processes via services.optimizer.parallel. Smart sampling stays single
    process because it needs the ask/tell feedback loop.
    """
    obj = resolve_objective(objective)
    total = validate_request(base_payload, param_specs, method, sample_n)

    result_store.init_job(
        job_id,
        total=total,
        method=method,
        objective=obj.name,
        # Persist base_payload so the on-demand ZIP builder (which reads from the
        # job meta) gets the date range AND the Midcap overlay config. Without
        # this, meta.base_payload was empty → ZIP combos had no Midcap columns.
        extra={"sample_n": sample_n, "algorithm": algorithm, "zip_naming": zip_naming,
               "base_payload": base_payload},
    )

    result_store.write_run_config(
        job_id,
        method=method,
        objective=obj.name,
        param_specs=list(param_specs),
        base_payload=base_payload,
        sample_n=sample_n,
        algorithm=algorithm,
        total_combos=total,
    )

    # ── Parallel fast-path ──────────────────────────────────────────────────
    # Smart sampling needs the in-loop tell() feedback, so we always run it
    # sequentially. Exhaustive and Random are pure producers — safe to fan out.
    from services.optimizer.parallel import get_parallelism, run_parallel

    # OPTIMIZE_PARALLELISM env var is the hard ceiling — it always overrides
    # the caller (frontend) value. On HDD hardware this must stay at 1 because
    # each parallel worker reloads the full ~500MB options dataset independently,
    # causing OOM. The env var is the safe knob to raise it when hardware allows.
    _env_par = int(os.environ.get("OPTIMIZE_PARALLELISM", "0") or "0")
    if method not in ("exhaustive", "random"):
        parallelism = 1
    elif _env_par >= 1:
        parallelism = _env_par
    elif parallelism is None or parallelism < 1:
        parallelism = get_parallelism()

    if parallelism > 1:
        try:
            # Pre-build the Rust feather ONCE in the parent process so that
            # worker processes can mmap it without any DB bulk-load. This is
            # the key to keeping P=6 inside the 3 GB container limit: each
            # worker's peak memory becomes ~50 MB (mmap pages are shared by
            # the OS page cache) instead of ~500 MB (independent DB reload).
            logger.info("[OPTIM] pre-building Rust feather before spawning P=%d workers", parallelism)
            result_store.update_progress(job_id, done=0, phase="loading_data")
            parent_meta = _prepare_market_data(base_payload, lean=True)
            feather_root = parent_meta.get("feather_root")
            prebuilt_rust_context = parent_meta.get("rust_context")
            _teardown_market_data()  # clear parent's Python dicts after feather is built
            if feather_root:
                logger.info("[OPTIM] feather pre-built at %s — workers will mmap", feather_root)
                # Pre-warm feather into the OS page cache before spawning workers.
                # Each forkserver worker mmap-loads the feather independently; without
                # this the first combo per worker triggers a cold 275 MB HDD read (~29 s).
                # One sequential parent read here (~3-5 s) fills the page cache so
                # all workers' first access is a RAM cache hit.
                _warm_feather_page_cache(feather_root)
            else:
                logger.warning("[OPTIM] feather_root not available — workers will each bulk-load")

            # Materialise combos so we can chunk them. Capped by validate_request,
            # so this is bounded.
            sampler = build_sampler(
                param_specs,
                method=method,
                sample_n=sample_n,
                algorithm=None,
                budget=sample_n,
                seed=seed,
            )
            combos: List[Dict[str, Any]] = [dict(c) for c in sampler]
            for c in combos:
                c.pop("__optim_callback__", None)

            # Install the FULL context (with ohlc_df_pandas) into the parent's
            # _RUST_CONTEXT BEFORE forking — children inherit it via CoW fork
            # at zero cost. Without this set_rust_context() call, _RUST_CONTEXT
            # in the parent is None at fork time, so children can't inherit
            # the pandas OHLC and fall back to the per-child pyarrow load
            # (~32s on HDD with 2 children contending).
            set_rust_context(prebuilt_rust_context)

            # Strip ohlc_df and ohlc_df_pandas before pickling for workers.
            # ohlc_df (Polars) would deadlock in forked children (Rayon).
            # ohlc_df_pandas (pandas) is ~16-111 MB and shouldn't be pickled
            # through the pool pipe — children pick it up via CoW fork
            # inheritance from the set_rust_context call above instead.
            _worker_ctx = {k: v for k, v in (prebuilt_rust_context or {}).items()
                           if k not in ("ohlc_df", "ohlc_df_pandas")}
            agg = run_parallel(
                job_id=job_id,
                base_payload=base_payload,
                combos=combos,
                objective_name=obj.name,
                parallelism=parallelism,
                progress_cb=lambda done: result_store.update_progress(
                    job_id, done=done, total=total
                ),
                prebuilt_feather_root=feather_root,
                prebuilt_rust_context=_worker_ctx,
            )
            spill_path = result_store.maybe_spill_to_parquet(job_id)
            result_store.write_summary_csv(job_id, result_store.get_all_results(job_id))
            _prebuild_csv_zip(job_id, base_payload)
            result_store.update_progress(job_id, done=agg["done"], total=total)
            result_store.mark_complete(job_id)
            logger.info(
                "[OPTIM] COMPLETE job=%s | %d/%d combos done | %d failures | P=%d",
                job_id[:8],
                agg["done"],
                total,
                agg.get("failures", 0),
                parallelism,
            )
            return {
                "status": "success",
                "total": agg["done"],
                "failures": agg["failures"],
                "parquet_path": spill_path,
                "parallelism": parallelism,
            }
        except Exception as exc:
            msg = f"parallel runner crashed: {exc}"
            logger.error("[OPTIM] %s\n%s", msg, traceback.format_exc())
            result_store.mark_complete(job_id, error=msg)
            return {"status": "failed", "error": msg, "total": 0}

    # ── Sequential path (smart sampling, or single-CPU fallback) ────────────
    market_meta: Dict[str, Any] = {}
    try:
        lean = _payload_is_rust_compatible(base_payload)
        logger.info("[OPTIM] memory mode: %s", "lean (Rust-only)" if lean else "full (Python fallback path armed)")
        # Signal to the UI that we're in the data-load phase (can take 30-60s
        # on HDD). Without this the progress bar sits at 0% in silence.
        result_store.update_progress(job_id, done=0, phase="loading_data")
        market_meta = _prepare_market_data(base_payload, lean=lean)
        set_rust_context(market_meta.get("rust_context"))
        result_store.update_progress(job_id, done=0, phase="running")
    except Exception as exc:
        msg = f"market data load failed: {exc}"
        logger.error("[OPTIM] %s\n%s", msg, traceback.format_exc())
        result_store.mark_complete(job_id, error=msg)
        return {"status": "failed", "error": msg, "total": 0}

    sampler = build_sampler(
        param_specs,
        method=method,
        sample_n=sample_n,
        algorithm=algorithm,
        budget=sample_n,
        seed=seed,
    )

    _seq_from_date = base_payload.get("from_date") or base_payload.get("date_from") or ""
    _seq_to_date = base_payload.get("to_date") or base_payload.get("date_to") or ""
    _seq_index_str = str(base_payload.get("index") or base_payload.get("symbol") or "NIFTY").upper()

    done = 0
    failures = 0
    try:
        for combo in sampler:
            tell = combo.pop("__optim_callback__", None)

            merged = apply_combo_for_optim(base_payload, combo)
            t_combo = time.perf_counter()
            try:
                trades_df, summary = _run_single_backtest(merged)
            except Exception as exc:
                # Rust-only: a combo Rust can't handle hard-fails (no Python
                # fallback). Record it as a failure and keep going so the rest of
                # the optimization still completes — matches the parallel path.
                failures += 1
                logger.warning(
                    "[OPTIM] combo %d failed (Rust-only, no Python fallback): %s",
                    done + 1, exc,
                )
                done += 1
                if done == 1 or done % progress_every == 0 or done == total:
                    result_store.update_progress(job_id, done=done)
                continue
            elapsed_ms = round((time.perf_counter() - t_combo) * 1000.0, 2)

            optim_extra = compute_optim_metrics(trades_df, summary)
            flat_summary = {**summary, **optim_extra}

            labels = label_combo(merged)

            row = {
                "combo_id": done + 1,
                "combo": combo,
                "combo_label": labels["combo_label"],
                "combo_label_safe": f"{done + 1}_{safe_filename(labels['combo_label'])}",
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

            # Persist tradesheet CSV + XLSX for later ZIP download (one per combo)
            if not trades_df.empty:
                result_store.write_combo_tradesheet(
                    job_id, row["combo_label_safe"], trades_df
                )
                result_store.write_combo_xlsx(
                    job_id,
                    row["combo_label_safe"],
                    trades_df,
                    flat_summary,
                    combo_label=labels["combo_label"],
                    from_date=_seq_from_date,
                    to_date=_seq_to_date,
                    index_str=_seq_index_str,
                    trading_days=(_RUST_CONTEXT or {}).get("trading_days") or [],
                )

            if tell is not None:
                try:
                    tell(row["objective_value"])
                except Exception:
                    pass

            done += 1
            if done == 1 or done % progress_every == 0 or done == total:
                result_store.update_progress(job_id, done=done)

        # Spill to parquet if needed (>= OPTIM_SPILL_THRESHOLD rows)
        spill_path = result_store.maybe_spill_to_parquet(job_id)

        # Write master summary CSV for ZIP download
        result_store.write_summary_csv(job_id, result_store.get_all_results(job_id))
        _prebuild_csv_zip(job_id, base_payload)

        result_store.update_progress(job_id, done=done)
        result_store.mark_complete(job_id)
        # Strip non-JSON-serializable objects (Polars DataFrame) before Celery
        # serializes the task result.
        _rc = {k: v for k, v in (market_meta.get("rust_context") or {}).items()
               if k != "ohlc_df"}
        _safe_meta = {**{k: v for k, v in market_meta.items() if k != "rust_context"},
                      "rust_context": _rc}
        return {
            "status": "success",
            "total": done,
            "failures": failures,
            "parquet_path": spill_path,
            "market_meta": _safe_meta,
        }
    except Exception as exc:
        msg = f"runner crashed: {exc}"
        logger.error("[OPTIM] %s\n%s", msg, traceback.format_exc())
        result_store.mark_complete(job_id, error=msg)
        return {"status": "failed", "error": msg, "total": done}
    finally:
        _teardown_market_data()
