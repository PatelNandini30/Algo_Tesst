"""
Generic AlgoTest-Style Engine
Matches AlgoTest behavior exactly with DTE-based entry/exit
"""

# Set DEBUG = True to enable verbose logging for debugging
DEBUG = True

_EARLY_EXIT_REASONS = {
    'STOP_LOSS',
    'TARGET',
    'TRAIL_SL',
    'COMPLETE_STOP_LOSS',
    'COMPLETE_TARGET',
    'OVERALL_SL',
    'OVERALL_TARGET',
}

def _log(*args, **kwargs):
    """Helper to print only when DEBUG is True"""
    if DEBUG:
        print(*args, **kwargs)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os
import time
import traceback


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
    get_filter_segments,
    normalize_filter_segments,
    # load_base2,  # Commented out - not using base2 filter
    load_bhavcopy,
    compute_analytics,
    build_pivot,
    calculate_strike_from_premium_range,
    calculate_strike_from_closest_premium,
    get_all_strikes_with_premiums,
    load_super_trend_dates,
    get_super_trend_segments,
    get_active_str_segment,
    preload_all_data,
    clear_fast_lookup_caches,
    _resolve_nearest_future_expiry,
    _resolve_nearest_future_expiry_after,
    _resolve_futures_expiry_by_preference,
    resolve_futures_pnl_with_rollover,
)

from services.data_loader import get_loader


def _last_trading_day_on_or_before(trading_calendar_df, target_date):
    target_ts = pd.Timestamp(target_date)
    arr = trading_calendar_df['date'].values.astype('datetime64[ns]')
    ts = np.datetime64(target_ts, 'ns')
    idx = np.searchsorted(arr, ts, side='right') - 1
    if idx < 0:
        return None
    return pd.Timestamp(arr[idx])


def _next_trading_day_after(trading_calendar_df, target_date):
    target_ts = pd.Timestamp(target_date)
    arr = trading_calendar_df['date'].values.astype('datetime64[ns]')
    ts = np.datetime64(target_ts, 'ns')
    idx = np.searchsorted(arr, ts, side='right')
    if idx >= len(arr):
        return None
    return pd.Timestamp(arr[idx])


def apply_spot_adjustment_exit(
    entry_date,
    entry_spot,
    scheduled_exit_date,
    expiry_date,
    spot_adjustment_direction,
    spot_adjustment_pct,
    spot_adjustment_units,
    trading_calendar,
    index,
):
    """
    Example — Rise at 1%:
    Entry 22-May, entry spot 10,791.65
    Rise target = 10,791.65 × 1.01 = 10,899.57
    23-May spot 10,780 — below target, continue
    26-May spot 10,900.14 — above 10,899.57 — trigger
    Returns (26-May, True, 'RISE')
    Trade exits 26-May at that day's closing option premiums

    Example — Both at 1%:
    Rise target 10,899.57, Fall target 10,683.73
    Engine watches both levels simultaneously each day
    Whichever is hit first becomes the exit date
    If neither is hit before scheduled exit, trade exits normally

    Example — Neither triggered:
    Spot stays between 10,683 and 10,899 throughout holding period
    Returns (scheduled_exit_date, False, None)
    Trade exits at normal scheduled exit, exit reason unchanged
    """
    scheduled_ts = pd.Timestamp(scheduled_exit_date)
    start_ts = _next_trading_day_after(trading_calendar, entry_date)
    if start_ts is None or entry_spot is None:
        return scheduled_ts, False, None

    arr = trading_calendar['date'].values.astype('datetime64[ns]')
    start_idx = np.searchsorted(arr, np.datetime64(start_ts, 'ns'), side='left')
    end_idx = np.searchsorted(arr, np.datetime64(scheduled_ts, 'ns'), side='right') - 1

    if start_idx >= len(arr) or end_idx < start_idx:
        return scheduled_ts, False, None

    try:
        threshold = float(spot_adjustment_pct)
    except (TypeError, ValueError):
        threshold = 0.0

    if threshold <= 0:
        return scheduled_ts, False, None

    if spot_adjustment_units == 'points':
        rise_target = entry_spot + threshold
        fall_target = entry_spot - threshold
    else:
        rise_target = entry_spot * (1 + (threshold / 100))
        fall_target = entry_spot * (1 - (threshold / 100))

    watch_rise = spot_adjustment_direction in ('rise', 'both')
    watch_fall = spot_adjustment_direction in ('fall', 'both')

    for idx in range(start_idx, min(end_idx, len(arr) - 1) + 1):
        current_ts = pd.Timestamp(arr[idx])
        if current_ts > scheduled_ts:
            break

        current_spot = get_spot_price_from_db(current_ts.strftime('%Y-%m-%d'), index)
        if current_spot is None:
            continue

        if watch_rise and current_spot >= rise_target:
            return current_ts, True, 'RISE'

        if watch_fall and current_spot <= fall_target:
            return current_ts, True, 'FALL'

    return scheduled_ts, False, None



def _normalize_sl_tgt_type(mode_str):
    """
    Map any frontend mode string to one canonical internal key.
    Handles all casings and aliases the frontend may send.

    Canonical values:
        'pct'            – Percent of entry premium (% adverse move on the leg's own premium)
        'points'         – Absolute premium points  (premium moved adversely by X points)
        'underlying_pts' – Underlying index moved adversely by X absolute points from entry spot
        'underlying_pct' – Underlying index moved adversely by X% from entry spot
    """
    if mode_str is None:
        return 'pct'
    m = str(mode_str).upper().replace(' ', '_').replace('-', '_').strip()
    if m in ('PERCENT', 'PCT', '%', 'PER', 'PERCENTAGE', 'PREMIUM_PCT',
             'PREMIUM_PERCENT', 'PREMIUM_%'):
        return 'pct'
    if m in ('POINTS', 'PTS', 'POINT', 'PT', 'POINTS_PTS', 'PREMIUM_POINTS',
             'PREMIUM_PTS', 'PREMIUM_PT', 'ABS', 'ABSOLUTE'):
        return 'points'
    if m in ('UNDERLYING_POINTS', 'UNDERLYING_PTS', 'UNDERLYING_PT',
             'UNDERLYINGPOINTS', 'UNDERLYINGPTS', 'UNDERLYING_POINT',
             'INDEX_POINTS', 'INDEX_PTS', 'SPOT_POINTS', 'SPOT_PTS'):
        return 'underlying_pts'
    if m in ('UNDERLYING_PERCENT', 'UNDERLYING_PCT', 'UNDERLYING_%',
             'UNDERLYINGPERCENT', 'UNDERLYINGPCT', 'UNDERLYING_PERCENTAGE',
             'INDEX_PCT', 'INDEX_PERCENT', 'SPOT_PCT', 'SPOT_PERCENT'):
        return 'underlying_pct'
    return 'pct'  # safe fallback


def _resolve_strike(leg_config, entry_date, entry_spot, expiry_date, strike_interval, index):
    """
    Universal strike resolver — handles ALL AlgoTest strike criteria.

    Supported modes (via leg_config keys):
      strike_selection_type = 'PREMIUM_RANGE'    → lower <= premium <= upper
                            = 'CLOSEST_PREMIUM'  → premium closest to target value
                            = 'PREMIUM_GTE'      → premium >= value, ATM-closest
                            = 'PREMIUM_LTE'      → premium <= value, ATM-closest
                            = anything else      → ATM/ITM/OTM string via calculate_strike_from_selection

    AlgoTest behaviour:
      All premium-based criteria scan the bhavcopy for `entry_date` (which is the
      previous trading day's close, already resolved by calculate_trading_days_before_expiry).
      This matches how AlgoTest selects strikes from the prior session's closing premiums.

    Returns:
      float  – resolved strike
      None   – no qualifying strike found (caller should skip this leg)
    """
    option_type     = leg_config.get('option_type', 'CE')
    strike_sel      = leg_config.get('strike_selection', 'ATM')
    strike_sel_type = str(leg_config.get('strike_selection_type', '')).upper().strip()

    # Accept dict form of strike_selection
    if not strike_sel_type and isinstance(strike_sel, dict):
        strike_sel_type = str(strike_sel.get('type', '')).upper().strip()

    _log(f"      DEBUG: strike_sel_type BEFORE normalization = '{strike_sel_type}'")
    
    # Normalise aliases the frontend may send
    _type_aliases = {
        'PREMIUMRANGE':    'PREMIUM_RANGE',
        'PREMIUM_RANGE':   'PREMIUM_RANGE',
        'CLOSESTPREMIUM':  'CLOSEST_PREMIUM',
        'CLOSEST_PREMIUM': 'CLOSEST_PREMIUM',
        'PREMIUM>=':       'PREMIUM_GTE',
        'PREMIUM_GTE':     'PREMIUM_GTE',
        'PREMIUMGTE':      'PREMIUM_GTE',
        'PREMIUM >=':      'PREMIUM_GTE',
        'PREMIUM<=':       'PREMIUM_LTE',
        'PREMIUM_LTE':     'PREMIUM_LTE',
        'PREMIUMLTE':      'PREMIUM_LTE',
        'PREMIUM <=':      'PREMIUM_LTE',
        'STRADDLEWIDTH':   'STRADDLE_WIDTH',
        'STRADDLE_WIDTH':  'STRADDLE_WIDTH',
        'STRADDLE':       'STRADDLE_WIDTH',
        'SYNTHETICFUTURE': 'SYNTHETIC_FUTURE',
        'SYNTHETIC_FUTURE': 'SYNTHETIC_FUTURE',
        'SYNTHETIC':      'SYNTHETIC_FUTURE',
        'SYNTHETIC_LONG': 'SYNTHETIC_FUTURE',
    }
    strike_sel_type = _type_aliases.get(strike_sel_type, strike_sel_type)
    
    _log(f"      DEBUG: strike_sel_type AFTER normalization = '{strike_sel_type}'")

    date_str  = entry_date.strftime('%Y-%m-%d')
    atm_strike = round(entry_spot / strike_interval) * strike_interval

    # ── PREMIUM RANGE: lower <= premium <= upper ───────────────────────────────
    if strike_sel_type == 'PREMIUM_RANGE':
        min_prem = leg_config.get('min_premium') or leg_config.get('lower')
        max_prem = leg_config.get('max_premium') or leg_config.get('upper')
        if min_prem is None or max_prem is None:
            _log(f"      WARNING: PREMIUM_RANGE missing lower/upper — falling back to ATM")
            return atm_strike
        _log(f"      PREMIUM_RANGE: Searching for strikes with premium between {min_prem} and {max_prem}")
        strike = calculate_strike_from_premium_range(
            date=date_str, index=index, expiry=expiry_date,
            option_type=option_type, spot_price=entry_spot,
            strike_interval=strike_interval,
            min_premium=float(min_prem), max_premium=float(max_prem),
        )
        _log(f"      PREMIUM_RANGE [{min_prem}, {max_prem}] → strike={strike}")
        return strike  # None if no qualifying strike

    # ── CLOSEST PREMIUM: nearest to target value ───────────────────────────────
    if strike_sel_type == 'CLOSEST_PREMIUM':
        target = (
            leg_config.get('premium')
            or leg_config.get('strike_selection_value')
            or (strike_sel if isinstance(strike_sel, (int, float)) else None)
        )
        if target is None and isinstance(strike_sel, dict):
            target = strike_sel.get('value')
        if target is None:
            _log(f"      WARNING: CLOSEST_PREMIUM missing target — falling back to ATM")
            return atm_strike
        strike = calculate_strike_from_closest_premium(
            date=date_str, index=index, expiry=expiry_date,
            option_type=option_type, spot_price=entry_spot,
            strike_interval=strike_interval, target_premium=float(target),
        )
        _log(f"      CLOSEST_PREMIUM target={target} → strike={strike}")
        return strike

    # ── PREMIUM >= : all strikes with premium >= value, pick ATM-closest ───────
    if strike_sel_type == 'PREMIUM_GTE':
        min_prem = (
            leg_config.get('premium')
            or leg_config.get('strike_selection_value')
            or (strike_sel if isinstance(strike_sel, (int, float)) else None)
        )
        if min_prem is None and isinstance(strike_sel, dict):
            min_prem = strike_sel.get('value')
        if min_prem is None:
            _log(f"      WARNING: PREMIUM_GTE missing value — falling back to ATM")
            return atm_strike
        _log(f"      PREMIUM_GTE: Searching for strikes with premium >= {min_prem}")
        all_strikes = get_all_strikes_with_premiums(
            date_str, index, expiry_date, option_type, entry_spot, strike_interval
        )
        _log(f"      Total strikes available: {len(all_strikes)}")
        qualifying = [s for s in all_strikes if s['premium'] >= float(min_prem)]
        if not qualifying:
            _log(f"      WARNING: No strike with premium >= {min_prem}")
            return None
        _log(f"      Found {len(qualifying)} qualifying strikes, showing first 5: {[(s['strike'], s['premium']) for s in qualifying[:5]]}")
        # Pick strike with premium closest to the target value (min_prem)
        # Deterministic tie-breaking: prefer higher strike for CE, lower for PE
        option_type_upper = option_type.upper() if option_type else 'CE'
        if option_type_upper in ['CE', 'CALL', 'C']:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(min_prem)), abs(x['strike'] - atm_strike), -x['strike']))
        else:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(min_prem)), abs(x['strike'] - atm_strike), x['strike']))
        _log(f"      PREMIUM_GTE >= {min_prem} → strike={best['strike']} (premium={best['premium']:.2f}, closest to target, ATM={atm_strike})")
        return best['strike']

    # ── PREMIUM <= : all strikes with premium <= value, pick ATM-closest ───────
    if strike_sel_type == 'PREMIUM_LTE':
        max_prem = (
            leg_config.get('premium')
            or leg_config.get('strike_selection_value')
            or (strike_sel if isinstance(strike_sel, (int, float)) else None)
        )
        if max_prem is None and isinstance(strike_sel, dict):
            max_prem = strike_sel.get('value')
        if max_prem is None:
            _log(f"      WARNING: PREMIUM_LTE missing value — falling back to ATM")
            return atm_strike
        _log(f"      PREMIUM_LTE: Searching for strikes with premium <= {max_prem}")
        all_strikes = get_all_strikes_with_premiums(
            date_str, index, expiry_date, option_type, entry_spot, strike_interval
        )
        _log(f"      Total strikes available: {len(all_strikes)}")
        qualifying = [s for s in all_strikes if s['premium'] <= float(max_prem)]
        if not qualifying:
            _log(f"      WARNING: No strike with premium <= {max_prem}")
            return None
        _log(f"      Found {len(qualifying)} qualifying strikes, showing first 5: {[(s['strike'], s['premium']) for s in qualifying[:5]]}")
        # Pick strike with premium closest to the target value (max_prem)
        # Deterministic tie-breaking: prefer higher strike for CE, lower for PE
        option_type_upper = option_type.upper() if option_type else 'CE'
        if option_type_upper in ['CE', 'CALL', 'C']:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(max_prem)), abs(x['strike'] - atm_strike), -x['strike']))
        else:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(max_prem)), abs(x['strike'] - atm_strike), x['strike']))
        _log(f"      PREMIUM_LTE <= {max_prem} → strike={best['strike']} (premium={best['premium']:.2f}, closest to target, ATM={atm_strike})")
        return best['strike']

    # ── STRADDLE WIDTH: ATM ± (multiplier × (ATM CE + ATM PE)) ────────────────
    if strike_sel_type == 'STRADDLE_WIDTH':
        atm_strike = round(entry_spot / strike_interval) * strike_interval

        multiplier = float(
            leg_config.get('straddle_multiplier')
            or leg_config.get('straddle_width_value')
            or leg_config.get('sw_multiplier')
            or (strike_sel.get('value') if isinstance(strike_sel, dict) else None)
            or 0.5
        )
        direction = str(
            leg_config.get('straddle_direction')
            or leg_config.get('sw_direction')
            or (strike_sel.get('direction') if isinstance(strike_sel, dict) else None)
            or '+'
        ).strip()

        ce_price = get_option_premium_from_db(entry_date, index, atm_strike, 'CE', expiry_date)
        pe_price = get_option_premium_from_db(entry_date, index, atm_strike, 'PE', expiry_date)

        if ce_price is not None and pe_price is not None:
            straddle_price = ce_price + pe_price
            shift = multiplier * straddle_price
            if direction == '-':
                raw_strike = atm_strike - shift
            else:
                raw_strike = atm_strike + shift
            final_strike = round(raw_strike / strike_interval) * strike_interval
            _log(
                f"      STRADDLE_WIDTH: ATM={atm_strike}, CE={ce_price}, "
                f"PE={pe_price}, straddle={straddle_price:.2f}, "
                f"multiplier={multiplier}, direction={direction}, "
                f"shift={shift:.2f} → {final_strike}"
            )
            return final_strike

        _log(f"      STRADDLE_WIDTH: Missing CE/PE data, fallback to ATM={atm_strike}")
        return atm_strike

    # ── SYNTHETIC FUTURE: Find strike where |CE - PE| is minimum ────────────────
    if strike_sel_type == 'SYNTHETIC_FUTURE':
        all_strikes = get_all_strikes_with_premiums(
            date_str, index, expiry_date, 'CE', entry_spot, strike_interval
        )
        if not all_strikes:
            return atm_strike
        
        min_diff = float('inf')
        best_strike = atm_strike
        
        for s in all_strikes:
            ce_price = s['premium']
            pe_price = get_option_premium_from_db(entry_date, index, s['strike'], 'PE', expiry_date)
            if pe_price is not None:
                diff = abs(ce_price - pe_price)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = s['strike']
        
        _log(f"      SYNTHETIC_FUTURE: best_strike={best_strike}, min_diff={min_diff:.2f}")
        return best_strike

    # ── ATM / ITM / OTM string ─────────────────────────────────────────────────
    sel_str = strike_sel
    if isinstance(sel_str, dict):
        sel_str = sel_str.get('strike_type') or sel_str.get('type') or 'ATM'
    sel_str = str(sel_str)
    strike = calculate_strike_from_selection(
        spot_price=entry_spot, strike_interval=strike_interval,
        selection=sel_str, option_type=option_type,
    )
    _log(f"      STRIKE_TYPE '{sel_str}' → strike={strike}")
    return strike


