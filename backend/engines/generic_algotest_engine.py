"""
Generic AlgoTest-Style Engine
Matches AlgoTest behavior exactly with DTE-based entry/exit
"""
import os
import sys

# Set DEBUG = True to enable verbose logging for debugging
DEBUG = True

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from services.index_metadata import get_lot_size_for_index

_EARLY_EXIT_REASONS = {
    'STOP_LOSS',
    'TARGET',
    'TRAIL_SL',
    'COMPLETE_STOP_LOSS',
    'COMPLETE_TARGET',
    'OVERALL_SL',
    'OVERALL_TARGET',
}


def _normalize_slippage_pct(value):
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return 0.0
    if pct < 0:
        return 0.0
    if pct > 100:
        return 100.0
    return pct


def _apply_slippage(price, position, side, slippage_pct):
    if price is None:
        return None
    try:
        raw_price = float(price)
    except (TypeError, ValueError):
        return price

    pct = _normalize_slippage_pct(slippage_pct)
    if pct <= 0:
        return round(raw_price, 2)

    pos = str(position or '').upper().strip()
    side_key = str(side or '').lower().strip()
    is_sell = pos == 'SELL'

    if side_key == 'entry':
        factor = 1 - (pct / 100.0) if is_sell else 1 + (pct / 100.0)
    else:
        factor = 1 + (pct / 100.0) if is_sell else 1 - (pct / 100.0)

    return round(max(raw_price * factor, 0.0), 2)

def _calculate_fo_charges(entry_price, exit_price, qty, position, segment='OPTION'):
    """
    Calculate Zerodha F&O transaction charges for one leg (entry + exit orders combined).

    Based on Zerodha's published charge schedule:
    - Options: ₹20 flat brokerage, 0.15% STT on sell-side premium, 0.03553% NSE txn,
               ₹10/crore SEBI, 0.003% stamp on buy-side, 18% GST on brokerage+txn+SEBI
    - Futures: ₹20 or 0.03% brokerage (lower), 0.05% STT on sell-side, 0.00183% NSE txn,
               ₹10/crore SEBI, 0.002% stamp on buy-side, 18% GST on brokerage+txn+SEBI

    Args:
        entry_price:  Per-unit entry price (after slippage, if any)
        exit_price:   Per-unit exit price (after slippage, if any)
        qty:          Total quantity (lots × lot_size)
        position:     'SELL' or 'BUY'
        segment:      'OPTION' (default) or 'FUTURE'

    Returns:
        dict:
            total_charges_inr      - Total charges in ₹
            entry_charge_per_unit  - Entry-side charges ÷ qty  (deduct from/add to entry price)
            exit_charge_per_unit   - Exit-side charges ÷ qty   (add to/deduct from exit price)
            breakdown              - Itemised ₹ amounts
    """
    _zero = {'total_charges_inr': 0.0, 'entry_charge_per_unit': 0.0,
             'exit_charge_per_unit': 0.0, 'breakdown': {}}

    if entry_price is None or exit_price is None:
        return _zero
    try:
        ep = float(entry_price)
        xp = float(exit_price)
        q  = float(qty)
    except (TypeError, ValueError):
        return _zero
    if q <= 0:
        return _zero

    is_sell    = str(position or '').upper().strip() == 'SELL'
    is_options = str(segment  or 'OPTION').upper().strip() != 'FUTURE'

    to_entry = ep * q   # turnover at entry
    to_exit  = xp * q   # turnover at exit

    if is_options:
        # ── OPTIONS ─────────────────────────────────────────────────────────
        brk_e = 20.0
        brk_x = 20.0

        # STT: 0.15% on sell-side premium only
        stt_e  = 0.0015 * to_entry if is_sell else 0.0
        stt_x  = 0.0    if is_sell else 0.0015 * to_exit   # exit is BUY side when is_sell

        # Stamp: 0.003% on buy-side only
        stmp_e = 0.0          if is_sell else 0.00003 * to_entry
        stmp_x = 0.00003 * to_exit if is_sell else 0.0

        # Exchange txn: NSE 0.03553% on premium
        txn_e = 0.0003553 * to_entry
        txn_x = 0.0003553 * to_exit

        # SEBI: ₹10 per crore = 0.000001 × turnover
        sebi_e = 1e-6 * to_entry
        sebi_x = 1e-6 * to_exit

        # GST 18% on (brokerage + txn + SEBI)  – not on STT / stamp
        gst_e = 0.18 * (brk_e + txn_e + sebi_e)
        gst_x = 0.18 * (brk_x + txn_x + sebi_x)

    else:
        # ── FUTURES ─────────────────────────────────────────────────────────
        brk_e = min(20.0, 0.0003 * to_entry)
        brk_x = min(20.0, 0.0003 * to_exit)

        stt_e  = 0.0005 * to_entry if is_sell else 0.0
        stt_x  = 0.0    if is_sell else 0.0005 * to_exit

        stmp_e = 0.0           if is_sell else 0.00002 * to_entry
        stmp_x = 0.00002 * to_exit if is_sell else 0.0

        txn_e = 0.0000183 * to_entry
        txn_x = 0.0000183 * to_exit

        sebi_e = 1e-6 * to_entry
        sebi_x = 1e-6 * to_exit

        gst_e = 0.18 * (brk_e + txn_e + sebi_e)
        gst_x = 0.18 * (brk_x + txn_x + sebi_x)

    total_entry = stt_e + brk_e + txn_e + sebi_e + stmp_e + gst_e
    total_exit  = stt_x + brk_x + txn_x + sebi_x + stmp_x + gst_x
    total_charges_inr = round(total_entry + total_exit, 4)

    return {
        'total_charges_inr':     total_charges_inr,
        'entry_charge_per_unit': round(total_entry / q, 6),
        'exit_charge_per_unit':  round(total_exit  / q, 6),
        'breakdown': {
            'stt':        round(stt_e  + stt_x,  2),
            'brokerage':  round(brk_e  + brk_x,  2),
            'txn':        round(txn_e  + txn_x,  2),
            'sebi':       round(sebi_e + sebi_x, 4),
            'stamp':      round(stmp_e + stmp_x, 4),
            'gst':        round(gst_e  + gst_x,  2),
        },
    }


def _log(*args, **kwargs):
    """Helper to print only when DEBUG is True"""
    if DEBUG:
        print(*args, **kwargs)


def format_date_dd_mm_yyyy(value):
    """Normalize dates to DD-MM-YYYY strings."""
    if value is None:
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return value
    if hasattr(value, 'strftime'):
        return value.strftime('%d-%m-%Y')
    try:
        parsed = pd.to_datetime(value)
        if pd.isna(parsed):
            return value
        return parsed.strftime('%d-%m-%Y')
    except Exception:
        return value

import pandas as pd
import numpy as np
import math as _math
from datetime import datetime, timedelta
import sys
import os
import time
import traceback
try:
    import polars as pl
except Exception:
    pl = None


def _futures_only_next_monthly_schedule(legs_config):
    """
    Futures-only strategies that contain a next-month futures leg must anchor
    their schedule like a next-expiry strategy: entry against the current
    expiry, exit against the next expiry.
    """
    opt_legs = [
        leg for leg in legs_config
        if str(leg.get('segment', 'OPTION')).upper() not in ('FUTURES', 'FUTURE')
    ]
    fut_legs = [
        leg for leg in legs_config
        if str(leg.get('segment', 'OPTION')).upper() in ('FUTURES', 'FUTURE')
    ]
    if opt_legs or not fut_legs:
        return False

    return any(
        str(leg.get('expiry', 'monthly') or 'monthly').lower() in (
            'next_monthly',
            'next_month',
            'monthly_t1',
        )
        for leg in fut_legs
    )


def get_lot_size(index, entry_date):
    """
    Returns index-specific lot size based on trade date.

    Kept as the engine-facing wrapper so existing P&L code paths remain
    unchanged while metadata stays centralized.
    """
    return get_lot_size_for_index(index, entry_date)


# Import from base.py
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import base as base_market_data
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
    get_futures_exit_date,
    get_futures_rollover_entry_date,
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


def _date_key(value):
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.strftime('%Y-%m-%d')
    except Exception:
        return str(value)[:10] if str(value) else None


def _safe_float(value):
    try:
        if value is None or value == '':
            return None
        val = float(value)
        if _math.isnan(val):
            return None
        return val
    except Exception:
        return None


