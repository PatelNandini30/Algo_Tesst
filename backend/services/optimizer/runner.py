"""
Optimization runner.

Responsibilities
----------------
1. Validate the request (parameter specs + base payload + method).
2. Bulk-load market data ONCE (this is the expensive step).
3. Build the in-process fast_lookup ONCE.
4. Iterate combinations from the chosen sampler; for each combo:
       a. Apply combo overrides to the base payload.
       b. Run the existing engine (`run_algotest_backtest`).
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


# fixed_late_entry is accepted for backwards compat in the Python engine but
# explicitly ignored — the chain always chains same-day (py:3747). Safe to pass
# through to the Rust path unchanged. No top-level truthy blockers remain.
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
        payload.get("from_date") or payload.get("date_from")
    )
    to_date = _normalize_cache_date(
        payload.get("to_date") or payload.get("date_to")
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


# Per-worker Rust context cache. _prepare_market_data sets this; combos read
# from it on every iteration so we avoid recomputing trading_days/expiries/spots
# per combo. Worker-local — never shared across processes.
_RUST_CONTEXT: Optional[Dict[str, Any]] = None


def set_rust_context(ctx: Optional[Dict[str, Any]]) -> None:
    global _RUST_CONTEXT
    _RUST_CONTEXT = ctx


def _calc_final_mae_for_trade(trade_legs: "pd.DataFrame") -> Optional[float]:
    """
    Compute finalMae for a group of trade legs.
    Mirrors buildTradeExcel.js calcTradeMae function exactly.
    Returns None when MAE/MFE are all zero (not yet computed).
    """
    def _sum(legs: "pd.DataFrame", col: str) -> Optional[float]:
        vals = pd.to_numeric(legs[col], errors="coerce")
        if vals.isna().any():
            return None
        return float(vals.sum())

    opt_types = {"CE", "PE", "CALL", "PUT"}
    opt_legs = trade_legs[trade_legs["Type"].str.upper().isin(opt_types)]
    fut_legs = trade_legs[trade_legs["Type"].str.upper() == "FUT"]
    if opt_legs.empty:
        return None

    opt_mae = _sum(opt_legs, "MAE")
    opt_mfe = _sum(opt_legs, "MFE")
    if opt_mae is None or opt_mfe is None:
        return None
    if opt_mae == 0.0 and opt_mfe == 0.0:
        return None  # MAE/MFE columns not yet computed

    if not fut_legs.empty:
        fut_mae = _sum(fut_legs, "MAE")
        fut_mfe = _sum(fut_legs, "MFE")
        if fut_mae is None or fut_mfe is None:
            return None
        net_mae1 = fut_mfe + opt_mae
        net_mae2 = opt_mfe + fut_mae
    else:
        buy_legs = opt_legs[opt_legs["B/S"].str.upper() == "BUY"]
        sell_legs = opt_legs[opt_legs["B/S"].str.upper() == "SELL"]
        if not buy_legs.empty and not sell_legs.empty:
            buy_mae = _sum(buy_legs, "MAE")
            buy_mfe = _sum(buy_legs, "MFE")
            sell_mae = _sum(sell_legs, "MAE")
            sell_mfe = _sum(sell_legs, "MFE")
            if any(v is None for v in (buy_mae, buy_mfe, sell_mae, sell_mfe)):
                return None
            net_mae1 = sell_mae + buy_mfe  # type: ignore[operator]
            net_mae2 = sell_mfe + buy_mae  # type: ignore[operator]
        else:
            net_mae1 = opt_mae
            net_mae2 = opt_mfe

    return round(min(net_mae1, net_mae2) * 10000) / 10000


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
            entry_str = _pd.Timestamp(entry_dt).strftime("%Y-%m-%d")
            exit_str = _pd.Timestamp(exit_dt).strftime("%Y-%m-%d")
            expiry_str = _pd.Timestamp(expiry_raw).strftime("%Y-%m-%d")
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
        rows_data.append({
            "pos": pos, "opt_type": opt_type, "strike": strike,
            "expiry_str": expiry_str, "win_start": win_start, "win_end": exit_str,
            "entry_price": entry_price, "position": position, "entry_spot": entry_spot,
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

    # Build OR filter for unique (expiry, option_type, strike) combinations
    unique_combos: Dict[Tuple, None] = {}
    for r in valid_rows:
        unique_combos[(r["expiry_str"], r["opt_type"], r["strike"])] = None

    try:
        import pandas as _pd3
        f = pl.lit(False)
        for (exp_str, opt, strike) in unique_combos:
            exp_dt = _pd3.Timestamp(exp_str).date()
            f = f | (
                (pl.col("ExpiryDate") == exp_dt)
                & (pl.col("OptionType") == opt)
                & ((pl.col("StrikePrice") - strike).abs() <= 0.5)
            )
        ohlc_raw = (
            pl.scan_ipc(str(feather))
            .filter(
                (pl.col("Symbol") == sym_upper)
                & (pl.col("Date") >= from_dt)
                & (pl.col("Date") <= to_dt)
                & f
            )
            .select(["ExpiryDate", "OptionType", "StrikePrice", "Date", "High", "Low"])
            .collect()
        )
    except Exception as exc:
        logger.debug("[OPTIM] MAE/MFE feather scan failed: %s", exc)
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
        ohlc_idx = ohlc_pd.set_index(["expiry_str", "OptionType", "strike_r", "date_str"])[["High", "Low"]]
    except Exception as exc:
        logger.debug("[OPTIM] MAE/MFE ohlc index build failed: %s", exc)
        return df

    df = df.copy()
    mae_vals = list(df["MAE"]) if "MAE" in df.columns else [0.0] * len(df)
    mfe_vals = list(df["MFE"]) if "MFE" in df.columns else [0.0] * len(df)

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

        highs: List[float] = []
        lows: List[float] = []
        for d in window_days:
            try:
                row_ohlc = ohlc_idx.loc[(exp, opt, strike_r, d)]
                # loc returns Series (single match) or DataFrame (rare duplicates)
                if isinstance(row_ohlc, pd.DataFrame):
                    row_ohlc = row_ohlc.iloc[0]
                highs.append(float(row_ohlc["High"]))
                lows.append(float(row_ohlc["Low"]))
            except (KeyError, IndexError, TypeError):
                pass

        if not highs or not lows:
            continue

        max_high = max(highs)
        min_low = min(lows)

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

    Formula (mirrors buildTradeExcel.js lines 253-272):
        finalMae = min(netMae1, netMae2)  — from _calc_final_mae_for_trade
        lowestNav = prevCum * (1 + finalMae/100)
        (prevCum = Cumulative of the prior trade, starting at 100.0)

    Only parent rows (first occurrence of each trade) carry the value;
    secondary leg rows get None — matching Python engine convention.
    """
    if df.empty or "MAE" not in df.columns or "MFE" not in df.columns:
        return df

    df = df.copy()
    prev_cum = 100.0
    trade_lowest_nav: Dict[str, Optional[float]] = {}

    # Process trades in the same sorted order used by cumulative computation
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
        aggregated["Net P&L"] = aggregated["CE P&L"] + aggregated["PE P&L"] + aggregated["FUT P&L"]
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
            pdd.append((cumulative - peak) / peak if peak != 0 else 0.0)
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
        trade_to_analytics = {
            str(r["Trade"]): {
                "Cumulative": r.get("Cumulative"),
                "Peak": r.get("Peak"),
                "DD": r.get("DD"),
                "%DD": r.get("%DD"),
            }
            for _, r in aggregated.iterrows()
        }
        parent_seen: set = set()
        c_vals, pk_vals, dd_vals, pdd_vals = [], [], [], []
        for _, row in df.iterrows():
            tid = str(row["Trade"])
            if tid in parent_seen:
                c_vals.append(None); pk_vals.append(None)
                dd_vals.append(None); pdd_vals.append(None)
                continue
            parent_seen.add(tid)
            v = trade_to_analytics.get(tid, {})
            c_vals.append(v.get("Cumulative"))
            pk_vals.append(v.get("Peak"))
            dd_vals.append(v.get("DD"))
            pdd_vals.append(v.get("%DD"))
        df["Cumulative"] = c_vals
        df["Peak"] = pk_vals
        df["DD"] = dd_vals
        df["%DD"] = pdd_vals

        # Compute MAE/MFE from the Arrow feather (ONE scan for all legs).
        # Entry/Exit Date are still Timestamps here — good for the window computation.
        _index_str = str(payload.get("index") or payload.get("symbol") or "NIFTY").upper()
        df = _compute_mae_mfe_batch(df, _index_str, ctx["trading_days"])

        # Compute 'Lowest NAV During Trade' from Final MAE (for backend metrics).
        # Uses the sorted aggregated df so prevCum tracks correctly.
        df = _compute_live_dd_from_mae(df, aggregated)

        # Format dates as DD-MM-YYYY strings to match backtest tradesheet output.
        for c in ("Entry Date", "Exit Date", "Leg Exit Date"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%d-%m-%Y")

        return df, summary
    except Exception as exc:
        logger.debug("[OPTIM] rust fast path failed (%s) — falling back", exc)
        return None


def _run_single_backtest(payload: Dict[str, Any]) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Run engine + analytics for one combo. Assumes market data already loaded.

    Order of attempts:
      1. Rust direct fast path (uses pre-computed worker context).
      2. Python engine fallback (for unsupported features).

    Returns (trades_df, summary). Empty df + empty summary on failure.
    """
    fast = _run_single_backtest_rust_fast(payload)
    if fast is not None:
        return fast

    # Python fallback — original path. Required when the payload uses a
    # feature the Rust slices don't yet cover (rollover, futures, lazy legs,
    # buffer strike, etc.).
    from engines.generic_algotest_engine import run_algotest_backtest

    try:
        df, engine_summary, _engine_pivot = run_algotest_backtest(payload)
    except Exception as exc:
        logger.warning("[OPTIM] engine failed for combo: %s", exc)
        return pd.DataFrame(), {}

    if df is None or df.empty:
        return pd.DataFrame(), engine_summary or {}

    # IMPORTANT: do NOT re-run compute_analytics on the per-leg df. The Python
    # engine puts the trade-aggregated Net P&L on each trade's parent (lowest
    # leg_id) row and per-leg P&L on others; summing Net P&L across all rows
    # double-counts. `engine_summary` is the engine's own per-trade-aggregated
    # summary (line 5300 of generic_algotest_engine.py runs compute_analytics
    # on `trades_aggregated`, not the per-leg df) — use that as-is.
    return df, engine_summary or {}


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
        extra={"sample_n": sample_n, "algorithm": algorithm},
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
                prebuilt_rust_context=prebuilt_rust_context,
            )
            spill_path = result_store.maybe_spill_to_parquet(job_id)
            result_store.update_progress(job_id, done=agg["done"], total=total)
            result_store.mark_complete(job_id)
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

    done = 0
    failures = 0
    try:
        for combo in sampler:
            tell = combo.pop("__optim_callback__", None)

            merged = apply_combo_for_optim(base_payload, combo)
            t_combo = time.perf_counter()
            trades_df, summary = _run_single_backtest(merged)
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

            # Persist tradesheet CSV for later ZIP download (one per combo)
            if not trades_df.empty:
                result_store.write_combo_tradesheet(
                    job_id, row["combo_label_safe"], trades_df
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

        result_store.update_progress(job_id, done=done)
        result_store.mark_complete(job_id)
        return {
            "status": "success",
            "total": done,
            "failures": failures,
            "parquet_path": spill_path,
            "market_meta": market_meta,
        }
    except Exception as exc:
        msg = f"runner crashed: {exc}"
        logger.error("[OPTIM] %s\n%s", msg, traceback.format_exc())
        result_store.mark_complete(job_id, error=msg)
        return {"status": "failed", "error": msg, "total": done}
    finally:
        _teardown_market_data()