def _recalc_leg_pnl(tleg, leg_exit_date, index, expiry_date, lot_size, fallback_spot):
    """
    Re-fetch market exit price/premium at leg_exit_date and rewrite pnl in-place.
    Works for both OPTION and FUTURE segment legs.
    P&L is calculated in POINTS (no quantity multiplication).
    """
    seg      = tleg.get('segment', 'OPTION')
    position = tleg['position']
    lots     = tleg.get('lots', 1)

    if seg in ('OPTION',):
        new_exit = get_option_premium_from_db(
            date=leg_exit_date.strftime('%Y-%m-%d'),
            index=index,
            strike=tleg['strike'],
            option_type=tleg['option_type'],
            expiry=expiry_date.strftime('%Y-%m-%d'),
        )
        if new_exit is None:
            spot = get_spot_price_from_db(leg_exit_date, index) or fallback_spot
            new_exit = calculate_intrinsic_value(spot=spot, strike=tleg['strike'],
                                                  option_type=tleg['option_type'])
        ep = tleg['entry_premium']
        tleg['exit_premium']    = new_exit
        tleg['early_exit_date'] = leg_exit_date
        
        # P&L in POINTS (no quantity multiplication)
        if position == 'BUY':
            tleg['pnl'] = new_exit - ep
        else:  # SELL
            tleg['pnl'] = ep - new_exit
        
        # Set CE P&L or PE P&L based on option type
        if tleg.get('option_type') == 'CALL':
            tleg['ce_pnl'] = tleg['pnl']
            tleg['pe_pnl'] = 0
        else:  # PUT
            tleg['ce_pnl'] = 0
            tleg['pe_pnl'] = tleg['pnl']

    else:  # FUTURE
        check_expiry = _resolve_nearest_future_expiry(index, leg_exit_date)
        if check_expiry is None:
            check_expiry = tleg.get('futures_expiry')
        new_exit = get_future_price_from_db(
            date=leg_exit_date.strftime('%Y-%m-%d'),
            index=index,
            expiry=check_expiry,
        ) or tleg['entry_price']
        ep = tleg['entry_price']
        tleg['exit_price']      = new_exit
        tleg['futures_expiry']  = check_expiry
        tleg['early_exit_date'] = leg_exit_date
        
        # P&L in POINTS (no quantity multiplication)
        if position == 'BUY':
            tleg['pnl'] = new_exit - ep
        else:  # SELL
            tleg['pnl'] = ep - new_exit
        
        # No CE/PE for futures
        tleg['ce_pnl'] = 0
        tleg['pe_pnl'] = 0


def _copy_sl_tgt_to_leg(leg_dict, leg_src):
    """Copy stopLoss / targetProfit config from leg_src (raw legs_config entry) into leg_dict."""
    if 'stopLoss' in leg_src and isinstance(leg_src['stopLoss'], dict):
        leg_dict['stop_loss']      = leg_src['stopLoss'].get('value')
        leg_dict['stop_loss_type'] = _normalize_sl_tgt_type(leg_src['stopLoss'].get('mode'))
    elif leg_src.get('stop_loss') is not None:
        leg_dict['stop_loss']      = leg_src['stop_loss']
        leg_dict['stop_loss_type'] = _normalize_sl_tgt_type(leg_src.get('stop_loss_type'))
    else:
        leg_dict['stop_loss']      = None
        leg_dict['stop_loss_type'] = 'pct'

    if 'targetProfit' in leg_src and isinstance(leg_src['targetProfit'], dict):
        leg_dict['target']      = leg_src['targetProfit'].get('value')
        leg_dict['target_type'] = _normalize_sl_tgt_type(leg_src['targetProfit'].get('mode'))
    elif leg_src.get('target') is not None:
        leg_dict['target']      = leg_src['target']
        leg_dict['target_type'] = _normalize_sl_tgt_type(leg_src.get('target_type'))
    else:
        leg_dict['target']      = None
        leg_dict['target_type'] = 'pct'


def _copy_trail_sl_to_leg(leg_dict, leg_src):
    """
    Parse trailSL config from frontend payload into per-leg dict.

    Frontend sends:
        leg.trailSL = { mode: 'POINTS'|'PERCENT', trigger: X, move: Y }

    Internal keys added to leg_dict:
        trail_sl_enabled  : bool
        trail_sl_mode     : 'points' | 'pct'
        trail_sl_trigger  : float  (X — favorable move quantum)
        trail_sl_move     : float  (Y — SL shift per trigger)
    """
    tsl = leg_src.get('trailSL') or leg_src.get('trail_sl') or {}

    if isinstance(tsl, dict) and tsl:
        raw_mode = str(tsl.get('mode', 'POINTS')).upper()
        mode = 'pct' if raw_mode in ('PERCENT', 'PCT', '%') else 'points'
        trigger = tsl.get('trigger') or tsl.get('x') or 0
        move    = tsl.get('move')    or tsl.get('y') or 0

        try:
            trigger_val = float(trigger)
            move_val = float(move)
        except (TypeError, ValueError):
            trigger_val = 0.0
            move_val = 0.0

        if trigger_val > 0 and move_val > 0:
            leg_dict['trail_sl_enabled'] = True
            leg_dict['trail_sl_mode']    = mode
            leg_dict['trail_sl_trigger'] = trigger_val
            leg_dict['trail_sl_move']    = move_val
        else:
            leg_dict['trail_sl_enabled'] = False
    else:
        leg_dict['trail_sl_enabled'] = False


def _apply_overall_sl_to_per_leg(per_leg_results, overall_date, overall_reason, n_legs, scheduled_exit_date=None):
    """
    Override per_leg_results with overall SL/TGT date for any leg whose exit
    is not already earlier than the overall trigger date.
    Creates the list if it is None.

    Rules (matching AlgoTest):
      - Leg not yet triggered → override with overall date/reason
      - Leg already triggered BEFORE overall date → keep existing (per-leg wins)
      - Leg triggered on SAME or LATER date → override with overall
    """
    if per_leg_results is None:
        per_leg_results = [
            {'triggered': False,
             'exit_date': scheduled_exit_date,
             'exit_reason': 'EXPIRY'}
            for _ in range(n_legs)
        ]
    for i, r in enumerate(per_leg_results):
        leg_exit = r.get('exit_date')
        # Override when not triggered, or exit_date unknown, or exits same/after overall
        if not r['triggered'] or leg_exit is None or leg_exit >= overall_date:
            per_leg_results[i] = {
                'triggered':   True,
                'exit_date':   overall_date,
                'exit_reason': overall_reason,
            }
    return per_leg_results



