"""
Generic AlgoTest-Style Engine
Matches AlgoTest behavior exactly with DTE-based entry/exit
"""

# Set DEBUG = False to disable verbose logging for faster execution
DEBUG = False

def _log(*args, **kwargs):
    """Helper to print only when DEBUG is True"""
    if DEBUG:
        print(*args, **kwargs)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os


def get_lot_size(index, entry_date):
    """
    Returns correct lot size based on index and trade date.
    NSE official lot size history:
    
    NIFTY:
      Jun 2000 – Sep 2010 : 200
      Oct 2010 – Oct 2015 : 50
      Oct 2015 – Oct 2019 : 75
      Nov 2019 – present  : 65  # Updated to match AlgoTest
    
    BANKNIFTY:
      Jun 2000 – Sep 2010 : 50
      Oct 2010 – Oct 2015 : 25
      Oct 2015 – Oct 2019 : 20
      Nov 2019 – present  : 15
    """
    d = pd.Timestamp(entry_date)
    if index.upper() == 'NIFTY':
        if d < pd.Timestamp("2010-10-01"):
            return 200
        elif d < pd.Timestamp("2015-10-29"):
            return 50
        elif d < pd.Timestamp("2019-11-01"):
            return 75
        else:
            return 65  # Changed from 50 to 65 to match AlgoTest
    elif index.upper() == 'BANKNIFTY':
        if d < pd.Timestamp("2010-10-01"):
            return 50
        elif d < pd.Timestamp("2015-10-29"):
            return 25
        elif d < pd.Timestamp("2019-11-01"):
            return 20
        else:
            return 15
    return 1  # fallback for other indexes


# Import from base.py
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from base import (
    calculate_trading_days_before_expiry,
    get_trading_calendar,
    calculate_strike_from_selection,
    calculate_strike_advanced,
    get_expiry_for_selection,
    get_strike_interval,
    get_option_premium_from_db,
    get_future_price_from_db,
    calculate_intrinsic_value,
    get_expiry_dates,
    get_spot_price_from_db,
    get_custom_expiry_dates,
    get_next_expiry_date,
    get_monthly_expiry_date,
    get_strike_data,
    # load_base2,  # Commented out - not using base2 filter
    load_bhavcopy,
    compute_analytics,
    build_pivot
)



def _normalize_sl_tgt_type(mode_str):
    """
    Map any frontend mode string to one canonical internal key.
    Handles all casings and aliases the frontend may send.
    
    Canonical values:
        'pct'            – Percent of premium (% P&L on the leg)
        'points'         – Absolute points movement of the premium itself
        'underlying_pts' – Spot index moved by X absolute points
        'underlying_pct' – Spot index moved by X percent
    """
    if mode_str is None:
        return 'pct'
    m = str(mode_str).upper().replace(' ', '_').replace('-', '_')
    if m in ('PERCENT', 'PCT', '%', 'PER', 'PERCENTAGE'):
        return 'pct'
    if m in ('POINTS', 'PTS', 'POINT', 'PT', 'POINTS_PTS', 'PREMIUM_POINTS'):
        return 'points'
    if m in ('UNDERLYING_POINTS', 'UNDERLYING_PTS', 'UNDERLYING_PT',
             'UNDERLYINGPOINTS', 'UNDERLYINGPTS', 'UNDERLYING_POINT'):
        return 'underlying_pts'
    if m in ('UNDERLYING_PERCENT', 'UNDERLYING_PCT', 'UNDERLYING_%',
             'UNDERLYINGPERCENT', 'UNDERLYINGPCT', 'UNDERLYING_PERCENTAGE'):
        return 'underlying_pct'
    return 'pct'  # safe fallback


def check_leg_stop_loss_target(entry_date, exit_date, expiry_date, entry_spot, legs_config,
                               index, trading_calendar, square_off_mode='partial'):
    """
    Check per-leg stop loss / target during the holding period.

    Each leg can carry its own:
        stop_loss       – numeric value or None  (absence = no SL)
        stop_loss_type  – 'pct'  → % of entry premium
                          'points' → absolute index points of net P&L on this leg
        target          – numeric value or None
        target_type     – 'pct' | 'points'

    square_off_mode:
        'partial'  – only the triggered leg is marked as "early-exit"; others continue.
        'complete' – the first triggered leg causes ALL legs to early-exit on the same day.

    Returns:
        A list of dicts, one per leg_config, with keys:
            'triggered'    : bool
            'exit_date'    : pd.Timestamp (original exit_date if not triggered)
            'exit_reason'  : str
        If no legs have SL/Target configured, returns None (caller uses original logic).
    """
    # Quick exit: nothing to check
    has_any_sl_target = any(
        (lg.get('stop_loss') is not None or lg.get('target') is not None)
        for lg in legs_config
    )
    if not has_any_sl_target:
        return None

    # All trading days between entry (exclusive) and planned exit (inclusive)
    holding_days = trading_calendar[
        (trading_calendar['date'] > entry_date) &
        (trading_calendar['date'] <= exit_date)
    ]['date'].tolist()

    # Per-leg tracking: once a leg is triggered it stays triggered
    leg_results = [
        {
            'triggered': False,
            'exit_date': exit_date,
            'exit_reason': 'SCHEDULED',
        }
        for _ in legs_config
    ]

    for check_date in holding_days:
        all_triggered = all(r['triggered'] for r in leg_results)
        if all_triggered:
            break  # Nothing left to check

        # Evaluate each still-active leg
        newly_triggered_this_day = []

        for li, leg in enumerate(legs_config):
            if leg_results[li]['triggered']:
                continue  # Already done

            sl_val   = leg.get('stop_loss')
            sl_type  = _normalize_sl_tgt_type(leg.get('stop_loss_type', 'pct'))
            tgt_val  = leg.get('target')
            tgt_type = _normalize_sl_tgt_type(leg.get('target_type', 'pct'))

            if sl_val is None and tgt_val is None:
                continue  # No SL/Target for this leg

            position = leg['position']
            lot_size = leg.get('lot_size', get_lot_size(index, entry_date))
            lots     = leg.get('lots', 1)

            segment = leg.get('segment', 'OPTION')
            if segment == 'FUTURES':
                current_price = get_future_price_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    expiry=expiry_date.strftime('%Y-%m-%d')
                )
                if current_price is None:
                    continue

                entry_price = leg.get('entry_price')
                if entry_price is None:
                    continue

                if position == 'BUY':
                    raw_pnl      = (current_price - entry_price) * lots * lot_size
                    raw_pnl_pct  = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
                    prem_move    = current_price - entry_price   # positive = price rose
                else:
                    raw_pnl      = (entry_price - current_price) * lots * lot_size
                    raw_pnl_pct  = ((entry_price - current_price) / entry_price * 100) if entry_price else 0
                    prem_move    = entry_price - current_price   # positive = price fell (good for SELL)

            else:  # OPTIONS
                option_type = leg.get('option_type')
                strike      = leg.get('strike')
                if not option_type or not strike:
                    continue

                current_premium = get_option_premium_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    strike=strike,
                    option_type=option_type,
                    expiry=expiry_date.strftime('%Y-%m-%d')
                )
                if current_premium is None:
                    continue

                entry_premium = leg.get('entry_premium')
                if entry_premium is None:
                    continue

                if position == 'BUY':
                    raw_pnl      = (current_premium - entry_premium) * lots * lot_size
                    raw_pnl_pct  = ((current_premium - entry_premium) / entry_premium * 100) if entry_premium else 0
                    prem_move    = current_premium - entry_premium
                else:
                    raw_pnl      = (entry_premium - current_premium) * lots * lot_size
                    raw_pnl_pct  = ((entry_premium - current_premium) / entry_premium * 100) if entry_premium else 0
                    prem_move    = entry_premium - current_premium

            # ── Spot movement for underlying-based modes ──
            spot_move_pts = 0.0
            spot_move_pct = 0.0
            if sl_type in ('underlying_pts', 'underlying_pct') or                tgt_type in ('underlying_pts', 'underlying_pct'):
                current_spot = get_spot_price_from_db(check_date, index)
                if current_spot is not None and entry_spot:
                    spot_move_pts = current_spot - entry_spot          # +ve = spot rose
                    spot_move_pct = spot_move_pts / entry_spot * 100

            # ── Evaluate SL ──
            # For SELL legs: SL fires when the position moves against us
            #   pct/points: P&L goes negative beyond threshold
            #   underlying_pts/pct: spot rises (CE SELL hurt) or falls (PE SELL hurt)
            # For BUY legs: mirror logic
            hit_sl = False
            if sl_val is not None:
                if sl_type == 'pct':
                    hit_sl = raw_pnl_pct <= -abs(sl_val)
                elif sl_type == 'points':
                    # 'points' means the premium moved adversely by sl_val points
                    hit_sl = prem_move <= -abs(sl_val)
                elif sl_type == 'underlying_pts':
                    # SELL: adversely affected when spot moves against the position
                    # CE SELL → spot rises hurts; PE SELL → spot falls hurts
                    # BUY: opposite
                    if position == 'SELL':
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_sl = spot_move_pts >= abs(sl_val)
                        else:  # PE
                            hit_sl = spot_move_pts <= -abs(sl_val)
                    else:  # BUY
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_sl = spot_move_pts <= -abs(sl_val)
                        else:
                            hit_sl = spot_move_pts >= abs(sl_val)
                elif sl_type == 'underlying_pct':
                    if position == 'SELL':
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_sl = spot_move_pct >= abs(sl_val)
                        else:
                            hit_sl = spot_move_pct <= -abs(sl_val)
                    else:
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_sl = spot_move_pct <= -abs(sl_val)
                        else:
                            hit_sl = spot_move_pct >= abs(sl_val)

            # ── Evaluate Target ──
            hit_tgt = False
            if tgt_val is not None:
                if tgt_type == 'pct':
                    hit_tgt = raw_pnl_pct >= abs(tgt_val)
                elif tgt_type == 'points':
                    hit_tgt = prem_move >= abs(tgt_val)
                elif tgt_type == 'underlying_pts':
                    # Target: favorable spot movement
                    if position == 'SELL':
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_tgt = spot_move_pts <= -abs(tgt_val)
                        else:
                            hit_tgt = spot_move_pts >= abs(tgt_val)
                    else:
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_tgt = spot_move_pts >= abs(tgt_val)
                        else:
                            hit_tgt = spot_move_pts <= -abs(tgt_val)
                elif tgt_type == 'underlying_pct':
                    if position == 'SELL':
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_tgt = spot_move_pct <= -abs(tgt_val)
                        else:
                            hit_tgt = spot_move_pct >= abs(tgt_val)
                    else:
                        opt_t = leg.get('option_type', '')
                        if opt_t == 'CE':
                            hit_tgt = spot_move_pct >= abs(tgt_val)
                        else:
                            hit_tgt = spot_move_pct <= -abs(tgt_val)

            if hit_sl or hit_tgt:
                reason = 'STOP_LOSS' if hit_sl else 'TARGET'
                _log(f"      {'🛑' if hit_sl else '✅'} Leg {li+1} {reason} hit on {check_date.strftime('%Y-%m-%d')} "
                     f"(pnl={raw_pnl:.2f}, pnl%={raw_pnl_pct:.2f}%)")
                newly_triggered_this_day.append((li, check_date, reason))

        # ── Apply triggers based on square_off_mode ──
        if newly_triggered_this_day:
            if square_off_mode == 'complete':
                # All legs exit on the earliest trigger date of this day
                trigger_date = newly_triggered_this_day[0][1]
                trigger_reason = newly_triggered_this_day[0][2]
                for li2, r in enumerate(leg_results):
                    if not r['triggered']:
                        leg_results[li2] = {
                            'triggered': True,
                            'exit_date': trigger_date,
                            'exit_reason': f'COMPLETE_{trigger_reason}',
                        }
                break  # No need to check further dates
            else:
                # 'partial' – mark only triggered legs
                for (li, tdate, treason) in newly_triggered_this_day:
                    leg_results[li] = {
                        'triggered': True,
                        'exit_date': tdate,
                        'exit_reason': treason,
                    }

    return leg_results


