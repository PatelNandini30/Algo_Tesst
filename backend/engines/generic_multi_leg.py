"""Generic Multi-Leg Strategy Engine

Handles arbitrary multi-leg strategies that don't map to existing engines.
Calculates P&L per leg and aggregates across all legs.
"""

import os
import sys
from datetime import timedelta
from typing import Any, Dict, Tuple, Optional

import pandas as pd
import numpy as np

# Add backend to path for direct imports
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from base import (
        apply_spot_adjustment,
        build_intervals,
        build_pivot,
        compute_analytics,
        get_active_str_segment,
        get_atm_strike,
        get_itm_strike,
        get_nearest_strike,
        get_otm_strike,
        get_strike_data,
        get_super_trend_segments,
        load_bhavcopy,
        load_expiry,
        load_super_trend_dates,
    )
except ImportError as e:
    print(f"Failed to import from base: {e}")
    raise

try:
    from strategies.strategy_types import (
        ExpiryType,
        InstrumentType,
        PositionType,
        StrikeSelectionType,
        StrategyDefinition,
        SuperTrendConfig,
    )
except ImportError:
    strategies_dir = os.path.join(backend_dir, "strategies")
    if strategies_dir not in sys.path:
        sys.path.insert(0, strategies_dir)
    from strategy_types import (  # type: ignore
        ExpiryType,
        InstrumentType,
        PositionType,
        StrikeSelectionType,
        StrategyDefinition,
        SuperTrendConfig,
    )


def _get_next_weekly_expiry_after(weekly_exp: pd.DataFrame, entry_date: pd.Timestamp):
    # FIX #3A: Use searchsorted instead of boolean filter + sort on every call.
    # weekly_exp is already sorted by Current Expiry from load_expiry().
    expiries = weekly_exp["Current Expiry"].values
    idx = np.searchsorted(expiries, entry_date, side="right")
    if idx >= len(expiries):
        return None
    return pd.Timestamp(expiries[idx])


def _get_last_trading_day_on_or_before(spot_dates_arr, target_date: pd.Timestamp):
    """
    FIX #3A: Accepts a pre-extracted sorted numpy array of dates instead of
    the full spot_df, and uses searchsorted for O(log n) lookup.
    Old code did a full DataFrame filter + sort on every call.
    """
    idx = np.searchsorted(spot_dates_arr, target_date, side="right") - 1
    if idx < 0:
        return None
    return pd.Timestamp(spot_dates_arr[idx])


def _same_segment(seg_a: Dict[str, Any], seg_b: Dict[str, Any]) -> bool:
    if not seg_a or not seg_b:
        return False
    return (
        pd.Timestamp(seg_a["start"]) == pd.Timestamp(seg_b["start"])
        and pd.Timestamp(seg_a["end"]) == pd.Timestamp(seg_b["end"])
    )


def _format_trade_index(base_index: int, roll_count: int):
    if roll_count == 0:
        return base_index
    if roll_count == 1:
        return f"{base_index}R"
    return f"{base_index}R{roll_count}"


def _get_all_strikes_for_expiry(bhav_df: pd.DataFrame, index_name: str, 
                                  expiry: pd.Timestamp, option_type: str) -> pd.DataFrame:
    """
    OPTIMIZED: Pre-filter once to get all strikes for an expiry.
    Returns DataFrame with StrikePrice and Close columns.
    This replaces the row-by-row scanning - O(1) filter instead of O(strikes) filters.
    """
    if bhav_df is None or bhav_df.empty:
        return pd.DataFrame()
    
    # Single filter for all strikes at once - vectorized operation
    expiry_mask = (
        (bhav_df["Instrument"] == "OPTIDX") &
        (bhav_df["Symbol"] == index_name) &
        (bhav_df["OptionType"] == option_type) &
        (
            (bhav_df["ExpiryDate"] == expiry) |
            (bhav_df["ExpiryDate"] == expiry - timedelta(days=1)) |
            (bhav_df["ExpiryDate"] == expiry + timedelta(days=1))
        )
    )
    
    # Filter once and return
    filtered = bhav_df[expiry_mask]
    # Get first row per strike (in case of duplicates)
    return filtered.drop_duplicates(subset=["StrikePrice"]).sort_values("StrikePrice")