def check_leg_stop_loss_target(entry_date, exit_date, expiry_date, entry_spot, legs_config,
                               index, trading_calendar, square_off_mode='partial'):
    """
    Check per-leg stop loss / target during the holding period.

    DATA MODEL — PREVIOUS-DAY CLOSE:
      entry_date is the previous trading day's close date used to fetch entry premiums.
      The holding window therefore starts from the NEXT trading day after entry_date.
      entry_spot is the underlying spot at entry_date (previous-day close).

    SL/Target modes (stop_loss_type / target_type):
      'pct'            → % of entry_premium.
                         SL fires when: (entry_prem - current_prem)/entry_prem*100 >= sl_val  [SELL]
                                         (current_prem - entry_prem)/entry_prem*100 >= sl_val  [BUY fails → adverse]
                         i.e. raw_adverse_pct >= sl_val
      'points'         → Absolute premium point move ADVERSE to position.
                         SELL: SL when (current_prem - entry_prem) >= sl_val  [premium rose = loss]
                         BUY:  SL when (entry_prem - current_prem) >= sl_val  [premium fell = loss]
      'underlying_pts' → Underlying spot moved adversely by X pts from entry_spot.
                         CE SELL / PE BUY : adverse = spot RISES  → SL when (spot - entry_spot) >= sl_val
                         PE SELL / CE BUY : adverse = spot FALLS  → SL when (entry_spot - spot) >= sl_val
      'underlying_pct' → Same logic but in % terms: (|spot - entry_spot| / entry_spot * 100) >= sl_val

    Target fires on the FAVORABLE side (mirror of SL).

    square_off_mode:
        'partial'  – only the triggered leg exits early; others hold to exit_date.
        'complete' – first triggered leg causes ALL legs to exit on the same day.

    Returns:
        list of dicts (one per leg), each:  {'triggered': bool, 'exit_date': Timestamp, 'exit_reason': str}
        Returns None when no leg has any SL/Target configured (caller uses scheduled exit).
    """
    # Quick exit: nothing to check
    has_any_sl_target = any(
        (lg.get('stop_loss') is not None or lg.get('target') is not None or lg.get('trail_sl_enabled'))
        for lg in legs_config
    )
    if not has_any_sl_target:
        return None
    


    # O(log n) searchsorted instead of full DataFrame boolean scan
    _tc_arr = trading_calendar['date'].values.astype('datetime64[ns]')
    _entry_ns = np.datetime64(pd.Timestamp(entry_date), 'ns')
    _exit_ns  = np.datetime64(pd.Timestamp(exit_date),  'ns')
    _lo = np.searchsorted(_tc_arr, _entry_ns, side='right')
    _hi = np.searchsorted(_tc_arr, _exit_ns, side='right')
    holding_days = trading_calendar.iloc[_lo:_hi]['date'].tolist()

    # Per-leg tracking: once a leg is triggered it stays triggered
    leg_results = [
        {
            'triggered': False,
            'exit_date': exit_date,
            'exit_reason': 'EXPIRY',
        }
        for _ in legs_config
    ]

    tsl_state = {}
    for li, leg in enumerate(legs_config):
        if not leg.get('trail_sl_enabled'):
            continue
        segment = (leg.get('segment') or 'OPTION').upper()
        entry_prem = leg.get('entry_price') if segment in ('FUTURES', 'FUTURE') else leg.get('entry_premium')
        if entry_prem is None:
            continue
        position = (leg.get('position') or 'SELL').upper()
        tsl_mode = str(leg.get('trail_sl_mode') or 'points').lower()
        try:
            X_raw = float(leg.get('trail_sl_trigger', 0))
            Y_raw = float(leg.get('trail_sl_move', 0))
        except (TypeError, ValueError):
            continue
        if X_raw <= 0 or Y_raw <= 0:
            continue
        if tsl_mode == 'pct':
            base = abs(entry_prem)
            if base <= 0:
                continue
            X_pts = base * (X_raw / 100.0)
            Y_pts = base * (Y_raw / 100.0)
        else:
            X_pts = X_raw
            Y_pts = Y_raw
        if X_pts <= 0 or Y_pts <= 0:
            continue
        sl_val = leg.get('stop_loss')
        sl_type = _normalize_sl_tgt_type(leg.get('stop_loss_type', 'pct'))
        sl_pts = None
        if sl_val is not None:
            sl_abs = abs(sl_val)
            if sl_type == 'pct':
                base = abs(entry_prem)
                if base:
                    sl_pts = base * (sl_abs / 100.0)
            elif sl_type == 'points':
                sl_pts = sl_abs
        if sl_pts is None:
            sl_pts = X_pts
        current_sl_level = (entry_prem + sl_pts) if position == 'SELL' else (entry_prem - sl_pts)
        tsl_state[li] = {
            'X_pts': X_pts,
            'Y_pts': Y_pts,
            'sl_pts': sl_pts,
            'best_prem': entry_prem,
            'current_sl_level': current_sl_level,
            'triggers_fired': 0,
            'entry_prem': entry_prem,
        }

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

            if sl_val is None and tgt_val is None and not leg.get('trail_sl_enabled'):
                continue  # No SL/Target/Trail-SL for this leg
            
            position = leg['position']
            lot_size = leg.get('lot_size', get_lot_size(index, entry_date))
            lots     = leg.get('lots', 1)

            segment = leg.get('segment', 'OPTION')
            option_type = leg.get('option_type', 'CE')  # safe default for underlying_* checks
            cp = None

            if segment in ('FUTURES', 'FUTURE'):
                check_expiry = _resolve_nearest_future_expiry(index, check_date)
                if check_expiry is None:
                    check_expiry = leg.get('futures_expiry')
                current_price = get_future_price_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    expiry=check_expiry
                )
                if current_price is None:
                    continue

                entry_price = leg.get('entry_price')
                if entry_price is None:
                    continue

                cp = current_price
                # For FUTURES: premium_move = current - entry (positive = rose)
                premium_move = current_price - entry_price
                # Adverse move: SELL hurts when price rises; BUY hurts when price falls
                adverse_premium_pts = premium_move if position == 'SELL' else -premium_move
                # Favorable move: mirror of adverse
                favorable_premium_pts = -adverse_premium_pts
                # % of entry
                adverse_pct = (adverse_premium_pts / entry_price * 100) if entry_price else 0
                favorable_pct = -adverse_pct

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

                cp = current_premium
                entry_premium = leg.get('entry_premium')
                if entry_premium is None:
                    continue

                # premium_move = current - entry (positive = premium rose)
                premium_move = current_premium - entry_premium
                # Adverse: SELL hurts when premium rises; BUY hurts when premium falls
                adverse_premium_pts = premium_move if position == 'SELL' else -premium_move
                favorable_premium_pts = -adverse_premium_pts
                # % of entry premium
                adverse_pct  = (adverse_premium_pts / entry_premium * 100) if entry_premium else 0
                favorable_pct = -adverse_pct

            # ── Spot movement (for underlying-based modes) ────────────────────
            # adverse_spot_pts: positive = spot moved adversely for THIS leg
            # 
            # UNDERLYING POINTS LOGIC:
            # For CE (CALL): Stop loss triggers when spot moves UP by X points from entry_spot
            #   Example: Entry spot 25500, SL 50 pts → triggers when spot >= 25550
            # For PE (PUT): Stop loss triggers when spot moves DOWN by X points from entry_spot
            #   Example: Entry spot 25500, SL 50 pts → triggers when spot <= 25450
            #
            # This is independent of position (BUY/SELL) - it's based on option type direction
            adverse_spot_pts  = 0.0
            adverse_spot_pct  = 0.0
            if sl_type in ('underlying_pts', 'underlying_pct') or \
               tgt_type in ('underlying_pts', 'underlying_pct'):
                current_spot = get_spot_price_from_db(check_date, index)
                if current_spot is not None and entry_spot:
                    spot_move = current_spot - entry_spot  # positive = spot rose, negative = spot fell
                    
                    opt = option_type.upper() if option_type else 'CE'
                    if opt in ('CE', 'CALL', 'C'):
                        # CE (CALL): Adverse when spot RISES (option becomes more ITM/valuable)
                        # For SELL: loss when premium rises (spot goes up)
                        # For BUY: loss when premium falls (spot goes down)
                        adverse_spot_pts = spot_move if position == 'SELL' else -spot_move
                    else:  # PE (PUT)
                        # PE (PUT): Adverse when spot FALLS (option becomes more ITM/valuable)
                        # For SELL: loss when premium rises (spot goes down)
                        # For BUY: loss when premium falls (spot goes up)
                        adverse_spot_pts = -spot_move if position == 'SELL' else spot_move
                    
                    adverse_spot_pct = (adverse_spot_pts / entry_spot * 100) if entry_spot else 0

            # ── Evaluate STOP LOSS ────────────────────────────────────────────
            # SL fires when the position has moved ADVERSELY beyond the threshold.
            # All thresholds are stored as positive numbers.
            skip_plain_sl = leg.get('trail_sl_enabled') and (li in tsl_state)
            hit_sl = False
            if sl_val is not None and not skip_plain_sl:
                sl_abs = abs(sl_val)
                if sl_type == 'pct':
                    # e.g. sl=50 → exit when position is down 50% of entry premium
                    hit_sl = adverse_pct >= sl_abs
                elif sl_type == 'points':
                    # e.g. sl=50 → exit when premium moved 50 pts against position
                    hit_sl = adverse_premium_pts >= sl_abs
                elif sl_type == 'underlying_pts':
                    # e.g. sl=100 → exit when spot moved 100 pts adversely
                    hit_sl = adverse_spot_pts >= sl_abs
                elif sl_type == 'underlying_pct':
                    # e.g. sl=1 → exit when spot moved 1% adversely
                    hit_sl = adverse_spot_pct >= sl_abs

            # ── Evaluate TARGET ───────────────────────────────────────────────
            # TGT fires when position moved FAVORABLY beyond the threshold.
            hit_tgt = False
            if tgt_val is not None:
                tgt_abs = abs(tgt_val)
                if tgt_type == 'pct':
                    hit_tgt = favorable_pct >= tgt_abs
                elif tgt_type == 'points':
                    hit_tgt = favorable_premium_pts >= tgt_abs
                elif tgt_type == 'underlying_pts':
                    hit_tgt = (-adverse_spot_pts) >= tgt_abs
                elif tgt_type == 'underlying_pct':
                    hit_tgt = (-adverse_spot_pct) >= tgt_abs

            # ── Trail SL Evaluation ──────────────────────────────────────────
            hit_tsl = False
            if leg.get('trail_sl_enabled') and li in tsl_state and cp is not None:
                ts = tsl_state[li]
                X_pts = ts['X_pts']
                Y_pts = ts['Y_pts']
                entry_prem = ts['entry_prem']
                if X_pts > 0 and Y_pts > 0:
                    if position == 'SELL':
                        if cp < ts['best_prem']:
                            ts['best_prem'] = cp
                        favorable_move = entry_prem - ts['best_prem']
                        new_triggers = int(favorable_move / X_pts) if X_pts > 0 else 0
                        if new_triggers > ts['triggers_fired']:
                            delta_triggers = new_triggers - ts['triggers_fired']
                            ts['triggers_fired'] = new_triggers
                            ts['current_sl_level'] -= delta_triggers * Y_pts
                            _log(f"    [TSL] Leg {li+1} SELL: favorable_move={favorable_move:.2f}, triggers={new_triggers}, new SL level={ts['current_sl_level']:.2f}")
                        if cp >= ts['current_sl_level']:
                            hit_tsl = True
                            _log(f"    [TSL] Leg {li+1} SELL: FIRED. current={cp:.2f} >= SL={ts['current_sl_level']:.2f}")
                    else:
                        if cp > ts['best_prem']:
                            ts['best_prem'] = cp
                        favorable_move = ts['best_prem'] - entry_prem
                        new_triggers = int(favorable_move / X_pts) if X_pts > 0 else 0
                        if new_triggers > ts['triggers_fired']:
                            delta_triggers = new_triggers - ts['triggers_fired']
                            ts['triggers_fired'] = new_triggers
                            ts['current_sl_level'] += delta_triggers * Y_pts
                            _log(f"    [TSL] Leg {li+1} BUY: favorable_move={favorable_move:.2f}, triggers={new_triggers}, new SL level={ts['current_sl_level']:.2f}")
                        if cp <= ts['current_sl_level']:
                            hit_tsl = True
                            _log(f"    [TSL] Leg {li+1} BUY: FIRED. current={cp:.2f} <= SL={ts['current_sl_level']:.2f}")

            if hit_sl or hit_tgt:
                reason = 'STOP_LOSS' if hit_sl else 'TARGET'
                newly_triggered_this_day.append((li, check_date, reason))
            elif hit_tsl:
                newly_triggered_this_day.append((li, check_date, 'TRAIL_SL'))

        if newly_triggered_this_day:
            if square_off_mode == 'complete':
                trigger_date   = newly_triggered_this_day[0][1]
                trigger_reason = newly_triggered_this_day[0][2]
                triggered_indices = {li for (li, _, _) in newly_triggered_this_day}
                for li2 in range(len(leg_results)):
                    if not leg_results[li2]['triggered']:
                        if li2 in triggered_indices:
                            # This leg actually triggered — keep its own reason
                            leg_results[li2] = {
                                'triggered': True,
                                'exit_date': trigger_date,
                                'exit_reason': trigger_reason,
                            }
                        else:
                            # Collateral exit — mark as COMPLETE_*
                            leg_results[li2] = {
                                'triggered': True,
                                'exit_date': trigger_date,
                                'exit_reason': f'COMPLETE_{trigger_reason}',
                            }
                break  # No need to check further dates
            else:
                # 'partial' – mark only triggered legs, others continue
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

    overall_sl_type supported values:
        'max_loss'           → overall_sl_value is a fixed ₹ amount  (e.g. 5000)
        'total_premium_pct'  → overall_sl_value is % of total entry premium (₹ terms)
                               e.g. 50 means "exit if combined P&L ≤ -50% of total premium collected"
        'points'             → overall_sl_value is absolute premium points per lot
                               threshold = overall_sl_value × total_qty  (summed across legs)
        'underlying_pts'     → overall_sl_value is a spot index move in points
                               Not a ₹ threshold — handled specially in check_overall_sl_target.
                               Returns the raw point value (caller interprets it).
        'underlying_pct'     → overall_sl_value is a spot % move
                               Returns the raw pct value.

    Returns:
        float — the positive ₹ (or point/pct) threshold.  None if overall_sl_value is None.
    """
    if overall_sl_value is None:
        return None

    ntype = _normalize_sl_tgt_type(overall_sl_type) if overall_sl_type else 'pct'

    # Legacy string matching for overall types
    _otype = str(overall_sl_type).lower().replace(' ', '_').replace('-', '_') if overall_sl_type else ''

    if _otype in ('max_loss', 'fixed', 'fixed_rs', 'rs', 'inr'):
        return float(overall_sl_value)

    if _otype in ('total_premium_pct', 'pct', 'percent', 'premium_pct') or ntype == 'pct':
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
        threshold = total_entry_premium_rs * (float(overall_sl_value) / 100.0)
        _log(f"      Overall SL Threshold (pct): {total_entry_premium_rs:.2f} × {overall_sl_value}% = ₹{threshold:.2f}")
        return threshold

    if ntype == 'points':
        # Points: overall_sl_value is the adverse premium points threshold
        # Convert to ₹ by summing qty across all legs
        total_qty = sum(leg.get('lots', 1) * leg.get('lot_size', 1) for leg in trade_legs)
        threshold = float(overall_sl_value) * total_qty if total_qty else float(overall_sl_value)
        _log(f"      Overall SL Threshold (points): {overall_sl_value} × qty={total_qty} = ₹{threshold:.2f}")
        return threshold

    if ntype in ('underlying_pts', 'underlying_pct'):
        # Raw value — check_overall_stop_loss_target handles spot-based check directly
        return float(overall_sl_value)

    # Fallback: treat as max_loss
    return float(overall_sl_value)


def compute_overall_target_threshold(trade_legs, overall_target_type, overall_target_value):
    """
    Compute the ₹ profit target threshold for the overall strategy.
    Mirrors compute_overall_sl_threshold — same type system.

    overall_target_type supported values:
        'max_profit'         → fixed ₹ amount
        'total_premium_pct'  → % of total entry premium
        'points'             → absolute premium points per lot (converted to ₹)
        'underlying_pts'     → raw spot points (handled in check_overall_stop_loss_target)
        'underlying_pct'     → raw spot pct  (handled in check_overall_stop_loss_target)

    Returns:
        float | None
    """
    if overall_target_value is None:
        return None

    ntype = _normalize_sl_tgt_type(overall_target_type) if overall_target_type else 'pct'
    _otype = str(overall_target_type).lower().replace(' ', '_').replace('-', '_') if overall_target_type else ''

    if _otype in ('max_profit', 'fixed', 'fixed_rs', 'rs', 'inr'):
        return float(overall_target_value)

    if _otype in ('total_premium_pct', 'pct', 'percent', 'premium_pct') or ntype == 'pct':
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
        threshold = total_entry_premium_rs * (float(overall_target_value) / 100.0)
        _log(f"      Overall TGT Threshold (pct): {total_entry_premium_rs:.2f} × {overall_target_value}% = ₹{threshold:.2f}")
        return threshold

    if ntype == 'points':
        total_qty = sum(leg.get('lots', 1) * leg.get('lot_size', 1) for leg in trade_legs)
        threshold = float(overall_target_value) * total_qty if total_qty else float(overall_target_value)
        return threshold

    if ntype in ('underlying_pts', 'underlying_pct'):
        return float(overall_target_value)

    return float(overall_target_value)

def check_overall_stop_loss_target(
    entry_date,
    exit_date,
    expiry_date,
    trade_legs,
    index,
    trading_calendar,
    sl_threshold_rs,
    tgt_threshold_rs,
    per_leg_results=None,
    overall_sl_type=None,
    overall_target_type=None,
):
    """
    Overall SL / Target checker.

    DATA MODEL — PREVIOUS-DAY CLOSE:
      entry_date is the previous trading day's close.  Holding starts the NEXT day.
      entry premiums in trade_legs already reflect that previous-day close price.

    For ₹-based types (max_loss, total_premium_pct, points):
      combined_live_pnl = Σ leg P&L using current market prices.
      SL fires when combined_live_pnl ≤ -sl_threshold_rs
      TGT fires when combined_live_pnl ≥ +tgt_threshold_rs

    For underlying_pts / underlying_pct types:
      sl_threshold_rs / tgt_threshold_rs hold the raw point/pct value.
      We compute spot_move from entry_spot and check directly.
      CE SELL / PE BUY: adverse = spot rises → SL when spot_move >= threshold
      PE SELL / CE BUY: adverse = spot falls → SL when -spot_move >= threshold
      (We use the FIRST leg to determine the overall strategy direction.)

    Args:
        per_leg_results: Optional list; closed legs are excluded from combined P&L.
        overall_sl_type / overall_target_type: needed for underlying_* mode detection.
    """

    _log(f"  ===== OVERALL SL/TGT CHECK =====")
    _log(f"  Entry Date: {entry_date}, Exit Date: {exit_date}, Expiry: {expiry_date}")
    _log(f"  SL Threshold: {sl_threshold_rs}, TGT Threshold: {tgt_threshold_rs}")
    _log(f"  Legs: {len(trade_legs)}")
    for i, leg in enumerate(trade_legs):
        _log(f"    Leg {i+1}: {leg.get('option_type')} {leg.get('strike')} {leg.get('position')} @ {leg.get('entry_premium')}")

    if sl_threshold_rs is None and tgt_threshold_rs is None:
        return None, None

    # Detect underlying-based mode
    _sl_ntype  = _normalize_sl_tgt_type(overall_sl_type)  if overall_sl_type  else 'pct'
    _tgt_ntype = _normalize_sl_tgt_type(overall_target_type) if overall_target_type else 'pct'
    sl_is_underlying  = _sl_ntype  in ('underlying_pts', 'underlying_pct')
    tgt_is_underlying = _tgt_ntype in ('underlying_pts', 'underlying_pct')

    # O(log n) searchsorted instead of full DataFrame boolean scan
    _tc_arr = trading_calendar['date'].values.astype('datetime64[ns]')
    _entry_ns = np.datetime64(pd.Timestamp(entry_date), 'ns')
    _exit_ns  = np.datetime64(pd.Timestamp(exit_date),  'ns')
    _lo = np.searchsorted(_tc_arr, _entry_ns, side='right')
    _hi = np.searchsorted(_tc_arr, _exit_ns, side='right')
    holding_days = trading_calendar.iloc[_lo:_hi]['date'].tolist()

    # Build set of leg indices that have already exited (for partial mode)
    closed_leg_indices = set()
    if per_leg_results is not None:
        for li, res in enumerate(per_leg_results):
            if res.get('triggered', False):
                closed_leg_indices.add(li)

    # Determine entry spot for underlying-based checks
    entry_spot_val = None
    if sl_is_underlying or tgt_is_underlying:
        entry_spot_val = get_spot_price_from_db(entry_date, index)

    combined_live_pnl = 0.0  # Initialize for debug logging
    combined_live_pnl = 0.0  # Initialize for debug logging
    for check_date in holding_days:
        combined_live_pnl = 0.0
        has_data = False

        for leg_idx, leg in enumerate(trade_legs):
            if leg_idx in closed_leg_indices:
                continue

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

                if current_premium is None:
                    continue

                has_data = True

                if position == 'BUY':
                    leg_live_pnl = (current_premium - entry_premium) * lots * lot_size
                else:
                    leg_live_pnl = (entry_premium - current_premium) * lots * lot_size

            elif seg in ('FUTURE', 'FUTURES'):
                entry_price = leg.get('entry_price')
                if entry_price is None:
                    continue

                check_expiry = _resolve_nearest_future_expiry(index, check_date)
                if check_expiry is None:
                    check_expiry = leg.get('futures_expiry')
                current_price = get_future_price_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    expiry=check_expiry
                )

                if current_price is None:
                    continue

                has_data = True

                if position == 'BUY':
                    leg_live_pnl = (current_price - entry_price) * lots * lot_size
                else:
                    leg_live_pnl = (entry_price - current_price) * lots * lot_size

            else:
                continue

            combined_live_pnl += leg_live_pnl

        if not has_data:
            continue

        # ── Underlying-based overall SL/TGT ─────────────────────────────────
        if sl_is_underlying or tgt_is_underlying:
            current_spot = get_spot_price_from_db(check_date, index)
            if current_spot is None or entry_spot_val is None:
                pass  # can't evaluate, skip
            else:
                spot_move = current_spot - entry_spot_val  # positive = spot rose
                spot_move_pct = (spot_move / entry_spot_val * 100) if entry_spot_val else 0

                # Determine adverse direction from first active leg
                first_leg = next((trade_legs[i] for i in range(len(trade_legs))
                                  if i not in closed_leg_indices), None)
                if first_leg:
                    fl_pos = first_leg.get('position', 'SELL')
                    fl_opt = first_leg.get('option_type', 'CE').upper()
                    # CE SELL / PE BUY: adverse = rising spot
                    if (fl_opt == 'CE' and fl_pos == 'SELL') or (fl_opt == 'PE' and fl_pos == 'BUY'):
                        adverse_spot_pts = spot_move
                        adverse_spot_pct = spot_move_pct
                    else:
                        adverse_spot_pts = -spot_move
                        adverse_spot_pct = -spot_move_pct

                    if sl_is_underlying and sl_threshold_rs is not None:
                        check_val = adverse_spot_pts if _sl_ntype == 'underlying_pts' else adverse_spot_pct
                        if check_val >= sl_threshold_rs:
                            return check_date, 'OVERALL_SL'

                    if tgt_is_underlying and tgt_threshold_rs is not None:
                        check_val = (-adverse_spot_pts) if _tgt_ntype == 'underlying_pts' else (-adverse_spot_pct)
                        if check_val >= tgt_threshold_rs:
                            return check_date, 'OVERALL_TARGET'

        # ── ₹-based overall SL/TGT ───────────────────────────────────────────
        if not sl_is_underlying and sl_threshold_rs is not None:
            if combined_live_pnl <= -sl_threshold_rs:
                return check_date, 'OVERALL_SL'

        if not tgt_is_underlying and tgt_threshold_rs is not None:
            if combined_live_pnl >= tgt_threshold_rs:
                return check_date, 'OVERALL_TARGET'

    return None, None





def run_algotest_backtest(params):
    """
    Main AlgoTest-style backtest function.

    ═══════════════════════════════════════════════════════════════
    DATA MODEL — PREVIOUS-DAY CLOSE (IMPORTANT)
    ═══════════════════════════════════════════════════════════════
    All bhavcopy / options data is stored as end-of-day (EOD) prices
    indexed on the TRADING DATE itself (i.e. today's closing prices
    are stored under today's date — NOT tomorrow).

    Because AlgoTest uses "previous day close" for strike selection
    and entry prices:
      • entry_date = calculate_trading_days_before_expiry(expiry, entry_dte)
        → this returns the trading day whose EOD data is the "previous
          day close" for the actual entry session.
      • entry_premium = get_option_premium_from_db(entry_date, ...)
        → fetches that day's closing premium (= previous-day close from
          the perspective of someone entering the next morning).
      • Holding window: trading days AFTER entry_date up to exit_date.
        The first check_date is entry_date + 1 trading day.

    So the "previous-day close" shift is already baked into how
    calculate_trading_days_before_expiry works — we do NOT need to
    shift entry_date by one more day.
    ═══════════════════════════════════════════════════════════════

    ═══════════════════════════════════════════════════════════════
    PREMIUM SELECTION MODES (_resolve_strike)
    ═══════════════════════════════════════════════════════════════
    All premium-based criteria scan the bhavcopy for entry_date
    (previous-day close), matching AlgoTest behaviour exactly.

    strike_selection_type:
      'ATM' / 'ITM1' / 'OTM2' etc.
          → calculate_strike_from_selection
      'CLOSEST_PREMIUM'   → strike whose EOD premium is nearest to target
      'PREMIUM_GTE'       → strike with premium >= value, ATM-closest
      'PREMIUM_LTE'       → strike with premium <= value, ATM-closest
      'PREMIUM_RANGE'     → strike with lower <= premium <= upper
    ═══════════════════════════════════════════════════════════════

    ═══════════════════════════════════════════════════════════════
    EXIT LOGIC SUMMARY
    ═══════════════════════════════════════════════════════════════
    Priority (highest first):
      1. Overall SL  (combined portfolio P&L ≤ -threshold)
      2. Overall Target (combined portfolio P&L ≥ +threshold)
      3. Per-leg SL / Target (each leg independently)
      4. Scheduled exit (exit_dte days before expiry)

    When Overall SL/Target fires → ALL legs exit on that date.
      Exit price = market price on that trigger date.
      NO re-entry is allowed after an overall exit.

    When Per-leg SL/Target fires:
      'partial' mode  → only that leg exits; others hold to exit_date.
      'complete' mode → all legs exit on the same trigger date.
      Exit price = market price on the trigger date.
      Re-entry is allowed (when re_entry_enabled=True).

    SL/Target units:
      'pct'            → % of entry premium (adverse direction)
      'points'         → absolute premium points adverse move
      'underlying_pts' → underlying spot moved adversely by X pts
      'underlying_pct' → underlying spot moved adversely by X%
    ═══════════════════════════════════════════════════════════════

    Args:
        params: dict with all strategy configuration (see code below).

    Returns:
        tuple: (trades_df, summary_dict, pivot_dict)
    """
    
    # ========== STEP 1: EXTRACT PARAMETERS ==========
    index = params['index']
    from_date = params['from_date']
    to_date = params['to_date']
    expiry_type = params.get('expiry_type', 'WEEKLY')
    expiry_day_of_week = params.get('expiry_day_of_week', None)
    def _coerce_int(value, default, label):
        try:
            return int(value)
        except (TypeError, ValueError):

            return default

    entry_dte = _coerce_int(params.get('entry_dte', 2), 2, 'Entry')
    exit_dte = _coerce_int(params.get('exit_dte', 0), 0, 'Exit')
    legs_config = params.get('legs', [])
    # Read super_trend_config ONLY from its dedicated key.
    # Never fall back to filter_config — they are separate concepts.
    _raw_stc = params.get('super_trend_config') or 'None'
    if hasattr(_raw_stc, 'value'):
        _raw_stc = _raw_stc.value
    super_trend_config = str(_raw_stc).strip()
    # Treat the string literal "None" as disabled
    str_enabled = super_trend_config in ('5x1', '5x2')
    str_segments = []
    if str_enabled:
        load_super_trend_dates()
        str_segments = get_super_trend_segments(super_trend_config)
        print(f"[STR DEBUG] super_trend_config={super_trend_config}, str_enabled={str_enabled}, segments={len(str_segments)}")
        _log(f"STR Filter ON: {super_trend_config}, segments={len(str_segments)}")
    else:
        print(f"[STR DEBUG] super_trend_config={super_trend_config}, str_enabled={str_enabled} - FILTER OFF")
        _log("STR Filter OFF")
    
    # ── NEW: Date Range Filter ──────────────────────────────────────────────────────
    # filter_config: '5x1', '5x2', 'base2', 'custom', or None (disabled)
    # filter_segments: list of {start, end} for custom CSV
    filter_config = params.get('filter_config', None)
    filter_segments_custom = params.get('filter_segments', []) or []

    # The date-range filter (Block B) is MUTUALLY EXCLUSIVE with STR filter (Block A).
    # - If STR is enabled: Block B is always off (STR handles date gating)
    # - If STR is off: Block B activates only for 'custom' or 'base2' configs
    # - '5x1' and '5x2' configs are STR-only — they must never activate Block B
    if str_enabled:
        _block_b_config = None
        filter_enabled = False
    else:
        _fc = str(filter_config).strip() if filter_config is not None else ''
        # Only enable Block B for custom CSV upload or base2.
        # '5x1'/'5x2' belong to STR path only — reject them here.
        filter_enabled = _fc in ('custom', 'base2') and (
            _fc != 'custom' or len(filter_segments_custom) > 0
        )
        print(f"[FILTER DEBUG] filter_config={filter_config}, _fc={_fc}, filter_enabled={filter_enabled}, custom_segments_count={len(filter_segments_custom)}")
        _block_b_config = filter_config if filter_enabled else None

    filter_segments = []
    filter_segments_ts = []
    if filter_enabled:
        try:
            from base import get_filter_segments
            if str(_block_b_config).strip() == 'custom':
                filter_segments = filter_segments_custom
                _log(f"Custom Filter ON: {len(filter_segments)} segments")
            else:
                filter_segments = get_filter_segments(str(_block_b_config).strip())
                _log(f"Filter ON: {_block_b_config}, segments={len(filter_segments)}")
        except Exception as e:
            _log(f"Warning: Error loading filter segments: {e}")
            filter_enabled = False
            filter_segments = []
    else:
        _log("Filter OFF (handled by STR path or disabled)")

    if filter_enabled and filter_segments:
        for seg in filter_segments:
            try:
                filter_segments_ts.append({
                    'start': pd.Timestamp(seg['start']),
                    'end':   pd.Timestamp(seg['end']),
                })
            except Exception:
                pass
        _log(f"Filter segments loaded: {len(filter_segments_ts)}")

    filter_entry_mode = str(
        params.get('filter_entry_mode', 'dte')
    ).lower().strip()
    fixed_entry_mode = (filter_entry_mode == 'fixed') and (str_enabled or filter_enabled)
    if filter_entry_mode == 'fixed' and not fixed_entry_mode:
        print("[WARN] filter_entry_mode='fixed' requested but no active filter/STR — falling back to DTE mode. "
              f"(str_enabled={str_enabled}, filter_enabled={filter_enabled}, "
              f"filter_config={filter_config})")
    
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
    underlying_type = str(params.get('underlying', 'cash') or 'cash').lower().strip()
    if underlying_type not in ('cash', 'futures'):
        underlying_type = 'cash'

    spot_adjustment_enabled = bool(params.get('spot_adjustment_enabled', False))
    spot_adjustment_direction = str(params.get('spot_adjustment_direction', 'rise') or 'rise').lower().strip()
    if spot_adjustment_direction not in ('rise', 'fall', 'both'):
        spot_adjustment_direction = 'rise'
    try:
        spot_adjustment_pct = float(params.get('spot_adjustment_pct', 1.0))
    except (TypeError, ValueError):
        spot_adjustment_pct = 1.0
    if spot_adjustment_pct < 0.25:
        print(f"[WARN] spot_adjustment_pct too low ({spot_adjustment_pct}) - clamping to 0.25")
        spot_adjustment_pct = 0.25
    elif spot_adjustment_pct > 5.0:
        print(f"[WARN] spot_adjustment_pct too high ({spot_adjustment_pct}) - clamping to 5.0")
        spot_adjustment_pct = 5.0
    spot_adjustment_units = str(params.get('spot_adjustment_units', 'percent') or 'percent').lower().strip()
    if spot_adjustment_units not in ('percent', 'points'):
        spot_adjustment_units = 'percent'

    # Re-entry settings (for both Weekly and Monthly strategies)
    re_entry_enabled = params.get('re_entry_enabled', False)
    re_entry_max = params.get('re_entry_max', 20)

    # ========== STEP 2: LOAD DATA FROM CSV (like generic_multi_leg) ==========
    t_spot = time.perf_counter()
    spot_df = get_strike_data(index, from_date, to_date)
    
    # Create trading calendar from spot data
    trading_calendar = spot_df[['Date']].drop_duplicates().sort_values('Date').reset_index(drop=True)
    trading_calendar.columns = ['date']
    # Pre-build sorted numpy array for O(log n) searchsorted lookups
    trading_calendar_arr = trading_calendar['date'].values.astype('datetime64[ns]')
    
    if expiry_day_of_week is not None:
        expiry_dates = get_custom_expiry_dates(index, expiry_day_of_week, from_date, to_date)
        expiry_df = pd.DataFrame({'Current Expiry': expiry_dates})
    else:
        if expiry_type.upper() == 'WEEKLY':
            expiry_df = get_expiry_dates(index, 'weekly', from_date, to_date)
        else:  # MONTHLY
            expiry_df = get_expiry_dates(index, 'monthly', from_date, to_date)
    
    # ========== STEP 4: INITIALIZE RESULTS ==========
    all_trades = []
    strike_interval = get_strike_interval(index)
    n_expiries = len(expiry_df)
    schedule = []
    for expiry_idx, expiry_row in expiry_df.iterrows():
        expiry_date = expiry_row['Current Expiry']
        entry_date = calculate_trading_days_before_expiry(
            expiry_date=expiry_date,
            days_before=entry_dte,
            trading_calendar_df=trading_calendar
        )
        exit_date = calculate_trading_days_before_expiry(
            expiry_date=expiry_date,
            days_before=exit_dte,
            trading_calendar_df=trading_calendar
        )

        if entry_date is None or exit_date is None:
            _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {expiry_date} ---")
            _log("  WARNING: Missing entry or exit date; skipping expiry")
            continue

        # Extra safety: ensure both dates are Timestamps before comparison
        try:
            entry_date = pd.Timestamp(entry_date)
            exit_date = pd.Timestamp(exit_date)
        except Exception:
            continue

        if entry_date > exit_date:
            _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {expiry_date} ---")
            _log(f"  WARNING: Entry ({entry_date}) after exit ({exit_date}) - skipping")
            continue

        if entry_date == exit_date:
            _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {expiry_date} ---")
            _log(f"  INFO: Entry == Exit ({entry_date}) — zero-holding trade skipped")
            continue

        schedule.append({
            'expiry_idx': expiry_idx,
            'expiry_date': expiry_date,
            'entry_date': entry_date,
            'exit_date': exit_date,
        })

    if not schedule:
        return pd.DataFrame(), {}, {}

    _log(f"Schedule entries constructed: {len(schedule)}")

    segments = []
    if str_enabled:
        if not str_segments:
            _log("STR Filter ON but no segments found - exiting")
            return pd.DataFrame(), {}, {}
        _log(f"DEBUG: Building {len(str_segments)} STR segments")
        if schedule:
            _log(f"DEBUG: Schedule has {len(schedule)} entries, first entry: {schedule[0]['entry_date']}")
        for seg in str_segments:
            seg_start = pd.Timestamp(seg['start'])
            seg_end = pd.Timestamp(seg['end'])
            # Count how many schedule entries fall in this segment
            matching = sum(1 for rec in schedule if seg_start <= pd.Timestamp(rec['entry_date']) <= seg_end) if schedule else 0
            _log(f"DEBUG: STR segment {seg_start.date()} -> {seg_end.date()}: {matching} matching entries")
            segments.append({
                'start': seg_start,
                'end': seg_end,
                'label': f"{seg_start.strftime('%d-%m-%Y')} -> {seg_end.strftime('%d-%m-%Y')}",
                'type': 'STR',
                'raw_segment': seg,
            })
    elif filter_enabled and filter_segments_ts:
                for seg in filter_segments_ts:
                    seg_start = seg['start']
                    seg_end = seg['end']
                    segments.append({
                        'start': seg_start,
                        'end': seg_end,
                        'label': f"{seg_start.strftime('%d-%m-%Y')} -> {seg_end.strftime('%d-%m-%Y')}",
                        'type': 'FILTER',
                    })
    else:
        fallback_start = pd.Timestamp(trading_calendar['date'].min())
        fallback_end = pd.Timestamp(trading_calendar['date'].max())
        segments.append({
            'start': fallback_start,
            'end': fallback_end,
            'label': f"Global {fallback_start.strftime('%d-%m-%Y')} -> {fallback_end.strftime('%d-%m-%Y')}",
            'type': 'GLOBAL',
        })

    segments.sort(key=lambda s: s['start'])
    segment_records = []
    total_entries = 0

    for segment in segments:
        seg_start = segment['start']
        seg_end   = segment['end']
        seg_entries = []

        if fixed_entry_mode:
            # Collect every expiry whose expiry_date falls on or after seg_start.
            # Do NOT filter by DTE entry_date — in Fixed mode the first entry is
            # always forced to seg_start regardless of where the DTE entry falls.
            seg_expiries = []
            for rec in schedule:
                if rec.get('entry_date') is None or rec.get('exit_date') is None:
                    continue
                expiry_ts = pd.Timestamp(rec['expiry_date'])
                if expiry_ts >= seg_start:
                    seg_expiries.append(rec)

            seg_expiries.sort(key=lambda r: pd.Timestamp(r['expiry_date']))

            if not seg_expiries:
                segment_records.append({'segment': segment, 'entries': []})
                continue

            # Forced first entry: first trading day on or after seg_start
            first_entry_ts = _next_trading_day_after(
                trading_calendar,
                seg_start - pd.Timedelta(days=1)
            )
            if first_entry_ts is None:
                segment_records.append({'segment': segment, 'entries': []})
                continue

            current_entry_ts = pd.Timestamp(first_entry_ts)
            if current_entry_ts > seg_end:
                segment_records.append({'segment': segment, 'entries': []})
                continue

            schedule_idx = 0
            while schedule_idx < len(seg_expiries):
                # Advance until we find the next expiry whose expiry_date is > current_entry
                while (schedule_idx < len(seg_expiries) and
                       pd.Timestamp(seg_expiries[schedule_idx]['expiry_date']) <= current_entry_ts):
                    schedule_idx += 1

                if schedule_idx >= len(seg_expiries):
                    break

                rec = seg_expiries[schedule_idx]
                expiry_ts = pd.Timestamp(rec['expiry_date'])

                scheduled_exit_ts = pd.Timestamp(rec['exit_date'])
                if scheduled_exit_ts <= current_entry_ts:
                    exit_ts = expiry_ts  # exit window has passed, hold to expiry
                else:
                    exit_ts = scheduled_exit_ts

                clamped_exit = False
                if exit_ts > seg_end:
                    last_day = _last_trading_day_on_or_before(
                        trading_calendar, seg_end
                    )
                    if last_day is None or pd.Timestamp(last_day) <= current_entry_ts:
                        break
                    exit_ts = pd.Timestamp(last_day)
                    clamped_exit = True

                if current_entry_ts > exit_ts:
                    schedule_idx += 1
                    continue

                seg_entries.append({
                    'segment':      segment,
                    'entry_date':   current_entry_ts,
                    'exit_date':    exit_ts,
                    'expiry_date':  rec['expiry_date'],
                    'clamped_exit': clamped_exit,
                })

                prev_exit_ts = exit_ts
                schedule_idx += 1

                next_entry_ts = None
                temp_idx = schedule_idx
                while temp_idx < len(seg_expiries):
                    candidate          = seg_expiries[temp_idx]
                    candidate_entry    = pd.Timestamp(candidate['entry_date'])
                    candidate_expiry   = pd.Timestamp(candidate['expiry_date'])

                    if candidate_expiry <= prev_exit_ts:
                        temp_idx += 1
                        continue

                    if candidate_entry < seg_start or candidate_entry > seg_end:
                        temp_idx += 1
                        continue

                    if candidate_entry <= prev_exit_ts:
                        temp_idx += 1
                        continue

                    next_entry_ts = candidate_entry
                    schedule_idx  = temp_idx
                    break

                if next_entry_ts is None:
                    break

                current_entry_ts = next_entry_ts

                if current_entry_ts > seg_end:
                    break

        else:
            for rec in schedule:
                if rec.get('entry_date') is None or rec.get('exit_date') is None:
                    continue
                entry_ts = pd.Timestamp(rec['entry_date'])
                if entry_ts < seg_start or entry_ts > seg_end:
                    continue
                exit_ts = pd.Timestamp(rec['exit_date'])
                clamped_exit = False
                if exit_ts > seg_end:
                    clamped_exit = True
                    last_day = _last_trading_day_on_or_before(
                        trading_calendar, seg_end
                    )
                    if last_day is None or pd.Timestamp(last_day) < entry_ts:
                        continue
                    exit_ts = pd.Timestamp(last_day)
                seg_entries.append({
                    'segment':      segment,
                    'entry_date':   rec['entry_date'],
                    'exit_date':    exit_ts,
                    'expiry_date':  rec['expiry_date'],
                    'clamped_exit': clamped_exit,
                })

        segment_records.append({
            'segment': segment,
            'entries': seg_entries,
        })
        total_entries += len(seg_entries)

    if total_entries == 0:
        _log("No trades after applying segment filters - exiting")
        return pd.DataFrame(), {}, {}

    for seg_scope in segment_records:
        segment = seg_scope['segment']
        # Handle case where segment might be a string instead of dict
        if not isinstance(segment, dict):
            _log(f"WARNING: segment is not a dict: {segment}, type: {type(segment)}")
            continue
        count = len(seg_scope['entries'])
        _log(f"[SEGMENT] {segment.get('label', 'N/A')} ({segment.get('start', 'N/A')} -> {segment.get('end', 'N/A')}), entries={count}")
    
    # ========== STEP 4: LOOP THROUGH SEGMENTED SCHEDULE ==========
    t_loop = time.perf_counter()
    trade_id = 0
    
    # ========== Series A & B Accumulators ==========
    # Series A: Spot-Anchored (absolute index points)
    cumulative = None          # will be set from first trade's entry spot
    peak = None                # running max of cumulative
    
    # Series B: Index-Based (compound geometric growth from 100)
    cumulative_index = 100.0   # starts at exactly 100, before any trade
    peak_index = 100.0         # running max of cumulative_index
    
    for seg_scope in segment_records:
        segment = seg_scope['segment']
        for entry_idx, trade_entry in enumerate(seg_scope['entries'], 1):
            entry_date = trade_entry['entry_date']
            exit_date = trade_entry['exit_date']
            expiry_date = trade_entry['expiry_date']
            clamped_exit = trade_entry['clamped_exit']
            trade_id += 1
            _log(f"--- Segment {segment['label']} | Trade {trade_id}/{total_entries} ---")
            _log(f"  Segment window: {segment['start'].strftime('%Y-%m-%d')} -> {segment['end'].strftime('%Y-%m-%d')}")
            _log(f"  Entry Date: {entry_date} | Exit Date: {exit_date}")
            _log(f"[TRADE] id={trade_id} | segment={segment['label']} | "
                 f"segment_window={segment['start'].strftime('%Y-%m-%d')} → {segment['end'].strftime('%Y-%m-%d')} | "
                 f"entry={pd.Timestamp(entry_date).strftime('%Y-%m-%d')} | "
                 f"exit={pd.Timestamp(exit_date).strftime('%Y-%m-%d')}")

            try:
                str_segment = None
                str_segment_label = ''
                base_exit_reason = 'Expiry'
                exit_ts = pd.Timestamp(exit_date)
                filter_exit_reason = None
                trade_segment_end = segment['end'] if segment['type'] == 'FILTER' else None
                if segment['type'] == 'FILTER' and trade_segment_end is not None:
                    seg_end_ts = pd.Timestamp(trade_segment_end)
                    if exit_ts >= seg_end_ts:
                        filter_exit_reason = 'FILTER_END'

                if str_enabled:
                    str_segment = segment.get('raw_segment') or get_active_str_segment(entry_date, super_trend_config)
                    if str_segment is None:
                        _log(f"  STR SKIP: entry {pd.Timestamp(entry_date).strftime('%Y-%m-%d')} NOT in any STR segment")
                        continue
                    seg_start = pd.Timestamp(str_segment['start'])
                    seg_end = pd.Timestamp(str_segment['end'])
                    str_segment_label = f"{seg_start.strftime('%d-%m-%Y')} -> {seg_end.strftime('%d-%m-%Y')}"
                    _log(f"  STR MATCH: entry {pd.Timestamp(entry_date).strftime('%Y-%m-%d')} in segment {str_segment_label}")
                    if clamped_exit:
                        base_exit_reason = 'STR_Exit'
                        _log(f"  STR EXIT at segment end: {pd.Timestamp(exit_date).strftime('%Y-%m-%d')}")
                    else:
                        base_exit_reason = 'Expiry'
                        _log(f"  STR EXIT at expiry: {pd.Timestamp(exit_date).strftime('%Y-%m-%d')}")
                elif segment['type'] == 'FILTER':
                    _log(f"  Filter segment active: entry falls inside {segment['label']}")
                    base_exit_reason = 'FILTER_END' if clamped_exit else 'Expiry'
                    str_segment_label = segment.get('label', '')
            
                # ========== STEP 7: GET ENTRY SPOT / UNDERLYING PRICE ==========
                # By default we use index spot, but Mode B requests futures entry spot
                if underlying_type == 'futures':
                    entry_spot = get_future_price_from_db(
                        entry_date,
                        index,
                        expiry=None,
                    )
                    if entry_spot is None:
                        _log(f"  WARNING: No futures price for {entry_date} - falling back to spot")
                        entry_spot = get_spot_price_from_db(entry_date, index)
                else:
                    entry_spot = get_spot_price_from_db(entry_date, index)

                if entry_spot is None:
                    _log(f"  WARNING: No spot/futures price for {entry_date} - skipping")
                    continue

                _log(f"  Entry Spot: {entry_spot}")

                if spot_adjustment_enabled:
                    adjusted_date, was_adjusted, triggered_direction = apply_spot_adjustment_exit(
                        entry_date=entry_date,
                        entry_spot=entry_spot,
                        scheduled_exit_date=exit_date,
                        expiry_date=expiry_date,
                        spot_adjustment_direction=spot_adjustment_direction,
                        spot_adjustment_pct=spot_adjustment_pct,
                        spot_adjustment_units=spot_adjustment_units,
                        trading_calendar=trading_calendar,
                        index=index,
                    )
                    if was_adjusted:
                        scheduled_exit_ts = pd.Timestamp(exit_date)
                        adjusted_ts = pd.Timestamp(adjusted_date)
                        if adjusted_ts > scheduled_exit_ts:
                            adjusted_ts = scheduled_exit_ts
                        expiry_ts = pd.Timestamp(expiry_date)
                        if adjusted_ts > expiry_ts:
                            adjusted_ts = expiry_ts
                        exit_date = adjusted_ts
                        base_exit_reason = 'SPOT_ADJ_RISE' if triggered_direction == 'RISE' else 'SPOT_ADJ_FALL'
                        _log(f"  Spot adjustment triggered on {adjusted_ts.strftime('%Y-%m-%d')} ({triggered_direction})")

                # ========== STEP 8: PROCESS EACH LEG ==========
                trade_legs = []
            
                for leg_idx, leg_config in enumerate(legs_config):
                    _log(f"\n    Processing Leg {leg_idx + 1}...")

                    # ========== CONVERT LEG FORMAT ==========
                    # Handle both simple format (from users) and full format (from router)
                    # Simple: {'action': 'sell', 'strike': 'ATM', 'opt_type': 'CE', 'premium': 0}
                    # Full:   {'segment': 'OPTIONS', 'position': 'SELL', 'lots': 1, 'option_type': 'CE', 'strike_selection': 'ATM'}
                    if 'segment' not in leg_config:
                        # Simple format — normalise into a COPY so we don't mutate params
                        leg_config = dict(leg_config)   # shallow copy — safe, dicts are flat here
                        leg_config['segment'] = 'OPTIONS'
                        leg_config['position'] = str(leg_config.get('action', leg_config.get('position', 'SELL'))).upper()
                        leg_config['lots'] = leg_config.get('lots', 1)
                        leg_config['option_type'] = leg_config.get('opt_type', leg_config.get('option_type', 'CE'))
                        leg_config['strike_selection'] = leg_config.get('strike', leg_config.get('strike_selection', 'ATM'))

                    # Rename to leg_segment to avoid shadowing the outer segment dict
                    leg_segment = leg_config['segment']
                    position = leg_config['position']
                    lots = int(leg_config.get('lots', 1))

                    if leg_segment == 'FUTURES':
                        _log(f"      Type: FUTURE")
                        _log(f"      Position: {position}")
                        lot_size = get_lot_size(index, entry_date)
                        futures_expiry_pref = str(leg_config.get('expiry', 'monthly') or 'monthly').lower().strip()
                        if futures_expiry_pref in ('next_monthly', 'next_month', 'mid_month'):
                            futures_expiry_pref = 'next_monthly'
                        else:
                            futures_expiry_pref = 'monthly'

                        entry_price, exit_price, _fut_expiry_str = resolve_futures_pnl_with_rollover(
                            entry_date=entry_date,
                            exit_date=exit_date,
                            index=index,
                            position=position,
                            preference=futures_expiry_pref,
                        )

                        if entry_price is None:
                            _log(f"      WARNING: No futures price for entry {entry_date} - skipping leg")
                            continue

                        if exit_price is None:
                            _log(f"      WARNING: No exit futures price - using entry price")
                            exit_price = entry_price

                        _log(f"      Entry Price: {entry_price} (contract expiry: {_fut_expiry_str})")
                        _log(f"      Exit Price: {exit_price}")

                        if position == 'BUY':
                            leg_pnl = exit_price - entry_price
                        else:  # SELL
                            leg_pnl = entry_price - exit_price

                        _log(f"      Lots: {lots}, P&L: {leg_pnl:,.2f}")

                        trade_legs.append({
                            'leg_number': leg_idx + 1,
                            'segment': 'FUTURE',
                            'position': position,
                            'lots': lots,
                            'lot_size': lot_size,
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'pnl': leg_pnl,
                            'ce_pnl': 0,
                            'pe_pnl': 0,
                            'futures_expiry': _fut_expiry_str,
                        })

                    else:  # OPTIONS
                        # ========== OPTIONS LEG ==========
                        option_type = leg_config['option_type']
                        strike_selection = leg_config['strike_selection']

                        _log(f"      Type: OPTION")
                        _log(f"      Option Type: {option_type}")
                        _log(f"      Position: {position}")
                        _log(f"      Strike Selection: {strike_selection}")
                        _log(f"      DEBUG: Full leg_config keys: {list(leg_config.keys())}")
                        _log(f"      DEBUG: leg_config['strike_selection'] = {leg_config.get('strike_selection')}")
                        _log(f"      DEBUG: leg_config['strike_selection_type'] = {leg_config.get('strike_selection_type')}")

                        # ========== CALCULATE STRIKE ==========
                        # Routes through _resolve_strike which handles ALL criteria:
                        # ATM/ITM/OTM, Premium Range, Closest Premium, Premium >=, Premium <=
                        # Uses entry_date bhavcopy (= previous-day close) matching AlgoTest.
                        strike = _resolve_strike(
                            leg_config=leg_config,
                            entry_date=entry_date,
                            entry_spot=entry_spot,
                            expiry_date=expiry_date,
                            strike_interval=strike_interval,
                            index=index,
                        )

                        if strike is None:
                            _log(f"      WARNING: No qualifying strike found for leg {leg_idx+1} — skipping")
                            continue

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

                        # Calculate P&L in POINTS (no quantity multiplication)
                        # CE P&L = Entry - Exit for CALL SELL, Exit - Entry for CALL BUY
                        # PE P&L = Entry - Exit for PUT SELL, Exit - Entry for PUT BUY
                        if position == 'BUY':
                            leg_pnl = exit_premium - entry_premium
                        else:  # SELL
                            leg_pnl = entry_premium - exit_premium

                        # Store CE P&L or PE P&L based on option type (in points, no qty)
                        if option_type == 'CALL':
                            ce_pnl = leg_pnl
                            pe_pnl = 0
                        else:  # PUT
                            ce_pnl = 0
                            pe_pnl = leg_pnl

                        # Store lot_size for DataFrame (but don't use in P&L calculation)
                        lot_size = get_lot_size(index, entry_date)

                        # Store CE P&L or PE P&L based on option type (in points, no qty)
                        if option_type == 'CALL':
                            ce_pnl = leg_pnl
                            pe_pnl = 0
                        else:  # PUT
                            ce_pnl = 0
                            pe_pnl = leg_pnl

                        _log(f"      Lots: {lots}, CE P&L: {ce_pnl:.2f}, PE P&L: {pe_pnl:.2f}, Net P&L: {leg_pnl:,.2f}")

                        trade_legs.append({
                            'leg_number': leg_idx + 1,
                            'segment': 'OPTION',
                            'option_type': option_type,
                            'strike': strike,
                            'position': position,
                            'lots': lots,
                            'lot_size': lot_size,
                            'entry_premium': entry_premium,
                            'exit_premium': exit_premium,
                            'pnl': leg_pnl,
                            'ce_pnl': ce_pnl,
                            'pe_pnl': pe_pnl
                        })
            
                # Guard: if all legs were skipped (no data), don't record this trade
                if not trade_legs:
                    _log(f"  SKIP: no legs resolved for expiry {expiry_date} - missing option data")
                    continue

                # ========== STEP 8B: ATTACH PER-LEG SL/TARGET CONFIG ==========
                for li, tleg in enumerate(trade_legs):
                    lsrc = legs_config[li] if li < len(legs_config) else {}
                    _copy_sl_tgt_to_leg(tleg, lsrc)
                    _copy_trail_sl_to_leg(tleg, lsrc)

                # ========== STEP 8C: PER-LEG SL/TARGET CHECK ==========
                # partial  – only triggered legs exit early; others hold to exit_date
                # complete – first trigger causes ALL remaining legs to exit same day
                per_leg_results = check_leg_stop_loss_target(
                    entry_date=entry_date,
                    exit_date=exit_date,
                    expiry_date=expiry_date,
                    entry_spot=entry_spot,
                    legs_config=trade_legs,
                    index=index,
                    trading_calendar=trading_calendar,
                    square_off_mode=square_off_mode,
                )
            
                # ========== STEP 8C-2: UPDATE EXIT PREMIUMS BASED ON PER-LEG EXIT DATES ==========
                # If per-leg stop loss triggered, recalculate exit premiums using actual exit dates
                if per_leg_results is not None:
                    for li, tleg in enumerate(trade_legs):
                        leg_result = per_leg_results[li]
                        actual_leg_exit_date = leg_result['exit_date']
                    
                        if actual_leg_exit_date != exit_date:
                            if tleg.get('segment') == 'OPTION':
                                # Recalculate option exit premium
                                new_exit_premium = get_option_premium_from_db(
                                    date=actual_leg_exit_date.strftime('%Y-%m-%d'),
                                    index=index,
                                    strike=tleg['strike'],
                                    option_type=tleg['option_type'],
                                    expiry=expiry_date.strftime('%Y-%m-%d')
                                )
                            
                                if new_exit_premium is not None:
                                    old_exit_premium = tleg['exit_premium']
                                    tleg['exit_premium'] = new_exit_premium
                                
                                    # Recalculate P&L in POINTS (no quantity multiplication)
                                    position = tleg['position']
                                    entry_premium = tleg['entry_premium']
                                
                                    if position == 'BUY':
                                        tleg['pnl'] = new_exit_premium - entry_premium
                                    else:  # SELL
                                        tleg['pnl'] = entry_premium - new_exit_premium
                                    
                                    # Set CE P&L or PE P&L
                                    if tleg.get('option_type') == 'CALL':
                                        tleg['ce_pnl'] = tleg['pnl']
                                        tleg['pe_pnl'] = 0
                                    else:  # PUT
                                        tleg['ce_pnl'] = 0
                                        tleg['pe_pnl'] = tleg['pnl']
                        
                            elif tleg.get('segment') == 'FUTURE':
                                early_exit_expiry = _resolve_nearest_future_expiry(
                                    index=index,
                                    date=actual_leg_exit_date,
                                ) or tleg.get('futures_expiry')
                                new_exit_price = get_future_price_from_db(
                                    date=actual_leg_exit_date.strftime('%Y-%m-%d'),
                                    index=index,
                                    expiry=early_exit_expiry
                                )
                            
                                if new_exit_price is not None:
                                    old_exit_price = tleg['exit_price']
                                    tleg['exit_price'] = new_exit_price
                                    tleg['futures_expiry'] = early_exit_expiry
                                
                                    position = tleg['position']
                                    entry_price = tleg['entry_price']
                                
                                    if position == 'BUY':
                                        tleg['pnl'] = new_exit_price - entry_price
                                    else:  # SELL
                                        tleg['pnl'] = entry_price - new_exit_price
                                    
                                    tleg['ce_pnl'] = 0
                                    tleg['pe_pnl'] = 0


                # ========== STEP 8D: OVERALL SL / TARGET CHECK ==========
                # Monitors combined portfolio ₹ P&L over FULL holding window.
                # Not clipped by per-leg exits: in partial mode other legs stay live.
                overall_sl_triggered_date   = None
                overall_sl_triggered_reason = None

                if overall_sl_value is not None or overall_target_value is not None:
                    sl_threshold_rs  = compute_overall_sl_threshold(
                        trade_legs, overall_sl_type, overall_sl_value)
                    tgt_threshold_rs = compute_overall_target_threshold(
                        trade_legs, overall_target_type, overall_target_value)
                    _log(f"  Overall thresholds: SL=₹{sl_threshold_rs}  TGT=₹{tgt_threshold_rs}")
                    overall_sl_triggered_date, overall_sl_triggered_reason = (
                        check_overall_stop_loss_target(
                            entry_date=entry_date,
                            exit_date=exit_date,
                            expiry_date=expiry_date,
                            trade_legs=trade_legs,
                            index=index,
                            trading_calendar=trading_calendar,
                            sl_threshold_rs=sl_threshold_rs,
                            tgt_threshold_rs=tgt_threshold_rs,
                            per_leg_results=per_leg_results,
                            overall_sl_type=overall_sl_type,
                            overall_target_type=overall_target_type,
                        )
                    )

                # ========== STEP 8E: MERGE OVERALL SL → PER-LEG RESULTS ==========
                # Overall SL overrides any per-leg exit that would happen LATER.
                # Earlier per-leg exits are preserved.
                if overall_sl_triggered_date is not None:
                    _log(f"  ⚡ OVERALL {overall_sl_triggered_reason} on "
                         f"{overall_sl_triggered_date.strftime('%Y-%m-%d')}")
                    per_leg_results = _apply_overall_sl_to_per_leg(
                        per_leg_results,
                        overall_sl_triggered_date,
                        overall_sl_triggered_reason,
                        len(trade_legs),
                        scheduled_exit_date=exit_date,
                    )

                # ========== STEP 8F: RECALCULATE EXIT PRICES FOR TRIGGERED LEGS ==
                # For EVERY triggered leg (per-leg SL/TGT or overall SL), re-fetch
                # the market price at leg_exit_date and recompute P&L.
                lot_size_for_pnl = get_lot_size(index, entry_date)
                sl_reason        = None
                any_early        = False

                if per_leg_results is not None:
                    for li, tleg in enumerate(trade_legs):
                        res = per_leg_results[li]
                        if res['triggered']:
                            any_early = True
                            leg_exit_date = res['exit_date']
                            _log(f"  ⚡ Leg {li+1}: exit={leg_exit_date.strftime('%Y-%m-%d')} "
                                 f"reason={res['exit_reason']}")
                            _recalc_leg_pnl(
                                tleg=tleg,
                                leg_exit_date=leg_exit_date,
                                index=index,
                                expiry_date=expiry_date,
                                lot_size=lot_size_for_pnl,
                                fallback_spot=entry_spot,
                            )
                            tleg['exit_reason'] = res['exit_reason']

                    if any_early:
                        first_t = next(
                            (r for r in per_leg_results
                             if r['triggered']
                             and r.get('exit_reason', '').split('[')[0].strip()
                                in _EARLY_EXIT_REASONS),
                            None
                        )
                        sl_reason = first_t['exit_reason'] if first_t else None

                # ========== STEP 9: TOTAL P&L ==========
                total_pnl = sum(leg['pnl'] for leg in trade_legs)
                
                # Calculate CE P&L and PE P&L (in points, no quantity)
                total_ce_pnl = sum(leg.get('ce_pnl', 0) for leg in trade_legs)
                total_pe_pnl = sum(leg.get('pe_pnl', 0) for leg in trade_legs)
                total_fut_pnl = sum(
                    leg.get('pnl', 0)
                    for leg in trade_legs
                    if leg.get('segment') == 'FUTURE'
                )
                
                _log(f"  Total P&L: ₹{total_pnl:,.2f}")
                _log(f"  CE P&L: {total_ce_pnl:.2f}, PE P&L: {total_pe_pnl:.2f}, FUT P&L: {total_fut_pnl:.2f}, Net P&L: {total_ce_pnl + total_pe_pnl + total_fut_pnl:.2f}")

                # ========== Series A & B Calculations ==========
                # Net P&L in points (no quantity multiplication)
                net_pnl = total_ce_pnl + total_pe_pnl + total_fut_pnl
                
                # Net P&L % as decimal (Net P&L / Entry Spot)
                net_pnl_pct = net_pnl / entry_spot if entry_spot != 0 else 0
                
                # ========== Series A: Spot-Anchored (absolute index points) ==========
                if cumulative is None:
                    # First trade: anchor to first trade's entry spot
                    cumulative = entry_spot + net_pnl
                else:
                    cumulative = cumulative + net_pnl
                
                # Peak: running maximum of Cumulative
                if peak is None:
                    peak = cumulative
                else:
                    peak = max(peak, cumulative)
                
                # DD: Cumulative - Peak (zero or negative)
                dd = cumulative - peak
                
                # %DD: (DD / Peak) * 100
                pct_dd = (dd / peak) * 100 if peak != 0 else 0
                
                # ========== Series B: Index-Based (compound geometric growth from 100) ==========
                # Cumulative Index: compound multiplication
                cumulative_index = cumulative_index * (1 + net_pnl_pct)
                
                # Peak Index: running maximum of Cumulative Index
                peak_index = max(peak_index, cumulative_index)
                
                # DD Index: Cumulative Index - Peak Index (zero or negative)
                dd_index = cumulative_index - peak_index
                
                # %DD Index: DD Index / Peak Index (decimal, NOT multiplied by 100)
                pct_dd_index = (dd_index / peak_index) if peak_index != 0 else 0
                
                _log(f"  Series A - Cumulative: {cumulative:.2f}, Peak: {peak:.2f}, DD: {dd:.2f}, %DD: {pct_dd:.2f}")
                _log(f"  Series B - CumIndex: {cumulative_index:.6f}, PeakIndex: {peak_index:.6f}, DDIndex: {dd_index:.6f}, %DDIndex: {pct_dd_index:.6f}")

                # ========== STEP 10: TRADE-LEVEL EXIT DATE ==========
                # Partial mode: legs exit on different days — trade closes when the
                # last leg closes. Use max() over all valid per-leg exit dates.
                if per_leg_results is not None:
                    valid_dates = [r['exit_date'] for r in per_leg_results if r.get('exit_date') is not None]
                    actual_exit_date = max(valid_dates) if valid_dates else exit_date
                else:
                    actual_exit_date = exit_date
            
                exit_spot = get_spot_price_from_db(actual_exit_date, index) or entry_spot

                # ========== STEP 11: RECORD TRADE ==========
                trade_record = {
                    'entry_date':      entry_date,
                    'exit_date':       actual_exit_date,
                    'expiry_date':     expiry_date,
                    'entry_dte':       entry_dte,
                    'exit_dte':        exit_dte,
                    'entry_spot':      entry_spot,
                    'exit_spot':       exit_spot,
                    'exit_reason':     sl_reason or filter_exit_reason or base_exit_reason,
                    'str_segment':     str_segment_label,
                    'segment':         segment,
                    'legs':            trade_legs,
                    'total_pnl':       total_pnl,
                    'total_ce_pnl':    total_ce_pnl,
                    'total_pe_pnl':    total_pe_pnl,
                    'total_fut_pnl':   total_fut_pnl,
                    'net_pnl':         net_pnl,
                    'net_pnl_pct':     net_pnl_pct,
                    # Series A: Spot-Anchored
                    'cumulative':      cumulative,
                    'peak':            peak,
                    'dd':              dd,
                    'pct_dd':          pct_dd,
                    # Series B: Index-Based (for charts)
                    'cumulative_index': cumulative_index,
                    'peak_index':      peak_index,
                    'dd_index':        dd_index,
                    'pct_dd_index':    pct_dd_index,
                    'square_off_mode': square_off_mode,
                    'per_leg_results': per_leg_results,
                    'index':           index,
                }

                all_trades.append(trade_record)

                # ========== RE-ENTRY LOGIC ==========
                # When a per-leg SL/TGT triggered early, re-enter next trading day
                # with fresh strikes per same criteria, hold until exit_date.
                # Chains up to re_entry_max times.
                #
                # IMPORTANT: Re-entry is ONLY triggered by per-leg SL/Target.
                # OVERALL_SL / OVERALL_TARGET end the trade for the entire expiry — NO re-entry.
                if re_entry_enabled:
                    _SL_TGT_REASONS = {
                        # Per-leg reasons only — OVERALL exits do NOT trigger re-entry
                        'STOP_LOSS', 'TARGET', 'TRAIL_SL',
                        'COMPLETE_STOP_LOSS', 'COMPLETE_TARGET',
                    }

                    def _is_sl_tgt_exit(reason_str):
                        if not reason_str:
                            return False
                        return reason_str.split('[')[0].strip() in _SL_TGT_REASONS

                    def _is_overall_exit(reason_str):
                        if not reason_str:
                            return False
                        base = reason_str.split('[')[0].strip()
                        return base in ('OVERALL_SL', 'OVERALL_TARGET')

                    # Guard: if trade-level exit was OVERALL, skip re-entry entirely
                    if _is_overall_exit(sl_reason):
                        pass  # no re-entry after overall exit
                    else:
                        # Re-entry triggers on the EARLIEST per-leg SL/TGT exit before exit_date
                        earliest_trigger = None
                        if per_leg_results:
                            for r in per_leg_results:
                                if (r['triggered']
                                        and _is_sl_tgt_exit(r['exit_reason'])
                                        and r['exit_date'] < exit_date):
                                    if earliest_trigger is None or r['exit_date'] < earliest_trigger:
                                        earliest_trigger = r['exit_date']

                        re_entry_count  = 0
                        re_trigger_date = earliest_trigger  # None → no re-entry

                        while re_trigger_date is not None and re_entry_count < re_entry_max:
                            future_days = trading_calendar[
                                trading_calendar['date'] > re_trigger_date
                            ]['date'].tolist()
                            if not future_days:
                                break

                            re_entry_date = future_days[0]
                            if re_entry_date >= exit_date:
                                break

                            re_entry_spot = get_spot_price_from_db(re_entry_date, index)
                            if re_entry_spot is None:
                                break

                            re_lot_size   = get_lot_size(index, re_entry_date)
                            re_trade_legs = []
                            re_ok         = True

                            for rli, rlc in enumerate(legs_config):
                                rseg = rlc['segment']
                                rpos = rlc['position']
                                rlts = rlc['lots']

                                if rseg == 'FUTURES':
                                    re_fut_pref = str(rlc.get('expiry', 'monthly') or 'monthly').lower().strip()
                                    if re_fut_pref in ('next_monthly', 'next_month', 'mid_month'):
                                        re_fut_pref = 'next_monthly'
                                    else:
                                        re_fut_pref = 'monthly'
                                    rep, rxp, re_fut_expiry = resolve_futures_pnl_with_rollover(
                                        entry_date=re_entry_date,
                                        exit_date=exit_date,
                                        index=index,
                                        position=rpos,
                                        preference=re_fut_pref,
                                    )
                                    if rep is None:
                                        re_ok = False; break
                                    if rxp is None:
                                        rxp = rep
                                    rpnl = (rxp - rep) if rpos == 'BUY' else (rep - rxp)
                                    re_leg = {
                                        'leg_number': rli + 1, 'segment': 'FUTURE',
                                        'position': rpos, 'lots': rlts, 'lot_size': re_lot_size,
                                        'entry_price': rep, 'exit_price': rxp, 'pnl': rpnl,
                                        'futures_expiry': re_fut_expiry,
                                    }
                                else:  # OPTIONS — same strike criteria as initial entry
                                    ropt = rlc.get('option_type', 'CE')
                                    rstk = _resolve_strike(
                                        leg_config=rlc,
                                        entry_date=re_entry_date,
                                        entry_spot=re_entry_spot,
                                        expiry_date=expiry_date,
                                        strike_interval=strike_interval,
                                        index=index,
                                    )
                                    if rstk is None:
                                        re_ok = False; break
                                    rep2 = get_option_premium_from_db(
                                        date=re_entry_date.strftime('%Y-%m-%d'),
                                        index=index, strike=rstk, option_type=ropt,
                                        expiry=expiry_date.strftime('%Y-%m-%d'),
                                    )
                                    if rep2 is None:
                                        re_ok = False; break
                                    rxp2 = get_option_premium_from_db(
                                        date=exit_date.strftime('%Y-%m-%d'),
                                        index=index, strike=rstk, option_type=ropt,
                                        expiry=expiry_date.strftime('%Y-%m-%d'),
                                    )
                                    if rxp2 is None:
                                        s2   = get_spot_price_from_db(exit_date, index) or re_entry_spot
                                        rxp2 = calculate_intrinsic_value(spot=s2, strike=rstk, option_type=ropt)
                                    rpnl2 = (rxp2 - rep2) if rpos == 'BUY' else (rep2 - rxp2)
                                    re_leg = {
                                        'leg_number': rli + 1, 'segment': 'OPTION',
                                        'option_type': ropt, 'strike': rstk,
                                        'position': rpos, 'lots': rlts, 'lot_size': re_lot_size,
                                        'entry_premium': rep2, 'exit_premium': rxp2, 'pnl': rpnl2,
                                    }

                                _copy_sl_tgt_to_leg(re_leg, rlc)
                                _copy_trail_sl_to_leg(re_leg, rlc)
                                re_trade_legs.append(re_leg)

                            if not re_ok or not re_trade_legs:
                                break

                            # Per-leg SL/TGT for this re-entry
                            re_per_leg = check_leg_stop_loss_target(
                                entry_date=re_entry_date,
                                exit_date=exit_date,
                                expiry_date=expiry_date,
                                entry_spot=re_entry_spot,
                                legs_config=re_trade_legs,
                                index=index,
                                trading_calendar=trading_calendar,
                                square_off_mode=square_off_mode,
                            )

                            # Overall SL/TGT for this re-entry
                            re_sl_thr  = compute_overall_sl_threshold(
                                re_trade_legs, overall_sl_type, overall_sl_value)
                            re_tgt_thr = compute_overall_target_threshold(
                                re_trade_legs, overall_target_type, overall_target_value)
                            re_ovr_date, re_ovr_reason = check_overall_stop_loss_target(
                                entry_date=re_entry_date,
                                exit_date=exit_date,
                                expiry_date=expiry_date,
                                trade_legs=re_trade_legs,
                                index=index,
                                trading_calendar=trading_calendar,
                                sl_threshold_rs=re_sl_thr,
                                tgt_threshold_rs=re_tgt_thr,
                                per_leg_results=re_per_leg,
                                overall_sl_type=overall_sl_type,
                                overall_target_type=overall_target_type,
                            )

                            if re_ovr_date is not None:
                                re_per_leg = _apply_overall_sl_to_per_leg(
                                    re_per_leg, re_ovr_date, re_ovr_reason, len(re_trade_legs),
                                    scheduled_exit_date=exit_date,
                                )

                            # Recalculate P&L for triggered re-entry legs
                            re_sl_reason    = None
                            re_next_trigger = None
                            re_lot_sz_pnl   = get_lot_size(index, re_entry_date)

                            if re_per_leg is not None:
                                for rli2, rtleg in enumerate(re_trade_legs):
                                    rres = re_per_leg[rli2]
                                    if rres['triggered']:
                                        _recalc_leg_pnl(
                                            tleg=rtleg,
                                            leg_exit_date=rres['exit_date'],
                                            index=index,
                                            expiry_date=expiry_date,
                                            lot_size=re_lot_sz_pnl,
                                            fallback_spot=re_entry_spot,
                                        )
                                        rtleg['exit_reason'] = rres['exit_reason']
                                        # Only per-leg SL/TGT triggers chaining (not OVERALL)
                                        if (_is_sl_tgt_exit(rres['exit_reason'])
                                                and rres['exit_date'] < exit_date):
                                            if re_next_trigger is None or rres['exit_date'] < re_next_trigger:
                                                re_next_trigger = rres['exit_date']

                                first_re = next((r for r in re_per_leg if r['triggered']), None)
                                re_sl_reason = first_re['exit_reason'] if first_re else None

                            re_total_pnl = sum(l['pnl'] for l in re_trade_legs)
                            re_total_ce_pnl = sum(l.get('ce_pnl', 0) for l in re_trade_legs)
                            re_total_pe_pnl = sum(l.get('pe_pnl', 0) for l in re_trade_legs)
                            re_total_fut_pnl = sum(
                                l.get('pnl', 0) for l in re_trade_legs if l.get('segment') == 'FUTURE'
                            )
                           
                            # Re-entry Series A & B calculations
                            re_net_pnl = re_total_ce_pnl + re_total_pe_pnl + re_total_fut_pnl
                            re_net_pnl_pct = re_net_pnl / re_entry_spot if re_entry_spot != 0 else 0
                            
                            # Series A
                            if cumulative is None:
                                cumulative = re_entry_spot + re_net_pnl
                            else:
                                cumulative = cumulative + re_net_pnl
                            peak = max(peak, cumulative) if peak else cumulative
                            dd = cumulative - peak
                            pct_dd = (dd / peak) * 100 if peak != 0 else 0
                            
                            # Series B
                            cumulative_index = cumulative_index * (1 + re_net_pnl_pct)
                            peak_index = max(peak_index, cumulative_index)
                            dd_index = cumulative_index - peak_index
                            pct_dd_index = (dd_index / peak_index) if peak_index != 0 else 0
                            
                            if re_per_leg is not None:
                                valid_re_dates = [r['exit_date'] for r in re_per_leg if r.get('exit_date') is not None]
                                re_actual_exit = max(valid_re_dates) if valid_re_dates else exit_date
                            else:
                                re_actual_exit = exit_date
                            re_exit_spot   = get_spot_price_from_db(re_actual_exit, index) or re_entry_spot
                            re_suffix      = f'[RE{re_entry_count + 1}]'
                            re_exit_reason = (re_sl_reason or 'EXPIRY') + re_suffix

                            all_trades.append({
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
                                'total_ce_pnl':    re_total_ce_pnl,
                                'total_pe_pnl':    re_total_pe_pnl,
                                'total_fut_pnl':   re_total_fut_pnl,
                                'net_pnl':         re_net_pnl,
                                'net_pnl_pct':     re_net_pnl_pct,
                                'cumulative':      cumulative,
                                'peak':            peak,
                                'dd':              dd,
                                'pct_dd':          pct_dd,
                                'cumulative_index': cumulative_index,
                                'peak_index':      peak_index,
                                'dd_index':        dd_index,
                                'pct_dd_index':    pct_dd_index,
                                'square_off_mode': square_off_mode,
                                'per_leg_results': re_per_leg,
                                'index':           index,
                            })
                            re_entry_count += 1

                            # Chain: only if re-entry itself hit a per-leg SL/TGT (not OVERALL)
                            re_ovr_exit = re_ovr_date is not None  # OVERALL fired on this re-entry
                            if re_next_trigger is not None and re_next_trigger > re_trigger_date and not re_ovr_exit:
                                re_trigger_date = re_next_trigger
                            else:
                                break

            except Exception as e:
                print(f"  ERROR: {str(e)}")
                traceback.print_exc()
                continue
    
    print(f"[DEBUG] After main loop: all_trades has {len(all_trades)} items")
    if all_trades:
        print(f"[DEBUG] Sample trade: entry_date={all_trades[0].get('entry_date')}, legs={len(all_trades[0].get('legs', []))}")
    
    # ========== STEP 11: CONVERT TO DATAFRAME ==========
    # Filter out trades with no legs (skipped due to missing option data)
    all_trades = [t for t in all_trades if t.get('legs')]
    _log(f"[DEBUG] all_trades after filtering: {len(all_trades)}")
    if all_trades:
        _log(f"[DEBUG] First trade keys: {list(all_trades[0].keys())}")
        _log(f"[DEBUG] First trade legs: {all_trades[0].get('legs')}")
    
    if not all_trades:
        _log("[DEBUG] all_trades is empty after filtering!")
        return pd.DataFrame(), {}, {}
    
    # Pre-fetch exit spot prices to avoid repeated DB calls
    _all_exit_dates = set()
    for t in all_trades:
        _all_exit_dates.add(str(t.get('exit_date', '')))
        per_leg = t.get('per_leg_results') or []
        for plr in per_leg:
            _all_exit_dates.add(str(plr.get('exit_date', '')))
    _exit_spot_cache = {}
    for _ed in _all_exit_dates:
        if _ed:
            _sp = get_spot_price_from_db(_ed, index)
            if _sp is not None:
                _exit_spot_cache[_ed] = _sp

    # Flatten for DataFrame - Create rows for EACH leg (AlgoTest format)
    # But we'll aggregate them back for analytics
    trades_flat = []
    trade_counter = 0
    flatten_errors = []
    _log(f"[DEBUG] Starting flatten loop for {len(all_trades)} trades")
    for trade_idx, trade in enumerate(all_trades, 1):
        entry_spot_val = trade['entry_spot']
        per_leg_res    = trade.get('per_leg_results')  # None if no SL/Target configured

        # Create SEPARATE row for EACH leg (like AlgoTest CSV format)
        for leg_idx, leg in enumerate(trade['legs']):
            try:
                leg_num = leg['leg_number']
                li      = leg_num - 1  # 0-based index

                # ── Resolve per-leg exit date & reason ────────────────────────────
                # In partial mode different legs can exit on different dates.
                # In complete / overall-SL mode all legs share the same date.
                if per_leg_res is not None and li < len(per_leg_res):
                    leg_exit_date   = per_leg_res[li].get('exit_date') or trade['exit_date']
                    leg_exit_reason = per_leg_res[li].get('exit_reason', 'EXPIRY')
                else:
                    leg_exit_date   = trade['exit_date']
                    leg_exit_reason = trade.get('exit_reason', 'EXPIRY')

                # ── Exit spot price taken from the leg's own exit date ─────────────
                # Each leg may exit on a different day (partial mode), so we fetch
                # the spot price for that specific exit date.
                leg_exit_spot = _exit_spot_cache.get(str(leg_exit_date))
                if leg_exit_spot is None:
                    leg_exit_spot = trade.get('exit_spot', entry_spot_val)

                # ── Check if trade has any options legs (for Spot columns visibility) ─
                has_options_leg = any(l.get('segment') != 'FUTURE' for l in trade['legs'])
                has_fut_leg = any(l.get('segment') == 'FUTURE' for l in trade['legs'])
                _log(f"[DEBUG] Trade {trade_idx}: has_fut_leg={has_fut_leg}, leg segment={leg.get('segment')}")

                # ── Entry / Exit price (premium for options, price for futures) ────
                if leg['segment'] == 'FUTURE':
                    leg_option_type = 'FUT'
                    position    = leg['position']
                    strike      = ''
                    entry_price = leg.get('entry_price', 0)
                    exit_price  = leg.get('exit_price', 0)
                    fut_entry_price = entry_price
                    fut_exit_price = exit_price
                    leg_pnl     = leg.get('pnl')
                    if leg_pnl is None:
                        direction = -1 if position == 'BUY' else 1
                        leg_pnl = direction * (entry_price - exit_price)
                    ce_pnl_val  = 0
                    pe_pnl_val  = 0
                    fut_pnl_val = leg_pnl
                else:
                    leg_option_type = leg['option_type']
                    position    = leg['position']
                    strike      = leg['strike']
                    entry_price = leg['entry_premium']
                    exit_price  = leg.get('exit_premium', 0)
                    fut_entry_price = np.nan
                    fut_exit_price = np.nan
                    leg_pnl     = leg['pnl']
                    # CE P&L and PE P&L in points (no quantity)
                    ce_pnl_val  = leg.get('ce_pnl', 0)
                    pe_pnl_val  = leg.get('pe_pnl', 0)
                    fut_pnl_val = 0

                lots          = leg.get('lots', 1)
                lot_size      = leg.get('lot_size', 65)
                qty           = lots * lot_size
                
                # ── Trade-level P&L (sum of all legs) for Net P&L and % P&L ────────
                # Get trade-level totals (already calculated in trade record)
                trade_total_ce_pnl = trade.get('total_ce_pnl', 0)
                trade_total_pe_pnl = trade.get('total_pe_pnl', 0)
                trade_total_fut_pnl = trade.get('total_fut_pnl', 0)
                trade_net_pnl = trade.get('net_pnl', 0)  # in points (no qty)
                
                # Net P&L = points only (no quantity multiplication)
                net_pnl_points = trade_net_pnl
                
                # % P&L = (Trade Net P&L / Trade Entry Spot) * 100
                if pd.notna(entry_spot_val) and float(entry_spot_val) > 1000:
                    pct_pnl = round((trade_net_pnl / float(entry_spot_val)) * 100, 2)
                else:
                    pct_pnl = 0.0
                    _log(f"  WARNING: Invalid entry_spot_val={entry_spot_val} for Trade {trade_idx} — %P&L set to 0")

                segment_meta = trade.get('segment') or {}
                segment_type = segment_meta.get('type')
                segment_column_name = 'Filter Segment' if segment_type == 'FILTER' else 'STR Segment'
                
                # % P&L = (Net P&L / Entry Price) * 100 (entry price = premium for options, futures price for futures)
                # pct_pnl already computed above

                # Get trade-level values (Series B - compound index for CSV)
                trade_cumulative_index = round(trade.get('cumulative_index', 100.0), 6)
                trade_peak_index = round(trade.get('peak_index', 100.0), 6)
                trade_dd_index = round(trade.get('dd_index', 0), 6)
                trade_pct_dd_index = round(trade.get('pct_dd_index', 0), 8)
                
                # ── Show/Hide Spot columns based on leg type ──────────────────────
                # If only futures legs, hide Entry/Exit Spot and Spot P&L
                show_spot_cols = has_options_leg
                
                row = {
                    'Trade':        trade_idx,
                    'Leg':          leg_num,
                    'Index':        trade_idx,
                    'Entry Date':   trade['entry_date'],
                    'Exit Date':    leg_exit_date,
                'Type':         leg_option_type,
                'Strike':       strike,
                'B/S':          position,
                'Qty':          qty,
                'Entry Price':  entry_price,
                'Exit Price':   exit_price,
                'Entry Spot':   entry_spot_val if entry_spot_val is not None else np.nan,
                'Exit Spot':    leg_exit_spot if leg_exit_spot is not None else np.nan,
                'Spot P&L':     (round(leg_exit_spot - entry_spot_val, 2)
                                 if show_spot_cols and leg_exit_spot is not None and entry_spot_val is not None
                                 else np.nan),
                    'Expiry':       (
                        leg.get('futures_expiry') 
                        if leg.get('segment') == 'FUTURE' 
                        else (
                            trade['expiry_date'].strftime('%Y-%m-%d') 
                            if hasattr(trade['expiry_date'], 'strftime') 
                            else str(trade['expiry_date'])[:10]
                        )
                    ),
                    'CE P&L':       ce_pnl_val,
                    'PE P&L':       pe_pnl_val,
                    'FUT P&L':      fut_pnl_val,
                    'FUT Entry Price': fut_entry_price if leg.get('segment') == 'FUTURE' else '',
                    'FUT Exit Price': fut_exit_price if leg.get('segment') == 'FUTURE' else '',
                    'Net P&L':      net_pnl_points,
                    '% P&L':        pct_pnl,
                    'Cumulative':   trade_cumulative_index,
                    'Peak':         trade_peak_index,
                    'DD':           trade_dd_index,
                    '%DD':          trade_pct_dd_index,
                    'Exit Reason':  leg_exit_reason,
                }
                row[segment_column_name] = trade.get('str_segment', '')
                if leg.get('segment') == 'FUTURE':
                    _log(f"[DEBUG] Adding FUT columns: segment={leg.get('segment')}, entry={fut_entry_price}, exit={fut_exit_price}")

                trades_flat.append(row)
            except Exception as e:
                flatten_errors.append(f"Trade {trade_idx}, Leg {leg_idx}: {str(e)}")
                print(f"[DEBUG] ERROR in flatten: Trade {trade_idx}, Leg {leg_idx}: {str(e)}")
                continue

        trade_counter += len(trade['legs'])
    
    t_loop_elapsed = time.perf_counter() - t_loop
    t_agg = time.perf_counter()
    
    print(f"[DEBUG] flatten: {len(all_trades)} trades, {len(trades_flat)} rows, errors: {flatten_errors}")
    if not trades_flat:
        print(f"[DEBUG] trades_flat is empty! all_trades had {len(all_trades)} items")
    trades_df = pd.DataFrame(trades_flat)
    print(f"[DEBUG] trades_df created: {len(trades_df)} rows, cols: {list(trades_df.columns)[:10]}")
    
    # ========== AGGREGATE LEGS INTO TRADES FOR ANALYTICS ==========
    if trades_df.empty:
        _log("[DEBUG] trades_df is empty after DataFrame creation!")
        return pd.DataFrame(), {}, {}
    
    # Group by Trade number and sum P&L to get one row per trade
    trades_aggregated = trades_df.groupby('Trade').agg({
        'Entry Date': 'first',
        'Exit Date': 'first',
        'Entry Spot': 'first',
        'Exit Spot': 'first',
        'Spot P&L': 'first',
        'CE P&L': 'sum',      # Sum CE P&L across all legs
        'PE P&L': 'sum',      # Sum PE P&L across all legs
        'FUT P&L': 'sum',     # Sum Future P&L across all legs
        'FUT Entry Price': lambda grp: next((v for v in grp if pd.notna(v) and v != ''), np.nan),
        'FUT Exit Price': lambda grp: next((v for v in grp if pd.notna(v) and v != ''), np.nan),
        'Net P&L': 'sum',    # Sum P&L across all legs
        'Exit Reason': 'first'
    }).reset_index()
    
    # Calculate Trade-level % P&L = Total Points P&L / Entry Spot * 100
    trades_aggregated['Net P&L'] = (
        trades_aggregated['CE P&L'] +
        trades_aggregated['PE P&L'] +
        trades_aggregated['FUT P&L']
    )

    spot_column = 'Entry Spot' if 'Entry Spot' in trades_aggregated else 'entry_spot'
    numeric_cols = ['Spot P&L', 'CE P&L', 'PE P&L', 'FUT P&L', 'FUT Entry Price', 'FUT Exit Price', spot_column, 'Exit Spot', 'Net P&L']
    for col in numeric_cols:
        if col in trades_aggregated.columns:
            trades_aggregated[col] = pd.to_numeric(trades_aggregated[col], errors='coerce')

    spot_series = trades_aggregated[spot_column]
    trades_aggregated['% P&L'] = (
        (trades_aggregated['Net P&L'] / spot_series.replace(0, float('nan'))) * 100
    ).round(2).fillna(0)
    
    # ========== STEP 12: COMPUTE ANALYTICS (ADDS CUMULATIVE, PEAK, DD, %DD) ==========
    trades_aggregated, summary = compute_analytics(trades_aggregated)
    
    # ========== MERGE ANALYTICS BACK TO DETAILED TRADES ==========
    # Merge Cumulative, Peak, DD, %DD from aggregated back to detailed leg-by-leg DataFrame
    # NOTE: trades_flat already has 'Cumulative' from Series B (compound index), so we need to
    # preserve it by renaming the analytics columns to avoid collision
    analytics_cols = ['Trade', 'Cumulative', 'Peak', 'DD', '%DD']
    trades_aggregated_subset = trades_aggregated[analytics_cols].rename(columns={
        'Cumulative': 'Cumulative_SeriesA',
        'Peak': 'Peak_SeriesA',
        'DD': 'DD_SeriesA',
        '%DD': 'PctDD_SeriesA'
    })
    trades_df = trades_df.merge(trades_aggregated_subset, on='Trade', how='left')
    
    # print(f"\nDEBUG: trades_df columns after merge: {list(trades_df.columns)}")
    # print(f"DEBUG: First row Cumulative: {trades_df.iloc[0]['Cumulative'] if 'Cumulative' in trades_df.columns else 'MISSING'}")
    
    # ========== STEP 13: BUILD PIVOT TABLE ==========
    t_pivot = time.perf_counter()
    pivot = build_pivot(trades_aggregated, 'Exit Date')
    
    t_end = time.perf_counter()
    t_total = t_end - t_spot

    # Print timing summary (only if not too fast — avoid log spam)
    if t_total > 0.5:
        t_agg_actual = t_pivot - t_agg
        t_pivot_actual = t_end - t_pivot
        try:
            n_exp = len(expiry_df)
        except:
            n_exp = 'N/A'
        print(f"[PERF] {index} {from_date}→{to_date} | "
              f"data_load={t_loop - t_spot:.2f}s | "
              f"loop({n_exp} exps)={t_agg - t_loop:.2f}s | "
              f"agg+analytics={t_agg_actual:.2f}s | "
              f"pivot={t_pivot_actual:.2f}s | "
              f"TOTAL={t_total:.2f}s")
    
    return trades_df, summary, pivot

# Add debug at the very end