# ── Overall Stop Loss / Target — supports both AlgoTest modes ────────────────
#
# AlgoTest has two Overall SL modes:
#
#   1. "Max Loss"  (overall_sl_type = 'max_loss')
#      ─────────────────────────────────────────
#      A fixed ₹ amount.  Exit ALL legs the moment combined live P&L ≤ -overall_sl_value.
#
#        SL threshold (₹) = overall_sl_value          (same every trade)
#
#   2. "Total Premium %"  (overall_sl_type = 'total_premium_pct')
#      ────────────────────────────────────────────────────────────
#      A percentage of the total premium received/paid at ENTRY.
#      AlgoTest uses the PREVIOUS DAY CLOSE prices for strike selection,
#      so entry_premium values in trade_legs already reflect that.
#
#        total_entry_premium = Σ (entry_premium × lots × lot_size)   for each leg
#        SL threshold (₹)   = total_entry_premium × (overall_sl_value / 100)
#
#      This makes the threshold dynamic — it automatically widens on high-IV days
#      (fat premiums) and tightens on low-IV quiet days.
#
#   Similarly for Overall Target:
#   1. "Max Profit"        (overall_target_type = 'max_profit')      → fixed ₹
#   2. "Total Premium %"   (overall_target_type = 'total_premium_pct') → % of total entry premium
#
# How the combined live P&L is computed on each holding day:
#
#   For each leg:
#     • OPTIONS SELL  → pnl = (entry_premium - current_premium) × lots × lot_size
#     • OPTIONS BUY   → pnl = (current_premium - entry_premium) × lots × lot_size
#     • FUTURES BUY   → pnl = (current_price   - entry_price)   × lots × lot_size
#     • FUTURES SELL  → pnl = (entry_price     - current_price)  × lots × lot_size
#
#   combined_live_pnl = Σ leg_pnl
#
#   SL triggered  when  combined_live_pnl ≤ -sl_threshold
#   TGT triggered when  combined_live_pnl ≥ +tgt_threshold
#
# ─────────────────────────────────────────────────────────────────────────────

def compute_overall_sl_threshold(trade_legs, overall_sl_type, overall_sl_value):
    """
    Compute the ₹ stop-loss threshold for the overall strategy.

    Args:
        trade_legs       : list of leg dicts (already populated with entry_premium,
                           entry_price, lots, lot_size, position, segment)
        overall_sl_type  : 'max_loss' | 'total_premium_pct'
        overall_sl_value : float  (₹ for max_loss;  % for total_premium_pct)

    Returns:
        float  — positive ₹ threshold value.
                 e.g. 1374.75 means "exit if combined P&L falls below -1374.75"
        None   — if overall_sl_value is None (no SL configured)
    """
    if overall_sl_value is None:
        return None

    if overall_sl_type == 'max_loss':
        # Fixed ₹ amount — same every trade
        return float(overall_sl_value)

    elif overall_sl_type == 'total_premium_pct':
        # Dynamic: percentage of total entry premium (in ₹ terms)
        # Total entry premium = Σ (entry_premium × lots × lot_size) for OPTIONS legs
        # For FUTURES legs we include them in P&L tracking but NOT in the premium base
        total_entry_premium_rs = 0.0
        for leg in trade_legs:
            seg = leg.get('segment', 'OPTION')
            if seg in ('OPTION', 'OPTIONS'):
                ep   = leg.get('entry_premium', 0) or 0
                lots = leg.get('lots', 1)
                ls   = leg.get('lot_size', 1)
                total_entry_premium_rs += ep * lots * ls

        if total_entry_premium_rs <= 0:
            _log("      WARNING: total_entry_premium_rs is 0 — Overall SL disabled for this trade")
            return None

        threshold = total_entry_premium_rs * (overall_sl_value / 100.0)
        _log(f"      Overall SL Threshold: {total_entry_premium_rs:.2f} × {overall_sl_value}% = ₹{threshold:.2f}")
        return threshold

    else:
        raise ValueError(
            f"Unknown overall_sl_type '{overall_sl_type}'. "
            f"Use 'max_loss' or 'total_premium_pct'."
        )