def _select_strike_vectorized(available_strikes_df: pd.DataFrame, adjusted_spot: float,
                               strike_selection) -> Optional[float]:
    """
    OPTIMIZED: Vectorized strike selection using pre-filtered DataFrame.
    Replaces the row-by-row loop with pandas vectorized operations.
    """
    if available_strikes_df is None or available_strikes_df.empty:
        return None
    
    strikes = available_strikes_df["StrikePrice"].values
    premiums = available_strikes_df["Close"].values
    
    if len(strikes) == 0:
        return None
    
    strike_type = strike_selection.type
    
    if strike_type == StrikeSelectionType.ATM:
        return get_atm_strike(adjusted_spot, pd.Series(strikes))
    
    elif strike_type == StrikeSelectionType.OTM_PERCENT:
        return get_otm_strike(adjusted_spot, pd.Series(strikes), 
                              strike_selection.value, strike_selection.option_type.value)
    
    elif strike_type == StrikeSelectionType.ITM_PERCENT:
        return get_itm_strike(adjusted_spot, pd.Series(strikes),
                              strike_selection.value, strike_selection.option_type.value)
    
    elif strike_type == StrikeSelectionType.CLOSEST_PREMIUM:
        target_premium = strike_selection.value
        # Vectorized: calculate all differences at once
        diffs = np.abs(premiums - target_premium)
        min_diff = np.min(diffs)
        # Find all strikes with minimum difference
        min_indices = np.where(diffs == min_diff)[0]
        
        if len(min_indices) == 0:
            return None
        
        tie_candidates = [(strikes[i], premiums[i]) for i in min_indices]
        
        # Break ties: higher strike for CE, lower for PE
        opt = strike_selection.option_type.value.upper()
        if opt in ["CE", "CALL", "C"]:
            return max(tie_candidates, key=lambda x: x[0])[0]
        else:
            return min(tie_candidates, key=lambda x: x[0])[0]
    
    elif strike_type == StrikeSelectionType.PREMIUM_RANGE:
        premium_min = strike_selection.premium_min or 0.0
        premium_max = strike_selection.premium_max or float("inf")
        
        # Vectorized: find all strikes in range
        valid_mask = (premiums >= premium_min) & (premiums <= premium_max)
        valid_strikes = strikes[valid_mask]
        valid_premiums = premiums[valid_mask]
        
        if len(valid_strikes) == 0:
            return None
        
        # Find strike closest to boundary
        dist_to_min = np.abs(valid_premiums - premium_min)
        dist_to_max = np.abs(valid_premiums - premium_max)
        min_dists = np.minimum(dist_to_min, dist_to_max)
        best_idx = np.argmin(min_dists)
        
        return valid_strikes[best_idx]
    
    elif strike_type == StrikeSelectionType.SPOT:
        return get_nearest_strike(adjusted_spot, pd.Series(strikes))
    
    return None


# FIX #3B: Per-date pandas cache — each unique date converts Polars→Pandas
# exactly once per backtest run instead of once per trade entry/exit.
_bhav_pandas_cache: dict = {}