def _expiry_candidates(expiry):
    try:
        expiry_ts = pd.Timestamp(expiry)
        if pd.isna(expiry_ts):
            return []
        return [
            expiry_ts.strftime('%Y-%m-%d'),
            (expiry_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
            (expiry_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
        ]
    except Exception:
        return []


def _is_future_leg(leg):
    return str(leg.get('segment', '') or '').upper() in ('FUTURE', 'FUTURES') or str(leg.get('option_type', '') or '').upper() == 'FUT'


def _get_leg_expiry(leg, trade=None):
    if _is_future_leg(leg):
        return leg.get('futures_expiry') or leg.get('expiry_date') or leg.get('expiry')
    expiry = leg.get('_resolved_expiry') or leg.get('expiry_date') or leg.get('expiry')
    if expiry is None and trade is not None:
        expiry = trade.get('expiry_date')
    return expiry


def _ohlc_from_polars(date_df, index, leg, expiry):
    if pl is None or date_df is None or not hasattr(date_df, 'filter') or date_df.is_empty():
        return None

    cols = set(date_df.columns)
    high_col = 'High' if 'High' in cols else 'Close'
    low_col = 'Low' if 'Low' in cols else 'Close'
    if high_col not in cols or low_col not in cols or 'Symbol' not in cols:
        return None

    filtered = date_df.filter(pl.col('Symbol') == str(index).upper())
    if filtered.is_empty():
        return None

    if _is_future_leg(leg):
        if 'Instrument' not in cols:
            return None
        filtered = filtered.filter(pl.col('Instrument').cast(pl.Utf8).str.to_uppercase().str.contains('FUT'))
    else:
        if not {'OptionType', 'StrikePrice'}.issubset(cols):
            return None
        opt = str(leg.get('option_type', '') or '').upper()
        if opt in ('CALL', 'C'):
            opt = 'CE'
        elif opt in ('PUT', 'P'):
            opt = 'PE'
        strike = _safe_float(leg.get('strike'))
        if strike is None:
            return None
        filtered = filtered.filter(
            (pl.col('OptionType').cast(pl.Utf8).str.to_uppercase() == opt) &
            ((pl.col('StrikePrice').cast(pl.Float64) - float(strike)).abs() <= 0.5)
        )

    if filtered.is_empty():
        return None

    expiry_values = _expiry_candidates(expiry)
    if expiry_values and 'ExpiryDate' in cols:
        expiry_expr = pl.col('ExpiryDate').cast(pl.Date).cast(pl.Utf8)
        exact = None
        for expiry_str in expiry_values:
            exact = filtered.filter(expiry_expr == expiry_str)
            if not exact.is_empty():
                filtered = exact
                break
        else:
            return None

    try:
        agg = filtered.select([
            pl.col(high_col).cast(pl.Float64).max().alias('High'),
            pl.col(low_col).cast(pl.Float64).min().alias('Low'),
        ])
        if agg.is_empty():
            return None
        high = _safe_float(agg['High'][0])
        low = _safe_float(agg['Low'][0])
        if high is None or low is None:
            return None
        return high, low
    except Exception:
        return None


def _ohlc_from_pandas(date_df, index, leg, expiry):
    if date_df is None or date_df.empty or 'Symbol' not in date_df.columns:
        return None

    high_col = 'High' if 'High' in date_df.columns else 'Close'
    low_col = 'Low' if 'Low' in date_df.columns else 'Close'
    if high_col not in date_df.columns or low_col not in date_df.columns:
        return None

    filtered = date_df[date_df['Symbol'].astype(str).str.upper() == str(index).upper()].copy()
    if filtered.empty:
        return None

    if _is_future_leg(leg):
        if 'Instrument' not in filtered.columns:
            return None
        filtered = filtered[filtered['Instrument'].astype(str).str.upper().str.contains('FUT', na=False)]
    else:
        opt = str(leg.get('option_type', '') or '').upper()
        if opt in ('CALL', 'C'):
            opt = 'CE'
        elif opt in ('PUT', 'P'):
            opt = 'PE'
        strike = _safe_float(leg.get('strike'))
        if strike is None or 'OptionType' not in filtered.columns or 'StrikePrice' not in filtered.columns:
            return None
        filtered = filtered[
            (filtered['OptionType'].astype(str).str.upper() == opt) &
            ((pd.to_numeric(filtered['StrikePrice'], errors='coerce') - float(strike)).abs() <= 0.5)
        ]

    if filtered.empty:
        return None

    expiry_values = _expiry_candidates(expiry)
    if expiry_values and 'ExpiryDate' in filtered.columns:
        exp_series = pd.to_datetime(filtered['ExpiryDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        matched = filtered[exp_series.isin(expiry_values)]
        if matched.empty:
            return None
        filtered = matched

    highs = pd.to_numeric(filtered[high_col], errors='coerce')
    lows = pd.to_numeric(filtered[low_col], errors='coerce')
    if highs.dropna().empty or lows.dropna().empty:
        return None
    return float(highs.max()), float(lows.min())


def _get_ohlc_for_leg_on_date(index, date_str, leg, expiry):
    bulk_by_date = getattr(base_market_data, '_bulk_bhav_by_date', {}) or {}
    date_df = bulk_by_date.get(date_str)
    if date_df is not None:
        result = _ohlc_from_polars(date_df, index, leg, expiry)
        if result is not None:
            return result

    try:
        bhav_df = load_bhavcopy(date_str)
    except Exception:
        return None
    return _ohlc_from_pandas(bhav_df, index, leg, expiry)


def _calculate_mae_mfe_from_extremes(entry_price, position, entry_spot, max_high, min_low):
    entry = _safe_float(entry_price)
    spot = _safe_float(entry_spot)
    high = _safe_float(max_high)
    low = _safe_float(min_low)
    if entry is None or spot is None or spot == 0 or high is None or low is None:
        return None, None

    pos = str(position or '').upper().strip()
    if pos == 'SELL':
        mae = (entry - high) / spot
        mfe = (entry - low) / spot
    else:
        mae = (low - entry) / spot
        mfe = (high - entry) / spot

    # Store percent-points, matching the existing % P&L style in the trade sheet.
    return round(mae * 100, 4), round(mfe * 100, 4)


def _calculate_leg_mae_mfe(index, entry_date, exit_date, leg, entry_price, position, entry_spot, trading_calendar_df, trade=None):
    if trading_calendar_df is None or trading_calendar_df.empty:
        return None, None

    start = _next_trading_day_after(trading_calendar_df, entry_date)
    if start is None:
        return None, None

    try:
        end = pd.Timestamp(exit_date)
        if pd.isna(end) or start > end:
            return None, None
    except Exception:
        return None, None

    cal = trading_calendar_df.copy()
    cal['date'] = pd.to_datetime(cal['date'])
    window = cal[(cal['date'] >= start) & (cal['date'] <= end)]
    if window.empty:
        return None, None

    expiry = _get_leg_expiry(leg, trade)
    highs = []
    lows = []
    for date_value in window['date']:
        date_str = _date_key(date_value)
        if not date_str:
            continue
        ohlc = _get_ohlc_for_leg_on_date(index, date_str, leg, expiry)
        if ohlc is None:
            continue
        high, low = ohlc
        highs.append(high)
        lows.append(low)

    if not highs or not lows:
        return None, None

    return _calculate_mae_mfe_from_extremes(
        entry_price=entry_price,
        position=position,
        entry_spot=entry_spot,
        max_high=max(highs),
        min_low=min(lows),
    )


def _parse_futures_rollover_config(leg_config):
    exit_mode = str(leg_config.get('fut_exit_mode', 'ON_EXPIRY') or 'ON_EXPIRY').upper()
    if exit_mode not in ("ON_EXPIRY", "N_DAYS_BEFORE_EXPIRY", "LAST_WEEK_BEFORE_EXPIRY"):
        exit_mode = "ON_EXPIRY"
    try:
        n_days = int(leg_config.get('fut_n_days', 5))
    except (TypeError, ValueError):
        n_days = 5
    if n_days < 1:
        n_days = 1
    if n_days > 15:
        n_days = 15
    return {
        "exit_mode": exit_mode,
        "n_days": n_days,
        "with_filter": bool(leg_config.get('fut_with_filter', True)),
        "sl_override": bool(leg_config.get('fut_sl_override', True)),
        "target_override": bool(leg_config.get('fut_target_override', True)),
        "with_spot_adj": bool(leg_config.get('fut_with_spot_adj', True)),
    }


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


def compute_buffer_reference_price(
    spot_price: float,
    buffer_value: float,
    buffer_unit: str,
    buffer_position: str,
) -> float:
    """Compute the shifted spot reference used for strike selection."""
    if buffer_unit == 'percent':
        buffer_pts = spot_price * (buffer_value / 100.0)
    else:
        buffer_pts = float(buffer_value)

    if buffer_position == 'above':
        return spot_price + buffer_pts
    return spot_price - buffer_pts


def snap_to_strike_interval(ref_price: float, strike_interval: int) -> float:
    """Round the buffered reference price to the nearest valid strike interval."""
    return float(round(ref_price / strike_interval) * strike_interval)



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
        'PCT_OF_ATM':     'PCT_OF_ATM',
        'PCTOFATM':       'PCT_OF_ATM',
        '%OFATM':         'PCT_OF_ATM',
        'PERCENTOFATM':   'PCT_OF_ATM',
        'PERCENT_OF_ATM': 'PCT_OF_ATM',
        'ATM_STRADDLE_PREM_PCT': 'ATM_STRADDLE_PREM_PCT',
        'ATM_STRADDLE_PREMIUM_PCT': 'ATM_STRADDLE_PREM_PCT',
        'ATMSTRADDLEPREMIUMPCT': 'ATM_STRADDLE_PREM_PCT',
        'ATMSTRADDLEPREMPCT': 'ATM_STRADDLE_PREM_PCT',
    }
    strike_sel_type = _type_aliases.get(strike_sel_type, strike_sel_type)
    
    _log(f"      DEBUG: strike_sel_type AFTER normalization = '{strike_sel_type}'")

    date_str  = entry_date.strftime('%Y-%m-%d')
    spot_atm_strike = round(entry_spot / strike_interval) * strike_interval

    _buf_enabled = bool(leg_config.get('_buffer_strike_enabled', False))
    try:
        _buf_value = float(leg_config.get('_buffer_strike_value', 0.5))
    except (TypeError, ValueError):
        _buf_value = 0.5
    _buf_unit = str(leg_config.get('_buffer_strike_unit', 'percent') or 'percent').lower().strip()
    if _buf_unit not in ('percent', 'points'):
        _buf_unit = 'percent'
    _buf_apply_to = str(leg_config.get('_buffer_strike_apply_to', 'both') or 'both').lower().strip()
    if _buf_apply_to not in ('call', 'put', 'both'):
        _buf_apply_to = 'both'
    _buf_above = bool(leg_config.get('_buffer_position_above', True))
    _buf_below = bool(leg_config.get('_buffer_position_below', True))

    option_type_for_buf = str(leg_config.get('option_type', option_type) or '').upper().strip()
    _is_ce = option_type_for_buf in ('CE', 'CALL', 'C')
    _is_pe = option_type_for_buf in ('PE', 'PUT', 'P')

    # "Above" checkbox gates CE buffering; "Below" checkbox gates PE buffering.
    # A leg whose checkbox is NOT checked gets no buffer — it stays at ATM.
    # CE is always buffered ABOVE (more OTM); PE is always buffered BELOW (more OTM).
    _checkbox_allows = (_is_ce and _buf_above) or (_is_pe and _buf_below)

    _apply_buffer_to_this_leg = (
        _buf_enabled and _buf_value > 0 and (_is_ce or _is_pe) and
        _checkbox_allows and
        (
            _buf_apply_to == 'both' or
            (_buf_apply_to == 'call' and _is_ce) or
            (_buf_apply_to == 'put' and _is_pe)
        )
    )

    # IMPORTANT: Strike buffer must be applied AFTER the base strike is resolved.
    # Direction is fixed by leg type: CE always goes above spot (more OTM),
    # PE always goes below spot (more OTM). The checkboxes gate whether
    # buffering applies at all, not which direction.
    base_atm = spot_atm_strike
    buffer_position = None
    if _apply_buffer_to_this_leg:
        buffer_position = 'above' if _is_ce else 'below'

    atm_strike = spot_atm_strike

    def _apply_strike_buffer_after_selection(base_strike):
        if base_strike is None:
            return None, 0, None

        if not _apply_buffer_to_this_leg or not buffer_position:
            return float(base_strike), 0, float(base_strike)

        try:
            base = float(base_strike)
        except:
            return base_strike, 0, None

        if base <= 0:
            return base_strike, 0, None

        if _buf_unit == 'points':
            pct = (float(_buf_value) / base) * 100.0
        else:
            pct = float(_buf_value)

        if pct <= 0:
            return float(base), 0, float(base)

        buffer_value = base * (pct / 100.0)

        if option_type_for_buf in ('CE', 'CALL', 'C'):
            if buffer_position == 'above':
                shifted = base + buffer_value
            else:
                shifted = base - buffer_value
        else:
            if buffer_position == 'below':
                shifted = base - buffer_value
            else:
                shifted = base + buffer_value

        if option_type_for_buf in ('CE', 'CALL', 'C'):
            snapped = _math.ceil(shifted / strike_interval) * strike_interval
        else:
            snapped = _math.floor(shifted / strike_interval) * strike_interval

        offset = snapped - base

        # buffer_ref_price = ATM base ± buffer_value (the raw shifted price before snapping)
        # e.g. ATM=22800, 0.5% → 22800 + 114 = 22914, then snapped to 22950
        # ref_price shows exactly where the buffer lands before interval rounding
        ref_price = shifted

        _log(
            f"      [BUFFER] {option_type_for_buf} leg: base_strike={base:.0f}, "
            f"pct={pct:.2f}%, buffer_value={buffer_value:.2f}, shifted={shifted:.2f}, "
            f"final_strike={snapped:.0f}, offset={offset:.0f}, ref_price={ref_price:.2f}"
        )
        return snapped, offset, ref_price

    leg_config['_buffer_runtime'] = {
        'enabled': _buf_enabled,
        'applied': _apply_buffer_to_this_leg,
        'position': buffer_position,
        # Kept for logging/traceability; buffer is NOT used to modify ATM/reference for selection.
        'reference_price': None,
        'base_atm': base_atm,
        'spot_atm_strike': spot_atm_strike,
        'atm_strike': atm_strike,
    }

    # Helper: extract value from strike_selection dict (frontend stores params inside it)
    _ss = strike_sel if isinstance(strike_sel, dict) else {}

    # ── PREMIUM RANGE: lower <= premium <= upper ───────────────────────────────
    if strike_sel_type == 'PREMIUM_RANGE':
        min_prem = (leg_config.get('min_premium') or leg_config.get('lower')
                    or _ss.get('lower') or _ss.get('min_premium'))
        max_prem = (leg_config.get('max_premium') or leg_config.get('upper')
                    or _ss.get('upper') or _ss.get('max_premium'))
        if min_prem is None or max_prem is None:
            _log(f"      WARNING: PREMIUM_RANGE missing lower/upper — skipping trade")
            return None, 0, None
        _log(f"      PREMIUM_RANGE: Searching for strikes with premium between {min_prem} and {max_prem}")
        strike = calculate_strike_from_premium_range(
            date=date_str, index=index, expiry=expiry_date,
            option_type=option_type, spot_price=entry_spot,
            strike_interval=strike_interval,
            min_premium=float(min_prem), max_premium=float(max_prem),
        )
        _log(f"      PREMIUM_RANGE [{min_prem}, {max_prem}] → strike={strike}")
        if strike is None:
            _log(f"      WARNING: No strike found in premium range [{min_prem}, {max_prem}] — skipping trade")
            return None, 0, None
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(strike)
        return final_strike, offset, ref_price

    # ── CLOSEST PREMIUM: nearest to target value ───────────────────────────────
    if strike_sel_type == 'CLOSEST_PREMIUM':
        target = (
            leg_config.get('premium')
            or leg_config.get('strike_selection_value')
            or (strike_sel if isinstance(strike_sel, (int, float)) else None)
            or _ss.get('premium') or _ss.get('value')
        )
        if target is None:
            _log(f"      WARNING: CLOSEST_PREMIUM missing target — skipping trade")
            return None, 0, None
        strike = calculate_strike_from_closest_premium(
            date=date_str, index=index, expiry=expiry_date,
            option_type=option_type, spot_price=entry_spot,
            strike_interval=strike_interval, target_premium=float(target),
        )
        _log(f"      CLOSEST_PREMIUM target={target} → strike={strike}")
        if strike is None:
            _log(f"      WARNING: CLOSEST_PREMIUM found no strike — skipping trade")
            return None, 0, None
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(strike)
        return final_strike, offset, ref_price

    # ── PREMIUM >= : all strikes with premium >= value, pick ATM-closest ───────
    if strike_sel_type == 'PREMIUM_GTE':
        min_prem = (
            leg_config.get('premium')
            or leg_config.get('strike_selection_value')
            or (strike_sel if isinstance(strike_sel, (int, float)) else None)
            or _ss.get('premium') or _ss.get('value')
        )
        if min_prem is None:
            min_prem = _ss.get('lower')
        if min_prem is None:
            _log(f"      WARNING: PREMIUM_GTE missing value — skipping trade")
            return None, 0, None
        _log(f"      PREMIUM_GTE: Searching for strikes with premium >= {min_prem}")
        all_strikes = get_all_strikes_with_premiums(
            date_str, index, expiry_date, option_type, entry_spot, strike_interval
        )
        _log(f"      Total strikes available: {len(all_strikes)}")
        qualifying = [s for s in all_strikes if s['premium'] >= float(min_prem)]
        if not qualifying:
            _log(f"      WARNING: No strike with premium >= {min_prem}")
            return None, 0, None
        _log(f"      Found {len(qualifying)} qualifying strikes, showing first 5: {[(s['strike'], s['premium']) for s in qualifying[:5]]}")
        # Pick strike with premium closest to the target value (min_prem)
        # Deterministic tie-breaking: prefer higher strike for CE, lower for PE
        option_type_upper = option_type.upper() if option_type else 'CE'
        if option_type_upper in ['CE', 'CALL', 'C']:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(min_prem)), abs(x['strike'] - atm_strike), -x['strike']))
        else:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(min_prem)), abs(x['strike'] - atm_strike), x['strike']))
        _log(f"      PREMIUM_GTE >= {min_prem} → strike={best['strike']} (premium={best['premium']:.2f}, closest to target, ATM={atm_strike})")
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(best['strike'])
        return final_strike, offset, ref_price

    # ── PREMIUM <= : all strikes with premium <= value, pick ATM-closest ───────
    if strike_sel_type == 'PREMIUM_LTE':
        max_prem = (
            leg_config.get('premium')
            or leg_config.get('strike_selection_value')
            or (strike_sel if isinstance(strike_sel, (int, float)) else None)
            or _ss.get('premium') or _ss.get('value') or _ss.get('upper')
        )
        if max_prem is None:
            _log(f"      WARNING: PREMIUM_LTE missing value — skipping trade")
            return None, 0, None
        _log(f"      PREMIUM_LTE: Searching for strikes with premium <= {max_prem}")
        all_strikes = get_all_strikes_with_premiums(
            date_str, index, expiry_date, option_type, entry_spot, strike_interval
        )
        _log(f"      Total strikes available: {len(all_strikes)}")
        qualifying = [s for s in all_strikes if s['premium'] <= float(max_prem)]
        if not qualifying:
            _log(f"      WARNING: No strike with premium <= {max_prem}")
            return None, 0, None
        _log(f"      Found {len(qualifying)} qualifying strikes, showing first 5: {[(s['strike'], s['premium']) for s in qualifying[:5]]}")
        # Pick strike with premium closest to the target value (max_prem)
        # Deterministic tie-breaking: prefer higher strike for CE, lower for PE
        option_type_upper = option_type.upper() if option_type else 'CE'
        if option_type_upper in ['CE', 'CALL', 'C']:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(max_prem)), abs(x['strike'] - atm_strike), -x['strike']))
        else:
            best = min(qualifying, key=lambda x: (abs(x['premium'] - float(max_prem)), abs(x['strike'] - atm_strike), x['strike']))
        _log(f"      PREMIUM_LTE <= {max_prem} → strike={best['strike']} (premium={best['premium']:.2f}, closest to target, ATM={atm_strike})")
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(best['strike'])
        return final_strike, offset, ref_price

    # ── STRADDLE WIDTH: ATM ± (multiplier × (ATM CE + ATM PE)) ────────────────
    if strike_sel_type == 'STRADDLE_WIDTH':
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
            final_strike, offset, ref_price = _apply_strike_buffer_after_selection(final_strike)
            return final_strike, offset, ref_price

        _log(f"      STRADDLE_WIDTH: Missing CE/PE data, fallback to ATM={atm_strike}")
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(atm_strike)
        return final_strike, offset, ref_price

    # ── SYNTHETIC FUTURE: Find strike where |CE - PE| is minimum ────────────────
    if strike_sel_type == 'SYNTHETIC_FUTURE':
        all_strikes = get_all_strikes_with_premiums(
            date_str, index, expiry_date, 'CE', entry_spot, strike_interval
        )
        if not all_strikes:
            final_strike, offset, ref_price = _apply_strike_buffer_after_selection(atm_strike)
            return final_strike, offset, ref_price
        
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
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(best_strike)
        return final_strike, offset, ref_price

    # ── % OF ATM: ATM ± (pct% of ATM strike) ──────────────────────────────────
    if strike_sel_type == 'PCT_OF_ATM':
        pct = (
            leg_config.get('pct_value')
            or (strike_sel.get('value') if isinstance(strike_sel, dict) else None)
            or (strike_sel.get('pct') if isinstance(strike_sel, dict) else None)
            or 0
        )
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            pct = 0.0
        direction = (
            leg_config.get('pct_direction')
            or (strike_sel.get('direction') if isinstance(strike_sel, dict) else None)
            or '-'
        )
        direction = str(direction).strip()
        shift = (atm_strike * pct) / 100.0
        raw_strike = atm_strike - shift if direction == '-' else atm_strike + shift
        final = round(raw_strike / strike_interval) * strike_interval
        _log(f"      PCT_OF_ATM: ATM={atm_strike}, pct={pct}, direction={direction} → {final}")
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(final)
        return final_strike, offset, ref_price

    # ── ATM STRADDLE PREMIUM %: target = pct% × (ATM CE+PE), then closest-premium ──
    if strike_sel_type == 'ATM_STRADDLE_PREM_PCT':
        pct = (
            leg_config.get('atm_straddle_prem_pct')
            or (strike_sel.get('value') if isinstance(strike_sel, dict) else None)
            or 0
        )
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            pct = 0.0

        ce_price = get_option_premium_from_db(entry_date, index, atm_strike, 'CE', expiry_date)
        pe_price = get_option_premium_from_db(entry_date, index, atm_strike, 'PE', expiry_date)
        if ce_price is None or pe_price is None:
            _log("      ATM_STRADDLE_PREM_PCT: Missing ATM CE/PE premium — skipping trade")
            return None, 0, None

        straddle_price = float(ce_price) + float(pe_price)
        target_premium = (pct / 100.0) * straddle_price
        _log(f"      ATM_STRADDLE_PREM_PCT: ATM={atm_strike}, straddle={straddle_price:.2f}, pct={pct} → target={target_premium:.2f}")

        strike = calculate_strike_from_closest_premium(
            date=date_str, index=index, expiry=expiry_date,
            option_type=option_type, spot_price=entry_spot,
            strike_interval=strike_interval, target_premium=target_premium,
        )
        if strike is None:
            _log("      ATM_STRADDLE_PREM_PCT: No strike found for target premium — skipping trade")
            return None, 0, None
        final_strike, offset, ref_price = _apply_strike_buffer_after_selection(strike)
        return final_strike, offset, ref_price

    # ── ATM / ITM / OTM string ─────────────────────────────────────────────────
    sel_str = strike_sel
    if isinstance(sel_str, dict):
        sel_str = sel_str.get('strike_type') or sel_str.get('type') or 'ATM'
    sel_str = str(sel_str)
    strike = calculate_strike_from_selection(
        spot_price=entry_spot,
        strike_interval=strike_interval,
        selection=sel_str, option_type=option_type,
    )
    _log(f"      STRIKE_TYPE '{sel_str}' → strike={strike}")
    final_strike, offset, ref_price = _apply_strike_buffer_after_selection(strike)
    return final_strike, offset, ref_price


