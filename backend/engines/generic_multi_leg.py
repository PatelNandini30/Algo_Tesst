"""Generic Multi-Leg Strategy Engine

Handles arbitrary multi-leg strategies that don't map to existing engines.
Calculates P&L per leg and aggregates across all legs.
"""

import os
import sys
from datetime import timedelta
from typing import Any, Dict, Tuple

import pandas as pd

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


def _process_trade_legs(
    strategy_def: StrategyDefinition,
    index_name: str,
    from_date: pd.Timestamp,
    to_date: pd.Timestamp,
    curr_expiry: pd.Timestamp,
    fut_expiry: pd.Timestamp,
    entry_spot: float,
):
    bhav_entry = load_bhavcopy(from_date.strftime("%Y-%m-%d"))
    bhav_exit = load_bhavcopy(to_date.strftime("%Y-%m-%d"))
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

            if leg.strike_selection.type == StrikeSelectionType.ATM:
                selected_strike = get_atm_strike(adjusted_spot, available_strikes_series)
            elif leg.strike_selection.type == StrikeSelectionType.OTM_PERCENT:
                selected_strike = get_otm_strike(
                    adjusted_spot,
                    available_strikes_series,
                    leg.strike_selection.value,
                    leg.option_type.value,
                )
            elif leg.strike_selection.type == StrikeSelectionType.ITM_PERCENT:
                selected_strike = get_itm_strike(
                    adjusted_spot,
                    available_strikes_series,
                    leg.strike_selection.value,
                    leg.option_type.value,
                )
            elif leg.strike_selection.type == StrikeSelectionType.CLOSEST_PREMIUM:
                target_premium = leg.strike_selection.value
                min_diff = float("inf")
                tie_candidates = []

                for strike in available_strikes:
                    strike_mask = bhav_entry[
                        (bhav_entry["Instrument"] == "OPTIDX")
                        & (bhav_entry["Symbol"] == index_name)
                        & (bhav_entry["OptionType"] == leg.option_type.value)
                        & (
                            (bhav_entry["ExpiryDate"] == curr_expiry)
                            | (bhav_entry["ExpiryDate"] == curr_expiry - timedelta(days=1))
                            | (bhav_entry["ExpiryDate"] == curr_expiry + timedelta(days=1))
                        )
                        & (bhav_entry["StrikePrice"] == strike)
                        & (bhav_entry["TurnOver"] > 0)
                    ]
                    if strike_mask.empty:
                        continue
                    premium = strike_mask.iloc[0]["Close"]
                    diff = abs(premium - target_premium)
                    if diff < min_diff:
                        min_diff = diff
                        tie_candidates = [(strike, premium)]
                    elif diff == min_diff:
                        tie_candidates.append((strike, premium))

                if tie_candidates:
                    if leg.option_type.value.upper() in ["CE", "CALL", "C"]:
                        selected_strike = max(tie_candidates, key=lambda x: x[0])[0]
                    else:
                        selected_strike = min(tie_candidates, key=lambda x: x[0])[0]

            elif leg.strike_selection.type == StrikeSelectionType.PREMIUM_RANGE:
                premium_min = leg.strike_selection.premium_min or 0.0
                premium_max = leg.strike_selection.premium_max or float("inf")
                valid_strikes = []
                for strike in available_strikes:
                    strike_mask = bhav_entry[
                        (bhav_entry["Instrument"] == "OPTIDX")
                        & (bhav_entry["Symbol"] == index_name)
                        & (bhav_entry["OptionType"] == leg.option_type.value)
                        & (
                            (bhav_entry["ExpiryDate"] == curr_expiry)
                            | (bhav_entry["ExpiryDate"] == curr_expiry - timedelta(days=1))
                            | (bhav_entry["ExpiryDate"] == curr_expiry + timedelta(days=1))
                        )
                        & (bhav_entry["StrikePrice"] == strike)
                        & (bhav_entry["TurnOver"] > 0)
                    ]
                    if strike_mask.empty:
                        continue
                    premium = strike_mask.iloc[0]["Close"]
                    if premium_min <= premium <= premium_max:
                        valid_strikes.append((strike, premium))
                if valid_strikes:
                    def nearest_boundary_dist(item):
                        _, prem = item
                        return min(abs(prem - premium_min), abs(prem - premium_max))
                    selected_strike = min(valid_strikes, key=nearest_boundary_dist)[0]

            elif leg.strike_selection.type == StrikeSelectionType.SPOT:
                selected_strike = get_nearest_strike(adjusted_spot, available_strikes_series)
            else:
                raise ValueError(f"Invalid strike selection type: {leg.strike_selection.type}")

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
                current_entry = nexwt_entry

    if not trades:
        return pd.DataFrame(), {}, {}

    df = pd.DataFrame(trades)
    df, summary = compute_analytics(df)
    pivot = build_pivot(df, "Future Expiry")
    return df, summary, pivot