def compute_overall_target_threshold(trade_legs, overall_target_type, overall_target_value):
    """
    Compute the ₹ profit target threshold for the overall strategy.

    Args:
        trade_legs            : list of leg dicts
        overall_target_type   : 'max_profit' | 'total_premium_pct'
        overall_target_value  : float

    Returns:
        float | None
    """
    if overall_target_value is None:
        return None

    if overall_target_type == 'max_profit':
        return float(overall_target_value)

    elif overall_target_type == 'total_premium_pct':
        total_entry_premium_rs = 0.0
        for leg in trade_legs:
            seg = leg.get('segment', 'OPTION')
            if seg in ('OPTION', 'OPTIONS'):
                ep   = leg.get('entry_premium', 0) or 0
                lots = leg.get('lots', 1)
                ls   = leg.get('lot_size', 1)
                total_entry_premium_rs += ep * lots * ls

        if total_entry_premium_rs <= 0:
            return None

        threshold = total_entry_premium_rs * (overall_target_value / 100.0)
        _log(f"      Overall Target Threshold: {total_entry_premium_rs:.2f} × {overall_target_value}% = ₹{threshold:.2f}")
        return threshold

    else:
        raise ValueError(
            f"Unknown overall_target_type '{overall_target_type}'. "
            f"Use 'max_profit' or 'total_premium_pct'."
        )

def check_overall_stop_loss_target(
    entry_date,
    exit_date,
    expiry_date,
    trade_legs,
    index,
    trading_calendar,
    sl_threshold_rs,
    tgt_threshold_rs,
):
    """
    Debug-enabled Overall SL / Target checker
    """

    if sl_threshold_rs is None and tgt_threshold_rs is None:
        return None, None

    holding_days = trading_calendar[
        (trading_calendar['date'] > entry_date) &
        (trading_calendar['date'] <= exit_date)
    ]['date'].tolist()

    # print("\n========== OVERALL SL DEBUG START ==========")
    # print(f"Entry Date: {entry_date}")
    # print(f"Exit Date: {exit_date}")
    # print(f"Expiry Date: {expiry_date}")
    # print(f"SL Threshold: -₹{sl_threshold_rs}")
    # print("=============================================\n")

    for check_date in holding_days:
        combined_live_pnl = 0.0
        has_data = False

        # print(f"\n--- Checking Date: {check_date.strftime('%Y-%m-%d')} ---")

        for leg in trade_legs:
            seg      = leg.get('segment', 'OPTION')
            position = leg.get('position')
            lots     = leg.get('lots', 1)
            lot_size = leg.get('lot_size', 1)

            if seg in ('OPTION', 'OPTIONS'):
                option_type   = leg.get('option_type')
                strike        = leg.get('strike')
                entry_premium = leg.get('entry_premium')

                if strike is None or entry_premium is None:
                    continue

                current_premium = get_option_premium_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    strike=strike,
                    option_type=option_type,
                    expiry=expiry_date.strftime('%Y-%m-%d')
                )

                # print(f"  OPTION LEG -> Strike: {strike}, Type: {option_type}")
                # print(f"      Entry Premium: {entry_premium}")
                # print(f"      Current Premium from DB: {current_premium}")

                if current_premium is None:
                    # print("      ⚠ No data for this date")
                    continue

                has_data = True

                if position == 'BUY':
                    leg_live_pnl = (current_premium - entry_premium) * lots * lot_size
                else:
                    leg_live_pnl = (entry_premium - current_premium) * lots * lot_size

                # print(f"      Leg Live PnL: ₹{leg_live_pnl}")

            elif seg in ('FUTURE', 'FUTURES'):
                entry_price = leg.get('entry_price')

                current_price = get_future_price_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    expiry=expiry_date.strftime('%Y-%m-%d')
                )

                # print(f"  FUTURE LEG")
                # print(f"      Entry Price: {entry_price}")
                # print(f"      Current Price: {current_price}")

                if current_price is None:
                    continue

                has_data = True

                if position == 'BUY':
                    leg_live_pnl = (current_price - entry_price) * lots * lot_size
                else:
                    leg_live_pnl = (entry_price - current_price) * lots * lot_size

                # print(f"      Leg Live PnL: ₹{leg_live_pnl}")

            else:
                continue

            combined_live_pnl += leg_live_pnl

        if not has_data:
            # print("  ⚠ No data available for this date. Skipping.")
            continue

        # print(f"\n  >>> Combined Live PnL = ₹{combined_live_pnl}")
        # print(f"  >>> SL Trigger Level  = -₹{sl_threshold_rs}")

        # STOP LOSS CHECK
        if sl_threshold_rs is not None and combined_live_pnl <= -sl_threshold_rs:
            # print(f"\n🛑 OVERALL SL HIT on {check_date.strftime('%Y-%m-%d')}")
            # print("========== OVERALL SL DEBUG END ==========\n")
            return check_date, 'OVERALL_SL'

        # TARGET CHECK
        if tgt_threshold_rs is not None and combined_live_pnl >= tgt_threshold_rs:
            # print(f"\n✅ OVERALL TARGET HIT on {check_date.strftime('%Y-%m-%d')}")
            # print("========== OVERALL SL DEBUG END ==========\n")
            return check_date, 'OVERALL_TARGET'

    # print("\nNo Overall SL/Target Triggered")
    # print("========== OVERALL SL DEBUG END ==========\n")

    return None, None


