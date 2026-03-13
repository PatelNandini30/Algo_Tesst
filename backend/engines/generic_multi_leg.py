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
    candidates = weekly_exp[weekly_exp["Current Expiry"] > entry_date].sort_values("Current Expiry")
    if candidates.empty:
        return None
    return candidates.iloc[0]["Current Expiry"]


def _get_last_trading_day_on_or_before(spot_df: pd.DataFrame, target_date: pd.Timestamp):
    candidates = spot_df[spot_df["Date"] <= target_date].sort_values("Date")
    if candidates.empty:
        return None
    return candidates.iloc[-1]["Date"]


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


def _get_bhav_data(date: pd.Timestamp) -> pd.DataFrame:
    """
    Get bhavcopy data for a date - uses bulk-loaded in-memory data if available,
    falls back to load_bhavcopy DB query if not.
    This is the KEY optimization: avoids 100+ DB queries inside the loop.
    """
    from base import is_bulk_data_loaded, fast_get_strikes_for_date
    
    date_str = date.strftime("%Y-%m-%d")
    
    # Try to use bulk-loaded data first
    if is_bulk_data_loaded():
        try:
            # Get all options for this date from bulk-loaded Polars DataFrame
            result = fast_get_strikes_for_date(date_str, None, None)
            if result is not None and not result.is_empty():
                # Convert Polars to Pandas for compatibility with existing code
                return result.to_pandas()
        except Exception as e:
            print(f"[WARN] Bulk lookup failed for {date_str}: {e}")
    
    # Fallback to original DB query
    return load_bhavcopy(date_str)