def _get_future_price_for_held_contract(date, index, leg):
    """Prefer the futures contract already stored on the leg, then fall back."""
    date_ts = pd.Timestamp(date)
    stored_expiry = leg.get('futures_expiry')

    if stored_expiry:
        price = get_future_price_from_db(
            date=date_ts.strftime('%Y-%m-%d'),
            index=index,
            expiry=stored_expiry,
        )
        if price is not None:
            return price, stored_expiry

    fallback_expiry = _resolve_nearest_future_expiry(index, date_ts)
    if fallback_expiry and fallback_expiry != stored_expiry:
        price = get_future_price_from_db(
            date=date_ts.strftime('%Y-%m-%d'),
            index=index,
            expiry=fallback_expiry,
        )
        if price is not None:
            return price, fallback_expiry

    return None, stored_expiry or fallback_expiry


def _recalc_leg_pnl(tleg, leg_exit_date, index, expiry_date, lot_size, fallback_spot, slippage_pct=0.0):
    """
    Re-fetch market exit price/premium at leg_exit_date and rewrite pnl in-place.
    Works for both OPTION and FUTURE segment legs.
    P&L is calculated in POINTS (no quantity multiplication).
    """
    seg      = tleg.get('segment', 'OPTION')
    position = tleg['position']
    lots     = tleg.get('lots', 1)

    if seg in ('OPTION',):
        _leg_expiry_for_lookup = tleg.get('_resolved_expiry') or expiry_date
        new_exit = get_option_premium_from_db(
            date=leg_exit_date.strftime('%Y-%m-%d'),
            index=index,
            strike=tleg['strike'],
            option_type=tleg['option_type'],
            expiry=pd.Timestamp(_leg_expiry_for_lookup).strftime('%Y-%m-%d'),
        )
        if new_exit is None:
            spot = get_spot_price_from_db(leg_exit_date, index) or fallback_spot
            new_exit = calculate_intrinsic_value(spot=spot, strike=tleg['strike'],
                                                  option_type=tleg['option_type'])
        ep = tleg['entry_premium']
        adjusted_exit = _apply_slippage(new_exit, position, 'exit', slippage_pct)
        tleg['market_exit_premium'] = new_exit
        tleg['raw_exit_premium'] = new_exit
        tleg['exit_premium']    = adjusted_exit
        tleg['early_exit_date'] = leg_exit_date
        
        # P&L in POINTS (no quantity multiplication)
        if position == 'BUY':
            tleg['pnl'] = adjusted_exit - ep
        else:  # SELL
            tleg['pnl'] = ep - adjusted_exit
        
        # Set CE P&L or PE P&L based on option type
        if tleg.get('option_type') in ('CE', 'CALL', 'C'):
            tleg['ce_pnl'] = tleg['pnl']
            tleg['pe_pnl'] = 0
        elif tleg.get('option_type') in ('PE', 'PUT', 'P'):
            tleg['ce_pnl'] = 0
            tleg['pe_pnl'] = tleg['pnl']
        else:
            tleg['ce_pnl'] = 0
            tleg['pe_pnl'] = 0

    else:  # FUTURE
        new_exit, check_expiry = _get_future_price_for_held_contract(leg_exit_date, index, tleg)
        if new_exit is None:
            new_exit = tleg['entry_price']
        ep = tleg['entry_price']
        adjusted_exit = _apply_slippage(new_exit, position, 'exit', slippage_pct)
        tleg['market_exit_price'] = new_exit
        tleg['raw_exit_price'] = new_exit
        tleg['exit_price']      = adjusted_exit
        tleg['futures_expiry']  = check_expiry
        tleg['early_exit_date'] = leg_exit_date
        
        # P&L in POINTS (no quantity multiplication)
        if position == 'BUY':
            tleg['pnl'] = adjusted_exit - ep
        else:  # SELL
            tleg['pnl'] = ep - adjusted_exit
        
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


def _parse_leg_reentry_config(leg_src: dict) -> dict:
    """
    Parse per-leg re-entry config from a raw leg payload.

    Supports the frontend camelCase payload and the older snake_case fallback.
    Re-entry counts are capped at 20 and clamped to a minimum of 1.
    Lazy-leg configs are preserved for the dedicated lazy-leg execution path.
    """
    def _normalize_count(value):
        try:
            return max(1, min(int(value or 1), 20))
        except (TypeError, ValueError):
            return 1

    def _normalize_mode(value):
        mode = str(value or 'RE_ASAP').upper().strip()
        if mode == 'RE_COST':
            return 'RE_ASAP'
        if mode == 'RE_COST_REV':
            return 'RE_ASAP_REV'
        return mode or 'RE_ASAP'

    cfg = {
        're_entry_on_target': False,
        're_entry_on_target_count': 1,
        're_entry_on_target_mode': 'RE_ASAP',
        're_entry_on_target_lazy_leg_config': None,
        're_entry_on_sl': False,
        're_entry_on_sl_count': 1,
        're_entry_on_sl_mode': 'RE_ASAP',
        're_entry_on_sl_lazy_leg_config': None,
        'on_target': None,
        'on_sl': None,
    }

    ret = leg_src.get('reEntryOnTarget') or leg_src.get('re_entry_on_target') or {}
    if isinstance(ret, dict) and ret:
        mode = _normalize_mode(ret.get('mode', 'RE_ASAP'))
        count = _normalize_count(ret.get('count', 1))
        lazy_cfg = ret.get('lazyLegConfig') or ret.get('lazy_leg_config')
        cfg['re_entry_on_target'] = True
        cfg['re_entry_on_target_count'] = count
        cfg['re_entry_on_target_mode'] = mode
        cfg['re_entry_on_target_lazy_leg_config'] = lazy_cfg
        cfg['on_target'] = {'mode': mode, 'count': count, 'lazy_leg_config': lazy_cfg}
    elif bool(leg_src.get('re_entry_target_enabled', False)):
        mode = _normalize_mode(leg_src.get('re_entry_target_mode', 'RE_ASAP'))
        count = _normalize_count(leg_src.get('re_entry_target_count', 1))
        lazy_cfg = leg_src.get('re_entry_target_lazy_leg_config')
        cfg['re_entry_on_target'] = True
        cfg['re_entry_on_target_count'] = count
        cfg['re_entry_on_target_mode'] = mode
        cfg['re_entry_on_target_lazy_leg_config'] = lazy_cfg
        cfg['on_target'] = {'mode': mode, 'count': count, 'lazy_leg_config': lazy_cfg}

    resl = leg_src.get('reEntryOnSL') or leg_src.get('re_entry_on_sl') or {}
    if isinstance(resl, dict) and resl:
        mode = _normalize_mode(resl.get('mode', 'RE_ASAP'))
        count = _normalize_count(resl.get('count', 1))
        lazy_cfg = resl.get('lazyLegConfig') or resl.get('lazy_leg_config')
        cfg['re_entry_on_sl'] = True
        cfg['re_entry_on_sl_count'] = count
        cfg['re_entry_on_sl_mode'] = mode
        cfg['re_entry_on_sl_lazy_leg_config'] = lazy_cfg
        cfg['on_sl'] = {'mode': mode, 'count': count, 'lazy_leg_config': lazy_cfg}
    elif bool(leg_src.get('re_entry_sl_enabled', False)):
        mode = _normalize_mode(leg_src.get('re_entry_sl_mode', 'RE_ASAP'))
        count = _normalize_count(leg_src.get('re_entry_sl_count', 1))
        lazy_cfg = leg_src.get('re_entry_sl_lazy_leg_config')
        cfg['re_entry_on_sl'] = True
        cfg['re_entry_on_sl_count'] = count
        cfg['re_entry_on_sl_mode'] = mode
        cfg['re_entry_on_sl_lazy_leg_config'] = lazy_cfg
        cfg['on_sl'] = {'mode': mode, 'count': count, 'lazy_leg_config': lazy_cfg}

    return cfg


def _resolve_lazy_leg_expiry(lazy_leg_config: dict, entry_date, index: str, fallback_expiry) -> pd.Timestamp:
    """Resolve the option expiry for a lazy leg from its own expiry selection."""
    expiry_raw = str(lazy_leg_config.get('expiry', 'WEEKLY') or 'WEEKLY').upper().strip()
    if expiry_raw in ('WEEKLY_T1', 'NEXT_WEEK', 'NEXTWEEKLY'):
        expiry_raw = 'NEXT_WEEKLY'
    elif expiry_raw in ('MONTHLY_T1', 'NEXT_MONTH', 'NEXTMONTHLY'):
        expiry_raw = 'NEXT_MONTHLY'
    elif expiry_raw not in ('WEEKLY', 'NEXT_WEEKLY', 'MONTHLY', 'NEXT_MONTHLY'):
        expiry_raw = 'WEEKLY'

    try:
        return pd.Timestamp(get_expiry_for_selection(entry_date, index, expiry_raw))
    except Exception as exc:
        _log(f"      [LAZY LEG] Expiry resolve failed for {expiry_raw}: {exc}; using {fallback_expiry}")
        return pd.Timestamp(fallback_expiry)


def _lazy_base_reason(reason_str: str) -> str:
    """Normalize an exit reason to its base lazy-leg trigger category."""
    base = str(reason_str or '').split('[')[0].strip().upper()
    if base.startswith('COMPLETE_'):
        base = base.replace('COMPLETE_', '', 1)
    return base


def _execute_lazy_leg(
    lazy_leg_config: dict,
    entry_date,
    exit_date,
    expiry_date,
    entry_spot: float,
    index: str,
    trading_calendar,
    square_off_mode: str,
    slippage_pct: float,
    strike_interval: int,
    depth: int = 0,
) -> list:
    """
    Enter a separately configured lazy leg on the parent trigger date, monitor its
    own risk rules, and return trade-leg dicts compatible with normal legs.
    """
    max_chain_depth = 3
    if not isinstance(lazy_leg_config, dict) or not lazy_leg_config:
        _log("      [LAZY LEG] Missing lazyLegConfig - skipping")
        return []
    if depth > max_chain_depth:
        _log(f"      [LAZY LEG] Chain depth {depth} exceeded - stopping recursion")
        return []

    entry_ts = pd.Timestamp(entry_date)
    exit_ts = pd.Timestamp(exit_date)
    if entry_ts >= exit_ts:
        _log(f"      [LAZY LEG] Entry {entry_ts.date()} >= exit {exit_ts.date()} - skipping")
        return []

    reentry_cutoff = str(lazy_leg_config.get('no_reentry_after') or '').strip()
    if reentry_cutoff and entry_ts >= exit_ts:
        _log(f"      [LAZY LEG] No Re-Entry After gate blocked entry on {entry_ts.date()}")
        return []

    option_type = str(lazy_leg_config.get('option_type', 'CE') or 'CE').upper().strip()
    if option_type in ('CALL', 'C'):
        option_type = 'CE'
    elif option_type in ('PUT', 'P'):
        option_type = 'PE'
    position = str(lazy_leg_config.get('position', 'SELL') or 'SELL').upper().strip()
    lots = int(lazy_leg_config.get('lots', lazy_leg_config.get('lot', 1)) or 1)
    lot_size = get_lot_size(index, entry_ts)
    ll_expiry = _resolve_lazy_leg_expiry(lazy_leg_config, entry_ts, index, expiry_date)

    ll_strike_config = {
        **lazy_leg_config,
        'option_type': option_type,
        'strike_selection': lazy_leg_config.get('strike_selection', {}),
        'strike_selection_type': (
            lazy_leg_config.get('strike_selection_type')
            or (lazy_leg_config.get('strike_selection') or {}).get('type', 'STRIKE_TYPE')
        ),
    }
    strike, _, _ = _resolve_strike(
        leg_config=ll_strike_config,
        entry_date=entry_ts,
        entry_spot=entry_spot,
        expiry_date=ll_expiry,
        strike_interval=strike_interval,
        index=index,
    )
    if strike is None:
        _log("      [LAZY LEG] Strike resolution failed - skipping")
        return []

    raw_entry = get_option_premium_from_db(
        date=entry_ts.strftime('%Y-%m-%d'),
        index=index,
        strike=strike,
        option_type=option_type,
        expiry=ll_expiry.strftime('%Y-%m-%d'),
    )
    if raw_entry is None:
        _log(f"      [LAZY LEG] Missing entry premium for {option_type} {strike} on {entry_ts.date()}")
        return []
    entry_premium = _apply_slippage(raw_entry, position, 'entry', slippage_pct)

    raw_exit = get_option_premium_from_db(
        date=exit_ts.strftime('%Y-%m-%d'),
        index=index,
        strike=strike,
        option_type=option_type,
        expiry=ll_expiry.strftime('%Y-%m-%d'),
    )
    if raw_exit is None:
        fallback_spot = get_spot_price_from_db(exit_ts, index) or entry_spot
        raw_exit = calculate_intrinsic_value(spot=fallback_spot, strike=strike, option_type=option_type)
    exit_premium = _apply_slippage(raw_exit, position, 'exit', slippage_pct)
    pnl = (exit_premium - entry_premium) if position == 'BUY' else (entry_premium - exit_premium)

    lazy_leg = {
        'leg_number': 9000 + depth,
        'segment': 'OPTION',
        'option_type': option_type,
        'strike': strike,
        'position': position,
        'lots': lots,
        'lot_size': lot_size,
        'entry_date': entry_ts,
        'exit_date': exit_ts,
        'entry_spot': entry_spot,
        'exit_spot': get_spot_price_from_db(exit_ts, index) or entry_spot,
        'entry_premium': entry_premium,
        'exit_premium': exit_premium,
        'raw_entry_premium': raw_entry,
        'raw_exit_premium': raw_exit,
        'market_entry_premium': raw_entry,
        'market_exit_premium': raw_exit,
        'pnl': pnl,
        'ce_pnl': pnl if option_type in ('CE', 'CALL', 'C') else 0,
        'pe_pnl': pnl if option_type in ('PE', 'PUT', 'P') else 0,
        '_resolved_expiry': ll_expiry,
        '_is_lazy_leg': True,
        '_lazy_leg_name': lazy_leg_config.get('lazy_leg_name') or lazy_leg_config.get('name') or f'lazy{depth + 1}',
        '_lazy_entry_date': entry_ts,
        '_lazy_exit_date': exit_ts,
        '_lazy_depth': depth,
    }
    _copy_sl_tgt_to_leg(lazy_leg, lazy_leg_config)
    _copy_trail_sl_to_leg(lazy_leg, lazy_leg_config)
    lazy_leg['_reentry'] = _parse_leg_reentry_config(lazy_leg_config)

    lazy_check = check_leg_stop_loss_target(
        entry_date=entry_ts,
        exit_date=exit_ts,
        expiry_date=ll_expiry,
        entry_spot=entry_spot,
        legs_config=[lazy_leg],
        index=index,
        trading_calendar=trading_calendar,
        square_off_mode='partial',
        slippage_pct=slippage_pct,
    )

    actual_exit_date = exit_ts
    exit_reason = 'EXPIRY'
    if lazy_check and lazy_check[0].get('triggered'):
        actual_exit_date = pd.Timestamp(lazy_check[0].get('exit_date') or exit_ts)
        exit_reason = lazy_check[0].get('exit_reason', 'EXPIRY')
        new_raw_exit = get_option_premium_from_db(
            date=actual_exit_date.strftime('%Y-%m-%d'),
            index=index,
            strike=strike,
            option_type=option_type,
            expiry=ll_expiry.strftime('%Y-%m-%d'),
        )
        if new_raw_exit is None:
            fallback_spot = get_spot_price_from_db(actual_exit_date, index) or entry_spot
            new_raw_exit = calculate_intrinsic_value(spot=fallback_spot, strike=strike, option_type=option_type)
        new_exit = _apply_slippage(new_raw_exit, position, 'exit', slippage_pct)
        lazy_leg['exit_date'] = actual_exit_date
        lazy_leg['_lazy_exit_date'] = actual_exit_date
        lazy_leg['exit_spot'] = get_spot_price_from_db(actual_exit_date, index) or entry_spot
        lazy_leg['exit_premium'] = new_exit
        lazy_leg['raw_exit_premium'] = new_raw_exit
        lazy_leg['market_exit_premium'] = new_raw_exit
        lazy_leg['pnl'] = (new_exit - entry_premium) if position == 'BUY' else (entry_premium - new_exit)
        lazy_leg['ce_pnl'] = lazy_leg['pnl'] if option_type in ('CE', 'CALL', 'C') else 0
        lazy_leg['pe_pnl'] = lazy_leg['pnl'] if option_type in ('PE', 'PUT', 'P') else 0

    lazy_leg['exit_reason'] = exit_reason
    result_legs = [lazy_leg]

    if lazy_check and lazy_check[0].get('triggered') and actual_exit_date < exit_ts:
        trigger_base = _lazy_base_reason(exit_reason)
        reentry_cfg = None
        if trigger_base in {'STOP_LOSS', 'TRAIL_SL'}:
            reentry_cfg = (lazy_leg.get('_reentry') or {}).get('on_sl')
        elif trigger_base == 'TARGET':
            reentry_cfg = (lazy_leg.get('_reentry') or {}).get('on_target')

        if reentry_cfg:
            mode = str(reentry_cfg.get('mode', '') or '').upper().strip()
            child_cfg = reentry_cfg.get('lazy_leg_config')
            if mode == 'LAZY_LEG' and child_cfg:
                result_legs.extend(_execute_lazy_leg(
                    lazy_leg_config=child_cfg,
                    entry_date=actual_exit_date,
                    exit_date=exit_ts,
                    expiry_date=expiry_date,
                    entry_spot=get_spot_price_from_db(actual_exit_date, index) or entry_spot,
                    index=index,
                    trading_calendar=trading_calendar,
                    square_off_mode=square_off_mode,
                    slippage_pct=slippage_pct,
                    strike_interval=strike_interval,
                    depth=depth + 1,
                ))
            elif mode in ('RE_ASAP', 'RE_ASAP_REV', 'RE_MOMENTUM', 'RE_MOMENTUM_REV'):
                repeat_cfg = dict(lazy_leg_config)
                if _is_reentry_mode_reverse(mode):
                    repeat_cfg['position'] = 'BUY' if position == 'SELL' else 'SELL'
                result_legs.extend(_execute_lazy_leg(
                    lazy_leg_config=repeat_cfg,
                    entry_date=actual_exit_date,
                    exit_date=exit_ts,
                    expiry_date=expiry_date,
                    entry_spot=get_spot_price_from_db(actual_exit_date, index) or entry_spot,
                    index=index,
                    trading_calendar=trading_calendar,
                    square_off_mode=square_off_mode,
                    slippage_pct=slippage_pct,
                    strike_interval=strike_interval,
                    depth=depth + 1,
                ))

    return result_legs