def _get_bhav_data(date: pd.Timestamp) -> pd.DataFrame:
    """
    Get bhavcopy data for a date - uses bulk-loaded in-memory data if available,
    falls back to load_bhavcopy DB query if not.

    FIX #3B: Added per-date pandas cache so that entry and exit lookups for the
    same date pay the Polars->Pandas conversion cost only once.
    Old code called .to_pandas() on every entry AND exit — 700-1400 conversions
    per backtest.  Now each unique date converts exactly once.
    """
    from base import is_bulk_data_loaded, fast_get_strikes_for_date

    date_str = date.strftime("%Y-%m-%d")

    # Return cached slice immediately if available
    cached = _bhav_pandas_cache.get(date_str)
    if cached is not None:
        return cached

    # Try to use bulk-loaded data first
    if is_bulk_data_loaded():
        try:
            result = fast_get_strikes_for_date(date_str, None, None)
            if result is not None and not result.is_empty():
                pd_result = result.to_pandas()
                # Cache and evict if too large (keep last 20 unique dates)
                _bhav_pandas_cache[date_str] = pd_result
                if len(_bhav_pandas_cache) > 20:
                    _bhav_pandas_cache.pop(next(iter(_bhav_pandas_cache)))
                return pd_result
        except Exception as e:
            print(f"[WARN] Bulk lookup failed for {date_str}: {e}")

    # Fallback to original DB query
    result = load_bhavcopy(date_str)
    _bhav_pandas_cache[date_str] = result
    return result


def _process_trade_legs(
    strategy_def: StrategyDefinition,
    index_name: str,
    from_date: pd.Timestamp,
    to_date: pd.Timestamp,
    curr_expiry: pd.Timestamp,
    fut_expiry: pd.Timestamp,
    entry_spot: float,
):
    # Use optimized data loading - cached, no repeated Polars->Pandas conversion
    bhav_entry = _get_bhav_data(from_date)
    bhav_exit  = _get_bhav_data(to_date)
    if bhav_entry is None or bhav_exit is None:
        return []

    leg_rows = []

    for leg in strategy_def.legs:
        leg_pnl         = 0.0
        leg_entry_price = None
        leg_exit_price  = None
        selected_strike = None

        if leg.instrument == InstrumentType.OPTION:
            adjusted_spot = apply_spot_adjustment(
                entry_spot,
                leg.strike_selection.spot_adjustment_mode,
                leg.strike_selection.spot_adjustment,
            )

            # FIX #3C: _get_all_strikes_for_expiry already applies the same
            # Instrument/Symbol/OptionType/ExpiryDate±1 filter that option_mask
            # was doing.  We no longer need the separate option_mask scan — use
            # all_strikes_df for everything: strike selection AND entry price.
            all_strikes_df = _get_all_strikes_for_expiry(
                bhav_entry, index_name, curr_expiry, leg.option_type.value
            )

            if all_strikes_df.empty:
                continue

            # Strike selection — vectorized
            selected_strike = _select_strike_vectorized(
                all_strikes_df, adjusted_spot, leg.strike_selection
            )
            if selected_strike is None:
                continue

            # FIX #3C: Pull entry price directly from already-filtered
            # all_strikes_df — no new full-DataFrame mask scan needed.
            strike_row = all_strikes_df[all_strikes_df["StrikePrice"] == selected_strike]
            if strike_row.empty:
                continue
            leg_entry_price = strike_row.iloc[0]["Close"]

            # Exit price still needs a scan on bhav_exit (different date)
            # but we use _get_all_strikes_for_expiry to keep it consistent
            # (single filter call instead of a multi-condition boolean mask).
            exit_strikes_df = _get_all_strikes_for_expiry(
                bhav_exit, index_name, curr_expiry, leg.option_type.value
            )
            if exit_strikes_df.empty:
                continue
            exit_row = exit_strikes_df[exit_strikes_df["StrikePrice"] == selected_strike]
            if exit_row.empty:
                continue
            leg_exit_price = exit_row.iloc[0]["Close"]

            if leg.position == PositionType.BUY:
                leg_pnl = round(leg_exit_price - leg_entry_price, 2)
            else:
                leg_pnl = round(leg_entry_price - leg_exit_price, 2)

            leg_rows.append(
                {
                    "Type": leg.option_type.value,
                    "Strike": selected_strike,
                    "B/S": leg.position.value,
                    "Qty": leg.lots,
                    "Entry Price": leg_entry_price,
                    "Exit Price": leg_exit_price,
                    "Net P&L": leg_pnl,
                }
            )

        elif leg.instrument == InstrumentType.FUTURE:
            fut_expiry_for_leg = fut_expiry if leg.expiry_type == ExpiryType.MONTHLY else curr_expiry

            fut_entry_mask = bhav_entry[
                (bhav_entry["Instrument"] == "FUTIDX")
                & (bhav_entry["Symbol"] == index_name)
                & (bhav_entry["ExpiryDate"].dt.month == fut_expiry_for_leg.month)
                & (bhav_entry["ExpiryDate"].dt.year == fut_expiry_for_leg.year)
            ]
            fut_exit_mask = bhav_exit[
                (bhav_exit["Instrument"] == "FUTIDX")
                & (bhav_exit["Symbol"] == index_name)
                & (bhav_exit["ExpiryDate"].dt.month == fut_expiry_for_leg.month)
                & (bhav_exit["ExpiryDate"].dt.year == fut_expiry_for_leg.year)
            ]
            if fut_entry_mask.empty or fut_exit_mask.empty:
                continue

            leg_entry_price = fut_entry_mask.iloc[0]["Close"]
            leg_exit_price  = fut_exit_mask.iloc[0]["Close"]
            if leg.position == PositionType.BUY:
                leg_pnl = round(leg_exit_price - leg_entry_price, 2)
            else:
                leg_pnl = round(leg_entry_price - leg_exit_price, 2)

            leg_rows.append(
                {
                    "Type": "FUT",
                    "Strike": "",
                    "B/S": leg.position.value,
                    "Qty": leg.lots,
                    "Entry Price": leg_entry_price,
                    "Exit Price": leg_exit_price,
                    "Net P&L": leg_pnl,
                }
            )

    return leg_rows