def _process_trade_legs(
    strategy_def: StrategyDefinition,
    index_name: str,
    from_date: pd.Timestamp,
    to_date: pd.Timestamp,
    curr_expiry: pd.Timestamp,
    fut_expiry: pd.Timestamp,
    entry_spot: float,
):
    # Use optimized data loading - the KEY fix for Phase 1
    bhav_entry = _get_bhav_data(from_date)
    bhav_exit = _get_bhav_data(to_date)
    if bhav_entry is None or bhav_exit is None:
        return []

    leg_rows = []

    for leg in strategy_def.legs:
        leg_pnl = 0.0
        leg_entry_price = None
        leg_exit_price = None
        selected_strike = None

        if leg.instrument == InstrumentType.OPTION:
            adjusted_spot = apply_spot_adjustment(
                entry_spot,
                leg.strike_selection.spot_adjustment_mode,
                leg.strike_selection.spot_adjustment,
            )

            option_mask = (
                (bhav_entry["Instrument"] == "OPTIDX")
                & (bhav_entry["Symbol"] == index_name)
                & (bhav_entry["OptionType"] == leg.option_type.value)
                & (
                    (bhav_entry["ExpiryDate"] == curr_expiry)
                    | (bhav_entry["ExpiryDate"] == curr_expiry - timedelta(days=1))
                    | (bhav_entry["ExpiryDate"] == curr_expiry + timedelta(days=1))
                )
                & (bhav_entry["TurnOver"] > 0)
            )

            available_strikes = bhav_entry[option_mask]["StrikePrice"].unique()
            available_strikes_series = pd.Series(available_strikes)
            if available_strikes_series.empty:
                continue

            # OPTIMIZED: Use vectorized strike selection instead of row-by-row loop
            # Pre-filter once to get all strikes with premiums
            all_strikes_df = _get_all_strikes_for_expiry(
                bhav_entry, index_name, curr_expiry, leg.option_type.value
            )
            
            if not all_strikes_df.empty:
                # Use vectorized selection
                selected_strike = _select_strike_vectorized(
                    all_strikes_df, adjusted_spot, leg.strike_selection
                )
            else:
                # Fallback to old logic if bulk lookup didn't work
                selected_strike = None
                if leg.strike_selection.type == StrikeSelectionType.ATM:
                    selected_strike = get_atm_strike(adjusted_spot, available_strikes_series)
                elif leg.strike_selection.type == StrikeSelectionType.OTM_PERCENT:
                    selected_strike = get_otm_strike(adjusted_spot, available_strikes_series, 
                                                     leg.strike_selection.value, leg.option_type.value)
                elif leg.strike_selection.type == StrikeSelectionType.ITM_PERCENT:
                    selected_strike = get_itm_strike(adjusted_spot, available_strikes_series,
                                                      leg.strike_selection.value, leg.option_type.value)
                elif leg.strike_selection.type == StrikeSelectionType.SPOT:
                    selected_strike = get_nearest_strike(adjusted_spot, available_strikes_series)

            if selected_strike is None:
                continue

            entry_mask = bhav_entry[
                (bhav_entry["Instrument"] == "OPTIDX")
                & (bhav_entry["Symbol"] == index_name)
                & (bhav_entry["OptionType"] == leg.option_type.value)
                & (
                    (bhav_entry["ExpiryDate"] == curr_expiry)
                    | (bhav_entry["ExpiryDate"] == curr_expiry - timedelta(days=1))
                    | (bhav_entry["ExpiryDate"] == curr_expiry + timedelta(days=1))
                )
                & (bhav_entry["StrikePrice"] == selected_strike)
                & (bhav_entry["TurnOver"] > 0)
            ]
            if entry_mask.empty:
                continue
            leg_entry_price = entry_mask.iloc[0]["Close"]

            exit_mask = bhav_exit[
                (bhav_exit["Instrument"] == "OPTIDX")
                & (bhav_exit["Symbol"] == index_name)
                & (bhav_exit["OptionType"] == leg.option_type.value)
                & (
                    (bhav_exit["ExpiryDate"] == curr_expiry)
                    | (bhav_exit["ExpiryDate"] == curr_expiry - timedelta(days=1))
                    | (bhav_exit["ExpiryDate"] == curr_expiry + timedelta(days=1))
                )
                & (bhav_exit["StrikePrice"] == selected_strike)
            ]
            if exit_mask.empty:
                continue
            leg_exit_price = exit_mask.iloc[0]["Close"]

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
            leg_exit_price = fut_exit_mask.iloc[0]["Close"]
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

                # Expiry for this trade/roll
                trade_curr_expiry = _get_next_weekly_expiry_after(weekly_exp, current_entry)
                if trade_curr_expiry is None:
                    break

                # Monthly expiry for futures legs
                curr_monthly = monthly_exp[
                    monthly_exp["Current Expiry"] >= trade_curr_expiry
                ].sort_values("Current Expiry").reset_index(drop=True)
                if curr_monthly.empty:
                    break
                trade_fut_expiry = curr_monthly.iloc[0]["Current Expiry"]

                # Calendar exit competition: Expiry vs STR segment end
                effective_to_date = trade_curr_expiry
                exit_reason = "Expiry"
                if str_enabled and active_segment is not None:
                    seg_end = pd.Timestamp(active_segment["end"])
                    if seg_end < pd.Timestamp(trade_curr_expiry):
                        last_trade_day = _get_last_trading_day_on_or_before(spot_df, seg_end)
                        if last_trade_day is None:
                            break
                        effective_to_date = pd.Timestamp(last_trade_day)
                        exit_reason = "STR_Exit"

                entry_row = spot_df[spot_df["Date"] == current_entry]
                exit_row = spot_df[spot_df["Date"] == effective_to_date]
                if entry_row.empty or exit_row.empty:
                    break

                entry_spot = entry_row.iloc[0]["Close"]
                exit_spot = exit_row.iloc[0]["Close"]

                leg_rows = _process_trade_legs(
                    strategy_def=strategy_def,
                    index_name=index_name,
                    from_date=current_entry,
                    to_date=effective_to_date,
                    curr_expiry=pd.Timestamp(trade_curr_expiry),
                    fut_expiry=pd.Timestamp(trade_fut_expiry),
                    entry_spot=entry_spot,
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
                            "Entry Spot": entry_spot,
                            "Exit Spot": exit_spot,
                            "Spot P&L": round(exit_spot - entry_spot, 2),
                            "Future Expiry": trade_fut_expiry,
                            "Net P&L": leg_row["Net P&L"],
                            "Exit Reason": exit_reason if str_enabled else "",
                            "STR Segment": str_segment_str if str_enabled else "",
                        }
                    )

                # Roll logic: only after Expiry exits while segment remains same
                if (not str_enabled) or exit_reason != "Expiry" or active_segment is None:
                    break

                next_days = spot_df[spot_df["Date"] > effective_to_date].sort_values("Date")
                if next_days.empty:
                    break
                next_entry = pd.Timestamp(next_days.iloc[0]["Date"])
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