def _reentry_mode_base(mode_str: str) -> str:
    mode = str(mode_str or 'RE_ASAP').upper().strip()
    if mode.endswith('_REV'):
        return mode[:-4]
    return mode


def _is_reentry_mode_reverse(mode_str: str) -> bool:
    return str(mode_str or '').upper().strip().endswith('_REV')


def _resolve_reentry_position(position: str, mode_str: str) -> str:
    pos = str(position or 'SELL').upper().strip()
    if not _is_reentry_mode_reverse(mode_str):
        return pos
    return 'BUY' if pos == 'SELL' else 'SELL'


def _execute_per_leg_reentry(
    leg_config: dict,
    original_exit_date,
    original_exit_reason: str,
    expiry_date,
    cycle_exit_date,
    index: str,
    trading_calendar,
    strike_interval: int,
    slippage_pct: float,
    buffer_strike_enabled: bool = False,
    buffer_strike_value: float = 0.0,
    buffer_strike_unit: str = 'percent',
    buffer_strike_apply_to: str = 'both',
    buffer_position_above: bool = True,
    buffer_position_below: bool = True,
    square_off_mode: str = 'partial',
) -> list:
    """
    Execute per-leg re-entry for a single option or futures leg after SL/Target fires.

    Only RE_ASAP / RE_ASAP_REV are supported here because the engine uses one EOD
    observation per date. That means re-entry occurs on the same calendar date as
    the trigger using that date's closing price, and subsequent trigger checks
    start from the next trading day.
    """
    segment = str(leg_config.get('segment', '') or '').upper()
    is_futures = segment in ('FUTURE', 'FUTURES')

    reentry_cfg = leg_config.get('_reentry') or {}
    if not reentry_cfg:
        _log(f"[REENTRY] Skip: no re-entry config present (leg={leg_config.get('leg_number', '?')})")
        return []

    def _base_reason(reason_str):
        return str(reason_str or '').split('[')[0].strip().upper()

    sl_reasons = {'STOP_LOSS', 'TRAIL_SL', 'COMPLETE_STOP_LOSS'}
    tgt_reasons = {'TARGET', 'COMPLETE_TARGET'}

    first_reason = _base_reason(original_exit_reason)
    if first_reason not in sl_reasons | tgt_reasons:
        _log(
            f"[REENTRY] Skip: trigger reason '{first_reason}' is not SL/Target "
            f"(leg={leg_config.get('leg_number', '?')}, exit_date={pd.Timestamp(original_exit_date).strftime('%Y-%m-%d')})"
        )
        return []

    re_on_sl = bool(reentry_cfg.get('re_entry_on_sl', False))
    re_on_tgt = bool(reentry_cfg.get('re_entry_on_target', False))
    max_sl = int(reentry_cfg.get('re_entry_on_sl_count', 1) or 1)
    max_tgt = int(reentry_cfg.get('re_entry_on_target_count', 1) or 1)

    if re_on_sl and leg_config.get('stop_loss') is None:
        _log(
            f"[REENTRY] WARNING: Re-entry on SL enabled for leg {leg_config.get('leg_number', '?')} "
            "but no stop_loss is configured"
        )
    if re_on_tgt and leg_config.get('target') is None:
        _log(
            f"[REENTRY] WARNING: Re-entry on Target enabled for leg {leg_config.get('leg_number', '?')} "
            "but no target is configured"
        )

    if first_reason in sl_reasons and not re_on_sl:
        _log(
            f"[REENTRY] Skip: SL re-entry disabled for leg={leg_config.get('leg_number', '?')} "
            f"(reason={first_reason})"
        )
        return []
    if first_reason in tgt_reasons and not re_on_tgt:
        _log(
            f"[REENTRY] Skip: Target re-entry disabled for leg={leg_config.get('leg_number', '?')} "
            f"(reason={first_reason})"
        )
        return []

    current_trigger_date = pd.Timestamp(original_exit_date)
    current_reason = original_exit_reason
    sl_used = 0
    tgt_used = 0
    reentry_results = []
    lot_size = get_lot_size(index, current_trigger_date)
    option_type = str(leg_config.get('option_type', 'CE') or 'CE').upper()
    current_position = str(leg_config.get('position', 'SELL') or 'SELL').upper()
    reentry_mode = 'RE_ASAP'

    while True:
        base_reason = _base_reason(current_reason)
        is_sl = base_reason in sl_reasons
        is_tgt = base_reason in tgt_reasons

        if is_sl:
            if not re_on_sl or sl_used >= max_sl:
                _log(
                    f"[REENTRY] Stop: SL budget exhausted or disabled "
                    f"(leg={leg_config.get('leg_number', '?')}, used={sl_used}, max={max_sl})"
                )
                break
            mode = str(reentry_cfg.get('re_entry_on_sl_mode', 'RE_ASAP') or 'RE_ASAP').upper().strip()
            if _reentry_mode_base(mode) != 'RE_ASAP':
                _log(f"[REENTRY] Skip: unsupported SL re-entry mode '{mode}' (leg={leg_config.get('leg_number', '?')})")
                break
        elif is_tgt:
            if not re_on_tgt or tgt_used >= max_tgt:
                _log(
                    f"[REENTRY] Stop: Target budget exhausted or disabled "
                    f"(leg={leg_config.get('leg_number', '?')}, used={tgt_used}, max={max_tgt})"
                )
                break
            mode = str(reentry_cfg.get('re_entry_on_target_mode', 'RE_ASAP') or 'RE_ASAP').upper().strip()
            if _reentry_mode_base(mode) != 'RE_ASAP':
                _log(f"[REENTRY] Skip: unsupported Target re-entry mode '{mode}' (leg={leg_config.get('leg_number', '?')})")
                break
        else:
            _log(f"[REENTRY] Stop: current trigger reason no longer SL/Target (reason={current_reason})")
            break

        reentry_mode = mode
        if _is_reentry_mode_reverse(reentry_mode):
            current_position = 'BUY' if current_position == 'SELL' else 'SELL'
        position = current_position

        re_entry_date = pd.Timestamp(current_trigger_date)
        if re_entry_date >= pd.Timestamp(cycle_exit_date):
            _log(
                f"[REENTRY] Stop: re-entry date {re_entry_date.strftime('%Y-%m-%d')} "
                f"is at/after cycle exit {pd.Timestamp(cycle_exit_date).strftime('%Y-%m-%d')}"
            )
            break

        re_spot = get_spot_price_from_db(re_entry_date, index)
        if re_spot is None:
            _log(f"[REENTRY] Stop: missing spot data for {re_entry_date.strftime('%Y-%m-%d')}")
            break

        re_leg_config = {
            **leg_config,
            '_buffer_strike_enabled': buffer_strike_enabled,
            '_buffer_strike_value': buffer_strike_value,
            '_buffer_strike_unit': buffer_strike_unit,
            '_buffer_strike_apply_to': buffer_strike_apply_to,
            '_buffer_position_above': buffer_position_above,
            '_buffer_position_below': buffer_position_below,
        }

        re_strike = _resolve_strike(
            leg_config=re_leg_config,
            entry_date=re_entry_date,
            entry_spot=re_spot,
            expiry_date=expiry_date,
            strike_interval=strike_interval,
            index=index,
        )
        if re_strike is None:
            _log(f"[REENTRY] Stop: strike resolution failed for {re_entry_date.strftime('%Y-%m-%d')}")
            break
        re_buffer_offset = 0
        re_buffer_ref_price = None
        if isinstance(re_strike, (tuple, list)):
            re_strike_value = re_strike[0] if len(re_strike) > 0 else None
            re_buffer_offset = re_strike[1] if len(re_strike) > 1 else 0
            re_buffer_ref_price = re_strike[2] if len(re_strike) > 2 else None
        else:
            re_strike_value = re_strike
        if re_strike_value is None:
            _log(f"[REENTRY] Stop: normalized strike is missing for {re_entry_date.strftime('%Y-%m-%d')}")
            break
        buffer_runtime = re_leg_config.get('_buffer_runtime', {})

        if is_futures:
            raw_entry_fut, re_futures_expiry = _get_future_price_for_held_contract(
                re_entry_date, index, leg_config
            )
            if raw_entry_fut is None:
                _log(
                    f"[REENTRY] Stop: missing futures entry price for "
                    f"{re_entry_date.strftime('%Y-%m-%d')} expiry={leg_config.get('futures_expiry')}"
                )
                break
            entry_price_fut = _apply_slippage(raw_entry_fut, position, 'entry', slippage_pct)

            re_leg_stub = {
                **leg_config,
                'segment': 'FUTURE',
                'option_type': 'FUT',
                'strike': '',
                'position': position,
                'lots': leg_config.get('lots', 1),
                'lot_size': lot_size,
                'entry_date': re_entry_date,
                'exit_date': pd.Timestamp(cycle_exit_date),
                'entry_spot': re_spot,
                'entry_price': entry_price_fut,
                'exit_price': entry_price_fut,
                'futures_expiry': re_futures_expiry,
            }
            _copy_sl_tgt_to_leg(re_leg_stub, leg_config)
            _copy_trail_sl_to_leg(re_leg_stub, leg_config)

            re_check = check_leg_stop_loss_target(
                entry_date=re_entry_date,
                exit_date=cycle_exit_date,
                expiry_date=expiry_date,
                entry_spot=re_spot,
                legs_config=[re_leg_stub],
                index=index,
                trading_calendar=trading_calendar,
                square_off_mode=square_off_mode,
                slippage_pct=slippage_pct,
            )

            actual_exit_date = pd.Timestamp(cycle_exit_date)
            actual_exit_reason = 'EXPIRY'
            next_trigger_date = None
            next_trigger_reason = None
            if re_check and re_check[0].get('triggered'):
                triggered = re_check[0]
                actual_exit_date = pd.Timestamp(triggered.get('exit_date') or cycle_exit_date)
                actual_exit_reason = triggered.get('exit_reason', 'EXPIRY')
                trig_base = _base_reason(actual_exit_reason)
                if trig_base in sl_reasons and re_on_sl and sl_used < max_sl:
                    next_trigger_date = actual_exit_date
                    next_trigger_reason = actual_exit_reason
                elif trig_base in tgt_reasons and re_on_tgt and tgt_used < max_tgt:
                    next_trigger_date = actual_exit_date
                    next_trigger_reason = actual_exit_reason
                else:
                    _log(
                        f"[REENTRY] Stop: chained trigger '{trig_base}' did not qualify "
                        f"for further re-entry (leg={leg_config.get('leg_number', '?')})"
                    )

            raw_exit_fut, _ = _get_future_price_for_held_contract(
                actual_exit_date,
                index,
                {**leg_config, 'futures_expiry': re_futures_expiry},
            )
            if raw_exit_fut is None:
                raw_exit_fut = raw_entry_fut
            exit_price_fut = _apply_slippage(raw_exit_fut, position, 'exit', slippage_pct)
            leg_pnl = (exit_price_fut - entry_price_fut) if position == 'BUY' else (entry_price_fut - exit_price_fut)

            re_leg = {
                'leg_number': leg_config.get('leg_number', 1),
                'segment': 'FUTURE',
                'option_type': 'FUT',
                'strike': '',
                'position': position,
                'lots': leg_config.get('lots', 1),
                'lot_size': lot_size,
                'entry_date': re_entry_date,
                'exit_date': actual_exit_date,
                'exit_reason': actual_exit_reason,
                'entry_spot': re_spot,
                'exit_spot': get_spot_price_from_db(actual_exit_date, index) or re_spot,
                'entry_price': entry_price_fut,
                'exit_price': exit_price_fut,
                'raw_entry_price': raw_entry_fut,
                'raw_exit_price': raw_exit_fut,
                'market_entry_price': raw_entry_fut,
                'market_exit_price': raw_exit_fut,
                'futures_expiry': re_futures_expiry,
                'buffer_strike_enabled': bool(buffer_strike_enabled),
                'buffer_position': buffer_runtime.get('position'),
                'buffer_ref_price': buffer_runtime.get('reference_price'),
                'buffer_spot_atm': buffer_runtime.get('spot_atm_strike'),
                'buffer_atm_strike': buffer_runtime.get('atm_strike'),
                'buffer_applied': bool(buffer_runtime.get('applied', False)),
                'buffer_strike_offset': re_buffer_offset if re_buffer_offset is not None else buffer_runtime.get('offset', 0),
                'buffer_ref_price_raw': re_buffer_ref_price,
                'pnl': leg_pnl,
                'ce_pnl': 0,
                'pe_pnl': 0,
                're_entry_index': len(reentry_results) + 1,
                're_entry_trigger': 'SL' if is_sl else 'TARGET',
                're_entry_mode': reentry_mode,
            }
            _copy_sl_tgt_to_leg(re_leg, leg_config)
            _copy_trail_sl_to_leg(re_leg, leg_config)
            re_leg['_reentry'] = reentry_cfg
        else:
            raw_entry = get_option_premium_from_db(
                date=re_entry_date.strftime('%Y-%m-%d'),
                index=index,
                strike=re_strike_value,
                option_type=option_type,
                expiry=pd.Timestamp(expiry_date).strftime('%Y-%m-%d'),
            )
            if raw_entry is None:
                _log(
                    f"[REENTRY] Stop: missing entry premium for strike {re_strike_value} "
                    f"on {re_entry_date.strftime('%Y-%m-%d')} (expiry={pd.Timestamp(expiry_date).strftime('%Y-%m-%d')})"
                )
                break
            entry_premium = _apply_slippage(raw_entry, position, 'entry', slippage_pct)

            re_leg_stub = {
                **leg_config,
                'segment': 'OPTION',
                'option_type': option_type,
                'strike': re_strike_value,
                'position': position,
                'lots': leg_config.get('lots', 1),
                'lot_size': lot_size,
                'entry_date': re_entry_date,
                'entry_spot': re_spot,
                'exit_date': pd.Timestamp(cycle_exit_date),
                'entry_premium': entry_premium,
                'exit_premium': entry_premium,
                'pnl': 0.0,
                '_resolved_expiry': leg_config.get('_resolved_expiry') or expiry_date,
            }
            _copy_sl_tgt_to_leg(re_leg_stub, leg_config)
            _copy_trail_sl_to_leg(re_leg_stub, leg_config)

            re_check = check_leg_stop_loss_target(
                entry_date=re_entry_date,
                exit_date=cycle_exit_date,
                expiry_date=expiry_date,
                entry_spot=re_spot,
                legs_config=[re_leg_stub],
                index=index,
                trading_calendar=trading_calendar,
                square_off_mode=square_off_mode,
                slippage_pct=slippage_pct,
            )

            actual_exit_date = pd.Timestamp(cycle_exit_date)
            actual_exit_reason = 'EXPIRY'
            next_trigger_date = None
            next_trigger_reason = None
            if re_check and re_check[0].get('triggered'):
                triggered = re_check[0]
                actual_exit_date = pd.Timestamp(triggered.get('exit_date') or cycle_exit_date)
                actual_exit_reason = triggered.get('exit_reason', 'EXPIRY')
                trig_base = _base_reason(actual_exit_reason)
                if trig_base in sl_reasons and re_on_sl and sl_used < max_sl:
                    next_trigger_date = actual_exit_date
                    next_trigger_reason = actual_exit_reason
                elif trig_base in tgt_reasons and re_on_tgt and tgt_used < max_tgt:
                    next_trigger_date = actual_exit_date
                    next_trigger_reason = actual_exit_reason
                else:
                    _log(
                        f"[REENTRY] Stop: chained trigger '{trig_base}' did not qualify "
                        f"for further re-entry (leg={leg_config.get('leg_number', '?')})"
                    )

            raw_exit = get_option_premium_from_db(
                date=actual_exit_date.strftime('%Y-%m-%d'),
                index=index,
                strike=re_strike_value,
                option_type=option_type,
                expiry=pd.Timestamp(expiry_date).strftime('%Y-%m-%d'),
            )
            if raw_exit is None:
                fallback_spot = get_spot_price_from_db(actual_exit_date, index) or re_spot
                raw_exit = calculate_intrinsic_value(spot=fallback_spot, strike=re_strike_value, option_type=option_type)
                _log(
                    f"[REENTRY] Fallback exit premium used for strike {re_strike_value} "
                    f"on {actual_exit_date.strftime('%Y-%m-%d')} (spot={fallback_spot})"
                )
            exit_premium = _apply_slippage(raw_exit, position, 'exit', slippage_pct)
            leg_pnl = (exit_premium - entry_premium) if position == 'BUY' else (entry_premium - exit_premium)

            re_leg = {
                'leg_number': leg_config.get('leg_number', 1),
                'segment': 'OPTION',
                'option_type': option_type,
                'strike': re_strike_value,
                'position': position,
                'lots': leg_config.get('lots', 1),
                'lot_size': lot_size,
                'entry_date': re_entry_date,
                'exit_date': actual_exit_date,
                'exit_reason': actual_exit_reason,
                'entry_spot': re_spot,
                'exit_spot': get_spot_price_from_db(actual_exit_date, index) or re_spot,
                'entry_premium': entry_premium,
                'exit_premium': exit_premium,
                'raw_entry_premium': raw_entry,
                'raw_exit_premium': raw_exit,
                'market_entry_premium': raw_entry,
                'market_exit_premium': raw_exit,
                'buffer_strike_enabled': bool(buffer_strike_enabled),
                'buffer_position': buffer_runtime.get('position'),
                'buffer_ref_price': buffer_runtime.get('reference_price'),
                'buffer_spot_atm': buffer_runtime.get('spot_atm_strike'),
                'buffer_atm_strike': buffer_runtime.get('atm_strike'),
                'buffer_applied': bool(buffer_runtime.get('applied', False)),
                'buffer_strike_offset': re_buffer_offset if re_buffer_offset is not None else buffer_runtime.get('offset', 0),
                'buffer_ref_price_raw': re_buffer_ref_price,
                'pnl': leg_pnl,
                'ce_pnl': leg_pnl if option_type in ('CE', 'CALL', 'C') else 0,
                'pe_pnl': leg_pnl if option_type in ('PE', 'PUT', 'P') else 0,
                're_entry_index': len(reentry_results) + 1,
                're_entry_trigger': 'SL' if is_sl else 'TARGET',
                're_entry_mode': reentry_mode,
                '_resolved_expiry': leg_config.get('_resolved_expiry'),
            }
            _copy_sl_tgt_to_leg(re_leg, leg_config)
            _copy_trail_sl_to_leg(re_leg, leg_config)
            re_leg['_reentry'] = reentry_cfg

        reentry_results.append(re_leg)
        if is_sl:
            sl_used += 1
        else:
            tgt_used += 1

        if next_trigger_date is not None and next_trigger_date < pd.Timestamp(cycle_exit_date):
            current_trigger_date = next_trigger_date
            current_reason = next_trigger_reason
            continue
        break

    return reentry_results


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
                               index, trading_calendar, square_off_mode='partial',
                               slippage_pct=0.0):
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
                current_price_raw, _ = _get_future_price_for_held_contract(check_date, index, leg)
                if current_price_raw is None:
                    continue

                entry_price = leg.get('entry_price')
                if entry_price is None:
                    continue

                current_price = _apply_slippage(current_price_raw, position, 'exit', slippage_pct)
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

                # Use per-leg resolved expiry if available (set during main trade processing)
                _sl_expiry = leg.get('_resolved_expiry') or expiry_date

                current_premium_raw = get_option_premium_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    strike=strike,
                    option_type=option_type,
                    expiry=pd.Timestamp(_sl_expiry).strftime('%Y-%m-%d')
                )
                if current_premium_raw is None:
                    continue

                current_premium = _apply_slippage(current_premium_raw, position, 'exit', slippage_pct)
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


