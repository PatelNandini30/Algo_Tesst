"""
Generic AlgoTest-Style Engine
Matches AlgoTest behavior exactly with DTE-based entry/exit
"""

# Set DEBUG = True to enable verbose logging for debugging
DEBUG = True

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
    build_pivot,
    calculate_strike_from_premium_range,
    calculate_strike_from_closest_premium,
    get_all_strikes_with_premiums,
)



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
        tleg['pnl'] = ((new_exit - ep) if position == 'BUY' else (ep - new_exit)) * lots * lot_size

    else:  # FUTURE
        new_exit = get_future_price_from_db(
            date=leg_exit_date.strftime('%Y-%m-%d'),
            index=index,
            expiry=expiry_date.strftime('%Y-%m-%d'),
        ) or tleg['entry_price']
        ep = tleg['entry_price']
        tleg['exit_price']      = new_exit
        tleg['early_exit_date'] = leg_exit_date
        tleg['pnl'] = ((new_exit - ep) if position == 'BUY' else (ep - new_exit)) * lots * lot_size


def _copy_sl_tgt_to_leg(leg_dict, leg_src):
    """Copy stopLoss / targetProfit config from leg_src (raw legs_config entry) into leg_dict."""
    if 'stopLoss' in leg_src and isinstance(leg_src['stopLoss'], dict):
        leg_dict['stop_loss']      = leg_src['stopLoss'].get('value')
        leg_dict['stop_loss_type'] = _normalize_sl_tgt_type(leg_src['stopLoss'].get('mode'))
        print(f"  ✓ Leg {leg_dict.get('leg_number', '?')}: Stop Loss configured - "
              f"value={leg_dict['stop_loss']}, type={leg_dict['stop_loss_type']} "
              f"(from stopLoss dict)")
    elif leg_src.get('stop_loss') is not None:
        leg_dict['stop_loss']      = leg_src['stop_loss']
        leg_dict['stop_loss_type'] = _normalize_sl_tgt_type(leg_src.get('stop_loss_type'))
        print(f"  ✓ Leg {leg_dict.get('leg_number', '?')}: Stop Loss configured - "
              f"value={leg_dict['stop_loss']}, type={leg_dict['stop_loss_type']} "
              f"(from flat keys)")
    else:
        leg_dict['stop_loss']      = None
        leg_dict['stop_loss_type'] = 'pct'
        print(f"  ✗ Leg {leg_dict.get('leg_number', '?')}: No Stop Loss configured")

    if 'targetProfit' in leg_src and isinstance(leg_src['targetProfit'], dict):
        leg_dict['target']      = leg_src['targetProfit'].get('value')
        leg_dict['target_type'] = _normalize_sl_tgt_type(leg_src['targetProfit'].get('mode'))
        print(f"  ✓ Leg {leg_dict.get('leg_number', '?')}: Target configured - "
              f"value={leg_dict['target']}, type={leg_dict['target_type']}")
    elif leg_src.get('target') is not None:
        leg_dict['target']      = leg_src['target']
        leg_dict['target_type'] = _normalize_sl_tgt_type(leg_src.get('target_type'))
        print(f"  ✓ Leg {leg_dict.get('leg_number', '?')}: Target configured - "
              f"value={leg_dict['target']}, type={leg_dict['target_type']}")
    else:
        leg_dict['target']      = None
        leg_dict['target_type'] = 'pct'
        print(f"  ✗ Leg {leg_dict.get('leg_number', '?')}: No Target configured")


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
             'exit_reason': 'SCHEDULED'}
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
        (lg.get('stop_loss') is not None or lg.get('target') is not None)
        for lg in legs_config
    )
    if not has_any_sl_target:
        return None
    
    print(f"\n>>> check_leg_stop_loss_target: square_off_mode = '{square_off_mode}'")

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
            
            # Log what we're checking (only when SL/Target is configured)
            if sl_val is not None or tgt_val is not None:
                print(f"    Checking Leg {li+1} on {check_date.strftime('%Y-%m-%d')}: "
                      f"SL={sl_val} ({sl_type}), Target={tgt_val} ({tgt_type})")

            position = leg['position']
            lot_size = leg.get('lot_size', get_lot_size(index, entry_date))
            lots     = leg.get('lots', 1)

            segment = leg.get('segment', 'OPTION')
            option_type = leg.get('option_type', 'CE')  # safe default for underlying_* checks

            if segment in ('FUTURES', 'FUTURE'):
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
            hit_sl = False
            if sl_val is not None:
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

            if hit_sl or hit_tgt:
                reason = 'STOP_LOSS' if hit_sl else 'TARGET'
                # ALWAYS log when SL/Target triggers (even when DEBUG=False)
                print(f"      {'🛑' if hit_sl else '✅'} Leg {li+1} {reason} on "
                      f"{check_date.strftime('%Y-%m-%d')} "
                      f"| Mode: {sl_type if hit_sl else tgt_type} "
                      f"| Threshold: {sl_val if hit_sl else tgt_val} "
                      f"| adverse_pct={adverse_pct:.2f}% "
                      f"| adverse_pts={adverse_premium_pts:.2f} "
                      f"| adverse_spot_pts={adverse_spot_pts:.2f} "
                      f"| adverse_spot_pct={adverse_spot_pct:.2f}%")
                newly_triggered_this_day.append((li, check_date, reason))

        # ── Apply triggers based on square_off_mode ──
        if newly_triggered_this_day:
            print(f"    >>> Applying square_off_mode='{square_off_mode}' for {len(newly_triggered_this_day)} triggered leg(s)")
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

    holding_days = trading_calendar[
        (trading_calendar['date'] > entry_date) &
        (trading_calendar['date'] <= exit_date)
    ]['date'].tolist()

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

                current_price = get_future_price_from_db(
                    date=check_date.strftime('%Y-%m-%d'),
                    index=index,
                    expiry=expiry_date.strftime('%Y-%m-%d')
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
    print(f"\n>>> ENGINE: square_off_mode = '{square_off_mode}' (type: {type(square_off_mode)})")


    # Re-entry settings (for both Weekly and Monthly strategies)
    # If trade exits before expiry due to SL/Target:
    # - re_entry_enabled: whether to allow re-entry after SL/Target trigger
    # - re_entry_max: maximum number of re-entries allowed per expiry
    re_entry_enabled = params.get('re_entry_enabled', False)  # Default: DISABLED - set True to enable
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
            
            # ========== STEP 8B: ATTACH PER-LEG SL/TARGET CONFIG ==========
            for li, tleg in enumerate(trade_legs):
                lsrc = legs_config[li] if li < len(legs_config) else {}
                _copy_sl_tgt_to_leg(tleg, lsrc)

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
                    
                    # Only recalculate if exit date changed from scheduled
                    if actual_leg_exit_date != exit_date:
                        print(f"      🔄 Leg {li+1}: Recalculating exit premium for early exit on {actual_leg_exit_date.strftime('%Y-%m-%d')}")
                        
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
                                
                                # Recalculate P&L
                                position = tleg['position']
                                entry_premium = tleg['entry_premium']
                                lots = tleg['lots']
                                lot_size = tleg['lot_size']
                                
                                if position == 'BUY':
                                    tleg['pnl'] = (new_exit_premium - entry_premium) * lots * lot_size
                                else:  # SELL
                                    tleg['pnl'] = (entry_premium - new_exit_premium) * lots * lot_size
                                
                                print(f"         Old exit premium: {old_exit_premium}, New: {new_exit_premium}, New P&L: ₹{tleg['pnl']:,.2f}")
                        
                        elif tleg.get('segment') == 'FUTURE':
                            # Recalculate future exit price
                            new_exit_price = get_future_price_from_db(
                                date=actual_leg_exit_date.strftime('%Y-%m-%d'),
                                index=index,
                                expiry=expiry_date.strftime('%Y-%m-%d')
                            )
                            
                            if new_exit_price is not None:
                                old_exit_price = tleg['exit_price']
                                tleg['exit_price'] = new_exit_price
                                
                                # Recalculate P&L
                                position = tleg['position']
                                entry_price = tleg['entry_price']
                                lots = tleg['lots']
                                lot_size = tleg['lot_size']
                                
                                if position == 'BUY':
                                    tleg['pnl'] = (new_exit_price - entry_price) * lots * lot_size
                                else:  # SELL
                                    tleg['pnl'] = (entry_price - new_exit_price) * lots * lot_size
                                
                                print(f"         Old exit price: {old_exit_price}, New: {new_exit_price}, New P&L: ₹{tleg['pnl']:,.2f}")


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
                    first_t = next((r for r in per_leg_results if r['triggered']), None)
                    sl_reason = first_t['exit_reason'] if first_t else None

            # ========== STEP 9: TOTAL P&L ==========
            total_pnl = sum(leg['pnl'] for leg in trade_legs)
            _log(f"  Total P&L: ₹{total_pnl:,.2f}")

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
            # Log detailed exit information
            print(f"\n{'='*70}")
            print(f"TRADE SUMMARY - Entry: {entry_date.strftime('%Y-%m-%d')}")
            print(f"{'='*70}")
            for li, tleg in enumerate(trade_legs):
                leg_exit = per_leg_results[li] if per_leg_results else None
                if leg_exit and leg_exit.get('triggered'):
                    print(f"  Leg {li+1}: EXIT on {leg_exit['exit_date'].strftime('%Y-%m-%d')} "
                          f"- Reason: {leg_exit['exit_reason']} - P&L: ₹{tleg['pnl']:,.2f}")
                else:
                    print(f"  Leg {li+1}: EXIT on {exit_date.strftime('%Y-%m-%d')} "
                          f"- Reason: SCHEDULED - P&L: ₹{tleg['pnl']:,.2f}")
            print(f"  Trade Exit Date: {actual_exit_date.strftime('%Y-%m-%d')}")
            print(f"  Total P&L: ₹{total_pnl:,.2f}")
            print(f"{'='*70}\n")
            
            trade_record = {
                'entry_date':      entry_date,
                'exit_date':       actual_exit_date,
                'expiry_date':     expiry_date,
                'entry_dte':       entry_dte,
                'exit_dte':        exit_dte,
                'entry_spot':      entry_spot,
                'exit_spot':       exit_spot,
                'exit_reason':     sl_reason or 'SCHEDULED',
                'legs':            trade_legs,
                'total_pnl':       total_pnl,
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
                    'STOP_LOSS', 'TARGET',
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
                                rep = get_future_price_from_db(
                                    date=re_entry_date.strftime('%Y-%m-%d'),
                                    index=index,
                                    expiry=expiry_date.strftime('%Y-%m-%d'),
                                )
                                if rep is None:
                                    re_ok = False; break
                                rxp = get_future_price_from_db(
                                    date=exit_date.strftime('%Y-%m-%d'),
                                    index=index,
                                    expiry=expiry_date.strftime('%Y-%m-%d'),
                                ) or rep
                                rpnl = ((rxp - rep) if rpos == 'BUY' else (rep - rxp)) * rlts * re_lot_size
                                re_leg = {
                                    'leg_number': rli + 1, 'segment': 'FUTURE',
                                    'position': rpos, 'lots': rlts, 'lot_size': re_lot_size,
                                    'entry_price': rep, 'exit_price': rxp, 'pnl': rpnl,
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
                                rpnl2 = ((rxp2 - rep2) if rpos == 'BUY' else (rep2 - rxp2)) * rlts * re_lot_size
                                re_leg = {
                                    'leg_number': rli + 1, 'segment': 'OPTION',
                                    'option_type': ropt, 'strike': rstk,
                                    'position': rpos, 'lots': rlts, 'lot_size': re_lot_size,
                                    'entry_premium': rep2, 'exit_premium': rxp2, 'pnl': rpnl2,
                                }

                            _copy_sl_tgt_to_leg(re_leg, rlc)
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
                        if re_per_leg is not None:
                            valid_re_dates = [r['exit_date'] for r in re_per_leg if r.get('exit_date') is not None]
                            re_actual_exit = max(valid_re_dates) if valid_re_dates else exit_date
                        else:
                            re_actual_exit = exit_date
                        re_exit_spot   = get_spot_price_from_db(re_actual_exit, index) or re_entry_spot
                        re_suffix      = f'[RE{re_entry_count + 1}]'
                        re_exit_reason = (re_sl_reason or 'SCHEDULED') + re_suffix

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
        per_leg_res    = trade.get('per_leg_results')  # None if no SL/Target configured

        # Create SEPARATE row for EACH leg (like AlgoTest CSV format)
        for leg in trade['legs']:
            leg_num = leg['leg_number']
            li      = leg_num - 1  # 0-based index

            # ── Resolve per-leg exit date & reason ────────────────────────────
            # In partial mode different legs can exit on different dates.
            # In complete / overall-SL mode all legs share the same date.
            if per_leg_res is not None and li < len(per_leg_res):
                leg_exit_date   = per_leg_res[li].get('exit_date') or trade['exit_date']
                leg_exit_reason = per_leg_res[li].get('exit_reason', 'SCHEDULED')
            else:
                leg_exit_date   = trade['exit_date']
                leg_exit_reason = trade.get('exit_reason', 'SCHEDULED')

            # ── Exit spot price taken from the leg's own exit date ─────────────
            # Each leg may exit on a different day (partial mode), so we fetch
            # the spot price for that specific exit date.
            leg_exit_spot = get_spot_price_from_db(leg_exit_date, trade.get('index', 'NIFTY'))
            if leg_exit_spot is None:
                leg_exit_spot = trade.get('exit_spot', entry_spot_val)

            # ── Entry / Exit price (premium for options, price for futures) ────
            if leg['segment'] == 'FUTURE':
                option_type = 'FUT'
                position    = leg['position']
                strike      = ''
                entry_price = leg['entry_price']
                # Use early_exit_date's price if the leg was triggered early
                exit_price  = leg.get('exit_price', 0)
            else:
                option_type = leg['option_type']
                position    = leg['position']
                strike      = leg['strike']
                entry_price = leg['entry_premium']
                # exit_premium is always updated to the correct exit date's market price:
                #   • for triggered legs: set during the SL/TGT recalc block above
                #   • for scheduled legs: set during initial options processing
                exit_price  = leg.get('exit_premium', 0)

            leg_pnl  = leg['pnl']
            lots     = leg.get('lots', 1)
            lot_size = leg.get('lot_size', 65)
            qty      = lots * lot_size

            # % P&L — direction-aware: positive = profitable for this leg's position
            entry_value = entry_price * qty
            pct_pnl = round(leg_pnl / entry_value * 100, 2) if entry_value else 0

            row = {
                'Trade':        trade_idx,
                'Leg':          leg_num,
                'Index':        trade_counter + leg_num,
                'Entry Date':   trade['entry_date'],
                'Exit Date':    leg_exit_date,          # per-leg (differs in partial mode)
                'Type':         option_type,
                'Strike':       strike,
                'B/S':          position,
                'Qty':          qty,
                'Entry Price':  entry_price,
                'Exit Price':   exit_price,             # correct price for this leg's exit date
                'Entry Spot':   entry_spot_val,
                'Exit Spot':    leg_exit_spot,          # spot on this leg's exit date
                'Spot P&L':     round(leg_exit_spot - entry_spot_val, 2) if leg_exit_spot and entry_spot_val else 0,
                'Future Expiry': trade['expiry_date'],
                'Net P&L':      leg_pnl,
                '% P&L':        pct_pnl,
                'Exit Reason':  leg_exit_reason,        # per-leg reason
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