def run_algotest_backtest(params):
    """
    Main AlgoTest-style backtest function
    
    This matches AlgoTest exactly:
    - DTE-based entry/exit
    - Strike selection (ATM/ITM/OTM)
    - Proper expiry settlement
    - Multi-leg support
    
    Args:
        params: dict with:
            - index: str (NIFTY, BANKNIFTY, etc.)
            - from_date: str (YYYY-MM-DD)
            - to_date: str (YYYY-MM-DD)
            - expiry_type: str ('WEEKLY' or 'MONTHLY')
            - expiry_day_of_week: int (0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday) - Optional, defaults to standard expiry days
            - entry_dte: int (0-4 for weekly, 0-24 for monthly)
            - exit_dte: int (0-4 for weekly, 0-24 for monthly)
            - legs: list of dicts, each with:
                - segment: 'OPTIONS' or 'FUTURES'
                - option_type: 'CE' or 'PE' (for options)
                - position: 'BUY' or 'SELL'
                - lots: int
                - strike_selection: 'ATM', 'ITM1', 'OTM2', etc.
                - expiry: 'WEEKLY', 'MONTHLY', etc.
    
    Returns:
        tuple: (trades_df, summary_dict, pivot_dict)
    """
    
    # ========== STEP 1: EXTRACT PARAMETERS ==========
    index = params['index']
    from_date = params['from_date']
    to_date = params['to_date']
    expiry_type = params.get('expiry_type', 'WEEKLY')
    expiry_day_of_week = params.get('expiry_day_of_week', None)
    entry_dte = params.get('entry_dte', 2)
    exit_dte = params.get('exit_dte', 0)
    legs_config = params.get('legs', [])
    
    # ── Overall Stop Loss ──────────────────────────────────────────────────────
    # overall_sl_type:
    #   'max_loss'           → overall_sl_value is a fixed ₹ amount
    #   'total_premium_pct'  → overall_sl_value is % of total entry premium (₹ terms)
    # overall_target_type:
    #   'max_profit'         → overall_target_value is a fixed ₹ amount
    #   'total_premium_pct'  → overall_target_value is % of total entry premium (₹ terms)
    #
    # Legacy keys (stop_loss_pct / target_pct) are remapped automatically below
    # so old callers keep working without changes.
    overall_sl_type     = params.get('overall_sl_type') or 'max_loss'
    overall_sl_value    = params.get('overall_sl_value')          # None = disabled
    overall_target_type  = params.get('overall_target_type') or 'max_profit'
    overall_target_value = params.get('overall_target_value')     # None = disabled

    # Backward-compat: honour old 'stop_loss_pct' / 'target_pct' keys
    # (treat them as total_premium_pct for legacy paths that used that convention)
    _legacy_sl_pct  = params.get('stop_loss_pct',  None)
    _legacy_tgt_pct = params.get('target_pct',     None)
    if overall_sl_value is None and _legacy_sl_pct is not None:
        overall_sl_type  = 'total_premium_pct'
        overall_sl_value = _legacy_sl_pct
    if overall_target_value is None and _legacy_tgt_pct is not None:
        overall_target_type  = 'total_premium_pct'
        overall_target_value = _legacy_tgt_pct

    square_off_mode = params.get('square_off_mode', 'partial')  # 'partial' | 'complete'

    # Re-entry settings (for both Weekly and Monthly strategies)
    # If trade exits before expiry due to SL/Target:
    # - re_entry_enabled: whether to allow re-entry after SL/Target trigger
    # - re_entry_max: maximum number of re-entries allowed per expiry
    re_entry_enabled = params.get('re_entry_enabled', True)  # Default: allow re-entry
    re_entry_max = params.get('re_entry_max', 20)  # Default: max 20 re-entries per expiry

    print(f"\n{'='*60}")
    print(f"ALGOTEST-STYLE BACKTEST")
    print(f"{'='*60}")
    print(f"Index: {index}")
    print(f"Date Range: {from_date} to {to_date}")
    print(f"Expiry Type: {expiry_type}")
    print(f"Entry DTE: {entry_dte} (days before expiry)")
    print(f"Exit DTE: {exit_dte} (days before expiry)")
    print(f"Overall SL:  type={overall_sl_type}, value={overall_sl_value}")
    print(f"Overall TGT: type={overall_target_type}, value={overall_target_value}")
    print(f"Re-entry: enabled={re_entry_enabled}, max_per_expiry={re_entry_max}")
    print(f"Legs: {len(legs_config)}")
    print(f"{'='*60}\n")

    
    # ========== STEP 2: LOAD DATA FROM CSV (like generic_multi_leg) ==========
    print("Loading spot data from CSV...")
    spot_df = get_strike_data(index, from_date, to_date)
    print(f"  Loaded {len(spot_df)} spot records\n")
    
    # Create trading calendar from spot data
    trading_calendar = spot_df[['Date']].drop_duplicates().sort_values('Date').reset_index(drop=True)
    trading_calendar.columns = ['date']
    print(f"  Trading calendar: {len(trading_calendar)} trading days\n")
    
    # NOTE: base2 filter removed - using all trading days
    print("  Using all trading days (base2 filter disabled)\n")
    
    print("Loading expiry dates...")
    if expiry_day_of_week is not None:
        # Use custom expiry days
        expiry_dates = get_custom_expiry_dates(index, expiry_day_of_week, from_date, to_date)
        # Create a dataframe similar to the standard one
        expiry_df = pd.DataFrame({'Current Expiry': expiry_dates})
        print(f"  Loaded {len(expiry_df)} custom expiries (Day {expiry_day_of_week})\n")
    else:
        # Use standard expiry dates
        if expiry_type == 'WEEKLY':
            expiry_df = get_expiry_dates(index, 'weekly', from_date, to_date)
        else:  # MONTHLY
            expiry_df = get_expiry_dates(index, 'monthly', from_date, to_date)
        print(f"  Loaded {len(expiry_df)} standard expiries\n")
    
    # ========== STEP 3: SPOT ADJUSTMENT FILTERING ==========
    spot_adjustment_type = params.get('spot_adjustment_type', 0)
    spot_adjustment = params.get('spot_adjustment', 1.0)
    
    if spot_adjustment_type != 0:
        print(f"Applying spot adjustment filter...")
        print(f"  Type: {['None', 'Rises', 'Falls', 'RisesOrFalls'][spot_adjustment_type]}")
        print(f"  Threshold: {spot_adjustment}%\n")
        
        # Build intervals based on spot movement
        intervals = build_intervals(spot_df, spot_adjustment_type, spot_adjustment)
        print(f"  Generated {len(intervals)} trading intervals\n")
        
        # Filter expiry dates to only those within valid intervals
        valid_expiries = []
        for expiry_row in expiry_df.itertuples():
            expiry_date = expiry_row[1]  # Current Expiry column
            
            # Check if this expiry falls within any valid interval
            for interval_start, interval_end in intervals:
                if interval_start <= expiry_date <= interval_end:
                    valid_expiries.append(expiry_row)
                    break
        
        # Update expiry_df to only include valid expiries
        if valid_expiries:
            expiry_df = pd.DataFrame(valid_expiries, columns=expiry_df.columns)
            print(f"  Filtered to {len(expiry_df)} expiries (from {len(expiry_df) + len(expiry_df.index) - len(valid_expiries)} total)\n")
        else:
            print(f"  WARNING: No expiries match spot adjustment criteria - no trades will be executed\n")
            return pd.DataFrame(), {}, {}
    
    # ========== STEP 4: INITIALIZE RESULTS ==========
    all_trades = []
    strike_interval = get_strike_interval(index)
    
    # ========== STEP 4: LOOP THROUGH EXPIRIES ==========
    if DEBUG:
        print("Processing expiries...\n")
    
    for expiry_idx, expiry_row in expiry_df.iterrows():
        expiry_date = expiry_row['Current Expiry']
        
        _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {expiry_date} ---")
        
        try:
            # ========== STEP 5: CALCULATE ENTRY DATE ==========
            entry_date = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=entry_dte,
                trading_calendar_df=trading_calendar
            )
            
            _log(f"  Entry Date (DTE={entry_dte}): {entry_date}")
            
            # ========== STEP 6: CALCULATE EXIT DATE ==========
            exit_date = calculate_trading_days_before_expiry(
                expiry_date=expiry_date,
                days_before=exit_dte,
                trading_calendar_df=trading_calendar
            )
            
            _log(f"  Exit Date (DTE={exit_dte}): {exit_date}")
            
            # Validate entry before exit
            if entry_date > exit_date:
                _log(f"  WARNING: Entry after exit - skipping")
                continue

            # Skip same-day entry=exit: with EOD data entry_price == exit_price → 0 P&L
            if entry_date == exit_date:
                _log(f"  INFO: Entry == Exit ({entry_date}) — zero-holding trade skipped")
                continue
            
            # ========== STEP 7: GET ENTRY SPOT PRICE ==========
            # Get spot from database (use index price at entry_date)
            entry_spot = get_spot_price_from_db(entry_date, index)
            
            if entry_spot is None:
                _log(f"  WARNING: No spot data for {entry_date} - skipping")
                continue
            
            _log(f"  Entry Spot: {entry_spot}")
            
            # ========== STEP 8: PROCESS EACH LEG ==========
            trade_legs = []
            
            for leg_idx, leg_config in enumerate(legs_config):
                _log(f"\n    Processing Leg {leg_idx + 1}...")
                
                segment = leg_config['segment']
                position = leg_config['position']
                lots = leg_config['lots']
                
                if segment == 'FUTURES':
                    # ========== FUTURES LEG ==========
                    _log(f"      Type: FUTURE")
                    _log(f"      Position: {position}")
                    
                    # Get future expiry
                    future_expiry = expiry_date  # Use same expiry for simplicity
                    
                    # Get entry price
                    entry_price = get_future_price_from_db(
                        date=entry_date.strftime('%Y-%m-%d'),
                        index=index,
                        expiry=future_expiry.strftime('%Y-%m-%d')
                    )
                    
                    if entry_price is None:
                        _log(f"      WARNING: No future price - skipping leg")
                        continue
                    
                    _log(f"      Entry Price: {entry_price}")
                    
                    # Get exit price
                    exit_price = get_future_price_from_db(
                        date=exit_date.strftime('%Y-%m-%d'),
                        index=index,
                        expiry=future_expiry.strftime('%Y-%m-%d')
                    )
                    
                    if exit_price is None:
                        _log(f"      WARNING: No exit price - using entry")
                        exit_price = entry_price
                    
                    _log(f"      Exit Price: {exit_price}")
                    
                    # Calculate P&L with correct lot size
                    lot_size = get_lot_size(index, entry_date)
                    
                    if position == 'BUY':
                        leg_pnl = (exit_price - entry_price) * lots * lot_size
                    else:  # SELL
                        leg_pnl = (entry_price - exit_price) * lots * lot_size
                    
                    _log(f"      Lots: {lots}, P&L: {leg_pnl:,.2f}")
                    
                    trade_legs.append({
                        'leg_number': leg_idx + 1,
                        'segment': 'FUTURE',
                        'position': position,
                        'lots': lots,
                        'lot_size': lot_size,  # Store lot_size for DataFrame
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl': leg_pnl
                    })
                
                else:  # OPTIONS
                    # ========== OPTIONS LEG ==========
                    option_type = leg_config['option_type']
                    strike_selection = leg_config['strike_selection']
                    
                    _log(f"      Type: OPTION")
                    _log(f"      Option Type: {option_type}")
                    _log(f"      Position: {position}")
                    _log(f"      Strike Selection: {strike_selection}")
                    
                    # ========== CALCULATE STRIKE ==========
                    strike = calculate_strike_from_selection(
                        spot_price=entry_spot,
                        strike_interval=strike_interval,
                        selection=strike_selection,
                        option_type=option_type
                    )
                    
                    _log(f"      Calculated Strike: {strike}")
                    
                    # Get entry premium
                    entry_premium = get_option_premium_from_db(
                        date=entry_date.strftime('%Y-%m-%d'),
                        index=index,
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry_date.strftime('%Y-%m-%d')
                    )
                    
                    if entry_premium is None:
                        _log(f"      WARNING: No entry premium - skipping leg")
                        continue
                    
                    _log(f"      Entry Premium: {entry_premium}")
                    
                    # ========== GET EXIT PREMIUM ==========
                    # CRITICAL FIX: Always try to fetch MARKET premium first,
                    # regardless of whether exit is at expiry or before.
                    # AlgoTest uses actual closing prices even on expiry day.
                    # Only fall back to intrinsic value if market data is missing.
                    
                    exit_premium = get_option_premium_from_db(
                        date=exit_date.strftime('%Y-%m-%d'),
                        index=index,
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry_date.strftime('%Y-%m-%d')
                    )
                    
                    if exit_premium is not None:
                        # Market data found — use it
                        _log(f"      SUCCESS: Exit Premium (market data): {exit_premium}")
                    else:
                        # Market data missing — fallback to intrinsic value
                        _log(f"      WARNING: No market data found for exit - calculating intrinsic value")
                        
                        exit_spot = get_spot_price_from_db(exit_date, index)
                        if exit_spot is None:
                            _log(f"      WARNING: No exit spot data - using entry spot")
                            exit_spot = entry_spot
                        
                        exit_premium = calculate_intrinsic_value(
                            spot=exit_spot,
                            strike=strike,
                            option_type=option_type
                        )
                        
                        _log(f"      Exit Spot Price: {exit_spot}")
                        _log(f"      Strike Price: {strike}")
                        _log(f"      Option Type: {option_type}")
                        
                        if option_type.upper() == 'CE':
                            intrinsic_calc = f"max(0, {exit_spot} - {strike}) = max(0, {exit_spot - strike})"
                        else:  # PE
                            intrinsic_calc = f"max(0, {strike} - {exit_spot}) = max(0, {strike - exit_spot})"
                        
                        _log(f"      🧮 Intrinsic Value Calculation: {intrinsic_calc}")
                        _log(f"      💰 Exit Premium (intrinsic): {exit_premium}")
                        
                        if exit_premium == 0:
                            _log(f"      INFO: Option expired WORTHLESS (OTM)")
                    
                    # Calculate P&L with correct lot size based on index and entry date
                    lot_size = get_lot_size(index, entry_date)
                    
                    if position == 'BUY':
                        leg_pnl = (exit_premium - entry_premium) * lots * lot_size
                    else:  # SELL
                        leg_pnl = (entry_premium - exit_premium) * lots * lot_size
                    
                    _log(f"      Lots: {lots}, P&L: {leg_pnl:,.2f}")
                    
                    trade_legs.append({
                        'leg_number': leg_idx + 1,
                        'segment': 'OPTION',
                        'option_type': option_type,
                        'strike': strike,
                        'position': position,
                        'lots': lots,
                        'lot_size': lot_size,  # Store lot_size for DataFrame
                        'entry_premium': entry_premium,
                        'exit_premium': exit_premium,
                        'pnl': leg_pnl
                    })
            
            # ========== STEP 8B: PER-LEG STOP LOSS / TARGET ==========
            # Attach per-leg SL/Target config from frontend payload into trade_legs.
            # Supports both old format (stop_loss/stop_loss_type) and new frontend
            # format (stopLoss: {mode, value} / targetProfit: {mode, value}).
            # All mode strings are normalised through _normalize_sl_tgt_type().
            for li, tleg in enumerate(trade_legs):
                lsrc = legs_config[li] if li < len(legs_config) else {}

                # ── Stop Loss ──
                if 'stopLoss' in lsrc and isinstance(lsrc['stopLoss'], dict):
                    tleg['stop_loss']      = lsrc['stopLoss'].get('value')
                    tleg['stop_loss_type'] = _normalize_sl_tgt_type(lsrc['stopLoss'].get('mode'))
                elif lsrc.get('stop_loss') is not None:
                    tleg['stop_loss']      = lsrc['stop_loss']
                    tleg['stop_loss_type'] = _normalize_sl_tgt_type(lsrc.get('stop_loss_type'))
                else:
                    tleg['stop_loss']      = None
                    tleg['stop_loss_type'] = 'pct'

                # ── Target Profit ──
                if 'targetProfit' in lsrc and isinstance(lsrc['targetProfit'], dict):
                    tleg['target']      = lsrc['targetProfit'].get('value')
                    tleg['target_type'] = _normalize_sl_tgt_type(lsrc['targetProfit'].get('mode'))
                elif lsrc.get('target') is not None:
                    tleg['target']      = lsrc['target']
                    tleg['target_type'] = _normalize_sl_tgt_type(lsrc.get('target_type'))
                else:
                    tleg['target']      = None
                    tleg['target_type'] = 'pct'

            per_leg_results = check_leg_stop_loss_target(
                entry_date=entry_date,
                exit_date=exit_date,
                expiry_date=expiry_date,
                entry_spot=entry_spot,
                legs_config=trade_legs,
                index=index,
                trading_calendar=trading_calendar,
                square_off_mode=square_off_mode
            )

            # ========== STEP 8C: OVERALL STOP LOSS / TARGET ==========
            #
            # This runs AFTER per-leg checks.  It monitors the combined portfolio
            # P&L in ₹ terms (not % of individual legs) and exits ALL legs together
            # on breach — exactly how AlgoTest's "Overall Strategy Settings" works.
            #
            # Two modes supported (set via params):
            #   overall_sl_type = 'max_loss'          → fixed ₹ (e.g. 5000)
            #   overall_sl_type = 'total_premium_pct' → % of total entry premium in ₹
            #                                           (e.g. 10 → 10% of total entry premium)
            #
            # IMPORTANT: because your engine uses PREVIOUS DAY CLOSE for strike
            # selection, the entry_premium values already stored in trade_legs are
            # the previous-day closing premiums — which is exactly what AlgoTest
            # uses to compute the "Total Premium" base for the % mode.

            overall_sl_triggered_date  = None
            overall_sl_triggered_reason = None

            if overall_sl_value is not None or overall_target_value is not None:
                # Compute ₹ thresholds for this specific trade
                # (they differ per trade in 'total_premium_pct' mode)
                sl_threshold_rs  = compute_overall_sl_threshold(
                    trade_legs, overall_sl_type, overall_sl_value
                )
                tgt_threshold_rs = compute_overall_target_threshold(
                    trade_legs, overall_target_type, overall_target_value
                )

                _log(f"\n  Overall SL Check — sl_threshold=₹{sl_threshold_rs}, "
                     f"tgt_threshold=₹{tgt_threshold_rs}")

                # Determine the effective exit date to scan up to.
                # If per-leg SL already triggered, use the earliest of those dates
                # so we don't scan past a leg that already exited.
                scan_exit_date = exit_date
                if per_leg_results is not None:
                    earliest_leg_exit = min(r['exit_date'] for r in per_leg_results)
                    scan_exit_date = min(scan_exit_date, earliest_leg_exit)

                overall_sl_triggered_date, overall_sl_triggered_reason = (
                    check_overall_stop_loss_target(
                        entry_date=entry_date,
                        exit_date=scan_exit_date,
                        expiry_date=expiry_date,
                        trade_legs=trade_legs,
                        index=index,
                        trading_calendar=trading_calendar,
                        sl_threshold_rs=sl_threshold_rs,
                        tgt_threshold_rs=tgt_threshold_rs,
                    )
                )

            # ── If Overall SL triggered, it overrides per-leg results ──
            # Overall SL ALWAYS causes complete square-off of all legs on the
            # triggered date (mirrors AlgoTest behaviour: it's a portfolio-level stop).
            if overall_sl_triggered_date is not None:
                _log(f"  ⚡ OVERALL SL/TGT OVERRIDES PER-LEG RESULTS "
                     f"— all legs exit on {overall_sl_triggered_date.strftime('%Y-%m-%d')}")
                per_leg_results = [
                    {
                        'triggered':   True,
                        'exit_date':   overall_sl_triggered_date,
                        'exit_reason': overall_sl_triggered_reason,
                    }
                    for _ in trade_legs
                ]

            # ── Apply per-leg exit dates & recalculate P&L where needed ──
            #
            # BUG FIX NOTES:
            #   1. sl_reason is now always initialised before the block so it is
            #      never undefined when per_leg_results is not None but any_early is False.
            #   2. The early-exit condition now compares leg_exit_date against
            #      `actual_exit_date` (not the original `exit_date`) so that when
            #      Overall SL fires and we already updated actual_exit_date = SL date,
            #      the comparison is still meaningful.
            #   3. Exit Spot in each flat row is taken from the leg's own exit date,
            #      not the trade-level exit date, so partial-mode rows are correct too.

            sl_reason = None   # always initialise; overwritten below if SL triggered

            # Calculate actual_exit_date early - this is the date we'll use for exit
            # If overall SL fired, use that date; otherwise use planned exit date
            computed_actual_exit_date = (
                overall_sl_triggered_date
                if overall_sl_triggered_date is not None
                else exit_date
            )

            if per_leg_results is not None:
                lot_size_sl = get_lot_size(index, entry_date)
                any_early = False

                _log(f"DEBUG: per_leg_results exists, overall_sl_triggered_date={overall_sl_triggered_date}, computed_actual_exit_date={computed_actual_exit_date}")

                for li, tleg in enumerate(trade_legs):
                    res = per_leg_results[li]
                    leg_exit_date = res['exit_date']
                    
                    _log(f"DEBUG: Leg {li+1}: res_triggered={res['triggered']}, leg_exit_date={leg_exit_date}, exit_date={exit_date}")

                    # FIX: Use <= to catch case where leg_exit_date == computed_actual_exit_date (when overall SL fires)
                    # Also check if triggered to ensure we recalculate when SL/TGT was hit
                    # if res['triggered'] and leg_exit_date <= computed_actual_exit_date and leg_exit_date != exit_date:
                    if res['triggered'] and leg_exit_date < exit_date:
                        any_early = True
                        _log(f"  ⚡ Leg {li+1} exits early on {leg_exit_date.strftime('%Y-%m-%d')} "
                             f"({res['exit_reason']})")
                        _log(f"      DEBUG: leg_exit_date={leg_exit_date}, computed_actual_exit_date={computed_actual_exit_date}, exit_date={exit_date}")

                        # Recalculate exit price / premium for the early exit date
                        if tleg.get('segment') == 'OPTION':
                            opt_type     = tleg.get('option_type')
                            strike       = tleg.get('strike')
                            position_leg = tleg.get('position')
                            lots_leg     = tleg.get('lots', 1)
                            entry_prem   = tleg.get('entry_premium')

                            _log(f"      DEBUG: Fetching exit premium for date={leg_exit_date.strftime('%Y-%m-%d')}, strike={strike}, opt_type={opt_type}, expiry={expiry_date.strftime('%Y-%m-%d')}")
                            
                            new_exit_prem = get_option_premium_from_db(
                                date=leg_exit_date.strftime('%Y-%m-%d'),
                                index=index,
                                strike=strike,
                                option_type=opt_type,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            _log(f"      DEBUG: new_exit_prem from DB = {new_exit_prem}")
                            
                            if new_exit_prem is None:
                                e_spot = get_spot_price_from_db(leg_exit_date, index) or entry_spot
                                new_exit_prem = calculate_intrinsic_value(
                                    spot=e_spot, strike=strike, option_type=opt_type
                                )

                            if position_leg == 'BUY':
                                tleg['pnl'] = (new_exit_prem - entry_prem) * lots_leg * lot_size_sl
                            else:
                                tleg['pnl'] = (entry_prem - new_exit_prem) * lots_leg * lot_size_sl

                            tleg['exit_premium']    = new_exit_prem
                            tleg['early_exit_date'] = leg_exit_date
                            tleg['exit_reason']     = res['exit_reason']

                        elif tleg.get('segment') == 'FUTURE':
                            position_leg = tleg.get('position')
                            lots_leg     = tleg.get('lots', 1)
                            entry_price  = tleg.get('entry_price')

                            new_exit_price = get_future_price_from_db(
                                date=leg_exit_date.strftime('%Y-%m-%d'),
                                index=index,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            if new_exit_price is None:
                                new_exit_price = entry_price

                            if position_leg == 'BUY':
                                tleg['pnl'] = (new_exit_price - entry_price) * lots_leg * lot_size_sl
                            else:
                                tleg['pnl'] = (entry_price - new_exit_price) * lots_leg * lot_size_sl

                            tleg['exit_price']      = new_exit_price
                            tleg['early_exit_date'] = leg_exit_date
                            tleg['exit_reason']     = res['exit_reason']

                # For trade-level exit_reason, use the first triggered reason (if any)
                # Note: sl_reason is already None if no trigger happened (any_early is False)
                if any_early:
                    triggered = [r for r in per_leg_results if r['triggered']]
                    sl_reason = triggered[0]['exit_reason'] if triggered else None
            
            # ========== STEP 9: CALCULATE TOTAL P&L ==========
            total_pnl = sum(leg['pnl'] for leg in trade_legs)

            _log(f"\n  Total P&L: {total_pnl:,.2f}")

            # ========== STEP 10: GET EXIT SPOT ==========
            # BUG FIX: If Overall SL/Target fired, use the actual trigger date
            # as the trade exit date — NOT the scheduled expiry/exit date.
            # Without this fix, exit_spot, exit_date in trade_record (and therefore
            # in all flat rows and aggregated analytics) reflect the wrong date.
            actual_exit_date = (
                overall_sl_triggered_date
                if overall_sl_triggered_date is not None
                else exit_date
            )

            exit_spot = get_spot_price_from_db(actual_exit_date, index)
            if exit_spot is None:
                exit_spot = entry_spot

            # ========== STEP 11: RECORD TRADE ==========
            trade_record = {
                'entry_date': entry_date,
                'exit_date': actual_exit_date,   # real exit (SL date OR scheduled)
                'expiry_date': expiry_date,
                'entry_dte': entry_dte,
                'exit_dte': exit_dte,
                'entry_spot': entry_spot,
                'exit_spot': exit_spot,
                'exit_reason': sl_reason if sl_reason else 'SCHEDULED',
                'legs': trade_legs,
                'total_pnl': total_pnl,
                'square_off_mode': square_off_mode,
                'per_leg_results': per_leg_results,  # None if no SL/Target
            }
            
            all_trades.append(trade_record)

            # ========== RE-ENTRY LOGIC ==========
            # After a trade that exited early due to SL or Target, re-enter on
            # the next trading day with fresh strikes, holding until exit_date.
            # Works identically for weekly and monthly expiries.
            if re_entry_enabled:
                _SL_TGT_REASONS = {
                    'OVERALL_SL', 'OVERALL_TARGET',
                    'STOP_LOSS', 'TARGET',
                    'COMPLETE_STOP_LOSS', 'COMPLETE_TARGET',
                }

                def _is_sl_tgt_exit(reason_str):
                    if not reason_str:
                        return False
                    # Strip re-entry suffix like [RE1], [RE2]
                    base = reason_str.split('[')[0].strip()
                    return base in _SL_TGT_REASONS

                exit_reason_this = trade_record['exit_reason']
                is_early_sl_exit = (
                    _is_sl_tgt_exit(exit_reason_this)
                    and trade_record['exit_date'] < exit_date
                )

                re_entry_count  = 0
                re_trigger_date = trade_record['exit_date']

                while is_early_sl_exit and re_entry_count < re_entry_max:
                    # Next trading day after trigger
                    future_days = trading_calendar[
                        trading_calendar['date'] > re_trigger_date
                    ]['date'].tolist()

                    if not future_days:
                        break

                    re_entry_date = future_days[0]

                    # Must be strictly before exit_date (EOD: same day = 0 P&L)
                    if re_entry_date >= exit_date:
                        break

                    re_entry_spot = get_spot_price_from_db(re_entry_date, index)
                    if re_entry_spot is None:
                        break

                    # Build fresh legs for re-entry
                    re_trade_legs = []
                    re_ok = True
                    re_lot_size = get_lot_size(index, re_entry_date)

                    for rli, rlc in enumerate(legs_config):
                        rseg = rlc['segment']
                        rpos = rlc['position']
                        rlts = rlc['lots']

                        if rseg == 'FUTURES':
                            rep = get_future_price_from_db(
                                date=re_entry_date.strftime('%Y-%m-%d'),
                                index=index,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            if rep is None:
                                re_ok = False; break
                            rxp = get_future_price_from_db(
                                date=exit_date.strftime('%Y-%m-%d'),
                                index=index,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            ) or rep
                            rpnl = (rxp - rep if rpos == 'BUY' else rep - rxp) * rlts * re_lot_size
                            re_leg_dict = {
                                'leg_number': rli + 1, 'segment': 'FUTURE',
                                'position': rpos, 'lots': rlts, 'lot_size': re_lot_size,
                                'entry_price': rep, 'exit_price': rxp, 'pnl': rpnl,
                            }
                        else:  # OPTIONS
                            ropt  = rlc.get('option_type', 'CE')
                            rsel  = rlc.get('strike_selection', 'ATM')
                            # strike_selection may be a string like 'ATM','OTM1' or a dict
                            if isinstance(rsel, dict):
                                rsel = rsel.get('strike_type', 'ATM')
                            rstk  = calculate_strike_from_selection(
                                spot_price=re_entry_spot,
                                strike_interval=strike_interval,
                                selection=str(rsel),
                                option_type=ropt
                            )
                            rep2 = get_option_premium_from_db(
                                date=re_entry_date.strftime('%Y-%m-%d'),
                                index=index, strike=rstk,
                                option_type=ropt,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            if rep2 is None:
                                re_ok = False; break
                            rxp2 = get_option_premium_from_db(
                                date=exit_date.strftime('%Y-%m-%d'),
                                index=index, strike=rstk,
                                option_type=ropt,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            if rxp2 is None:
                                rxs2 = get_spot_price_from_db(exit_date, index) or re_entry_spot
                                rxp2 = calculate_intrinsic_value(
                                    spot=rxs2, strike=rstk, option_type=ropt)
                            rpnl2 = (rxp2 - rep2 if rpos == 'BUY' else rep2 - rxp2) * rlts * re_lot_size
                            re_leg_dict = {
                                'leg_number': rli + 1, 'segment': 'OPTION',
                                'option_type': ropt, 'strike': rstk,
                                'position': rpos, 'lots': rlts, 'lot_size': re_lot_size,
                                'entry_premium': rep2, 'exit_premium': rxp2, 'pnl': rpnl2,
                            }

                        # Copy per-leg SL/Target config
                        if 'stopLoss' in rlc and isinstance(rlc['stopLoss'], dict):
                            re_leg_dict['stop_loss']      = rlc['stopLoss'].get('value')
                            re_leg_dict['stop_loss_type'] = _normalize_sl_tgt_type(rlc['stopLoss'].get('mode'))
                        elif rlc.get('stop_loss') is not None:
                            re_leg_dict['stop_loss']      = rlc['stop_loss']
                            re_leg_dict['stop_loss_type'] = _normalize_sl_tgt_type(rlc.get('stop_loss_type'))
                        else:
                            re_leg_dict['stop_loss']      = None
                            re_leg_dict['stop_loss_type'] = 'pct'

                        if 'targetProfit' in rlc and isinstance(rlc['targetProfit'], dict):
                            re_leg_dict['target']      = rlc['targetProfit'].get('value')
                            re_leg_dict['target_type'] = _normalize_sl_tgt_type(rlc['targetProfit'].get('mode'))
                        elif rlc.get('target') is not None:
                            re_leg_dict['target']      = rlc['target']
                            re_leg_dict['target_type'] = _normalize_sl_tgt_type(rlc.get('target_type'))
                        else:
                            re_leg_dict['target']      = None
                            re_leg_dict['target_type'] = 'pct'

                        re_trade_legs.append(re_leg_dict)

                    if not re_ok or not re_trade_legs:
                        break

                    # Run per-leg SL/Target on the re-entry trade
                    re_per_leg = check_leg_stop_loss_target(
                        entry_date=re_entry_date,
                        exit_date=exit_date,
                        expiry_date=expiry_date,
                        entry_spot=re_entry_spot,
                        legs_config=re_trade_legs,
                        index=index,
                        trading_calendar=trading_calendar,
                        square_off_mode=square_off_mode
                    )

                    # Run overall SL/Target on re-entry
                    re_sl_thr  = compute_overall_sl_threshold(
                        re_trade_legs, overall_sl_type, overall_sl_value)
                    re_tgt_thr = compute_overall_target_threshold(
                        re_trade_legs, overall_target_type, overall_target_value)
                    re_overall_date, re_overall_reason = check_overall_stop_loss_target(
                        entry_date=re_entry_date,
                        exit_date=exit_date,
                        expiry_date=expiry_date,
                        trade_legs=re_trade_legs,
                        index=index,
                        trading_calendar=trading_calendar,
                        sl_threshold_rs=re_sl_thr,
                        tgt_threshold_rs=re_tgt_thr,
                    )

                    if re_overall_date is not None:
                        re_per_leg = [
                            {'triggered': True, 'exit_date': re_overall_date,
                             'exit_reason': re_overall_reason}
                            for _ in re_trade_legs
                        ]

                    # Recalculate P&L for any early-exit re-entry legs
                    re_sl_reason = None
                    if re_per_leg is not None:
                        re_any_early = False
                        for rli2, rtleg in enumerate(re_trade_legs):
                            rres = re_per_leg[rli2]
                            if rres['triggered'] and rres['exit_date'] < exit_date:
                                re_any_early = True
                                rlex = rres['exit_date']
                                if rtleg.get('segment') == 'OPTION':
                                    rnp = get_option_premium_from_db(
                                        date=rlex.strftime('%Y-%m-%d'),
                                        index=index, strike=rtleg['strike'],
                                        option_type=rtleg['option_type'],
                                        expiry=expiry_date.strftime('%Y-%m-%d')
                                    )
                                    if rnp is None:
                                        res2 = get_spot_price_from_db(rlex, index) or re_entry_spot
                                        rnp = calculate_intrinsic_value(res2, rtleg['strike'], rtleg['option_type'])
                                    ep_ = rtleg['entry_premium']
                                    pos_ = rtleg['position']
                                    rtleg['pnl'] = (rnp - ep_ if pos_ == 'BUY' else ep_ - rnp) * rtleg['lots'] * re_lot_size
                                    rtleg['exit_premium']    = rnp
                                    rtleg['early_exit_date'] = rlex
                                elif rtleg.get('segment') == 'FUTURE':
                                    rfp = get_future_price_from_db(
                                        date=rlex.strftime('%Y-%m-%d'),
                                        index=index,
                                        expiry=expiry_date.strftime('%Y-%m-%d')
                                    ) or rtleg['entry_price']
                                    pos_ = rtleg['position']
                                    rtleg['pnl'] = (rfp - rtleg['entry_price'] if pos_ == 'BUY' else rtleg['entry_price'] - rfp) * rtleg['lots'] * re_lot_size
                                    rtleg['exit_price']      = rfp
                                    rtleg['early_exit_date'] = rlex
                        if re_any_early:
                            rtriggered = [r for r in re_per_leg if r['triggered']]
                            re_sl_reason = rtriggered[0]['exit_reason'] if rtriggered else None

                    re_total_pnl   = sum(l['pnl'] for l in re_trade_legs)
                    re_actual_exit = re_overall_date if re_overall_date is not None else exit_date
                    re_exit_spot   = get_spot_price_from_db(re_actual_exit, index) or re_entry_spot
                    re_suffix      = f'[RE{re_entry_count + 1}]'
                    re_exit_reason = (re_sl_reason or 'SCHEDULED') + re_suffix

                    re_record = {
                        'entry_date':      re_entry_date,
                        'exit_date':       re_actual_exit,
                        'expiry_date':     expiry_date,
                        'entry_dte':       entry_dte,
                        'exit_dte':        exit_dte,
                        'entry_spot':      re_entry_spot,
                        'exit_spot':       re_exit_spot,
                        'exit_reason':     re_exit_reason,
                        'legs':            re_trade_legs,
                        'total_pnl':       re_total_pnl,
                        'square_off_mode': square_off_mode,
                        'per_leg_results': re_per_leg,
                    }
                    all_trades.append(re_record)
                    re_entry_count += 1

                    # Chain: if this re-entry also hit SL/TGT early, loop again
                    if (_is_sl_tgt_exit(re_sl_reason or '')
                            and re_actual_exit < exit_date
                            and re_actual_exit > re_trigger_date):
                        re_trigger_date = re_actual_exit
                    else:
                        break

        except Exception as e:
            # print(f"  ERROR: {str(e)}\n")
            continue
    
    # ========== STEP 11: CONVERT TO DATAFRAME ==========
    print(f"\n{'='*60}")
    print(f"BACKTEST COMPLETE")
    print(f"{'='*60}")
    print(f"Total Trades: {len(all_trades)}")
    
    if not all_trades:
        print("No trades executed - returning empty results")
        return pd.DataFrame(), {}, {}
    
    # Flatten for DataFrame - Create rows for EACH leg (AlgoTest format)
    # But we'll aggregate them back for analytics
    trades_flat = []
    trade_counter = 0
    for trade_idx, trade in enumerate(all_trades, 1):
        entry_spot_val = trade['entry_spot']
        exit_spot_val = trade.get('exit_spot', trade['entry_spot'])
        per_leg_res    = trade.get('per_leg_results')  # May be None

        # Create SEPARATE row for EACH leg (like AlgoTest CSV format)
        for leg in trade['legs']:
            leg_num = leg['leg_number']
            li      = leg_num - 1  # 0-based index

            # Resolve per-leg exit date/reason
            if per_leg_res is not None and li < len(per_leg_res):
                leg_exit_date   = per_leg_res[li]['exit_date']
                leg_exit_reason = per_leg_res[li]['exit_reason']
            else:
                leg_exit_date   = trade['exit_date']
                leg_exit_reason = trade.get('exit_reason', 'SCHEDULED')

            # Determine Type and Position
            if leg['segment'] == 'FUTURE':
                option_type = 'FUT'
                position = leg['position']
                strike = ''
                entry_price = leg['entry_price']
                exit_price = leg.get('exit_price', 0)
            else:
                option_type = leg['option_type']
                position = leg['position']
                strike = leg['strike']
                entry_price = leg['entry_premium']
                exit_price = leg.get('exit_premium', 0)
            
            leg_pnl = leg['pnl']
            lots = leg.get('lots', 1)
            lot_size = leg.get('lot_size', 65)
            qty = lots * lot_size
            
            # Calculate % P&L based on entry price
            pct_pnl = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price != 0 else 0
            
            row = {
                'Trade': trade_idx,
                'Leg': leg_num,
                'Index': trade_counter + leg_num,
                'Entry Date': trade['entry_date'],
                'Exit Date': leg_exit_date,         # Per-leg exit date (may differ in partial mode)
                'Type': option_type,
                'Strike': strike,
                'B/S': position,
                'Qty': qty,
                'Entry Price': entry_price,
                'Exit Price': exit_price,
                'Entry Spot': entry_spot_val,
                'Exit Spot': exit_spot_val,
                'Spot P&L': round(exit_spot_val - entry_spot_val, 2) if exit_spot_val and entry_spot_val else 0,
                'Future Expiry': trade['expiry_date'],
                'Net P&L': leg_pnl,
                '% P&L': pct_pnl,
                'Exit Reason': leg_exit_reason,     # Per-leg reason
            }
            
            trades_flat.append(row)
        
        trade_counter += len(trade['legs'])
    
    trades_df = pd.DataFrame(trades_flat)
    
    # ========== AGGREGATE LEGS INTO TRADES FOR ANALYTICS ==========
    # Group by Trade number and sum P&L to get one row per trade
    trades_aggregated = trades_df.groupby('Trade').agg({
        'Entry Date': 'first',
        'Exit Date': 'first',
        'Entry Spot': 'first',
        'Exit Spot': 'first',
        'Spot P&L': 'first',
        'Net P&L': 'sum',  # Sum P&L across all legs
        'Exit Reason': 'first'
    }).reset_index()
    
    # ========== STEP 12: COMPUTE ANALYTICS (ADDS CUMULATIVE, PEAK, DD, %DD) ==========
    if DEBUG:
        print(f"Computing analytics on {len(trades_aggregated)} trades...")
    
    # Call compute_analytics on AGGREGATED trades (one row per trade)
    # This adds: Cumulative, Peak, DD, %DD columns
    # And returns enhanced summary with CAGR, Max DD, etc.
    trades_aggregated, summary = compute_analytics(trades_aggregated)
    
    print(f"\n{'='*60}")
    print(f"ANALYTICS COMPLETE")
    print(f"{'='*60}")
    print(f"Total P&L: ₹{summary.get('total_pnl', 0):,.2f}")
    print(f"Win Rate: {summary.get('win_pct', 0):.2f}%")
    print(f"CAGR: {summary.get('cagr_options', 0):.2f}%")
    print(f"Max Drawdown: {summary.get('max_dd_pct', 0):.2f}%")
    print(f"CAR/MDD: {summary.get('car_mdd', 0):.2f}")
    print(f"{'='*60}\n")
    
    # ========== MERGE ANALYTICS BACK TO DETAILED TRADES ==========
    # Merge Cumulative, Peak, DD, %DD from aggregated back to detailed leg-by-leg DataFrame
    analytics_cols = ['Trade', 'Cumulative', 'Peak', 'DD', '%DD']
    trades_aggregated_subset = trades_aggregated[analytics_cols]
    trades_df = trades_df.merge(trades_aggregated_subset, on='Trade', how='left')
    
    # print(f"\nDEBUG: trades_df columns after merge: {list(trades_df.columns)}")
    # print(f"DEBUG: First row Cumulative: {trades_df.iloc[0]['Cumulative'] if 'Cumulative' in trades_df.columns else 'MISSING'}")
    
    # ========== STEP 13: BUILD PIVOT TABLE ==========
    pivot = build_pivot(trades_aggregated, 'Exit Date')
    
    return trades_df, summary, pivot