def run_generic_multi_leg(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    strategy_def = params["strategy"]
    index_name = params.get("index", "NIFTY")
    from_date = params.get("from_date", "2020-01-01")
    to_date = params.get("to_date", "2025-12-31")

    # STR initialization
    super_trend_config = getattr(strategy_def, "super_trend_config", SuperTrendConfig.NONE)
    str_enabled = bool(super_trend_config and super_trend_config != SuperTrendConfig.NONE)
    str_config_value = super_trend_config.value if hasattr(super_trend_config, "value") else str(super_trend_config)

    if str_enabled:
        load_super_trend_dates()
        str_segments = get_super_trend_segments(str_config_value)
        print(f"STR Filter ON: {str_config_value}, {len(str_segments)} segments loaded")
    else:
        print("STR Filter OFF")
    
    # NEW: Date Range Filter
    filter_config = params.get('filter_config', None)
    filter_segments_custom = params.get('filter_segments', [])
    
    filter_enabled = filter_config is not None and filter_config != ''
    filter_segments = []
    if filter_enabled:
        try:
            from base import get_filter_segments
            if filter_config == 'custom':
                filter_segments = filter_segments_custom
                print(f"Custom Filter ON: {len(filter_segments)} segments")
            else:
                filter_segments = get_filter_segments(filter_config)
                print(f"Filter ON: {filter_config}, {len(filter_segments)} segments")
        except Exception as e:
            print(f"Warning: Error loading filter segments: {e}")
            filter_enabled = False
            filter_segments = []
    else:
        print("Filter OFF")

    # ========== PHASE 1: BULK LOAD DATA INTO MEMORY ==========
    print("⚡ PHASE 1: Bulk loading data into memory...")
    try:
        from base import bulk_load_options
        bulk_stats = bulk_load_options(index_name, from_date, to_date)
        print(f"   ✅ Bulk load complete: {bulk_stats['options_rows']} options, {bulk_stats['spot_rows']} spot, {bulk_stats['expiry_rows']} expiries")
    except Exception as e:
        print(f"   ⚠️  Bulk load failed: {e}")
        print("   Falling back to per-row queries (slower)...")

    # Data load
    spot_df = get_strike_data(index_name, params["from_date"], params["to_date"]).sort_values("Date").reset_index(drop=True)
    weekly_exp = load_expiry(index_name, "weekly")
    monthly_exp = load_expiry(index_name, "monthly")

    # FIX #3A: Pre-extract numpy arrays from DataFrames that are used in
    # hot-path helper functions — avoids per-call DataFrame allocation.
    spot_dates_arr    = spot_df["Date"].values                        # for _get_last_trading_day_on_or_before
    spot_closes_arr   = spot_df["Close"].values                       # for O(1) spot price lookup
    spot_dates_index  = {pd.Timestamp(d): i for i, d in enumerate(spot_dates_arr)}

    # FIX #3D: Pre-build bisect arrays for filter_segments so the inner
    # per-entry loop is O(log n) instead of O(segments).
    import bisect as _bisect
    if filter_enabled and filter_segments:
        _fs_starts = [pd.Timestamp(s['start']) for s in filter_segments]
        _fs_ends   = [pd.Timestamp(s['end'])   for s in filter_segments]
    else:
        _fs_starts = []
        _fs_ends   = []

    # FIX #3A: Pre-sort monthly_exp and extract values array once
    monthly_exp_sorted   = monthly_exp.sort_values("Current Expiry").reset_index(drop=True)
    monthly_exp_arr      = monthly_exp_sorted["Current Expiry"].values

    trades = []

    for w in range(len(weekly_exp)):
        prev_expiry = weekly_exp.iloc[w]["Previous Expiry"]
        curr_expiry = weekly_exp.iloc[w]["Current Expiry"]

        filtered_data = spot_df[
            (spot_df["Date"] >= prev_expiry) & (spot_df["Date"] <= curr_expiry)
        ].sort_values("Date").reset_index(drop=True)
        if len(filtered_data) < 2:
            continue

        intervals = build_intervals(
            filtered_data,
            params.get("spot_adjustment_type", 0),
            params.get("spot_adjustment", 1),
        )
        if not intervals:
            continue

        interval_df = pd.DataFrame(intervals, columns=["From", "To"])

        for i in range(len(interval_df)):
            base_trade_number = i + 1
            initial_entry = pd.Timestamp(interval_df.iloc[i]["From"])
            if initial_entry == pd.Timestamp(interval_df.iloc[i]["To"]):
                continue

            roll_count = 0
            current_entry = initial_entry

            while True:
                # STR entry filter
                active_segment = None
                str_segment_str = ""
                if str_enabled:
                    active_segment = get_active_str_segment(current_entry, str_config_value)
                    if active_segment is None:
                        print(f"STR skip: {current_entry.strftime('%Y-%m-%d')}")
                        break
                    str_segment_str = (
                        f"{pd.Timestamp(active_segment['start']).strftime('%d-%m-%Y')} -> "
                        f"{pd.Timestamp(active_segment['end']).strftime('%d-%m-%Y')}"
                    )

                # FIX #3D: Date Range Filter — O(log n) bisect instead of O(n) loop
                active_filter_segment = None
                if filter_enabled and _fs_starts:
                    entry_ts = current_entry
                    idx = _bisect.bisect_right(_fs_starts, entry_ts) - 1
                    if idx >= 0 and entry_ts <= _fs_ends[idx]:
                        active_filter_segment = filter_segments[idx]
                    else:
                        print(f"Filter skip: entry {entry_ts.strftime('%Y-%m-%d')} outside filter segments")
                        break

                # Expiry for this trade/roll
                trade_curr_expiry = _get_next_weekly_expiry_after(weekly_exp, current_entry)
                if trade_curr_expiry is None:
                    break

                # FIX #3A: Replace monthly_exp filter+sort+reset_index with searchsorted
                midx = np.searchsorted(monthly_exp_arr, trade_curr_expiry, side="left")
                if midx >= len(monthly_exp_arr):
                    break
                trade_fut_expiry = pd.Timestamp(monthly_exp_arr[midx])

                # Calendar exit competition: Expiry vs STR segment end
                effective_to_date = trade_curr_expiry
                exit_reason = "Expiry"
                if str_enabled and active_segment is not None:
                    seg_end = pd.Timestamp(active_segment["end"])
                    if seg_end < pd.Timestamp(trade_curr_expiry):
                        # FIX #3A: use pre-extracted array
                        last_trade_day = _get_last_trading_day_on_or_before(spot_dates_arr, seg_end)
                        if last_trade_day is None:
                            break
                        effective_to_date = pd.Timestamp(last_trade_day)
                        exit_reason = "STR_Exit"

                # Date Range Filter exit adjustment
                if filter_enabled and active_filter_segment is not None:
                    filter_end = pd.Timestamp(active_filter_segment['end'])
                    if filter_end < pd.Timestamp(effective_to_date):
                        last_trade_day = _get_last_trading_day_on_or_before(spot_dates_arr, filter_end)
                        if last_trade_day is not None:
                            effective_to_date = pd.Timestamp(last_trade_day)
                            if exit_reason == "Expiry":
                                exit_reason = "FILTER_END"

                # FIX #3A: O(1) spot price lookup via pre-built index dict
                entry_idx = spot_dates_index.get(current_entry)
                exit_idx  = spot_dates_index.get(effective_to_date)
                if entry_idx is None or exit_idx is None:
                    break
                entry_spot_price = float(spot_closes_arr[entry_idx])
                exit_spot_price  = float(spot_closes_arr[exit_idx])

                leg_rows = _process_trade_legs(
                    strategy_def=strategy_def,
                    index_name=index_name,
                    from_date=current_entry,
                    to_date=effective_to_date,
                    curr_expiry=pd.Timestamp(trade_curr_expiry),
                    fut_expiry=pd.Timestamp(trade_fut_expiry),
                    entry_spot=entry_spot_price,
                )
                if not leg_rows:
                    break

                trade_index_label = _format_trade_index(base_trade_number, roll_count)
                for leg_row in leg_rows:
                    trades.append(
                        {
                            "Index": trade_index_label,
                            "Entry Date": current_entry,
                            "Exit Date": effective_to_date,
                            "Type": leg_row["Type"],
                            "Strike": leg_row["Strike"],
                            "B/S": leg_row["B/S"],
                            "Qty": leg_row["Qty"],
                            "Entry Price": leg_row["Entry Price"],
                            "Exit Price": leg_row["Exit Price"],
                            "Entry Spot": entry_spot_price,
                            "Exit Spot": exit_spot_price,
                            "Spot P&L": round(exit_spot_price - entry_spot_price, 2),
                            "Future Expiry": trade_fut_expiry,
                            "Net P&L": leg_row["Net P&L"],
                            "Exit Reason": exit_reason if str_enabled else "",
                            "STR Segment": str_segment_str if str_enabled else "",
                        }
                    )

                # Roll logic: only after Expiry exits while segment remains same
                if (not str_enabled) or exit_reason != "Expiry" or active_segment is None:
                    break

                # FIX #3A: Use searchsorted on pre-extracted array instead of
                # full DataFrame filter + sort to find next trading day
                eff_ts = np.datetime64(effective_to_date)
                nidx   = np.searchsorted(spot_dates_arr, eff_ts, side="right")
                if nidx >= len(spot_dates_arr):
                    break
                next_entry = pd.Timestamp(spot_dates_arr[nidx])
                next_seg = get_active_str_segment(next_entry, str_config_value)
                if not _same_segment(active_segment, next_seg):
                    break

                roll_count += 1
                current_entry = next_entry

    if not trades:
        return pd.DataFrame(), {}, {}

    df = pd.DataFrame(trades)
    df, summary = compute_analytics(df)
    pivot = build_pivot(df, "Future Expiry")
    return df, summary, pivot