def _resolve_leg_exit(per_leg_results, trade_exit_date, trade_exit_reason, leg_idx):
    """
    Resolve per-leg exit date & reason for output rows.

    per_leg_results is aligned to the in-memory trade['legs'] list order (0..n-1),
    not to leg_number (which can be non-contiguous if some configured legs were
    skipped due to missing data).
    """
    if per_leg_results is not None and 0 <= leg_idx < len(per_leg_results):
        r = per_leg_results[leg_idx] or {}
        return (r.get('exit_date') or trade_exit_date,
                r.get('exit_reason', 'EXPIRY'))
    return (trade_exit_date, trade_exit_reason or 'EXPIRY')


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
    slippage_pct=0.0,
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

                _trail_expiry = leg.get('_resolved_expiry') or expiry_date

                current_premium_raw = get_option_premium_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    strike=strike,
                    option_type=option_type,
                    expiry=pd.Timestamp(_trail_expiry).strftime('%Y-%m-%d')
                )

                if current_premium_raw is None:
                    continue

                current_premium = _apply_slippage(current_premium_raw, position, 'exit', slippage_pct)
                has_data = True

                if position == 'BUY':
                    leg_live_pnl = (current_premium - entry_premium) * lots * lot_size
                else:
                    leg_live_pnl = (entry_premium - current_premium) * lots * lot_size

            elif seg in ('FUTURE', 'FUTURES'):
                entry_price = leg.get('entry_price')
                if entry_price is None:
                    continue

                current_price_raw, _ = _get_future_price_for_held_contract(check_date, index, leg)

                if current_price_raw is None:
                    continue

                current_price = _apply_slippage(current_price_raw, position, 'exit', slippage_pct)
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
      Re-entry: per-leg re-entry is controlled via leg-level reEntryOnSL / reEntryOnTarget config.

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
    print(f"[DEBUG] Received entry_dte={entry_dte}, exit_dte={exit_dte}, expiry_type={params.get('expiry_type', 'WEEKLY')}, keys={list(params.keys())}")
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

    # ── Buffer Strike Selection ───────────────────────────────────────────────
    buffer_strike_enabled = bool(params.get('buffer_strike_enabled', False))

    buffer_strike_value = 0.5
    try:
        buffer_strike_value = float(params.get('buffer_strike_value', 0.5) or 0.5)
    except (TypeError, ValueError):
        buffer_strike_value = 0.5
    if buffer_strike_value <= 0:
        buffer_strike_enabled = False

    buffer_strike_unit = str(params.get('buffer_strike_unit', 'percent') or 'percent').lower().strip()
    if buffer_strike_unit not in ('percent', 'points'):
        buffer_strike_unit = 'percent'

    buffer_strike_apply_to = str(params.get('buffer_strike_apply_to', 'both') or 'both').lower().strip()
    if buffer_strike_apply_to not in ('call', 'put', 'both'):
        buffer_strike_apply_to = 'both'

    buffer_position_above = bool(params.get('buffer_position_above', True))
    buffer_position_below = bool(params.get('buffer_position_below', True))

    if not buffer_position_above and not buffer_position_below:
        buffer_strike_enabled = False

    _log(
        f"[BUFFER STRIKE] enabled={buffer_strike_enabled}, "
        f"value={buffer_strike_value}{buffer_strike_unit}, "
        f"apply_to={buffer_strike_apply_to}, "
        f"above={buffer_position_above}, below={buffer_position_below}"
    )

    slippage_pct = _normalize_slippage_pct(
        params.get('slippage_pct', params.get('slippage_percent', params.get('slippage', 0.0)))
    )

    # ========== STEP 2: LOAD DATA FROM CSV (like generic_multi_leg) ==========
    t_spot = time.perf_counter()
    spot_df = get_strike_data(index, from_date, to_date)
    
    # Create trading calendar from spot data
    trading_calendar = spot_df[['Date']].drop_duplicates().sort_values('Date').reset_index(drop=True)
    trading_calendar.columns = ['date']
    # Pre-build sorted numpy array for O(log n) searchsorted lookups
    trading_calendar_arr = trading_calendar['date'].values.astype('datetime64[ns]')
    
    _etype = expiry_type.upper()
    _is_next = _etype in ('NEXT_WEEKLY', 'WEEKLY_T1', 'NEXT_MONTHLY', 'MONTHLY_T1')

    if expiry_day_of_week is not None:
        expiry_dates = get_custom_expiry_dates(index, expiry_day_of_week, from_date, to_date)
        expiry_df = pd.DataFrame({'Current Expiry': expiry_dates})
    else:
        if _etype in ('WEEKLY', 'NEXT_WEEKLY', 'WEEKLY_T1'):
            expiry_df = get_expiry_dates(index, 'weekly', from_date, to_date)
        else:  # MONTHLY, NEXT_MONTHLY, MONTHLY_T1
            expiry_df = get_expiry_dates(index, 'monthly', from_date, to_date)

    # ========== STEP 4: INITIALIZE RESULTS ==========
    all_trades = []
    trade_id_counter = 0
    strike_interval = get_strike_interval(index)
    n_expiries = len(expiry_df)

    if 'Next Expiry' not in expiry_df.columns:
        expiry_df = expiry_df.copy().reset_index(drop=True)
        expiry_df['Next Expiry'] = expiry_df['Current Expiry'].shift(-1)

    opt_legs = [
        l for l in legs_config
        if str(l.get('segment', 'OPTION')).upper() not in ('FUTURES', 'FUTURE')
    ]
    _next_types = ('NEXT_WEEKLY', 'WEEKLY_T1', 'NEXT_MONTHLY', 'MONTHLY_T1')
    _all_opt_legs_next = bool(opt_legs) and all(
        str(l.get('expiry', 'WEEKLY') or 'WEEKLY').upper() in _next_types
        for l in opt_legs
    )
    _all_fut_legs_next_monthly = _futures_only_next_monthly_schedule(legs_config)
    _all_legs_next = _all_opt_legs_next or _all_fut_legs_next_monthly

    schedule = []
    for expiry_idx, expiry_row in expiry_df.iterrows():
        current_exp = pd.Timestamp(expiry_row['Current Expiry'])
        next_exp = pd.Timestamp(expiry_row['Next Expiry']) if 'Next Expiry' in expiry_row and pd.notna(expiry_row['Next Expiry']) else None

        # Determine DTE anchors for entry and exit dates.
        #
        # For NEXT_WEEKLY / NEXT_MONTHLY strategies ALL option legs trade the next
        # series contract. The correct market interpretation is:
        #   - Entry is relative to the CURRENT expiry (you roll into the next contract
        #     on/before the current expiry day — "0 entry DTE" = enter on roll day).
        #   - Exit  is relative to the NEXT expiry (you hold until N days before the
        #     contract you're trading actually expires).
        #
        # This handles all DTE combinations correctly:
        #   (0,0): entry=current_exp, exit=next_exp  [roll day → next expiry]
        #   (0,2): entry=current_exp, exit=next_exp-2d [roll day → 2 days before next]
        #   (2,0): entry=current_exp-2d, exit=next_exp [2 before roll → next expiry]
        #   (2,2): entry=current_exp-2d, exit=next_exp-2d
        #
        # For mixed / non-next strategies both dates use the same anchor (current_exp).
        schedule_anchor = current_exp
        if _all_legs_next and next_exp is not None:
            entry_date = calculate_trading_days_before_expiry(
                expiry_date=current_exp,
                days_before=entry_dte,
                trading_calendar_df=trading_calendar
            )
            exit_date = calculate_trading_days_before_expiry(
                expiry_date=next_exp,
                days_before=exit_dte,
                trading_calendar_df=trading_calendar
            )
        else:
            entry_date = calculate_trading_days_before_expiry(
                expiry_date=current_exp,
                days_before=entry_dte,
                trading_calendar_df=trading_calendar
            )
            exit_date = calculate_trading_days_before_expiry(
                expiry_date=current_exp,
                days_before=exit_dte,
                trading_calendar_df=trading_calendar
            )

        _log(f"[SCHED] Expiry={schedule_anchor.date()}, entry_dte={entry_dte}, exit_dte={exit_dte} → entry={entry_date}, exit={exit_date}")

        if entry_date is None or exit_date is None:
            _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {schedule_anchor} ---")
            _log("  WARNING: Missing entry or exit date; skipping expiry")
            continue

        # Extra safety: ensure both dates are Timestamps before comparison
        try:
            entry_date = pd.Timestamp(entry_date)
            exit_date = pd.Timestamp(exit_date)
        except Exception:
            continue

        if entry_date > exit_date:
            _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {schedule_anchor} ---")
            _log(f"  WARNING: Entry ({entry_date}) after exit ({exit_date}) - skipping")
            continue

        # For WEEKLY/MONTHLY: when entry_dte == exit_dte (> 0), entry and exit are on the same
        # or adjacent days. Since we use previous-day close data, this results in near-zero P&L.
        # Skip these trades entirely. For NEXT_WEEKLY/NEXT_MONTHLY, continue with normal logic
        # (entry on current expiry, exit on next expiry).
        _force_next_expiry = False
        _is_next_expiry_type = expiry_type.upper() in ('NEXT_WEEKLY', 'WEEKLY_T1', 'NEXT_MONTHLY', 'MONTHLY_T1') or _all_legs_next

        if entry_date == exit_date:
            if _is_next_expiry_type and next_exp is not None:
                exit_date = next_exp
                _force_next_expiry = True
                _log(f"  INFO: Entry == Exit on expiry day (NEXT expiry type) → exit shifted to next expiry {next_exp.date()}, forcing next-expiry contract")
            else:
                _log(f"--- Expiry {expiry_idx + 1}/{len(expiry_df)}: {schedule_anchor} ---")
                _log(f"  INFO: Entry == Exit ({entry_date.date()}) for {expiry_type} → skipping (entry_dte == exit_dte results in ~0 P&L with previous-day close data)")
                continue

        schedule.append({
            'expiry_idx':        expiry_idx,
            'expiry_date':       schedule_anchor,
            'current_expiry':    current_exp,
            'next_expiry':       next_exp,
            'entry_date':        entry_date,
            'exit_date':         exit_date,
            '_force_next_expiry': _force_next_expiry,
        })

    if schedule:
        seen_date_pairs = set()
        deduplicated_schedule = []
        for rec in schedule:
            key = (pd.Timestamp(rec['entry_date']), pd.Timestamp(rec['exit_date']))
            if key in seen_date_pairs:
                _log(f"[SCHED] Dedup: skipping duplicate entry/exit pair {key}")
                continue
            seen_date_pairs.add(key)
            deduplicated_schedule.append(rec)
        schedule = deduplicated_schedule

    if not schedule:
        return pd.DataFrame(), {}, {}

    _log(f"Schedule entries constructed: {len(schedule)}")
    if schedule:
        s0 = schedule[0]
        _log(f"[SCHED DEBUG] First schedule record: expiry_date={s0['expiry_date']} | current_expiry={s0['current_expiry']} | next_expiry={s0['next_expiry']} | entry={s0['entry_date']} | exit={s0['exit_date']}")

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
                    'segment':             segment,
                    'entry_date':          current_entry_ts,
                    'exit_date':           exit_ts,
                    'expiry_date':         rec['expiry_date'],
                    'current_expiry':      rec.get('current_expiry', rec['expiry_date']),
                    'next_expiry':         rec.get('next_expiry',    rec['expiry_date']),
                    '_force_next_expiry':  rec.get('_force_next_expiry', False),
                    'clamped_exit':        clamped_exit,
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
                    'segment':             segment,
                    'entry_date':          rec['entry_date'],
                    'exit_date':           exit_ts,
                    'expiry_date':         rec['expiry_date'],
                    'current_expiry':      rec.get('current_expiry', rec['expiry_date']),
                    'next_expiry':         rec.get('next_expiry',    rec['expiry_date']),
                    '_force_next_expiry':  rec.get('_force_next_expiry', False),
                    'clamped_exit':        clamped_exit,
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
    
    # ========== Cumulative % P&L Accumulators (additive, base 100) ==========
    cumulative = 100.0   # base 100, matches Excel seed
    peak       = 100.0
    
    for seg_scope in segment_records:
        segment = seg_scope['segment']
        for entry_idx, trade_entry in enumerate(seg_scope['entries'], 1):
            entry_date = trade_entry['entry_date']
            exit_date = trade_entry['exit_date']
            expiry_date = trade_entry['expiry_date']
            _sched_current_exp   = trade_entry.get('current_expiry') or expiry_date
            _sched_next_exp      = trade_entry.get('next_expiry')    or expiry_date
            _force_next_expiry   = trade_entry.get('_force_next_expiry', False)
            clamped_exit = trade_entry['clamped_exit']
            trade_id += 1
            _log(f"--- Segment {segment['label']} | Trade {trade_id}/{total_entries} ---")
            _log(f"  [EXPIRY DEBUG] expiry_type={expiry_type} | trade_entry keys={list(trade_entry.keys())}")
            _log(f"  [EXPIRY DEBUG] expiry_date={expiry_date} | current_expiry_raw={trade_entry.get('current_expiry')} | next_expiry_raw={trade_entry.get('next_expiry')}")
            _log(f"  [EXPIRY DEBUG] _sched_current_exp={_sched_current_exp} | _sched_next_exp={_sched_next_exp}")
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
                # Entry Spot is always the cash/spot index close price.
                # It is used as the reference price for % P&L and display.
                # The underlying_type ('futures'/'cash') only affects how trade
                # P&L is calculated (via FUT Entry/Exit Price), not this reference.
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
                        roll_cfg = _parse_futures_rollover_config(leg_config)
                        if futures_expiry_pref == 'next_monthly':
                            fut_exit_date = exit_date
                            exit_reason = 'Expiry'
                            _log(
                                f"      [FUT NEXT_MONTHLY] exit_anchor=next_exp ({_sched_next_exp}) | "
                                f"exit_date={exit_date} (DTE-computed, exit_dte={exit_dte}) | no rollover"
                            )
                        else:
                            futures_exit_anchor = _sched_current_exp
                            _log(
                                f"      [FUT EXPIRY DEBUG] pref={futures_expiry_pref} | "
                                f"exit_anchor={pd.Timestamp(futures_exit_anchor).strftime('%Y-%m-%d') if futures_exit_anchor is not None else 'None'}"
                            )

                            fut_exit_trigger = get_futures_exit_date(
                                futures_exit_anchor,
                                roll_cfg['exit_mode'],
                                roll_cfg['n_days'],
                                trading_calendar,
                            )
                            if fut_exit_trigger is None:
                                fut_exit_trigger = exit_date
                            fut_exit_date = pd.Timestamp(fut_exit_trigger)
                            if fut_exit_date > exit_date:
                                fut_exit_date = exit_date

                            exit_reason = 'Expiry'
                            if roll_cfg['exit_mode'] != 'ON_EXPIRY' and fut_exit_date < exit_date:
                                exit_reason = f"FUT_ROLL_{roll_cfg['exit_mode']}"

                        fut_result = resolve_futures_pnl_with_rollover(
                            entry_date=entry_date,
                            exit_date=fut_exit_date,
                            index=index,
                            position=position,
                            preference=futures_expiry_pref,
                        )

                        fut_segments = []
                        if isinstance(fut_result, list):
                            fut_segments = fut_result
                        elif fut_result is not None:
                            fut_segments = [fut_result]

                        normalized_segments = [
                            (seg[0], seg[1], seg[2])
                            for seg in fut_segments
                            if seg and len(seg) >= 3
                        ]

                        entry_price = None
                        exit_price = None
                        _fut_expiry_str = None

                        if normalized_segments:
                            entry_price = next(
                                (seg[0] for seg in normalized_segments if seg[0] is not None),
                                None
                            )
                            exit_price = next(
                                (seg[1] for seg in reversed(normalized_segments) if seg[1] is not None),
                                None
                            )
                            _fut_expiry_str = next(
                                (seg[2] for seg in reversed(normalized_segments) if seg[2]),
                                normalized_segments[-1][2]
                            )
                            if len(normalized_segments) > 1:
                                _log(f"      INFO: Aggregated {len(normalized_segments)} futures segments for rollover")

                        if entry_price is None and normalized_segments:
                            entry_price = normalized_segments[0][0]
                        if exit_price is None and normalized_segments:
                            exit_price = normalized_segments[-1][1]

                        if exit_price is None:
                            exit_price = entry_price

                        if entry_price is None:
                            _log(f"      WARNING: No futures price for entry {entry_date} - skipping leg")
                            continue

                        if exit_price is None:
                            _log(f"      WARNING: No exit futures price - using entry price")
                            exit_price = entry_price

                        roll_cost = 0.0
                        roll_entry_date = None
                        next_expiry_str = None
                        if futures_expiry_pref != 'next_monthly':
                            roll_entry_date = get_futures_rollover_entry_date(fut_exit_date, trading_calendar)

                        if roll_entry_date:
                            # ── Feature 2: Respect Filter on Roll ────────────────────────────────
                            # If with_filter=True and STR filter is enabled, check whether the
                            # filter has an active segment on the rollover entry date.
                            # No active segment → don't roll (skip roll_cost, tag exit reason).
                            roll_blocked_by_filter = False
                            if roll_cfg['with_filter'] and str_enabled:
                                roll_str_seg = get_active_str_segment(roll_entry_date, super_trend_config)
                                if roll_str_seg is None:
                                    roll_blocked_by_filter = True
                                    _log(
                                        f"      ROLL BLOCKED by filter: STR has no active segment on "
                                        f"{pd.Timestamp(roll_entry_date).strftime('%Y-%m-%d')}"
                                    )

                            # ── Feature 5: Spot Adj on Roll ──────────────────────────────────────
                            # If with_spot_adj=True and the global spot-adjustment is enabled,
                            # fetch the spot price on the rollover entry date and check whether it
                            # has breached the rise/fall threshold measured from the ORIGINAL entry
                            # spot of this trade.  A breach means market has moved too far — skip
                            # rolling into the next contract.
                            roll_blocked_by_spot = False
                            if (
                                not roll_blocked_by_filter
                                and roll_cfg['with_spot_adj']
                                and spot_adjustment_enabled
                                and entry_spot is not None
                            ):
                                roll_entry_date_str = pd.Timestamp(roll_entry_date).strftime('%Y-%m-%d')
                                roll_spot = get_spot_price_from_db(roll_entry_date_str, index)
                                if roll_spot is not None:
                                    if spot_adjustment_units == 'points':
                                        rise_target = entry_spot + spot_adjustment_pct
                                        fall_target  = entry_spot - spot_adjustment_pct
                                    else:
                                        rise_target = entry_spot * (1 + spot_adjustment_pct / 100)
                                        fall_target  = entry_spot * (1 - spot_adjustment_pct / 100)

                                    watch_rise = spot_adjustment_direction in ('rise', 'both')
                                    watch_fall = spot_adjustment_direction in ('fall', 'both')
                                    breached = (
                                        (watch_rise and roll_spot >= rise_target) or
                                        (watch_fall and roll_spot <= fall_target)
                                    )
                                    if breached:
                                        roll_blocked_by_spot = True
                                        _log(
                                            f"      ROLL BLOCKED by spot adj: spot={roll_spot:.2f} "
                                            f"vs entry_spot={entry_spot:.2f} "
                                            f"(rise_tgt={rise_target:.2f}, fall_tgt={fall_target:.2f}) "
                                            f"on {roll_entry_date_str}"
                                        )

                            # ── Compute roll cost only when rollover is not blocked ───────────────
                            if not roll_blocked_by_filter and not roll_blocked_by_spot and _fut_expiry_str:
                                try:
                                    curr_expiry_ts = pd.Timestamp(_fut_expiry_str)
                                except Exception:
                                    curr_expiry_ts = None
                                next_expiry_str = _resolve_nearest_future_expiry_after(
                                    index=index,
                                    date=fut_exit_date,
                                    min_expiry_after=curr_expiry_ts,
                                )
                                if next_expiry_str:
                                    roll_entry_price = get_future_price_from_db(
                                        roll_entry_date, index, expiry=next_expiry_str
                                    )
                                    if roll_entry_price is not None:
                                        roll_cost = round(roll_entry_price - exit_price, 2)

                            # Tag exit reason so the trade sheet reflects why rollover was skipped
                            if roll_blocked_by_filter:
                                exit_reason = exit_reason + '+NO_ROLL(FILTER)'
                            elif roll_blocked_by_spot:
                                exit_reason = exit_reason + '+NO_ROLL(SPOT_ADJ)'

                        _log(f"      Entry Price: {entry_price} (contract expiry: {_fut_expiry_str})")
                        _log(f"      Exit Price: {exit_price}")
                        if roll_cost:
                            _log(f"      Roll Cost (@ {roll_entry_date} → {next_expiry_str}): {roll_cost}")

                        market_entry_price = entry_price
                        market_exit_price = exit_price
                        raw_entry_price = market_entry_price
                        raw_exit_price = market_exit_price
                        entry_price = _apply_slippage(raw_entry_price, position, 'entry', slippage_pct)
                        exit_price = _apply_slippage(raw_exit_price, position, 'exit', slippage_pct)

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
                            'raw_entry_price': raw_entry_price,
                            'raw_exit_price': raw_exit_price,
                            'market_entry_price': market_entry_price,
                            'market_exit_price': market_exit_price,
                            'pnl': leg_pnl,
                            'ce_pnl': 0,
                            'pe_pnl': 0,
                            'futures_expiry': _fut_expiry_str,
                            'Roll Cost': roll_cost,
                            'Exit Reason': exit_reason,
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

                        # ── Resolve per-leg options expiry ─────────────────────────────────
                        # Each leg can independently trade a different expiry series
                        # (e.g., Leg 1 = WEEKLY, Leg 2 = NEXT_WEEKLY for calendar spreads).
                        # We use the schedule's stored current/next expiry directly to avoid
                        # mismatches from looking up by entry_date (which may be past current expiry
                        # when the global basis is NEXT_WEEKLY/NEXT_MONTHLY).
                        _leg_expiry_raw = str(leg_config.get('expiry', 'WEEKLY') or 'WEEKLY').upper()
                        _is_leg_next = _leg_expiry_raw in ('NEXT_WEEKLY', 'WEEKLY_T1', 'NEXT_MONTHLY', 'MONTHLY_T1')
                        if _force_next_expiry or _is_leg_next:
                            leg_options_expiry = pd.Timestamp(_sched_next_exp)
                        else:
                            leg_options_expiry = pd.Timestamp(_sched_current_exp)
                        _log(f"      [LEG EXPIRY DEBUG] leg.expiry={_leg_expiry_raw} | _is_leg_next={_is_leg_next}")
                        _log(f"      [LEG EXPIRY DEBUG] _sched_current_exp={_sched_current_exp} | _sched_next_exp={_sched_next_exp}")
                        _log(f"      [LEG EXPIRY DEBUG] → resolved leg_options_expiry={leg_options_expiry.strftime('%Y-%m-%d')}")

                        # Store resolved expiry in leg_config so SL checker can use it
                        leg_config = {**leg_config, '_resolved_expiry': leg_options_expiry}

                        # ========== CALCULATE STRIKE ==========
                        # Routes through _resolve_strike which handles ALL criteria:
                        # ATM/ITM/OTM, Premium Range, Closest Premium, Premium >=, Premium <=
                        # Uses entry_date bhavcopy (= previous-day close) matching AlgoTest.
                        leg_config_with_buffer = {
                            **leg_config,
                            '_buffer_strike_enabled': buffer_strike_enabled,
                            '_buffer_strike_value': buffer_strike_value,
                            '_buffer_strike_unit': buffer_strike_unit,
                            '_buffer_strike_apply_to': buffer_strike_apply_to,
                            '_buffer_position_above': buffer_position_above,
                            '_buffer_position_below': buffer_position_below,
                        }
                        strike, buffer_offset, buffer_ref_price = _resolve_strike(
                            leg_config=leg_config_with_buffer,
                            entry_date=entry_date,
                            entry_spot=entry_spot,
                            expiry_date=leg_options_expiry,
                            strike_interval=strike_interval,
                            index=index,
                        )

                        if strike is None:
                            _log(f"      WARNING: No qualifying strike found for leg {leg_idx+1} — skipping")
                            continue

                        buffer_runtime = leg_config_with_buffer.get('_buffer_runtime', {})
                        _log(f"      Calculated Strike: {strike}, buffer_offset={buffer_offset}, buffer_ref_price={buffer_ref_price}")

                        # Get entry premium
                        entry_premium = get_option_premium_from_db(
                            date=entry_date.strftime('%Y-%m-%d'),
                            index=index,
                            strike=strike,
                            option_type=option_type,
                            expiry=leg_options_expiry.strftime('%Y-%m-%d')
                        )

                        if entry_premium is None:
                            _log(f"      WARNING: No entry premium - skipping leg")
                            continue

                        market_entry_premium = entry_premium
                        raw_entry_premium = market_entry_premium
                        entry_premium = _apply_slippage(raw_entry_premium, position, 'entry', slippage_pct)
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
                            expiry=leg_options_expiry.strftime('%Y-%m-%d')
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

                        market_exit_premium = exit_premium
                        raw_exit_premium = market_exit_premium
                        exit_premium = _apply_slippage(raw_exit_premium, position, 'exit', slippage_pct)

                        # Calculate P&L in POINTS (no quantity multiplication)
                        # CE P&L = Entry - Exit for CALL SELL, Exit - Entry for CALL BUY
                        # PE P&L = Entry - Exit for PUT SELL, Exit - Entry for PUT BUY
                        if position == 'BUY':
                            leg_pnl = exit_premium - entry_premium
                        else:  # SELL
                            leg_pnl = entry_premium - exit_premium

                        # Store CE P&L or PE P&L based on option type (in points, no qty)
                        if option_type in ('CE', 'CALL', 'C'):
                            ce_pnl = leg_pnl
                            pe_pnl = 0
                        elif option_type in ('PE', 'PUT', 'P'):
                            ce_pnl = 0
                            pe_pnl = leg_pnl
                        else:
                            ce_pnl = 0
                            pe_pnl = 0

                        # Store lot_size for DataFrame (but don't use in P&L calculation)
                        lot_size = get_lot_size(index, entry_date)

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
                            'raw_entry_premium': raw_entry_premium,
                            'raw_exit_premium': raw_exit_premium,
                            'market_entry_premium': market_entry_premium,
                            'market_exit_premium': market_exit_premium,
                            'buffer_strike_enabled': buffer_strike_enabled,
                            'buffer_position': buffer_runtime.get('position'),
                            'buffer_ref_price': buffer_ref_price,
                            'buffer_spot_atm': buffer_runtime.get('spot_atm_strike'),
                            'buffer_atm_strike': buffer_runtime.get('atm_strike'),
                            'buffer_applied': buffer_offset != 0,
                            'buffer_strike_offset': buffer_offset,
                            'pnl': leg_pnl,
                            'ce_pnl': ce_pnl,
                            'pe_pnl': pe_pnl,
                            '_resolved_expiry': leg_options_expiry,
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
                    tleg['_reentry'] = _parse_leg_reentry_config(lsrc)

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
                    slippage_pct=slippage_pct,
                )
            
                # ========== STEP 8C-2: UPDATE EXIT PREMIUMS BASED ON PER-LEG EXIT DATES ==========
                # If per-leg stop loss triggered, recalculate exit premiums using actual exit dates
                if per_leg_results is not None:
                    for li, tleg in enumerate(trade_legs):
                        leg_result = per_leg_results[li]
                        actual_leg_exit_date = leg_result['exit_date']
                    
                        if actual_leg_exit_date != exit_date:
                            if tleg.get('segment') == 'OPTION':
                                # Recalculate option exit premium using leg's resolved expiry
                                _leg_expiry_8c = tleg.get('_resolved_expiry') or expiry_date
                                new_exit_premium = get_option_premium_from_db(
                                    date=actual_leg_exit_date.strftime('%Y-%m-%d'),
                                    index=index,
                                    strike=tleg['strike'],
                                    option_type=tleg['option_type'],
                                    expiry=pd.Timestamp(_leg_expiry_8c).strftime('%Y-%m-%d')
                                )
                            
                                if new_exit_premium is not None:
                                    tleg['market_exit_premium'] = new_exit_premium
                                    tleg['raw_exit_premium'] = new_exit_premium
                                    tleg['exit_premium'] = _apply_slippage(new_exit_premium, tleg['position'], 'exit', slippage_pct)
                                
                                    # Recalculate P&L in POINTS (no quantity multiplication)
                                    position = tleg['position']
                                    entry_premium = tleg['entry_premium']
                                
                                    if position == 'BUY':
                                        tleg['pnl'] = tleg['exit_premium'] - entry_premium
                                    else:  # SELL
                                        tleg['pnl'] = entry_premium - tleg['exit_premium']
                                    
                                    # Set CE P&L or PE P&L
                                    if tleg.get('option_type') in ('CE', 'CALL', 'C'):
                                        tleg['ce_pnl'] = tleg['pnl']
                                        tleg['pe_pnl'] = 0
                                    elif tleg.get('option_type') in ('PE', 'PUT', 'P'):
                                        tleg['ce_pnl'] = 0
                                        tleg['pe_pnl'] = tleg['pnl']
                                    else:
                                        tleg['ce_pnl'] = 0
                                        tleg['pe_pnl'] = 0
                        
                            elif tleg.get('segment') == 'FUTURE':
                                new_exit_price, early_exit_expiry = _get_future_price_for_held_contract(
                                    actual_leg_exit_date,
                                    index,
                                    tleg,
                                )
                            
                                if new_exit_price is not None:
                                    tleg['market_exit_price'] = new_exit_price
                                    tleg['raw_exit_price'] = new_exit_price
                                    tleg['exit_price'] = _apply_slippage(new_exit_price, tleg['position'], 'exit', slippage_pct)
                                    tleg['futures_expiry'] = early_exit_expiry
                                
                                    position = tleg['position']
                                    entry_price = tleg['entry_price']
                                
                                    if position == 'BUY':
                                        tleg['pnl'] = tleg['exit_price'] - entry_price
                                    else:  # SELL
                                        tleg['pnl'] = entry_price - tleg['exit_price']
                                    
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
                            slippage_pct=slippage_pct,
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
                                slippage_pct=slippage_pct,
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

                # ========== STEP 8G: FIRE LAZY LEGS ==========
                # Lazy legs are separately configured legs that start on the parent
                # leg's SL/Target trigger date and contribute to the same trade P&L.
                lazy_result_legs = []
                if per_leg_results is not None:
                    for li, tleg in enumerate(trade_legs):
                        if li >= len(per_leg_results):
                            continue
                        res = per_leg_results[li]
                        if not res.get('triggered'):
                            continue

                        trigger_base = _lazy_base_reason(res.get('exit_reason', ''))
                        reentry_cfg = tleg.get('_reentry') or {}
                        candidate_cfg = None
                        if trigger_base in {'STOP_LOSS', 'TRAIL_SL'}:
                            sl_cfg = reentry_cfg.get('on_sl') or {}
                            if sl_cfg.get('mode') == 'LAZY_LEG':
                                candidate_cfg = sl_cfg.get('lazy_leg_config')
                        elif trigger_base == 'TARGET':
                            tgt_cfg = reentry_cfg.get('on_target') or {}
                            if tgt_cfg.get('mode') == 'LAZY_LEG':
                                candidate_cfg = tgt_cfg.get('lazy_leg_config')

                        if not candidate_cfg:
                            if (
                                (trigger_base in {'STOP_LOSS', 'TRAIL_SL'} and reentry_cfg.get('re_entry_on_sl_mode') == 'LAZY_LEG') or
                                (trigger_base == 'TARGET' and reentry_cfg.get('re_entry_on_target_mode') == 'LAZY_LEG')
                            ):
                                _log(f"  [LAZY LEG] Leg {li + 1}: LAZY_LEG mode has no lazyLegConfig - skipping")
                            continue

                        lazy_entry_date = pd.Timestamp(res.get('exit_date') or exit_date)
                        if lazy_entry_date >= pd.Timestamp(exit_date):
                            _log(f"  [LAZY LEG] Trigger date {lazy_entry_date.date()} >= exit_date {pd.Timestamp(exit_date).date()} - no room")
                            continue

                        lazy_spot = get_spot_price_from_db(lazy_entry_date, index) or entry_spot
                        new_lazy_legs = _execute_lazy_leg(
                            lazy_leg_config=candidate_cfg,
                            entry_date=lazy_entry_date,
                            exit_date=exit_date,
                            expiry_date=expiry_date,
                            entry_spot=lazy_spot,
                            index=index,
                            trading_calendar=trading_calendar,
                            square_off_mode=square_off_mode,
                            slippage_pct=slippage_pct,
                            strike_interval=strike_interval,
                            depth=0,
                        )
                        for lazy_leg in new_lazy_legs:
                            lazy_leg['_lazy_parent_leg_number'] = tleg.get('leg_number', li + 1)
                            lazy_leg['_lazy_trigger'] = trigger_base
                        lazy_result_legs.extend(new_lazy_legs)
                        _log(f"  [LAZY LEG] Leg {li + 1} trigger={res.get('exit_reason')}: fired {len(new_lazy_legs)} lazy leg(s)")

                if lazy_result_legs:
                    trade_legs.extend(lazy_result_legs)

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

                # ========== P&L Calculations ==========
                # Net P&L in points (no quantity multiplication)
                net_pnl = total_ce_pnl + total_pe_pnl + total_fut_pnl

                # pct_pnl = round((net_pnl / entry_spot) * 100, 2) — matches AlgoTest Excel
                pct_pnl = round((net_pnl / entry_spot) * 100, 2) if entry_spot != 0 else 0.0
                net_pnl_pct = pct_pnl / 100.0

                # Cumulative: additive % P&L from base 100 (matches AlgoTest Excel formula exactly)
                cumulative = cumulative + pct_pnl
                peak       = max(cumulative, peak)
                dd         = cumulative - peak           # zero or negative
                pct_dd     = (dd / peak) if peak != 0 else 0.0   # decimal

                _log(f"  Cumulative: {cumulative:.2f}, Peak: {peak:.2f}, DD: {dd:.2f}, %DD: {pct_dd:.4f}")

                # ========== STEP 10: TRADE-LEVEL EXIT DATE ==========
                # Partial mode: legs exit on different days — trade closes when the
                # last leg closes. Use max() over all valid per-leg exit dates.
                if per_leg_results is not None:
                    valid_dates = [r['exit_date'] for r in per_leg_results if r.get('exit_date') is not None]
                    valid_dates.extend(
                        leg.get('_lazy_exit_date')
                        for leg in trade_legs
                        if leg.get('_is_lazy_leg') and leg.get('_lazy_exit_date') is not None
                    )
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
                    'cumulative': cumulative,
                    'peak':       peak,
                    'dd':         dd,
                    'pct_dd':     pct_dd,
                    'square_off_mode': square_off_mode,
                    'per_leg_results': per_leg_results,
                    'index':           index,
                }

                trade_id_counter += 1
                trade_record['trade_id'] = f"{trade_id_counter}"
                all_trades.append(trade_record)

                # ========== PER-LEG RE-ENTRY LOGIC ==========
                _reentry_applied = False
                _reentry_points = 0.0
                _reentry_ce_points = 0.0
                _reentry_pe_points = 0.0
                _reentry_fut_points = 0.0
                _reentry_count = 0

                if per_leg_results:
                    for li, (tleg, leg_result) in enumerate(zip(trade_legs, per_leg_results)):
                        if not leg_result.get('triggered'):
                            continue

                        leg_exit_base = str(leg_result.get('exit_reason', '') or '').split('[')[0].strip().upper()
                        if leg_exit_base in ('OVERALL_SL', 'OVERALL_TARGET'):
                            continue

                        reentry_cfg = tleg.get('_reentry') or {}
                        if not reentry_cfg.get('re_entry_on_sl') and not reentry_cfg.get('re_entry_on_target'):
                            continue
                        if (
                            (leg_exit_base in ('STOP_LOSS', 'TRAIL_SL') and reentry_cfg.get('re_entry_on_sl_mode') == 'LAZY_LEG') or
                            (leg_exit_base == 'TARGET' and reentry_cfg.get('re_entry_on_target_mode') == 'LAZY_LEG')
                        ):
                            continue

                        re_legs = _execute_per_leg_reentry(
                            leg_config=tleg,
                            original_exit_date=leg_result.get('exit_date', exit_date),
                            original_exit_reason=leg_result.get('exit_reason', 'EXPIRY'),
                            expiry_date=expiry_date,
                            cycle_exit_date=exit_date,
                            index=index,
                            trading_calendar=trading_calendar,
                            strike_interval=strike_interval,
                            slippage_pct=slippage_pct,
                            buffer_strike_enabled=buffer_strike_enabled,
                            buffer_strike_value=buffer_strike_value,
                            buffer_strike_unit=buffer_strike_unit,
                            buffer_strike_apply_to=buffer_strike_apply_to,
                            buffer_position_above=buffer_position_above,
                            buffer_position_below=buffer_position_below,
                        )

                        if not re_legs:
                            continue

                        tleg['re_entries'] = re_legs
                        tleg['re_entry_pnl'] = sum(float(r.get('pnl', 0) or 0) for r in re_legs)
                        _reentry_points += tleg['re_entry_pnl']
                        _reentry_count += len(re_legs)
                        _reentry_applied = True

                        for re_leg in re_legs:
                            _re_seg = str(re_leg.get('segment', 'OPTION') or 'OPTION').upper()
                            _re_opt = str(re_leg.get('option_type', '') or '').upper()
                            _re_pnl = float(re_leg.get('pnl', 0) or 0)
                            if _re_seg in ('FUTURE', 'FUTURES'):
                                _reentry_fut_points += _re_pnl
                            elif _re_opt in ('CE', 'CALL', 'C'):
                                _reentry_ce_points += _re_pnl
                            elif _re_opt in ('PE', 'PUT', 'P'):
                                _reentry_pe_points += _re_pnl

                if _reentry_applied:
                    trade_record['re_entry_pnl'] = _reentry_points
                    trade_record['re_entry_count'] = _reentry_count
                    trade_record['total_pnl'] = (trade_record.get('total_pnl', 0) or 0) + _reentry_points
                    trade_record['total_ce_pnl'] = (trade_record.get('total_ce_pnl', 0) or 0) + _reentry_ce_points
                    trade_record['total_pe_pnl'] = (trade_record.get('total_pe_pnl', 0) or 0) + _reentry_pe_points
                    trade_record['total_fut_pnl'] = (trade_record.get('total_fut_pnl', 0) or 0) + _reentry_fut_points
                    trade_record['net_pnl'] = (trade_record.get('net_pnl', 0) or 0) + _reentry_points
                    re_pct_pnl = round((_reentry_points / entry_spot) * 100, 2) if entry_spot != 0 else 0.0
                    cumulative = cumulative + re_pct_pnl
                    peak = max(cumulative, peak)
                    dd = cumulative - peak
                    pct_dd = (dd / peak) if peak != 0 else 0.0
                    trade_record['cumulative'] = cumulative
                    trade_record['peak'] = peak
                    trade_record['dd'] = dd
                    trade_record['pct_dd'] = pct_dd
                    trade_record['net_pnl_pct'] = round((trade_record['net_pnl'] / entry_spot) * 100, 2) / 100.0 if entry_spot != 0 else 0.0

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
        for _leg in t.get('legs', []):
            _leg_exit = _leg.get('_lazy_exit_date') or _leg.get('exit_date')
            if _leg_exit is not None:
                _all_exit_dates.add(str(_leg_exit))
            for _re in (_leg.get('re_entries') or []):
                _re_exit = _re.get('exit_date')
                if _re_exit is not None:
                    _all_exit_dates.add(str(_re_exit))
    _exit_spot_cache = {}
    for _ed in _all_exit_dates:
        if _ed:
            _sp = get_spot_price_from_db(_ed, index)
            if _sp is not None:
                _exit_spot_cache[_ed] = _sp

    # Flatten for DataFrame - Create rows for EACH leg (AlgoTest format)
    # But we'll aggregate them back for analytics
    trades_flat = []
    flatten_errors = []
    _log(f"[DEBUG] Starting flatten loop for {len(all_trades)} trades")
    for trade_idx, trade in enumerate(all_trades, 1):
        trade_id = trade.get('trade_id', trade_idx)
        entry_spot_val = trade['entry_spot']
        per_leg_res    = trade.get('per_leg_results')  # None if no SL/Target configured

        # Create SEPARATE row for EACH leg (like AlgoTest CSV format)
        for leg_idx, leg in enumerate(trade['legs']):
            try:
                leg_num = leg.get('_lazy_parent_leg_number') if leg.get('_is_lazy_leg', False) else leg['leg_number']
                if leg_num is None:
                    leg_num = leg.get('leg_number', 1)
                is_lazy_leg = bool(leg.get('_is_lazy_leg', False))
                lazy_leg_name = leg.get('_lazy_leg_name', '')
                lazy_entry_date_val = leg.get('_lazy_entry_date')
                lazy_exit_date_val = leg.get('_lazy_exit_date')
                # per_leg_results is aligned to trade['legs'] order (the list passed to
                # check_leg_stop_loss_target), NOT necessarily leg_number.
                # If any configured leg was skipped earlier due to missing data,
                # leg_number can be non-contiguous (e.g., first leg missing → only
                # leg_number=2 exists), so indexing per_leg_results via leg_number
                # misattributes the exit_date/reason (partial square-off mode).
                li = leg_idx  # 0-based index into per_leg_results

                # ── Resolve per-leg exit date & reason ────────────────────────────
                if is_lazy_leg:
                    leg_exit_date = lazy_exit_date_val or leg.get('exit_date') or trade['exit_date']
                    leg_exit_reason = leg.get('exit_reason', 'EXPIRY')
                else:
                    leg_exit_date, leg_exit_reason = _resolve_leg_exit(
                        per_leg_results=per_leg_res,
                        trade_exit_date=trade['exit_date'],
                        trade_exit_reason=trade.get('exit_reason', 'EXPIRY'),
                        leg_idx=li,
                    )

                # ── Exit spot price taken from the leg's own exit date ─────────────
                # Each leg may exit on a different day (partial mode), so we fetch
                # the spot price for that specific exit date.
                leg_exit_spot = _exit_spot_cache.get(str(leg_exit_date))
                if leg_exit_spot is None:
                    leg_exit_spot = leg.get('exit_spot', trade.get('exit_spot', entry_spot_val))

                # ── Check if trade has any options legs (for Spot columns visibility) ─
                has_options_leg = any(l.get('segment') != 'FUTURE' for l in trade['legs'])
                has_fut_leg = any(l.get('segment') == 'FUTURE' for l in trade['legs'])
                _log(f"[DEBUG] Trade {trade_idx}: has_fut_leg={has_fut_leg}, leg segment={leg.get('segment')}")

                row_entry_spot = entry_spot_val
                if is_lazy_leg:
                    row_entry_spot = (
                        leg.get('entry_spot')
                        or get_spot_price_from_db(lazy_entry_date_val, index)
                        or entry_spot_val
                    )

                # ── Entry / Exit price (premium for options, price for futures) ────
                if leg['segment'] == 'FUTURE':
                    leg_option_type = 'FUT'
                    position    = leg['position']
                    strike      = ''
                    entry_price = leg.get('entry_price', 0)
                    exit_price  = leg.get('exit_price', 0)
                    raw_entry_price = leg.get('raw_entry_price', leg.get('market_entry_price', entry_price))
                    raw_exit_price = leg.get('raw_exit_price', leg.get('market_exit_price', exit_price))
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
                    raw_entry_price = leg.get('raw_entry_premium', leg.get('market_entry_premium', entry_price))
                    raw_exit_price = leg.get('raw_exit_premium', leg.get('market_exit_premium', exit_price))
                    fut_entry_price = np.nan
                    fut_exit_price = np.nan
                    leg_pnl     = leg['pnl']
                    # CE P&L and PE P&L in points (no quantity)
                    ce_pnl_val  = leg.get('ce_pnl', 0)
                    pe_pnl_val  = leg.get('pe_pnl', 0)
                    fut_pnl_val = 0

                buffer_ref_price = leg.get('buffer_ref_price')
                buffer_atm_strike = leg.get('buffer_atm_strike')
                buffer_spot_atm = leg.get('buffer_spot_atm')
                buffer_applied = bool(leg.get('buffer_applied', False))
                buffer_strike_offset = leg.get('buffer_strike_offset', 0)
                buffer_position_value = leg.get('buffer_position')

                lots          = leg.get('lots', 1)
                lot_size      = leg.get('lot_size', 65)
                qty           = lots * lot_size
                
                # ── Per-leg P&L for display (each row shows individual leg values)
                # Respect trade totals on the first leg row for Net P&L/% P&L.
                trade_total_ce_pnl = trade.get('total_ce_pnl', 0)
                trade_total_pe_pnl = trade.get('total_pe_pnl', 0)
                trade_total_fut_pnl = trade.get('total_fut_pnl', 0)
                trade_net_pnl = trade.get('net_pnl', 0)  # trade-level total

                is_first_leg = leg_idx == 0
                if is_first_leg:
                    net_pnl_points = trade_net_pnl
                else:
                    net_pnl_points = leg_pnl if leg_pnl is not None else 0

                if pd.notna(row_entry_spot) and float(row_entry_spot) > 1000:
                    pct_pnl = round((net_pnl_points / float(row_entry_spot)) * 100, 2)
                else:
                    pct_pnl = 0.0
                    _log(f"  WARNING: Invalid row_entry_spot={row_entry_spot} for Trade {trade_idx} — %P&L set to 0")

                mae_val, mfe_val = _calculate_leg_mae_mfe(
                    index=index,
                    entry_date=lazy_entry_date_val if is_lazy_leg and lazy_entry_date_val is not None else trade['entry_date'],
                    exit_date=leg_exit_date,
                    leg=leg,
                    entry_price=entry_price,
                    position=position,
                    entry_spot=row_entry_spot,
                    trading_calendar_df=trading_calendar,
                    trade=trade,
                )

                segment_meta = trade.get('segment') or {}
                segment_type = segment_meta.get('type')
                segment_column_name = 'Filter Segment' if segment_type == 'FILTER' else 'STR Segment'
                
                # % P&L = (Net P&L / Entry Price) * 100 (entry price = premium for options, futures price for futures)
                # pct_pnl already computed above

                # Cumulative/Peak/DD/%DD only on Leg 1 rows, blank for all others
                if is_first_leg:
                    row_cumulative = round(trade.get('cumulative', 100.0), 2)
                    row_peak       = round(trade.get('peak',       100.0), 2)
                    row_dd         = round(trade.get('dd',           0.0), 2)
                    row_pct_dd     = round(trade.get('pct_dd',       0.0) * 100, 4)  # decimal → percentage
                else:
                    row_cumulative = None
                    row_peak       = None
                    row_dd         = None
                    row_pct_dd     = None
                
                # Always show Spot columns — useful for futures to track index movement vs entry
                show_spot_cols = True
                
                row = {
                    'Trade':          trade_id,
                    'Leg':            lazy_leg_name if is_lazy_leg and lazy_leg_name else leg_num,
                    'Index':          (
                        f"{trade_id}.{leg.get('_lazy_depth', 0) + 1}"
                        if is_lazy_leg
                        else trade_id
                    ),
                    'Entry Date':     format_date_dd_mm_yyyy(lazy_entry_date_val if is_lazy_leg and lazy_entry_date_val is not None else trade['entry_date']),
                    'Exit Date':      format_date_dd_mm_yyyy(leg_exit_date),
                    'Leg Exit Date':  format_date_dd_mm_yyyy(leg_exit_date),
                    'Type':           leg_option_type,
                    'Strike':         buffer_spot_atm if buffer_applied and buffer_spot_atm else strike,
                    'B/S':            position,
                    'Qty':            qty,
                    'Entry Price':    entry_price,
                    'Exit Price':     exit_price,
                    'Raw Entry Price': raw_entry_price,
                    'Raw Exit Price': raw_exit_price,
                    'MAE':            mae_val if mae_val is not None else np.nan,
                    'MFE':            mfe_val if mfe_val is not None else np.nan,
                    'buffer_strike_enabled': bool(buffer_strike_enabled),
                    'buffer_position': buffer_position_value if buffer_applied else None,
                    'buffer_ref_price': round(float(buffer_ref_price), 2) if buffer_applied and buffer_ref_price is not None else None,
                    'buffer_strike_offset': buffer_strike_offset,
                    'Entry Spot':     row_entry_spot if row_entry_spot is not None else np.nan,
                    'Exit Spot':      leg_exit_spot if leg_exit_spot is not None else np.nan,
                    'Spot P&L':       (round(leg_exit_spot - row_entry_spot, 2)
                                      if show_spot_cols and leg_exit_spot is not None and row_entry_spot is not None
                                      else np.nan),
                    'Expiry':         (
                        leg.get('futures_expiry')
                        if leg.get('segment') == 'FUTURE'
                        else (
                            leg['_resolved_expiry'].strftime('%Y-%m-%d')
                            if leg.get('_resolved_expiry') is not None and hasattr(leg['_resolved_expiry'], 'strftime')
                            else (
                                trade['expiry_date'].strftime('%Y-%m-%d')
                                if hasattr(trade['expiry_date'], 'strftime')
                                else str(trade['expiry_date'])[:10]
                            )
                        )
                    ),
                    'CE P&L':         ce_pnl_val,
                    'PE P&L':         pe_pnl_val,
                    'FUT P&L':        fut_pnl_val,
                    'FUT Entry Price': fut_entry_price if leg.get('segment') == 'FUTURE' else '',
                    'FUT Exit Price':  fut_exit_price if leg.get('segment') == 'FUTURE' else '',
                    'Net P&L':        net_pnl_points,
                    '% P&L':          pct_pnl,
                    'Cumulative':     row_cumulative,
                    'Peak':           row_peak,
                    'DD':             row_dd,
                    '%DD':            row_pct_dd,
                    'Exit Reason':    leg_exit_reason,
                    'ReEntryIndex':   leg.get('_lazy_depth', 0) + 1 if is_lazy_leg else '',
                    'ReEntryTrigger': f"LAZY-{str(leg.get('_lazy_trigger') or leg_exit_reason or '').replace('COMPLETE_', '')}" if is_lazy_leg else '',
                    'ReEntryMode':    'LAZY_LEG' if is_lazy_leg else '',
                    'Is Lazy Leg':    is_lazy_leg,
                    'Lazy Leg Name':  lazy_leg_name if is_lazy_leg else '',
                    'Lazy Entry Date': format_date_dd_mm_yyyy(lazy_entry_date_val) if is_lazy_leg and lazy_entry_date_val is not None else '',
                    'Lazy Exit Date': format_date_dd_mm_yyyy(lazy_exit_date_val) if is_lazy_leg and lazy_exit_date_val is not None else '',
                }
                row[segment_column_name] = trade.get('str_segment', '')
                if leg.get('segment') == 'FUTURE':
                    _log(f"[DEBUG] Adding FUT columns: segment={leg.get('segment')}, entry={fut_entry_price}, exit={fut_exit_price}")

                trades_flat.append(row)

                for re_idx, re_leg in enumerate(leg.get('re_entries', []) or []):
                    re_lots = re_leg.get('lots', 1) or 1
                    re_lot_size = re_leg.get('lot_size', 1) or 1
                    re_qty = re_lots * re_lot_size
                    re_segment = str(re_leg.get('segment', 'OPTION') or 'OPTION').upper()
                    re_is_futures = re_segment in ('FUTURE', 'FUTURES')
                    if re_is_futures:
                        re_entry_price = re_leg.get('entry_price', 0) or 0
                        re_exit_price = re_leg.get('exit_price', 0) or 0
                        re_raw_entry = re_leg.get('raw_entry_price', re_entry_price)
                        re_raw_exit = re_leg.get('raw_exit_price', re_exit_price)
                    else:
                        re_entry_price = re_leg.get('entry_premium', 0) or 0
                        re_exit_price = re_leg.get('exit_premium', 0) or 0
                        re_raw_entry = re_leg.get('raw_entry_premium', re_entry_price)
                        re_raw_exit = re_leg.get('raw_exit_premium', re_exit_price)
                    re_position = str(re_leg.get('position', '') or '').upper()
                    re_is_ce = str(re_leg.get('option_type', '') or '').upper() in ('CE', 'CALL', 'C')
                    re_is_pe = str(re_leg.get('option_type', '') or '').upper() in ('PE', 'PUT', 'P')
                    re_pnl_points = re_leg.get('pnl', 0) or (
                        (re_exit_price - re_entry_price) if re_position == 'BUY' else (re_entry_price - re_exit_price)
                    )
                    re_entry_spot = re_leg.get('entry_spot', entry_spot_val)
                    re_exit_spot = (
                        _exit_spot_cache.get(str(re_leg.get('exit_date', '')))
                        or re_leg.get('exit_spot')
                        or trade.get('exit_spot', entry_spot_val)
                    )
                    re_pct_pnl = round((re_pnl_points / float(entry_spot_val)) * 100, 2) if entry_spot_val and float(entry_spot_val) > 1000 else 0.0
                    re_index = f"{trade_id}.{leg_num + re_idx + 1}"
                    re_exit_date = re_leg.get('exit_date') or trade.get('exit_date')
                    re_mae_val, re_mfe_val = _calculate_leg_mae_mfe(
                        index=index,
                        entry_date=re_leg['entry_date'],
                        exit_date=re_exit_date,
                        leg=re_leg,
                        entry_price=re_entry_price,
                        position=re_position,
                        entry_spot=re_entry_spot,
                        trading_calendar_df=trading_calendar,
                        trade=trade,
                    )

                    re_row = {
                        'Trade':          trade_id,
                        'Leg':            leg_num,
                        'Index':          re_index,
                        'Entry Date':     format_date_dd_mm_yyyy(re_leg['entry_date']),
                        'Exit Date':      format_date_dd_mm_yyyy(re_exit_date),
                        'Leg Exit Date':  format_date_dd_mm_yyyy(re_exit_date),
                        'Type':           'FUT' if re_is_futures else re_leg.get('option_type', leg_option_type),
                        'Strike':         re_leg.get('buffer_spot_atm') if re_leg.get('buffer_applied') and re_leg.get('buffer_spot_atm') else re_leg.get('strike', ''),
                        'B/S':            re_position,
                        'Qty':            re_qty,
                        'Entry Price':    re_entry_price,
                        'Exit Price':     re_exit_price,
                        'Raw Entry Price': re_raw_entry,
                        'Raw Exit Price': re_raw_exit,
                        'MAE':            re_mae_val if re_mae_val is not None else np.nan,
                        'MFE':            re_mfe_val if re_mfe_val is not None else np.nan,
                        'buffer_strike_enabled': bool(re_leg.get('buffer_strike_enabled', False)),
                        'buffer_position': re_leg.get('buffer_position'),
                        'buffer_ref_price': round(float(re_leg.get('buffer_ref_price')), 2) if re_leg.get('buffer_ref_price') is not None else None,
                        'buffer_strike_offset': re_leg.get('buffer_strike_offset'),
                        'Entry Spot':     re_entry_spot if re_entry_spot is not None else np.nan,
                        'Exit Spot':      re_exit_spot if re_exit_spot is not None else np.nan,
                        'Spot P&L':       (round(float(re_exit_spot) - float(re_entry_spot), 2)
                                          if re_entry_spot is not None and re_exit_spot is not None
                                          else np.nan),
                        'Expiry':         (
                            re_leg.get('futures_expiry')
                            if re_is_futures
                            else (
                                re_leg['_resolved_expiry'].strftime('%Y-%m-%d')
                                if re_leg.get('_resolved_expiry') is not None and hasattr(re_leg['_resolved_expiry'], 'strftime')
                                else (
                                    trade['expiry_date'].strftime('%Y-%m-%d')
                                    if hasattr(trade['expiry_date'], 'strftime')
                                    else str(trade['expiry_date'])[:10]
                                )
                            )
                        ),
                        'CE P&L':         re_pnl_points if re_is_ce else 0,
                        'PE P&L':         re_pnl_points if re_is_pe else 0,
                        'FUT P&L':        re_pnl_points if re_is_futures else 0,
                        'FUT Entry Price': re_entry_price if re_is_futures else '',
                        'FUT Exit Price':  re_exit_price if re_is_futures else '',
                        'Net P&L':        re_pnl_points,
                        '% P&L':          re_pct_pnl,
                        'Cumulative':     None,
                        'Peak':           None,
                        'DD':             None,
                        '%DD':            None,
                        'Exit Reason':    re_leg.get('exit_reason', 'EXPIRY'),
                        'ReEntryIndex':   re_leg.get('re_entry_index', re_idx + 1),
                        'ReEntryTrigger': re_leg.get('re_entry_trigger', ''),
                        'ReEntryMode':    re_leg.get('re_entry_mode', 'RE_ASAP'),
                    }
                    re_row[segment_column_name] = trade.get('str_segment', '')
                    trades_flat.append(re_row)
            except Exception as e:
                flatten_errors.append(f"Trade {trade_idx}, Leg {leg_idx}: {str(e)}")
                print(f"[DEBUG] ERROR in flatten: Trade {trade_idx}, Leg {leg_idx}: {str(e)}")
                continue

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
        'Cumulative': 'first',
        'Peak': 'first',
        'DD': 'first',
        '%DD': 'first',
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
    # Preserve engine-computed cumulative series before compute_analytics can overwrite them.
    # compute_analytics has a has_series_b gate but it can fail (e.g. first cumulative > 110)
    # and fall back to a compound formula that differs from the engine's additive formula.
    _preserve_cols = ['Cumulative', 'Peak', 'DD', '%DD']
    _saved = {col: trades_aggregated[col].copy() for col in _preserve_cols if col in trades_aggregated.columns}

    trades_aggregated, summary = compute_analytics(trades_aggregated)

    # Restore correct cumulative series (engine uses additive formula, matches AlgoTest reference)
    for col, saved_series in _saved.items():
        trades_aggregated[col] = saved_series
    
    # Cumulative/Peak/DD/%DD are already embedded per-row in trades_flat — no merge needed.
    
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
