"""
Phase 2b Python orchestrator for the Rust engine path.

Responsibilities
----------------
1. Read the strategy payload + market data (trading calendar, spot series).
2. Call Rust `resolve_trade_specs` to enumerate every (trade, leg) tuple.
3. Call Rust `simulate_trades_batch` to price the entries.
4. Call Rust `check_leg_stop_loss_target` per trade to detect SL/Target/Trail
   triggers — this is the SAME Rust function the Python engine uses today, so
   the trigger logic is guaranteed identical.
5. Re-price triggered legs at their adjusted exit dates.
6. Return a list of priced trade rows compatible with the snapshot format.

Design rule (immutable)
-----------------------
**No calculation or formula is reimplemented in this module.** Strike
resolution, slippage, P&L, and SL/Target/Trail detection are all native
Rust calls. Anything this module does is purely structural: building the
right dict shapes, threading payloads through, gluing pieces together.

If the Rust path rejects the payload (returns empty specs), the caller
must fall back to the Python engine. This module does not implement any
Python-side strategy logic.
"""
from __future__ import annotations

import bisect
import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Match the leg-config keys the existing Rust `check_leg_stop_loss_target`
# function reads. See backend/native/src/lib.rs:574+.
_SL_MODE_MAP = {
    "PERCENT": "pct", "PCT": "pct", "%": "pct",
    "POINTS": "points", "PT": "points", "PTS": "points",
    "UNDERLYING_POINTS": "underlying_pts", "UNDERLYING_PTS": "underlying_pts",
    "UNDERLYING_PCT": "underlying_pct", "UNDERLYING_PERCENT": "underlying_pct",
}


def _norm_mode(mode: Any, default: str = "pct") -> str:
    if mode is None:
        return default
    raw = str(mode).strip().upper().replace(" ", "_").replace("-", "_")
    return _SL_MODE_MAP.get(raw, raw.lower() or default)


def _maybe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_leg_config_for_sl(
    spec: Dict[str, Any],
    leg_src: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Translate one (priced spec, original-leg-payload) pair into the dict shape
    expected by Rust `check_leg_stop_loss_target`.

    The Rust function reads these keys (camelCase NOT supported):
      segment, option_type, strike, _resolved_expiry, entry_premium,
      position, lots, lot_size,
      stop_loss, stop_loss_type, target, target_type,
      trail_sl_enabled, trail_sl_trigger, trail_sl_move, trail_sl_mode
    """
    sl = (leg_src.get("stopLoss") or {}) if isinstance(leg_src.get("stopLoss"), dict) else {}
    tp = (leg_src.get("targetProfit") or {}) if isinstance(leg_src.get("targetProfit"), dict) else {}
    trail = (leg_src.get("trailSL") or {}) if isinstance(leg_src.get("trailSL"), dict) else {}

    cfg: Dict[str, Any] = {
        "segment": "OPTIONS",
        "option_type": spec["option_type"],
        "strike": spec["strike"],
        "_resolved_expiry": spec["expiry"],
        "entry_premium": spec.get("entry_price", 0.0),
        "position": spec["position"],
        "lots": spec.get("lots", 1),
        "lot_size": spec.get("lot_size", 1),
        # Reasonable defaults so the Rust function doesn't choke on missing keys
        "trail_sl_enabled": False,
    }

    sl_val = _maybe_float(sl.get("value"))
    if sl_val is not None and sl_val != 0:
        cfg["stop_loss"] = sl_val
        cfg["stop_loss_type"] = _norm_mode(sl.get("mode"))

    tp_val = _maybe_float(tp.get("value"))
    if tp_val is not None and tp_val != 0:
        cfg["target"] = tp_val
        cfg["target_type"] = _norm_mode(tp.get("mode"))

    trail_trig = _maybe_float(trail.get("trigger") or trail.get("x"))
    trail_move = _maybe_float(trail.get("move") or trail.get("y"))
    if trail_trig and trail_move and trail_trig > 0 and trail_move > 0:
        cfg["trail_sl_enabled"] = True
        cfg["trail_sl_mode"] = _norm_mode(trail.get("mode"))
        cfg["trail_sl_trigger"] = trail_trig
        cfg["trail_sl_move"] = trail_move

    return cfg


def _normalize_iso(d: Any) -> str:
    """Coerce date-ish input to YYYY-MM-DD string."""
    if d is None:
        return ""
    s = str(d).strip()
    if len(s) >= 10:
        return s[:10]
    return s


_OVERALL_TYPE_MAP = {
    # SL-side aliases
    "MAX_LOSS": "max_loss",
    "FIXED": "max_loss",
    "FIXED_RS": "max_loss",
    "RS": "max_loss",
    "INR": "max_loss",
    # Target-side aliases
    "MAX_PROFIT": "max_profit",
    # Percent-of-premium aliases
    "TOTAL_PREMIUM_PCT": "total_premium_pct",
    "PREMIUM_PCT": "total_premium_pct",
    "PCT": "total_premium_pct",
    "PERCENT": "total_premium_pct",
    "%": "total_premium_pct",
    # Points / underlying — match Python's _normalize_sl_tgt_type canonical forms
    "POINTS": "points",
    "PTS": "points",
    "POINT": "points",
    "PT": "points",
    "UNDERLYING_POINTS": "underlying_pts",
    "UNDERLYING_PTS": "underlying_pts",
    "UNDERLYING_PT": "underlying_pts",
    "INDEX_POINTS": "underlying_pts",
    "INDEX_PTS": "underlying_pts",
    "SPOT_POINTS": "underlying_pts",
    "SPOT_PTS": "underlying_pts",
    "UNDERLYING_PCT": "underlying_pct",
    "UNDERLYING_PERCENT": "underlying_pct",
    "UNDERLYING_%": "underlying_pct",
    "INDEX_PCT": "underlying_pct",
    "INDEX_PERCENT": "underlying_pct",
    "SPOT_PCT": "underlying_pct",
    "SPOT_PERCENT": "underlying_pct",
}


def _norm_overall_type(mode: Any) -> str:
    if mode is None:
        return "total_premium_pct"
    raw = str(mode).strip().upper().replace(" ", "_").replace("-", "_")
    return _OVERALL_TYPE_MAP.get(raw, raw.lower() or "total_premium_pct")


def _compute_overall_threshold(legs: List[Dict[str, Any]], otype: str, value: float, side: str) -> Optional[float]:
    """
    Mirror of engines/generic_algotest_engine.py:compute_overall_sl_threshold /
    compute_overall_target_threshold. Converts an overall_sl/target value into
    the ₹ (or raw points/pct) threshold the Rust function expects.

    `legs` are simulate.rs trade rows with entry_price/lots/lot_size.
    `side` is "sl" or "target" — only used for choosing the max_* alias.
    """
    fixed = "max_loss" if side == "sl" else "max_profit"
    if otype == fixed or otype in ("max_loss", "max_profit"):
        return float(value)
    if otype == "total_premium_pct":
        total = 0.0
        for leg in legs:
            ep = float(leg.get("entry_price") or 0.0)
            total += ep * float(leg.get("lots") or 1) * float(leg.get("lot_size") or 1)
        if total <= 0:
            return None
        return total * (float(value) / 100.0)
    if otype == "points":
        total_qty = 0.0
        for leg in legs:
            total_qty += float(leg.get("lots") or 1) * float(leg.get("lot_size") or 1)
        return float(value) * total_qty if total_qty else float(value)
    if otype in ("underlying_pts", "underlying_pct"):
        # Raw value — Rust handles spot-based check directly
        return float(value)
    return float(value)


def _build_leg_dict_for_overall(spec: Dict[str, Any], expiry_date: str) -> Dict[str, Any]:
    """Minimal leg dict shape the Rust check_overall_stop_loss_target reads."""
    return {
        "segment": "OPTIONS",
        "option_type": spec["option_type"],
        "strike": spec["strike"],
        "_resolved_expiry": spec.get("expiry") or expiry_date,
        "entry_premium": spec.get("entry_price", 0.0),
        "position": spec["position"],
        "lots": spec.get("lots", 1),
        "lot_size": spec.get("lot_size", 1),
    }


_SL_REASONS = {"STOP_LOSS", "TRAIL_SL", "COMPLETE_STOP_LOSS"}
_TGT_REASONS = {"TARGET", "COMPLETE_TARGET"}


def _resolve_atm_strike(spot: float, strike_interval: float) -> float:
    """ATM strike = nearest multiple of strike_interval. Matches Python ATM."""
    return round(spot / strike_interval) * strike_interval


def _compute_spot_adjustment_trigger(
    entry_date: str,
    entry_spot: float,
    scheduled_exit: str,
    direction: str,
    pct: float,
    units: str,
    trading_days: List[str],
    spot_by_date: Dict[str, float],
) -> Optional[str]:
    """
    Port of engines/generic_algotest_engine.py:apply_spot_adjustment_exit.

    Scans trading days (entry_date, scheduled_exit] for the first day whose
    spot has moved past the rise/fall target. Returns the iso trigger date or
    None if no trigger.
    """
    if pct <= 0 or entry_spot is None:
        return None
    if units == "points":
        rise_target = entry_spot + pct
        fall_target = entry_spot - pct
    else:
        rise_target = entry_spot * (1.0 + pct / 100.0)
        fall_target = entry_spot * (1.0 - pct / 100.0)
    watch_rise = direction in ("rise", "both")
    watch_fall = direction in ("fall", "both")
    if not watch_rise and not watch_fall:
        return None
    for d in trading_days:
        if d <= entry_date:
            continue
        if d > scheduled_exit:
            break
        spot = spot_by_date.get(d)
        if spot is None:
            continue
        if watch_rise and spot >= rise_target:
            return d
        if watch_fall and spot <= fall_target:
            return d
    return None


def _load_filter_segments(payload: Dict[str, Any]) -> Optional[List[Tuple[str, str]]]:
    """
    Return [(seg_start_iso, seg_end_iso), ...] for the active STR / filter, or
    None when no filter is active. Returns an EMPTY list when a filter is
    configured but yields no segments — caller must drop all trades.

    Mirrors engines/generic_algotest_engine.py:3154-3222 STR/filter selection.
    """
    raw_stc = payload.get("super_trend_config")
    str_cfg = str(raw_stc).strip() if raw_stc is not None else "None"
    str_enabled = str_cfg in ("5x1", "5x2")

    raw_fc = payload.get("filter_config")
    filter_cfg = str(raw_fc).strip() if raw_fc is not None else ""
    filter_segments_custom = payload.get("filter_segments") or []
    filter_enabled = (
        bool(filter_cfg)
        and filter_cfg.lower() != "none"
        and (filter_cfg != "custom" or len(filter_segments_custom) > 0)
    )

    if not str_enabled and not filter_enabled:
        return None

    import pandas as pd
    segs: List[Tuple[str, str]] = []
    try:
        if str_enabled:
            from base import get_super_trend_segments
            raw = get_super_trend_segments(str_cfg)
        elif filter_cfg.lower() == "custom":
            raw = filter_segments_custom
        else:
            from base import get_filter_segments
            raw = get_filter_segments(filter_cfg)
    except Exception as exc:
        logger.warning("[ENGINE_RUST] filter segment load failed: %s", exc)
        return None

    for s in raw or []:
        try:
            start = pd.Timestamp(s["start"]).strftime("%Y-%m-%d")
            end = pd.Timestamp(s["end"]).strftime("%Y-%m-%d")
            segs.append((start, end))
        except Exception:
            continue
    segs.sort()
    return segs


def _last_trading_day_on_or_before(target: str, trading_days: List[str]) -> Optional[str]:
    """Return the latest trading day <= target, or None."""
    if not trading_days or not target:
        return None
    idx = bisect.bisect_right(trading_days, target) - 1
    if idx < 0:
        return None
    return trading_days[idx]


def _next_trading_day_on_or_after(trading_days: List[str], date_str: str) -> Optional[str]:
    """Return the first trading day >= date_str, or None."""
    if not trading_days or not date_str:
        return None
    idx = bisect.bisect_left(trading_days, date_str)
    return trading_days[idx] if idx < len(trading_days) else None


def _trading_day_n_before(expiry_str: str, n: int, trading_days: List[str]) -> Optional[str]:
    """Return the trading day n steps before expiry (expiry - n td). n=0 → expiry itself."""
    if not trading_days or not expiry_str:
        return None
    idx_exp = bisect.bisect_right(trading_days, expiry_str) - 1
    if idx_exp < 0:
        return None
    idx_exit = idx_exp - n
    if idx_exit < 0:
        return None
    return trading_days[idx_exit]


def _trading_day_gap_strict(from_str: str, to_str: str, trading_days: List[str]) -> int:
    """Trading-day gap (side='right' on both ends) — mirrors Python engine searchsorted."""
    idx_from = bisect.bisect_right(trading_days, from_str)
    idx_to = bisect.bisect_right(trading_days, to_str)
    return max(0, idx_to - idx_from)


def _trading_day_gap_left(from_str: str, to_str: str, trading_days: List[str]) -> int:
    """Trading-day gap (side='left' on both ends) — used for no_rollover_min_days check."""
    idx_from = bisect.bisect_left(trading_days, from_str)
    idx_to = bisect.bisect_left(trading_days, to_str)
    return max(0, idx_to - idx_from)


# ── Strike intervals per index ────────────────────────────────────────────────
# Duplicate of _BUFFER_STRIKE_INTERVALS for use before that dict is defined.
# (Defined early so _compute_strike_for_leg_python can reference it.)
_STRIKE_INTERVALS: Dict[str, float] = {
    "NIFTY": 50.0, "BANKNIFTY": 100.0, "FINNIFTY": 50.0, "MIDCPNIFTY": 25.0,
}

_NEXT_EXPIRY_TYPES: frozenset = frozenset({"NEXT_WEEKLY", "WEEKLY_T1", "NEXT_MONTHLY", "MONTHLY_T1"})


def _futures_get_exit_date(anchor: str, exit_mode: str, n_days: int, sorted_td: List[str]) -> str:
    """
    Inline port of base.get_futures_exit_date.

    For ON_EXPIRY: returns anchor unchanged (no calendar needed).
    For N_DAYS_BEFORE_EXPIRY / LAST_WEEK_BEFORE_EXPIRY: walks backward in
    sorted_td.  Uses the same calendar-index approach as base.py but operates
    on a plain sorted list of ISO date strings, avoiding a DataFrame build.
    """
    mode = str(exit_mode or "ON_EXPIRY").upper()
    if mode == "ON_EXPIRY":
        return anchor
    idx = bisect.bisect_right(sorted_td, anchor) - 1
    if idx < 0:
        return anchor
    if mode == "N_DAYS_BEFORE_EXPIRY":
        days = max(1, min(int(n_days or 5), 15))
        return sorted_td[max(0, idx - days)]
    if mode == "LAST_WEEK_BEFORE_EXPIRY":
        return sorted_td[max(0, idx - 5)]
    return anchor


def _build_futures_specs(
    payload: Dict[str, Any],
    expiry_dates: List[str],
    trading_days: List[str],
    spot_by_date: Dict[str, float],
    lot_size: int,
    segments: Optional[List[Tuple[str, str]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Build PRICED trade rows for strategies whose legs are FUTURES contracts.

    Unlike options specs (which are priced by simulate_trades_batch), futures
    rows are priced here using base.resolve_futures_pnl_with_rollover.  The
    rows returned can be passed directly to priced_to_tradesheet_records.

    Returns:
      * None  → unsupported (FUTURES with SL/Target/re-entry); caller falls back.
      * []    → no trades produced.
      * [row, …] → priced futures rows.
    """
    from base import resolve_futures_pnl_with_rollover

    index = str(payload.get("index") or "NIFTY").upper()
    legs_src = payload.get("legs") or []
    entry_dte = int(payload.get("entry_dte") or 0)
    exit_dte = int(payload.get("exit_dte") or 0)
    slippage = float(payload.get("slippage_pct") or 0.0)

    sorted_td = sorted(trading_days)
    sorted_exp = sorted(expiry_dates)

    out: List[Dict[str, Any]] = []
    prev_sched_exit: Optional[str] = None  # overlap-prevention sentinel

    for trade_id, exp_str in enumerate(sorted_exp, start=1):
        entry_date = _trading_day_n_before(exp_str, entry_dte, sorted_td)
        exit_date = _trading_day_n_before(exp_str, exit_dte, sorted_td)
        if not entry_date or not exit_date:
            continue
        entry_spot = spot_by_date.get(entry_date)
        if entry_spot is None:
            continue
        exit_spot = spot_by_date.get(exit_date, 0.0)

        # Overlap prevention: skip if entry before previous scheduled exit.
        if prev_sched_exit is not None and entry_date < prev_sched_exit:
            continue

        # STR / filter segment gating (same gate as DTE-mode options).
        effective_exit = exit_date
        if segments is not None:
            seg_match = None
            for s_start, s_end in segments:
                if s_start <= entry_date <= s_end:
                    seg_match = (s_start, s_end)
                    break
            if seg_match is None:
                prev_sched_exit = exit_date
                continue
            _, seg_end = seg_match
            if effective_exit > seg_end:
                clamped = _last_trading_day_on_or_before(seg_end, sorted_td)
                if clamped is None or clamped <= entry_date:
                    prev_sched_exit = exit_date
                    continue
                effective_exit = clamped

        prev_sched_exit = exit_date  # DTE-scheduled exit gates the next entry

        for leg_id, leg in enumerate(legs_src, start=1):
            if str(leg.get("segment") or "OPTION").upper() not in ("FUTURE", "FUTURES"):
                continue

            # SL / Target / Trail on futures not yet supported → Python fallback.
            for risk_key in ("stopLoss", "targetProfit", "trailSL"):
                v = leg.get(risk_key) or {}
                if isinstance(v, dict) and _maybe_float(v.get("value")):
                    return None
            # Re-entry on futures not yet supported.
            if leg.get("reEntryOnSL") or leg.get("reEntryOnTarget"):
                return None

            position = str(leg.get("position") or "SELL").upper()
            lots = int(leg.get("lots") or 1)

            fut_pref_raw = str(leg.get("expiry") or "monthly").lower()
            fut_pref = "next_monthly" if fut_pref_raw in ("next_monthly", "next_month", "mid_month") else "monthly"

            # Determine the actual futures exit date (may be before DTE exit if
            # the roll config uses N_DAYS_BEFORE_EXPIRY / LAST_WEEK_BEFORE_EXPIRY).
            exit_mode_raw = str(leg.get("fut_exit_mode") or "ON_EXPIRY").upper()
            if exit_mode_raw not in ("ON_EXPIRY", "N_DAYS_BEFORE_EXPIRY", "LAST_WEEK_BEFORE_EXPIRY"):
                exit_mode_raw = "ON_EXPIRY"
            try:
                n_days = max(1, min(int(leg.get("fut_n_days") or 5), 15))
            except (TypeError, ValueError):
                n_days = 5

            fut_exit_trigger = _futures_get_exit_date(exp_str, exit_mode_raw, n_days, sorted_td)
            fut_exit_date = min(fut_exit_trigger, effective_exit)

            entry_price_raw, exit_price_raw, fut_expiry = resolve_futures_pnl_with_rollover(
                entry_date=entry_date,
                exit_date=fut_exit_date,
                index=index,
                position=position,
                preference=fut_pref,
            )
            if entry_price_raw is None:
                continue
            if exit_price_raw is None:
                exit_price_raw = entry_price_raw

            # Slippage — mirrors _apply_slippage in generic_algotest_engine.py
            if slippage > 0:
                _entry_fac = (1.0 - slippage / 100.0) if position == "SELL" else (1.0 + slippage / 100.0)
                _exit_fac = (1.0 + slippage / 100.0) if position == "SELL" else (1.0 - slippage / 100.0)
                entry_price = round(max(float(entry_price_raw) * _entry_fac, 0.0), 2)
                exit_price = round(max(float(exit_price_raw) * _exit_fac, 0.0), 2)
            else:
                entry_price = round(float(entry_price_raw), 2)
                exit_price = round(float(exit_price_raw), 2)

            # P&L per unit — no lot_size multiplication (matches Python engine convention).
            net_pnl = round(
                (entry_price - exit_price) if position == "SELL" else (exit_price - entry_price),
                4,
            )

            out.append({
                "trade_id": trade_id,
                "leg_id": leg_id,
                "index": index,
                "entry_date": entry_date,
                "exit_date": fut_exit_date,
                "expiry": fut_expiry or "",
                "strike": 0.0,
                "option_type": "FUT",
                "position": position,
                "lots": lots,
                "lot_size": lot_size,
                "slippage_pct": slippage,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "raw_entry_price": round(float(entry_price_raw), 4),
                "raw_exit_price": round(float(exit_price_raw), 4),
                "net_pnl": net_pnl,
                "entry_spot": float(entry_spot),
                "exit_spot": float(exit_spot),
                "exit_reason": "EXPIRY",
            })

    return out


def _pick_by_premium(
    chain: List[Tuple[float, float]],
    target: float,
    atm: float,
    is_call: bool,
) -> Optional[Tuple[float, float]]:
    """
    Python mirror of simulate.rs::pick_by_premium tie-breaking logic.

    Primary key : |premium - target|
    Tie-break 1 : |strike - atm|
    Tie-break 2 : CE prefers higher strike; PE prefers lower strike
    """
    if not chain:
        return None
    def _key(item: Tuple[float, float]) -> Tuple[float, float, float]:
        strike, premium = item
        prem_diff = abs(premium - target)
        atm_dist = abs(strike - atm)
        dir_key = -strike if is_call else strike
        return (prem_diff, atm_dist, dir_key)
    return min(chain, key=_key)


def _compute_strike_for_leg_python(
    leg: Dict[str, Any],
    entry_spot: float,
    interval: float,
    *,
    entry_date: Optional[str] = None,
    expiry: Optional[str] = None,
    index: Optional[str] = None,
) -> Optional[float]:
    """
    Python mirror of simulate.rs::compute_strike_for_leg.

    Handles: ATM, ITM1..N, OTM1..N (strike_type), pct_of_atm — always.
    Premium-based modes (closest_premium, premium_gte/lte, straddle_width,
    atm_straddle_prem_pct) — supported when entry_date, expiry, and index are
    provided (uses algotest_native.get_strikes_for_date / get_option_price).
    Returns None when premium lookup is needed but params are absent, or when
    the feather has no data for the given date/expiry.
    """
    sel = leg.get("strike_selection") or {}
    if not isinstance(sel, dict):
        return None
    sel_type = str(sel.get("type") or "strike_type").lower().strip()
    opt_type = str(leg.get("option_type") or "CE").upper()
    is_ce = opt_type in ("CE", "CALL", "C")
    atm = round(entry_spot / interval) * interval

    if sel_type in ("strike_type", "") or sel_type.startswith(("atm", "itm", "otm")):
        # Compact form: {type: "ATM"} | {type: "ITM1"} | …
        if sel_type not in ("strike_type", ""):
            strike_type = sel_type.upper()
        else:
            strike_type = str(sel.get("strike_type") or "ATM").upper().strip()
        if strike_type == "ATM":
            return atm
        if strike_type.startswith("ITM"):
            n_str = strike_type[3:].strip()
            try:
                n = int(n_str) if n_str else 1
            except ValueError:
                return None
            return atm - n * interval if is_ce else atm + n * interval
        if strike_type.startswith("OTM"):
            n_str = strike_type[3:].strip()
            try:
                n = int(n_str) if n_str else 1
            except ValueError:
                return None
            return atm + n * interval if is_ce else atm - n * interval
        return None

    if sel_type == "pct_of_atm":
        try:
            value = float(sel.get("value") or 0.0)
        except (TypeError, ValueError):
            return None
        direction = str(sel.get("direction") or "+").strip()
        shift = entry_spot * value / 100.0
        raw = entry_spot - shift if direction == "-" else entry_spot + shift
        return round(raw / interval) * interval

    # Premium-based modes — need the Rust feather for option chain lookup.
    if not (entry_date and expiry and index):
        return None  # Caller must fall back if these aren't provided

    try:
        import algotest_native  # type: ignore
    except ImportError:
        return None

    index_up = index.upper()

    if sel_type in ("closest_premium", "premium_gte", "premium_lte", "premium_range"):
        chain = list(algotest_native.get_strikes_for_date(entry_date, index_up, expiry, opt_type))
        if not chain:
            return None
        if sel_type == "closest_premium":
            target = float(sel.get("premium") or 0.0)
            item = _pick_by_premium(chain, target, atm, is_ce)
        elif sel_type == "premium_gte":
            target = float(sel.get("premium") or 0.0)
            qualifying = [(s, p) for s, p in chain if p >= target]
            item = _pick_by_premium(qualifying, target, atm, is_ce)
        elif sel_type == "premium_lte":
            target = float(sel.get("premium") or 0.0)
            qualifying = [(s, p) for s, p in chain if p <= target]
            item = _pick_by_premium(qualifying, target, atm, is_ce)
        else:  # premium_range
            lower = float(sel.get("lower") or 0.0)
            upper = float(sel.get("upper") or 0.0)
            qualifying = [(s, p) for s, p in chain if lower <= p <= upper]
            item = _pick_by_premium(qualifying, upper, atm, is_ce)
        return float(item[0]) if item else None

    if sel_type in ("straddle_width", "atm_straddle_prem_pct"):
        try:
            ce_px = algotest_native.get_option_price(entry_date, index_up, atm, "CE", expiry)
            pe_px = algotest_native.get_option_price(entry_date, index_up, atm, "PE", expiry)
        except Exception:
            return None
        if ce_px is None or pe_px is None:
            return None
        if sel_type == "straddle_width":
            mult = float(sel.get("straddle_multiplier") or 0.5)
            direction = str(sel.get("straddle_direction") or "+").strip()
            shift = mult * (float(ce_px) + float(pe_px))
            raw = atm - shift if direction == "-" else atm + shift
            return round(raw / interval) * interval
        else:  # atm_straddle_prem_pct
            pct = float(sel.get("value") or 0.0)
            target = (pct / 100.0) * (float(ce_px) + float(pe_px))
            chain = list(algotest_native.get_strikes_for_date(entry_date, index_up, expiry, opt_type))
            item = _pick_by_premium(chain, target, atm, is_ce)
            return float(item[0]) if item else None

    return None


def _build_fixed_entry_specs(
    payload: Dict[str, Any],
    expiry_dates: List[str],
    trading_days: List[str],
    spot_by_date: Dict[str, float],
    lot_size: int,
    segments: Optional[List[Tuple[str, str]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Build trade specs for filter_entry_mode='fixed'.

    Mirrors the Python engine's fixed-entry while loop
    (generic_algotest_engine.py:3783–3938):
      * First entry = first trading day >= seg_start (not DTE-based).
      * Exit = target_expiry - exit_dte trading days.
      * Subsequent entries = previous exit (same-day chain) when rollover_toggle=True.
      * One trade per segment when rollover_toggle=False or no_rollover=True.

    Returns None if any leg uses a premium-based strike mode (needs Python fallback).
    Returns [] if the schedule produces no trades.
    """
    rollover_toggle = bool(payload.get("rollover_toggle", False))
    no_rollover_flag = bool(payload.get("no_rollover", False))
    rollover_min_days = int(payload.get("rollover_min_days_to_expiry", 0) or 0)
    exit_dte = int(payload.get("exit_dte", 0) or 0)
    legs_src = payload.get("legs") or []
    index_str = str(payload.get("index") or "NIFTY").upper()
    interval = _STRIKE_INTERVALS.get(index_str, 50.0)
    slippage = float(payload.get("slippage_pct") or 0.0)

    sorted_expiries = sorted(expiry_dates)

    # Build effective segments
    if segments is not None:
        effective_segs = segments
    else:
        from_date = str(payload.get("from_date") or payload.get("date_from") or "")
        to_date = str(payload.get("to_date") or payload.get("date_to") or "")
        effective_segs = [(from_date, to_date)] if from_date and to_date else []

    all_specs: List[Dict[str, Any]] = []
    trade_id = 1

    for seg_start, seg_end in effective_segs:
        current_entry = _next_trading_day_on_or_after(trading_days, seg_start)
        if current_entry is None or current_entry > seg_end:
            continue

        last_in_seg = _last_trading_day_on_or_before(seg_end, trading_days)
        if last_in_seg is None:
            continue

        max_iters = max(20, len(sorted_expiries) * 4)
        iter_count = 0

        while current_entry <= seg_end and iter_count < max_iters:
            iter_count += 1
            if current_entry < seg_start:
                break

            # First expiry >= current_entry
            target_idx = bisect.bisect_left(sorted_expiries, current_entry)
            if target_idx >= len(sorted_expiries):
                break
            target_expiry = sorted_expiries[target_idx]

            # Rollover min-DTE extension
            if rollover_toggle and rollover_min_days > 0:
                gap = _trading_day_gap_strict(current_entry, target_expiry, trading_days)
                if gap <= rollover_min_days and target_idx + 1 < len(sorted_expiries):
                    target_idx += 1
                    target_expiry = sorted_expiries[target_idx]

            # Exit = target_expiry - exit_dte trading days
            exit_date = _trading_day_n_before(target_expiry, exit_dte, trading_days)
            if exit_date is None:
                break

            # 0-day cycle: advance target expiry until exit > entry
            while exit_date <= current_entry:
                if target_idx + 1 >= len(sorted_expiries):
                    next_td = _next_trading_day_on_or_after(trading_days,
                        sorted_expiries[-1] + "x")  # guaranteed > last expiry
                    if next_td is None or next_td > seg_end:
                        exit_date = current_entry  # signal break
                        break
                    current_entry = next_td
                    exit_date = current_entry
                    break
                target_idx += 1
                target_expiry = sorted_expiries[target_idx]
                exit_date_new = _trading_day_n_before(target_expiry, exit_dte, trading_days)
                exit_date = exit_date_new if exit_date_new else current_entry

            if exit_date <= current_entry:
                if current_entry > seg_end:
                    break
                continue

            # Clamp to segment end
            clamped = False
            if exit_date > last_in_seg:
                if last_in_seg <= current_entry:
                    break
                exit_date = last_in_seg
                clamped = True

            entry_spot = spot_by_date.get(current_entry)
            if not entry_spot:
                break

            for leg_idx, leg in enumerate(legs_src):
                if not isinstance(leg, dict):
                    return None
                strike = _compute_strike_for_leg_python(
                    leg, entry_spot, interval,
                    entry_date=current_entry, expiry=target_expiry, index=index_str,
                )
                if strike is None:
                    return None  # Strike not resolvable — caller falls back to Python
                all_specs.append({
                    "trade_id": trade_id,
                    "leg_id": leg_idx + 1,
                    "index": index_str,
                    "entry_date": current_entry,
                    "exit_date": exit_date,
                    "expiry": target_expiry,
                    "strike": float(strike),
                    "option_type": str(leg.get("option_type") or "CE").upper(),
                    "position": str(leg.get("position") or "SELL").upper(),
                    "lots": int(leg.get("lots") or 1),
                    "lot_size": int(lot_size),
                    "slippage_pct": slippage,
                })

            trade_id += 1

            if clamped or no_rollover_flag or not rollover_toggle:
                break
            current_entry = exit_date  # same-day chain

    return all_specs


def _fetch_one_extra_expiry(expiry_dates: List[str], payload: Dict[str, Any]) -> List[str]:
    """
    Return expiry_dates extended by one extra cycle beyond the last date.

    For NEXT_WEEKLY / NEXT_MONTHLY strategies the last schedule cycle needs a
    `next_exp` that may lie outside the backtest range. The Python engine gets
    this from get_expiry_dates() which queries the DB. We do the same but for
    just one row so the overhead is negligible.
    """
    if not expiry_dates:
        return expiry_dates
    last_exp = max(expiry_dates)
    try:
        import pandas as pd
        from base import get_expiry_dates  # type: ignore
        # Determine whether we need weekly or monthly expiries.
        expiry_type_raw = str(payload.get("expiry_type") or "WEEKLY").upper()
        if expiry_type_raw in ("MONTHLY", "NEXT_MONTHLY", "MONTHLY_T1"):
            freq = "monthly"
        else:
            freq = "weekly"
        index = str(payload.get("index") or "NIFTY").upper()
        # Search two months ahead to be safe (one weekly = 7 days).
        import datetime as _dt
        last_dt = _dt.date.fromisoformat(last_exp)
        lookahead = (last_dt + _dt.timedelta(days=40)).isoformat()
        extra_df = get_expiry_dates(index, freq, last_exp, lookahead)
        if extra_df is not None and not extra_df.empty:
            col = "Current Expiry" if "Current Expiry" in extra_df.columns else extra_df.columns[0]
            candidates = sorted(
                pd.to_datetime(extra_df[col]).dt.strftime("%Y-%m-%d").unique().tolist()
            )
            # Add the first expiry strictly after last_exp.
            for c in candidates:
                if c > last_exp:
                    combined = sorted(set(expiry_dates) | {c})
                    return combined
    except Exception:
        pass
    return expiry_dates


def _build_next_expiry_specs(
    payload: Dict[str, Any],
    expiry_dates: List[str],
    trading_days: List[str],
    spot_by_date: Dict[str, float],
    lot_size: int,
) -> Optional[List[Dict[str, Any]]]:
    """
    Build trade specs for strategies with per-leg NEXT_WEEKLY / NEXT_MONTHLY
    option contracts (calendar spreads or pure next-expiry strategies).

    For each expiry cycle (current_exp, next_exp):
      * entry_date  = current_exp - entry_dte trading days
      * exit_date   = (next_exp if all legs are NEXT else current_exp) - exit_dte days
      * per-leg option expiry = next_exp if leg.expiry in NEXT_EXPIRY_TYPES else current_exp

    Mirrors generic_algotest_engine.py:3535-3590 + 4545-4562.

    Returns None if any strike is unresolvable (Python fallback required).
    Returns [] if no trades are generated.
    """
    entry_dte = int(payload.get("entry_dte") or 1)
    exit_dte = int(payload.get("exit_dte") or 0)
    legs_src = [leg for leg in (payload.get("legs") or []) if isinstance(leg, dict)]
    index_str = str(payload.get("index") or "NIFTY").upper()
    interval = _STRIKE_INTERVALS.get(index_str, 50.0)
    slippage = float(payload.get("slippage_pct") or 0.0)

    if not legs_src:
        return []

    # Per-leg next flag — mirrors Python _is_leg_next at engine:4552.
    leg_is_next = [
        str(leg.get("expiry") or "").upper() in _NEXT_EXPIRY_TYPES
        for leg in legs_src
    ]
    _all_legs_next = all(leg_is_next)

    td_sorted = sorted(trading_days)
    sorted_expiries = sorted(expiry_dates)
    n_exp = len(sorted_expiries)
    if n_exp == 0:
        return []

    all_specs: List[Dict[str, Any]] = []
    trade_id = 1

    for i, cur_exp in enumerate(sorted_expiries):
        if i + 1 < n_exp:
            next_exp = sorted_expiries[i + 1]
        else:
            # No next expiry in range — skip if all legs need it.
            # For mixed strategies fall back to cur_exp (matches Python engine behaviour
            # where next_exp=None causes the trade to be skipped via _all_legs_next guard).
            if _all_legs_next:
                continue
            next_exp = cur_exp

        entry_date = _trading_day_n_before(cur_exp, entry_dte, td_sorted)
        if entry_date is None:
            continue

        # Exit anchors to next_exp when all legs trade the next contract.
        exit_anchor = next_exp if _all_legs_next else cur_exp
        exit_date = _trading_day_n_before(exit_anchor, exit_dte, td_sorted)
        if exit_date is None:
            continue

        if entry_date >= exit_date:
            continue

        entry_spot = spot_by_date.get(entry_date)
        if not entry_spot:
            continue

        for leg_idx, (leg, is_next) in enumerate(zip(legs_src, leg_is_next)):
            per_leg_expiry = next_exp if is_next else cur_exp
            strike = _compute_strike_for_leg_python(
                leg, entry_spot, interval,
                entry_date=entry_date, expiry=per_leg_expiry, index=index_str,
            )
            if strike is None:
                return None  # Strike unresolvable — caller falls back to Python engine
            all_specs.append({
                "trade_id": trade_id,
                "leg_id": leg_idx + 1,
                "index": index_str,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "expiry": per_leg_expiry,
                "strike": float(strike),
                "option_type": str(leg.get("option_type") or "CE").upper(),
                "position": str(leg.get("position") or "SELL").upper(),
                "lots": int(leg.get("lots") or 1),
                "lot_size": int(lot_size),
                "slippage_pct": slippage,
            })

        trade_id += 1

    return all_specs


def _apply_min_days_filter(
    specs: List[Dict[str, Any]],
    payload: Dict[str, Any],
    trading_days: List[str],
    segments: List[Tuple[str, str]],
    spot_by_date: Dict[str, float],
) -> Optional[List[Dict[str, Any]]]:
    """
    Apply filter_entry_mode='min_days' first-trade entry adjustment.

    For each segment, checks the first trade's expiry DTE from seg_start:
      * DTE >= min_days_to_entry: entry moves to seg_start (or deferred skip_to date).
      * DTE < min_days_to_entry, exit > expiry (rollover): entry shifts to expiry.
      * DTE < min_days_to_entry, exit <= expiry (non-rollover): skip cycle; next
        trade's entry shifts to this cycle's expiry.

    Mirrors generic_algotest_engine.py:3950–3989.

    Groups by spec `expiry` (not DTE-based `entry_date`) so large entry_dte
    values (where entry < seg_start) don't hide the trade from processing.

    Returns None if any strike recomputation requires a premium-based lookup.
    Returns [] if no trades survive.
    """
    min_days_to_entry = int(payload.get("min_days_to_entry", 0) or 0)
    if min_days_to_entry <= 0:
        return specs  # No constraint — unchanged

    index_str = str(payload.get("index") or "NIFTY").upper()
    interval = _STRIKE_INTERVALS.get(index_str, 50.0)
    legs_src = payload.get("legs") or []

    # Group specs by trade_id; per-trade metadata
    trade_groups: Dict[int, List[Dict[str, Any]]] = {}
    for s in specs:
        tid = int(s.get("trade_id", 0))
        trade_groups.setdefault(tid, []).append(s)

    tid_meta: Dict[int, Dict[str, str]] = {}
    for tid, group in trade_groups.items():
        first = group[0]
        tid_meta[tid] = {
            "entry":  _normalize_iso(first.get("entry_date")),
            "exit":   _normalize_iso(first.get("exit_date")),
            "expiry": _normalize_iso(first.get("expiry")),
        }

    kept_tids: Set[int] = set()
    entry_overrides: Dict[int, str] = {}  # tid → new entry_date

    for seg_start, seg_end in segments:
        # Identify trades belonging to this segment by expiry (not DTE entry),
        # so large entry_dte values don't exclude near-seg-start expiries.
        seg_tids = sorted(
            tid for tid, m in tid_meta.items()
            if seg_start <= m["expiry"] <= seg_end
        )
        if not seg_tids:
            continue

        _skip_to: Optional[str] = None   # deferred entry from a skipped cycle
        _first_done = False               # True once first trade is settled

        for tid in seg_tids:
            m = tid_meta[tid]
            cur_exp   = m["expiry"]
            exit_date = m["exit"]

            if not _first_done:
                # First undecided trade in this segment — apply min_days logic.
                dte_gap = _trading_day_gap_left(seg_start, cur_exp, trading_days)

                if dte_gap >= min_days_to_entry:
                    new_entry = _skip_to if _skip_to else seg_start
                    _skip_to = None
                    if new_entry != m["entry"]:
                        entry_overrides[tid] = new_entry
                    kept_tids.add(tid)
                    _first_done = True
                else:
                    if exit_date > cur_exp:
                        # Rollover: shift entry to cur_exp
                        if cur_exp != m["entry"]:
                            entry_overrides[tid] = cur_exp
                        _skip_to = None
                        kept_tids.add(tid)
                        _first_done = True
                    else:
                        # Non-rollover: skip this cycle; defer entry to cur_exp
                        _skip_to = cur_exp
                        # Don't set _first_done — try next tid
            else:
                # Subsequent trades — keep unmodified
                kept_tids.add(tid)

    # Rebuild specs list with entry_date overrides (and recomputed strikes)
    result: List[Dict[str, Any]] = []
    for s in specs:
        tid = int(s.get("trade_id", 0))
        if tid not in kept_tids:
            continue
        if tid in entry_overrides:
            new_entry = entry_overrides[tid]
            new_spot  = spot_by_date.get(new_entry)
            if not new_spot:
                return None  # No spot data for new entry — Python fallback
            leg_idx = int(s.get("leg_id", 1)) - 1
            leg = legs_src[leg_idx] if 0 <= leg_idx < len(legs_src) else {}
            spec_expiry = _normalize_iso(s.get("expiry"))
            new_strike = _compute_strike_for_leg_python(
                leg, new_spot, interval,
                entry_date=new_entry, expiry=spec_expiry, index=index_str,
            )
            if new_strike is None:
                return None  # Strike not resolvable — Python fallback
            s = dict(s)
            s["entry_date"] = new_entry
            s["strike"] = float(new_strike)
        result.append(s)
    return result


def _apply_no_rollover(
    specs: List[Dict[str, Any]],
    payload: Dict[str, Any],
    trading_days: List[str],
    segments: Optional[List[Tuple[str, str]]],
) -> List[Dict[str, Any]]:
    """
    Post-process resolved specs to keep only the first trade per segment.

    Mirrors generic_algotest_engine.py:4054-4072:
      * seg_entries = seg_entries[:1]  (one trade per segment)
      * With no_rollover_min_days: if first expiry ≤ min_days from seg_start,
        extend first trade's exit to second trade's exit (min-DTE extension).

    Only called when no_rollover=True.
    """
    no_rollover_min_days = int(payload.get("no_rollover_min_days", 0) or 0)

    # Group specs by trade_id
    trade_groups: Dict[int, List[Dict[str, Any]]] = {}
    for s in specs:
        tid = int(s.get("trade_id", 0))
        trade_groups.setdefault(tid, []).append(s)

    if not trade_groups:
        return specs

    # Effective segments
    if segments is not None:
        effective_segs = segments
    else:
        from_date = str(payload.get("from_date") or payload.get("date_from") or "")
        to_date = str(payload.get("to_date") or payload.get("date_to") or "")
        effective_segs = [(from_date, to_date)] if from_date and to_date else []

    if not effective_segs:
        # Fallback: treat entire range as one segment
        sorted_tids = sorted(trade_groups.keys())
        kept_tids: Set[int] = {sorted_tids[0]}
        exit_overrides: Dict[int, str] = {}
        if no_rollover_min_days >= 1 and len(sorted_tids) >= 2:
            first_tid = sorted_tids[0]
            first_expiry = _normalize_iso(trade_groups[first_tid][0].get("expiry"))
            from_date = str(payload.get("from_date") or payload.get("date_from") or "")
            if first_expiry and from_date:
                gap = _trading_day_gap_left(from_date, first_expiry, trading_days)
                if gap <= no_rollover_min_days:
                    second_tid = sorted_tids[1]
                    second_exits = [_normalize_iso(s.get("exit_date")) for s in trade_groups[second_tid] if s.get("exit_date")]
                    if second_exits:
                        exit_overrides[first_tid] = min(second_exits)
    else:
        # Map each tid to its earliest entry date
        tid_entry: Dict[int, str] = {}
        for tid, group in trade_groups.items():
            entries = [_normalize_iso(s.get("entry_date")) for s in group if s.get("entry_date")]
            if entries:
                tid_entry[tid] = min(entries)

        kept_tids = set()
        exit_overrides = {}
        for seg_start, seg_end in effective_segs:
            seg_tids = sorted(
                tid for tid, entry in tid_entry.items()
                if seg_start <= entry <= seg_end
            )
            if not seg_tids:
                continue
            first_tid = seg_tids[0]
            kept_tids.add(first_tid)

            if no_rollover_min_days >= 1 and len(seg_tids) >= 2:
                first_expiry = _normalize_iso(trade_groups[first_tid][0].get("expiry"))
                if first_expiry:
                    gap = _trading_day_gap_left(seg_start, first_expiry, trading_days)
                    if gap <= no_rollover_min_days:
                        second_tid = seg_tids[1]
                        second_exits = [_normalize_iso(s.get("exit_date")) for s in trade_groups[second_tid] if s.get("exit_date")]
                        if second_exits:
                            exit_overrides[first_tid] = min(second_exits)

    result = []
    for s in specs:
        tid = int(s.get("trade_id", 0))
        if tid not in kept_tids:
            continue
        if tid in exit_overrides:
            s = dict(s)
            s["exit_date"] = exit_overrides[tid]
        result.append(s)
    return result


def _apply_fixed_rollover_strike(
    specs: List[Dict[str, Any]],
    payload: Dict[str, Any],
    segments: Optional[List[Tuple[str, str]]],
) -> List[Dict[str, Any]]:
    """
    Apply rollover_strike_mode='fixed': reuse the first rollover cycle's strike
    for all subsequent cycles in the same segment.

    Mirrors generic_algotest_engine.py:4601-4618:
      - entry_idx == 1: resolve fresh strike (and buffer) → save it
      - entry_idx >  1: reuse saved strike; buffer_offset = 0

    This is called AFTER _apply_buffer_strike_to_specs so the saved strike already
    includes the buffer adjustment — matching the Python engine's behavior where
    _seg_fixed_strikes[key] stores the fully-resolved (post-buffer) strike.

    Only effective when rollover_toggle=True (multiple trades per segment exist).
    """
    if not bool(payload.get("rollover_toggle", False)):
        return specs  # Each segment has at most one trade — no-op

    legs_src = payload.get("legs") or []
    fixed_leg_ids: Set[int] = {
        idx + 1
        for idx, leg in enumerate(legs_src)
        if isinstance(leg, dict) and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
    }
    if not fixed_leg_ids:
        return specs

    # Determine effective segment boundaries
    if segments is not None:
        effective_segs = segments
    else:
        from_date = str(payload.get("from_date") or payload.get("date_from") or "")
        to_date = str(payload.get("to_date") or payload.get("date_to") or "")
        effective_segs = [(from_date, to_date)] if from_date and to_date else []

    if not effective_segs:
        return specs

    # Group specs by trade_id; find each trade's earliest entry_date
    trade_groups: Dict[int, List[Dict[str, Any]]] = {}
    for s in specs:
        trade_groups.setdefault(int(s.get("trade_id", 0)), []).append(s)

    tid_entry: Dict[int, str] = {}
    for tid, group in trade_groups.items():
        entries = [_normalize_iso(s.get("entry_date")) for s in group if s.get("entry_date")]
        if entries:
            tid_entry[tid] = min(entries)

    # Build (tid, leg_id) → override_strike from each segment's first trade
    strike_overrides: Dict[Tuple[int, int], float] = {}

    for seg_start, seg_end in effective_segs:
        seg_tids = sorted(
            tid for tid, entry in tid_entry.items()
            if seg_start <= entry <= seg_end
        )
        if len(seg_tids) <= 1:
            continue  # Only one trade — nothing to override

        first_tid = seg_tids[0]
        seg_first_strikes: Dict[int, float] = {}
        for s in trade_groups.get(first_tid, []):
            leg_id = int(s.get("leg_id", 1))
            if leg_id in fixed_leg_ids:
                seg_first_strikes[leg_id] = float(s.get("strike") or 0.0)

        for tid in seg_tids[1:]:
            for leg_id, saved_strike in seg_first_strikes.items():
                strike_overrides[(tid, leg_id)] = saved_strike

    if not strike_overrides:
        return specs

    result: List[Dict[str, Any]] = []
    for s in specs:
        key = (int(s.get("trade_id", 0)), int(s.get("leg_id", 1)))
        if key in strike_overrides:
            s = dict(s)
            s["strike"] = strike_overrides[key]
        result.append(s)
    return result


def priced_to_tradesheet_records(
    priced: List[Dict[str, Any]],
    payload: Dict[str, Any],
    lot_size: int,
) -> List[Dict[str, Any]]:
    """
    Convert the Rust orchestrator's priced spec rows into tradesheet records
    matching the column shape that ``base.compute_analytics`` and the existing
    Python engine output produce.

    What's filled
    -------------
      * Trade / Leg / Index / Type / Strike / B/S / Qty
      * Entry Date / Exit Date / Entry Price / Exit Price
      * Raw Entry / Raw Exit  (== Entry / Exit; slippage already applied in Rust)
      * Entry Spot / Exit Spot / Spot P&L
      * Expiry
      * CE P&L / PE P&L / FUT P&L / Net P&L / % P&L
      * Exit Reason  (always 'Expiry' for now — Rust orchestrator doesn't yet
        thread the SL/Target trigger reason back into priced rows)
      * Defaults for buffer_*, ReEntry*, Is Lazy Leg, MAE, MFE columns

    Known gaps (deliberate — not yet computed by Rust)
    ---------------------------------------------------
      * MAE / MFE — would require an additional intra-trade scan
      * Exit Reason — currently always 'Expiry' regardless of SL/Target/Spot-Adj
        firing. The orchestrator HAS this info per leg_result; threading it
        through is a follow-up.
      * ReEntryIndex / ReEntryTrigger / ReEntryMode — re-entry rows are
        present, but not yet tagged
      * Cumulative / Peak / DD / %DD — these are added by compute_analytics

    Returns a list of dicts (one row per priced leg). Pass to
    ``pd.DataFrame(records)`` then ``compute_analytics(df)``.
    """
    index_str = str(payload.get("index") or "NIFTY").upper()
    out: List[Dict[str, Any]] = []
    for row in priced:
        opt_type = (row.get("option_type") or "").upper()
        position = (row.get("position") or "SELL").upper()
        entry_spot = float(row.get("entry_spot") or 0.0)
        exit_spot = float(row.get("exit_spot") or 0.0)
        spot_pnl = round(exit_spot - entry_spot, 2)
        net_pnl = float(row.get("net_pnl") or 0.0)
        # CE/PE P&L are PER-LEG values. The simulate.rs post-process puts the
        # trade total in the parent row's `net_pnl`, so we cannot read per-leg
        # P&L back from that column. Recompute it from entry/exit prices —
        # this matches Python's tradesheet builder which stores per-leg P&L
        # in CE P&L / PE P&L and then aggregates them in compute_analytics.
        entry_px = float(row.get("entry_price") or 0.0)
        exit_px = float(row.get("exit_price") or 0.0)
        is_fut = opt_type == "FUT"
        per_leg_pnl = round(
            (entry_px - exit_px) if position == "SELL" else (exit_px - entry_px), 4
        )
        ce_pnl = per_leg_pnl if opt_type == "CE" else 0
        pe_pnl = per_leg_pnl if opt_type == "PE" else 0
        fut_pnl = per_leg_pnl if is_fut else 0
        pct_pnl = round(net_pnl / entry_spot * 100.0, 4) if entry_spot else 0.0
        qty = int(row.get("lots") or 1) * int(row.get("lot_size") or lot_size or 1)
        # FUTURES: Strike = '' (matches Python engine convention); options: float.
        strike_val = "" if is_fut else float(row.get("strike") or 0.0)
        out.append({
            "Trade": str(row.get("trade_id") or ""),
            "Leg": int(row.get("leg_id") or 1),
            "Index": index_str,
            "Entry Date": _normalize_iso(row.get("entry_date")),
            "Exit Date": _normalize_iso(row.get("exit_date")),
            "Leg Exit Date": _normalize_iso(row.get("exit_date")),
            "Type": opt_type,
            "Strike": strike_val,
            "B/S": position,
            "Qty": qty,
            "Entry Price": entry_px,
            "Exit Price": exit_px,
            "Raw Entry Price": float(row.get("raw_entry_price") or entry_px),
            "Raw Exit Price": float(row.get("raw_exit_price") or exit_px),
            "MAE": 0.0,
            "MFE": 0.0,
            "buffer_strike_enabled": False,
            "buffer_position": None,
            "buffer_ref_price": None,
            "buffer_strike_offset": 0,
            "Entry Spot": entry_spot,
            "Exit Spot": exit_spot,
            "Spot P&L": spot_pnl,
            "Expiry": _normalize_iso(row.get("expiry")),
            "CE P&L": ce_pnl,
            "PE P&L": pe_pnl,
            "FUT P&L": fut_pnl,
            "FUT Entry Price": entry_px if is_fut else "",
            "FUT Exit Price": exit_px if is_fut else "",
            "Net P&L": net_pnl,
            "% P&L": pct_pnl,
            "Exit Reason": str(row.get("exit_reason") or "EXPIRY"),
            "ReEntryIndex": "",
            "ReEntryTrigger": "",
            "ReEntryMode": "",
            "Is Lazy Leg": False,
            "Lazy Leg Name": "",
            "Lazy Entry Date": "",
            "Lazy Exit Date": "",
        })
    return out


def _supports_reentry_strike(leg_src: Dict[str, Any]) -> bool:
    """
    Returns True when the leg's strike selection is supported by
    _compute_strike_for_leg_python (which now handles all modes including premium
    via the Rust feather when entry_date/expiry/index are provided).
    Only returns False for truly unknown/malformed strike_selection shapes.
    """
    sel = leg_src.get("strike_selection") or {}
    return isinstance(sel, dict)


_BUFFER_PREMIUM_TYPES = frozenset({
    "closest_premium", "premium_gte", "premium_lte", "premium_range",
    "atm_straddle_prem_pct", "straddle_width",
})
_BUFFER_STRIKE_INTERVALS: Dict[str, int] = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25,
}


def _apply_buffer_strike_to_specs(
    specs: List[Dict[str, Any]],
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Apply buffer strike offset to specs after Rust strike resolution.

    Mirrors engines/generic_algotest_engine.py _apply_strike_buffer_after_selection
    (lines 912-964). CE legs shift UP (more OTM), PE legs shift DOWN (more OTM).
    Not applied to premium-based strike selections.

    Only called when buffer_strike_enabled=True.
    """
    try:
        buf_val = float(payload.get("buffer_strike_value") or 0.5)
    except (TypeError, ValueError):
        buf_val = 0.5
    if buf_val <= 0:
        return specs

    buf_unit = str(payload.get("buffer_strike_unit") or "percent").lower().strip()
    if buf_unit not in ("percent", "points"):
        buf_unit = "percent"

    buf_apply_to = str(payload.get("buffer_strike_apply_to") or "both").lower().strip()
    if buf_apply_to not in ("call", "put", "both"):
        buf_apply_to = "both"

    buf_above = bool(payload.get("buffer_position_above", True))
    buf_below = bool(payload.get("buffer_position_below", True))
    if not buf_above and not buf_below:
        return specs

    index_str = str(payload.get("index") or "NIFTY").upper()
    interval = float(_BUFFER_STRIKE_INTERVALS.get(index_str, 50))
    legs_src = payload.get("legs") or []

    result: List[Dict[str, Any]] = []
    for spec in specs:
        leg_id = int(spec.get("leg_id") or 1)
        leg_src = legs_src[leg_id - 1] if 0 <= leg_id - 1 < len(legs_src) else {}
        sel = leg_src.get("strike_selection") or {}
        sel_type = str(sel.get("type") or "").lower().strip()
        if sel_type in _BUFFER_PREMIUM_TYPES:
            result.append(spec)
            continue

        opt = str(spec.get("option_type") or "").upper()
        is_ce = opt in ("CE", "CALL", "C")
        is_pe = opt in ("PE", "PUT", "P")
        if not (is_ce or is_pe):
            result.append(spec)
            continue

        checkbox_ok = (is_ce and buf_above) or (is_pe and buf_below)
        applies = checkbox_ok and (
            buf_apply_to == "both"
            or (buf_apply_to == "call" and is_ce)
            or (buf_apply_to == "put" and is_pe)
        )
        if not applies:
            result.append(spec)
            continue

        base = float(spec.get("strike") or 0.0)
        if base <= 0:
            result.append(spec)
            continue

        pct = (buf_val / base * 100.0) if buf_unit == "points" else buf_val
        if pct <= 0:
            result.append(spec)
            continue

        buf = base * (pct / 100.0)
        if is_ce:
            snapped = math.ceil((base + buf) / interval) * interval
        else:
            snapped = math.floor((base - buf) / interval) * interval

        new_spec = dict(spec)
        new_spec["strike"] = float(snapped)
        result.append(new_spec)
    return result


def run_rust_engine_pipeline(
    payload: Dict[str, Any],
    *,
    expiry_dates: List[str],
    trading_days: List[str],
    lot_size: int,
    spot_by_date: Dict[str, float],
    square_off_mode: str = "partial",
) -> Optional[List[Dict[str, Any]]]:
    """
    Run the Rust-accelerated pipeline end-to-end.

    Returns:
      * `None` if the Rust path cannot handle this payload — caller falls back.
      * A list of priced trade rows otherwise. Empty list means the strategy
        produced no trades (e.g., no resolvable expiries in the range).
    """
    try:
        import algotest_native  # type: ignore
    except ImportError:
        return None

    # Guard: if the Rust feather cache isn't loaded, simulate_trades_batch will
    # return empty for every spec. Fall back to Python rather than silently
    # returning zero trades. (Cache load happens in algotest_job before this call;
    # this catches the edge case where the feather was deleted and rebuild failed.)
    if not algotest_native.is_loaded():
        logger.warning("[ENGINE_RUST] Rust cache not loaded — falling back to Python engine")
        return None

    # Sorted expiry list used by NEXT_WEEKLY and LAZY_LEG expiry resolution.
    _sorted_expiries: List[str] = sorted(expiry_dates)

    # ── FUTURES legs ────────────────────────────────────────────────────────────
    # FUTURES pricing uses base.resolve_futures_pnl_with_rollover (Python DB
    # lookup), not the Rust feather.  Detect them early so we can dispatch to
    # _build_futures_specs before reaching the algotest_native calls below.
    _has_futures_leg = any(
        isinstance(_leg, dict) and str(_leg.get("segment") or "OPTION").upper() in ("FUTURE", "FUTURES")
        for _leg in (payload.get("legs") or [])
    )

    # ── Per-leg NEXT_WEEKLY / NEXT_MONTHLY expiry (calendar spreads) ────────────
    # Some legs independently trade the next expiry contract. Python resolves
    # per-leg contract expiry; Rust prices each spec via simulate_trades_batch.
    # Mirrors generic_algotest_engine.py:3535-3590, 4545-4562.
    _has_next_leg = any(
        isinstance(_leg, dict) and str(_leg.get("expiry") or "").upper() in _NEXT_EXPIRY_TYPES
        for _leg in (payload.get("legs") or [])
    )

    # ── Slice 8b: filter_entry_mode dispatch ────────────────────────────────
    # 'fixed': first entry anchored to seg_start; entries chain same-day.
    # 'min_days' with active filter: first-trade shifting logic — too complex
    #           for post-processing; fall back to Python only when a filter IS
    #           active (without a filter it behaves identically to 'dte').
    # 'dte' (default): Rust builds the DTE-based schedule; we apply segment
    #           gating as a post-processing filter below.
    filter_entry_mode = str(payload.get("filter_entry_mode") or "dte").lower().strip()
    segments = _load_filter_segments(payload)
    # Save before dispatch branches nullify segments so _apply_fixed_rollover_strike
    # can still use the original segment boundaries as grouping keys.
    original_segments = segments

    if _has_futures_leg:
        # FUTURES legs are priced via base.resolve_futures_pnl_with_rollover
        # (Python DB lookup), not the Rust feather.  Build and return complete
        # priced rows directly — bypass simulate_trades_batch and SL/re-entry.
        # FUTURES + NEXT_WEEKLY mixed strategies fall back to Python for now.
        if _has_next_leg:
            return None
        fut_rows = _build_futures_specs(
            payload, expiry_dates, trading_days, spot_by_date, int(lot_size), segments,
        )
        if fut_rows is None:
            return None  # SL/Target/re-entry on futures — Python handles it
        return fut_rows  # already priced; skip simulate_trades_batch entirely

    elif _has_next_leg:
        # Per-leg NEXT_WEEKLY / NEXT_MONTHLY: Python builds per-leg-expiry specs.
        # Extend expiry list by one extra cycle so the last cycle can resolve its
        # next_exp contract (Python engine gets this from an internal DB call).
        _ext_expiry_dates = _fetch_one_extra_expiry(expiry_dates, payload)
        specs = _build_next_expiry_specs(
            payload, _ext_expiry_dates, trading_days, spot_by_date, int(lot_size),
        )
        if specs is None:
            return None  # Strike unresolvable — Python engine handles it
        if not specs:
            return []
        if payload.get("buffer_strike_enabled"):
            specs = _apply_buffer_strike_to_specs(specs, payload)
        segments = None  # No further STR gating needed

    elif filter_entry_mode == "fixed":
        # Step 1 (fixed): Python builds schedule then Rust prices each spec.
        specs = _build_fixed_entry_specs(
            payload, expiry_dates, trading_days, spot_by_date, int(lot_size), segments,
        )
        if specs is None:
            return None  # Premium-based strike mode — Python engine handles it
        if not specs:
            return []
        # Buffer strike applies after strike resolution, same as DTE mode.
        if payload.get("buffer_strike_enabled"):
            specs = _apply_buffer_strike_to_specs(specs, payload)
        # Segment gating is already built into _build_fixed_entry_specs; skip below.
        segments = None

    elif filter_entry_mode == "min_days":
        # Slice 8c: min_days mode — both with and without an active STR/filter.
        # Step 1: Rust builds DTE-based specs normally.
        specs = algotest_native.resolve_trade_specs(
            payload, expiry_dates, trading_days, int(lot_size), spot_by_date
        )
        if not specs:
            return None
        if payload.get("buffer_strike_enabled"):
            specs = _apply_buffer_strike_to_specs(specs, payload)
        # Determine effective segment boundaries for the min_days adjustment.
        # With a STR filter, segments is already set. Without a filter, treat
        # the entire date range as one segment.
        if segments is not None:
            segs_for_min_days = segments
            if not segs_for_min_days:
                return []
        else:
            _from = str(payload.get("from_date") or payload.get("date_from") or "")
            _to = str(payload.get("to_date") or payload.get("date_to") or "")
            segs_for_min_days = [(_from, _to)] if _from and _to else []
        # Apply first-trade entry adjustment per segment.
        specs = _apply_min_days_filter(specs, payload, trading_days, segs_for_min_days, spot_by_date)
        if specs is None:
            return None  # Premium-based strike or missing spot — Python fallback
        if not specs:
            return []
        segments = None  # Suppress DTE segment gating below — already handled

    else:
        # Step 1 (DTE, default): Rust builds trade specs.
        specs = algotest_native.resolve_trade_specs(
            payload, expiry_dates, trading_days, int(lot_size), spot_by_date
        )
        if not specs:
            # Rust path rejected payload — feature outside supported slices.
            return None

        # Slice 7b: Buffer strike.
        if payload.get("buffer_strike_enabled"):
            specs = _apply_buffer_strike_to_specs(specs, payload)

        # Slice 8a: STR / filter date-range gating (DTE mode only).
        if segments is not None:
            if not segments:
                return []

            def _seg_for(entry_iso: str) -> Optional[Tuple[str, str]]:
                for s_start, s_end in segments:
                    if s_start <= entry_iso <= s_end:
                        return (s_start, s_end)
                return None

            filtered: List[Dict[str, Any]] = []
            for s in specs:
                entry_iso = _normalize_iso(s["entry_date"])
                seg = _seg_for(entry_iso)
                if seg is None:
                    continue
                _, seg_end = seg
                exit_iso = _normalize_iso(s["exit_date"])
                if exit_iso > seg_end:
                    clamped = _last_trading_day_on_or_before(seg_end, trading_days)
                    if clamped is None or clamped <= entry_iso:
                        continue
                    s = dict(s)
                    s["exit_date"] = clamped
                filtered.append(s)
            specs = filtered
            if not specs:
                return []

    # ── Slice 6b: no_rollover post-processing ───────────────────────────────
    # Keep only the first trade per segment (or globally when no filter).
    # Mirrors generic_algotest_engine.py:4054-4072.
    if payload.get("no_rollover"):
        specs = _apply_no_rollover(specs, payload, trading_days, segments)

    # ── Slice 9b: rollover_strike_mode='fixed' ───────────────────────────────
    # Reuse first rollover cycle's (already-buffered) strike for subsequent cycles.
    # Must run AFTER buffer-strike and segment gating, BEFORE simulate_trades_batch.
    # Mirrors generic_algotest_engine.py:4601-4618.
    if any(
        isinstance(leg, dict) and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in (payload.get("legs") or [])
    ):
        specs = _apply_fixed_rollover_strike(specs, payload, original_segments)

    # Step 2: price entries + scheduled exits.
    priced = algotest_native.simulate_trades_batch(specs)
    if not priced:
        return []

    # Step 3: per-trade SL/Target/Trail check using the existing Rust function.
    # Group priced rows by trade_id so we can pass each trade's legs together.
    # NOTE: Rust's check_leg_stop_loss_target expects `trading_calendar` as a
    # bare Vec<String> of ISO YYYY-MM-DD dates, NOT a list of dicts.
    legs_src = payload.get("legs") or []
    trading_calendar = list(trading_days)
    slippage = float(payload.get("slippage_pct") or 0.0)

    # Detect whether ANY leg has risk controls set. If none, skip the check
    # altogether — saves a per-trade PyO3 call.
    def _has_risk(leg: Dict[str, Any]) -> bool:
        for key in ("stopLoss", "targetProfit", "trailSL", "slWithBuffer"):
            v = leg.get(key) or {}
            if not isinstance(v, dict):
                continue
            if _maybe_float(v.get("value")):
                return True
            if v.get("trigger") and v.get("move"):
                return True
        return False

    any_risk = any(_has_risk(leg) for leg in legs_src if isinstance(leg, dict))
    has_overall_top = (
        (_maybe_float(payload.get("overall_sl_value")) or 0) != 0
        or (_maybe_float(payload.get("overall_target_value")) or 0) != 0
    )
    has_spot_adj = bool(payload.get("spot_adjustment_enabled")) and (
        (_maybe_float(payload.get("spot_adjustment_pct")) or 0) > 0
    )
    if not any_risk and not has_overall_top and not has_spot_adj:
        # No risk controls → priced output is the final answer.
        return list(priced)

    # Slice 4b: SL-with-Buffer pre-pass. Walk each priced spec and let Rust
    # detect a gap-triggered SL-with-Buffer exit. If triggered, the override
    # price is computed in Rust (using day high/low). We capture this as
    # `slb_overrides[(trade_id, leg_id)] = (date, override_price)` and apply
    # it AFTER the regular SL check, since the engine evaluates buffer
    # AFTER plain SL — see generic_algotest_engine.py:2654-2660.
    slb_overrides: Dict[Tuple[int, int], Tuple[str, float]] = {}
    if any(isinstance(leg, dict) and isinstance(leg.get("slWithBuffer"), dict)
           and _maybe_float((leg.get("slWithBuffer") or {}).get("value"))
           for leg in legs_src):
        try:
            slb = algotest_native.apply_sl_with_buffer_batch(
                list(priced), list(legs_src), list(trading_days)
            )
            for spec_row, result in zip(priced, slb):
                if result is not None:
                    date, price = result
                    slb_overrides[(spec_row["trade_id"], spec_row["leg_id"])] = (
                        _normalize_iso(date), float(price),
                    )
        except Exception as exc:
            logger.warning("[ENGINE_RUST] SL-with-Buffer pre-pass failed: %s", exc)

    # Group priced rows by trade_id while preserving leg order.
    from collections import defaultdict
    by_trade: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in priced:
        by_trade[row["trade_id"]].append(row)

    # Slice 7a: Spot Adjustment exit trigger. Computed per trade BEFORE the SL
    # check loop so the SL scan window is truncated. Matches Python flow at
    # engines/generic_algotest_engine.py:3972-3994 — apply_spot_adjustment_exit
    # overrides exit_date BEFORE leg processing.
    spot_adj_enabled = bool(payload.get("spot_adjustment_enabled"))
    spot_adj_pct = _maybe_float(payload.get("spot_adjustment_pct")) or 0.0
    spot_adj_direction = str(payload.get("spot_adjustment_direction") or "rise").lower()
    if spot_adj_direction not in ("rise", "fall", "both"):
        spot_adj_direction = "rise"
    spot_adj_units = str(payload.get("spot_adjustment_units") or "percent").lower()
    if spot_adj_units not in ("percent", "points"):
        spot_adj_units = "percent"
    # Mirror Python's clamp: pct in [0.25, 5.0] when enabled.
    if spot_adj_enabled and spot_adj_pct > 0 and spot_adj_units == "percent":
        spot_adj_pct = max(0.25, min(5.0, spot_adj_pct))

    spot_adj_overrides: Dict[int, str] = {}
    spot_adj_reasons: Dict[int, str] = {}
    if spot_adj_enabled and spot_adj_pct > 0:
        for trade_id, legs in by_trade.items():
            legs.sort(key=lambda r: r["leg_id"])
            first = legs[0]
            entry_iso = _normalize_iso(first["entry_date"])
            entry_spot = float(first.get("entry_spot") or spot_by_date.get(entry_iso) or 0.0)
            scheduled_exit = _normalize_iso(first["exit_date"])
            if entry_spot <= 0 or not scheduled_exit:
                continue
            trig = _compute_spot_adjustment_trigger(
                entry_iso,
                entry_spot,
                scheduled_exit,
                spot_adj_direction,
                spot_adj_pct,
                spot_adj_units,
                trading_days,
                spot_by_date,
            )
            if trig:
                spot_adj_overrides[trade_id] = trig
                if spot_adj_direction == "rise":
                    spot_adj_reasons[trade_id] = "SPOT_ADJ_RISE"
                elif spot_adj_direction == "fall":
                    spot_adj_reasons[trade_id] = "SPOT_ADJ_FALL"
                else:
                    # "both" — determine which threshold was crossed on the trigger day
                    trig_spot = spot_by_date.get(trig)
                    if trig_spot is not None:
                        if spot_adj_units == "points":
                            rise_tgt = entry_spot + spot_adj_pct
                        else:
                            rise_tgt = entry_spot * (1.0 + spot_adj_pct / 100.0)
                        spot_adj_reasons[trade_id] = "SPOT_ADJ_RISE" if trig_spot >= rise_tgt else "SPOT_ADJ_FALL"
                    else:
                        spot_adj_reasons[trade_id] = "SPOT_ADJ"

    # Slice 5: Overall SL / Target detection.
    # Engine flow (engines/generic_algotest_engine.py:4553-4594):
    #   1. Per-leg SL/Target/Trail computed first → per_leg_results
    #   2. Overall SL/Target computed second, passing per_leg_results so closed
    #      legs are excluded from the combined P&L sum.
    #   3. If overall fires: each leg whose existing exit_date is on/after the
    #      overall trigger gets overridden to that date+reason. Earlier per-leg
    #      exits are preserved (_apply_overall_sl_to_per_leg).
    # We mirror this in two steps: per-leg overrides first (below), then
    # overall (further below, after the per-leg loop).
    overall_sl_value = _maybe_float(payload.get("overall_sl_value"))
    overall_target_value = _maybe_float(payload.get("overall_target_value"))
    has_overall = (overall_sl_value is not None and overall_sl_value != 0) or (
        overall_target_value is not None and overall_target_value != 0
    )
    overall_sl_type_norm = _norm_overall_type(payload.get("overall_sl_type"))
    overall_target_type_norm = _norm_overall_type(payload.get("overall_target_type"))

    # Build {trade_id → list of (override_exit_date | None)} from SL checks.
    overrides: Dict[int, List[Optional[str]]] = {}
    # Per-trade list of {triggered, exit_date, exit_reason} mirroring the
    # Python per_leg_results structure — fed into the overall-SL Rust call.
    per_leg_results_by_trade: Dict[int, List[Dict[str, Any]]] = {}
    for trade_id, legs in by_trade.items():
        # Sort by leg_id to keep deterministic order.
        legs.sort(key=lambda r: r["leg_id"])
        # Build leg-config list for Rust SL function.
        first_leg = legs[0]
        leg_dicts: List[Dict[str, Any]] = []
        for row in legs:
            leg_src = legs_src[row["leg_id"] - 1] if 0 <= row["leg_id"] - 1 < len(legs_src) else {}
            leg_dicts.append(_build_leg_config_for_sl(row, leg_src))
        # Slice 7a: spot adjustment truncates the cycle exit BEFORE the SL scan.
        sl_cycle_exit = _normalize_iso(first_leg["exit_date"])
        spot_adj_clamp = spot_adj_overrides.get(trade_id)
        if spot_adj_clamp and spot_adj_clamp < sl_cycle_exit:
            sl_cycle_exit = spot_adj_clamp
        try:
            sl_results = algotest_native.check_leg_stop_loss_target(
                _normalize_iso(first_leg["entry_date"]),
                sl_cycle_exit,
                _normalize_iso(first_leg["expiry"]),
                float(first_leg.get("entry_spot") or 0.0),
                leg_dicts,
                str(payload.get("index") or "NIFTY").upper(),
                trading_calendar,
                str(square_off_mode or "partial"),
                slippage,
            )
        except Exception as exc:
            logger.warning("[ENGINE_RUST] SL check failed for trade %s: %s", trade_id, exc)
            sl_results = None

        # Map results back per leg.
        legs_overrides: List[Optional[str]] = [None] * len(legs)
        leg_results_for_overall: List[Dict[str, Any]] = []
        if isinstance(sl_results, list):
            for i, leg_result in enumerate(sl_results):
                triggered = False
                new_exit: Any = None
                exit_reason: Any = None
                if isinstance(leg_result, dict):
                    triggered = bool(leg_result.get("triggered") or leg_result.get("exit_reason"))
                    new_exit = leg_result.get("exit_date")
                    exit_reason = leg_result.get("exit_reason")
                elif isinstance(leg_result, (list, tuple)) and len(leg_result) >= 2:
                    triggered = bool(leg_result[0])
                    new_exit = leg_result[1]
                    if len(leg_result) >= 3:
                        exit_reason = leg_result[2]
                if triggered and new_exit:
                    legs_overrides[i] = _normalize_iso(new_exit)
                leg_results_for_overall.append({
                    "triggered": triggered,
                    "exit_date": _normalize_iso(new_exit) if new_exit else None,
                    "exit_reason": exit_reason,
                })
        else:
            leg_results_for_overall = [
                {"triggered": False, "exit_date": None, "exit_reason": None}
                for _ in legs
            ]
        overrides[trade_id] = legs_overrides
        per_leg_results_by_trade[trade_id] = leg_results_for_overall

    # Slice 5: Overall SL/Target — runs AFTER per-leg SL has been computed.
    # Per-trade `overall_overrides[trade_id] = iso_date` if overall triggered.
    overall_overrides: Dict[int, str] = {}
    overall_reasons: Dict[int, str] = {}
    if has_overall:
        index_str = str(payload.get("index") or "NIFTY").upper()
        for trade_id, legs in by_trade.items():
            legs.sort(key=lambda r: r["leg_id"])
            first_leg = legs[0]
            # Build leg dicts (option-only — Rust function skips FUTURES rows).
            leg_dicts = [
                _build_leg_dict_for_overall(row, first_leg["expiry"]) for row in legs
            ]
            # Convert overall_sl_value / overall_target_value into the ₹ (or raw)
            # threshold matching Python's compute_overall_*_threshold.
            sl_thresh = (
                _compute_overall_threshold(legs, overall_sl_type_norm, overall_sl_value, "sl")
                if overall_sl_value not in (None, 0)
                else None
            )
            tgt_thresh = (
                _compute_overall_threshold(legs, overall_target_type_norm, overall_target_value, "target")
                if overall_target_value not in (None, 0)
                else None
            )
            if sl_thresh is None and tgt_thresh is None:
                continue
            per_leg_for_call = per_leg_results_by_trade.get(trade_id)
            # Slice 7a: truncate overall SL window by spot adjustment too.
            overall_cycle_exit = _normalize_iso(first_leg["exit_date"])
            spot_adj_clamp = spot_adj_overrides.get(trade_id)
            if spot_adj_clamp and spot_adj_clamp < overall_cycle_exit:
                overall_cycle_exit = spot_adj_clamp
            try:
                result = algotest_native.check_overall_stop_loss_target(
                    _normalize_iso(first_leg["entry_date"]),
                    overall_cycle_exit,
                    _normalize_iso(first_leg["expiry"]),
                    leg_dicts,
                    index_str,
                    trading_calendar,
                    None if sl_thresh is None else float(sl_thresh),
                    None if tgt_thresh is None else float(tgt_thresh),
                    per_leg_for_call,
                    overall_sl_type_norm,
                    overall_target_type_norm,
                    slippage,
                )
            except Exception as exc:
                logger.warning("[ENGINE_RUST] Overall SL check failed for trade %s: %s", trade_id, exc)
                continue
            if isinstance(result, dict):
                trig_date = result.get("exit_date")
                if trig_date:
                    overall_overrides[trade_id] = _normalize_iso(trig_date)
                    overall_reasons[trade_id] = str(result.get("exit_reason") or "OVERALL_SL").upper()

    # Slice 6: Re-entry on SL / Target (RE_ASAP, ATM only).
    # For each triggered leg whose leg_src has reEntryOnSL or reEntryOnTarget
    # with mode=RE_ASAP, build a chain of re-entry specs at the trigger date(s).
    # Strike is re-resolved at each re-entry as the fresh ATM. The re-entry
    # leg's own SL is checked so it can exit early; cascading re-entries up to
    # the configured `count` budget are supported.
    #
    # If we encounter ANY case the orchestrator can't handle (non-ATM strike,
    # _REV mode, lazy leg, etc.), we return None so the caller falls back to
    # the Python engine — never produce wrong numbers.
    reentry_specs: List[Dict[str, Any]] = []
    reentry_reason_map: Dict[Tuple[int, int, str], str] = {}  # (trade_id, leg_id, entry_date) → reason
    index_str = str(payload.get("index") or "NIFTY").upper()
    for trade_id, legs in by_trade.items():
        legs.sort(key=lambda r: r["leg_id"])
        leg_results = per_leg_results_by_trade.get(trade_id, [])
        overall_date = overall_overrides.get(trade_id)
        for i, leg in enumerate(legs):
            leg_src = legs_src[leg["leg_id"] - 1] if 0 <= leg["leg_id"] - 1 < len(legs_src) else {}
            sl_cfg = leg_src.get("reEntryOnSL") if isinstance(leg_src.get("reEntryOnSL"), dict) else None
            tgt_cfg = leg_src.get("reEntryOnTarget") if isinstance(leg_src.get("reEntryOnTarget"), dict) else None
            if not sl_cfg and not tgt_cfg:
                continue
            # RE_ASAP, RE_ASAP_REV, LAZY_LEG, RE_MOMENTUM supported. Anything else → fall back.
            for cfg in (sl_cfg, tgt_cfg):
                if cfg:
                    _mode = (cfg.get("mode") or "RE_ASAP").upper()
                    if _mode not in ("RE_ASAP", "RE_ASAP_REV", "LAZY_LEG", "RE_MOMENTUM", "RE_MOMENTUM_REV"):
                        return None
            # Only non-premium strike modes (ATM/ITMn/OTMn/pct_of_atm) supported.
            if not _supports_reentry_strike(leg_src):
                return None
            result = leg_results[i] if i < len(leg_results) else {}
            if not result.get("triggered"):
                continue
            # If overall SL fires BEFORE the per-leg trigger, the leg is already
            # closed by overall — no re-entry. Python mirrors this implicitly via
            # _apply_overall_sl_to_per_leg.
            trig_date = _normalize_iso(result.get("exit_date"))
            if not trig_date:
                continue
            if overall_date is not None and overall_date <= trig_date:
                continue

            cycle_exit = leg["exit_date"]  # scheduled exit is the cycle deadline
            # Slice 7a: spot adjustment clamps the cycle exit for re-entry too.
            spot_adj_clamp = spot_adj_overrides.get(trade_id)
            if spot_adj_clamp and spot_adj_clamp < cycle_exit:
                cycle_exit = spot_adj_clamp
            max_sl = int((sl_cfg or {}).get("count") or 1)
            max_tgt = int((tgt_cfg or {}).get("count") or 1)
            sl_used = 0
            tgt_used = 0
            current_trig = trig_date
            current_reason = (result.get("exit_reason") or "").upper()
            strike_interval = float(
                leg_src.get("strike_interval")
                or _STRIKE_INTERVALS.get(index_str, 50.0)
            )
            slippage_val = float(leg["slippage_pct"])
            parent_expiry = _normalize_iso(leg["expiry"])
            # RE_ASAP_REV: flip position on re-entry (sell becomes buy and vice versa).
            re_mode = (
                str((sl_cfg or tgt_cfg or {}).get("mode") or "RE_ASAP").upper()
            )
            is_rev = re_mode.endswith("_REV")
            if is_rev:
                base_position = "BUY" if leg["position"] == "SELL" else "SELL"
            else:
                base_position = leg["position"]

            # Cascading loop: a re-entry can itself SL/Target, triggering another
            # re-entry up to `count`.
            while True:
                if current_reason in _SL_REASONS:
                    if not sl_cfg or sl_used >= max_sl:
                        break
                    sl_used += 1
                elif current_reason in _TGT_REASONS:
                    if not tgt_cfg or tgt_used >= max_tgt:
                        break
                    tgt_used += 1
                else:
                    break

                # Boundary check for all re-entry modes.
                if current_trig >= cycle_exit:
                    break

                # ── RE_MOMENTUM branch ──────────────────────────────────────
                # Mirrors _reentry_mode_trigger_date with mode_base='RE_MOMENTUM'
                # in generic_algotest_engine.py:1742-1828.
                # After the parent leg's SL/Target fires, scan daily closes
                # forward until price crosses back through the trigger level.
                if re_mode in ("RE_MOMENTUM", "RE_MOMENTUM_REV"):
                    # Get the trigger price (option price at the SL date).
                    try:
                        _tp_raw = algotest_native.get_option_price(
                            current_trig, index_str, float(leg["strike"]),
                            leg["option_type"], parent_expiry,
                        )
                        trigger_price = float(_tp_raw) if _tp_raw is not None else None
                    except Exception:
                        trigger_price = None
                    if trigger_price is None or trigger_price <= 0:
                        break  # Can't resolve trigger price — no re-entry
                    orig_position = leg["position"]  # position of the exited leg
                    is_sl_trig = current_reason in _SL_REASONS
                    # Scan dates strictly after current_trig and ≤ cycle_exit.
                    td_sorted_local = sorted(trading_days)
                    momentum_date: Optional[str] = None
                    for _cd in td_sorted_local:
                        if _cd <= current_trig or _cd > cycle_exit:
                            continue
                        try:
                            _cp = algotest_native.get_option_price(
                                _cd, index_str, float(leg["strike"]),
                                leg["option_type"], parent_expiry,
                            )
                            _cp = float(_cp) if _cp is not None else None
                        except Exception:
                            _cp = None
                        if _cp is None or _cp <= 0:
                            continue
                        # _hit_momentum logic from Python engine line 1803-1807:
                        if is_sl_trig:
                            hit = _cp >= trigger_price if orig_position == "SELL" else _cp <= trigger_price
                        else:
                            hit = _cp <= trigger_price if orig_position == "SELL" else _cp >= trigger_price
                        if hit:
                            momentum_date = _cd
                            break
                    if momentum_date is None:
                        break  # No momentum signal — no re-entry
                    current_trig = momentum_date  # Enter on momentum date, not trigger date

                # ── LAZY_LEG branch ─────────────────────────────────────────
                # Mirrors _execute_lazy_leg in generic_algotest_engine.py:1498-1700.
                # Creates a different option leg from lazyLegConfig on SL/Target trigger.
                if re_mode == "LAZY_LEG":
                    lazy_cfg_src = sl_cfg if current_reason in _SL_REASONS else tgt_cfg
                    lazy_leg_config = (lazy_cfg_src or {}).get("lazyLegConfig") or (lazy_cfg_src or {}).get("lazy_leg_config")
                    if not lazy_leg_config:
                        break  # No config — same as Python engine's skip
                    lazy_opt_type = str(lazy_leg_config.get("option_type") or "CE").upper()
                    if lazy_opt_type in ("CALL", "C"):
                        lazy_opt_type = "CE"
                    elif lazy_opt_type in ("PUT", "P"):
                        lazy_opt_type = "PE"
                    lazy_position = str(lazy_leg_config.get("position") or "SELL").upper()
                    lazy_lots = int(lazy_leg_config.get("lots") or lazy_leg_config.get("lot") or 1)
                    # Resolve lazy leg's option expiry. WEEKLY = parent expiry; NEXT_WEEKLY = next.
                    lazy_expiry_raw = str(lazy_leg_config.get("expiry") or "WEEKLY").upper()
                    if lazy_expiry_raw in _NEXT_EXPIRY_TYPES:
                        lazy_expiry = next(
                            (e for e in _sorted_expiries if e > parent_expiry), parent_expiry
                        )
                    else:
                        lazy_expiry = parent_expiry
                    lazy_spot = spot_by_date.get(current_trig)
                    if not lazy_spot:
                        break
                    lazy_strike = _compute_strike_for_leg_python(
                        lazy_leg_config, float(lazy_spot), strike_interval,
                        entry_date=current_trig, expiry=lazy_expiry, index=index_str,
                    )
                    if lazy_strike is None:
                        return None  # Strike unresolvable — Python fallback
                    lazy_spec = {
                        "trade_id": leg["trade_id"],
                        "leg_id": 9000 + sl_used + tgt_used,
                        "index": index_str,
                        "entry_date": current_trig,
                        "exit_date": cycle_exit,
                        "expiry": lazy_expiry,
                        "strike": lazy_strike,
                        "option_type": lazy_opt_type,
                        "position": lazy_position,
                        "lots": lazy_lots,
                        "lot_size": leg["lot_size"],
                        "slippage_pct": slippage_val,
                    }
                    try:
                        priced_lazy = algotest_native.simulate_trades_batch([lazy_spec])
                    except Exception as exc:
                        logger.warning("[ENGINE_RUST] lazy leg simulate failed: %s", exc)
                        return None
                    if not priced_lazy:
                        break
                    priced_lazy_row = priced_lazy[0]
                    # Check lazy leg's own SL/Target (lazyLegConfig may have stopLoss, etc.)
                    lazy_sl_cfg = _build_leg_config_for_sl(priced_lazy_row, lazy_leg_config)
                    try:
                        lazy_sl_res = algotest_native.check_leg_stop_loss_target(
                            _normalize_iso(current_trig),
                            _normalize_iso(cycle_exit),
                            lazy_expiry,
                            float(priced_lazy_row.get("entry_spot") or lazy_spot),
                            [lazy_sl_cfg],
                            index_str,
                            trading_calendar,
                            str(square_off_mode or "partial"),
                            slippage,
                        )
                    except Exception as exc:
                        logger.warning("[ENGINE_RUST] lazy leg SL check failed: %s", exc)
                        return None
                    lazy_exit = cycle_exit
                    lazy_reason = "EXPIRY"
                    if isinstance(lazy_sl_res, list) and lazy_sl_res:
                        r0 = lazy_sl_res[0]
                        if isinstance(r0, dict) and r0.get("triggered"):
                            lazy_exit = _normalize_iso(r0.get("exit_date") or cycle_exit)
                            lazy_reason = (r0.get("exit_reason") or "").upper()
                    if overall_date is not None and lazy_exit >= overall_date:
                        lazy_exit = overall_date
                        lazy_reason = overall_reasons.get(trade_id, "OVERALL_SL")
                    lazy_spec["exit_date"] = lazy_exit
                    lazy_spec["_entry_date_key"] = str(current_trig)
                    reentry_specs.append(lazy_spec)
                    reentry_reason_map[(int(leg["trade_id"]), int(lazy_spec["leg_id"]), str(current_trig))] = lazy_reason or "EXPIRY"
                    break  # Lazy leg doesn't cascade in the outer re-entry loop

                # ── RE_ASAP / RE_ASAP_REV branch ─────────────────────────────
                # Spot at re-entry date must be available to compute fresh strike.
                spot = spot_by_date.get(current_trig)
                if spot is None:
                    break
                new_strike = _compute_strike_for_leg_python(
                    leg_src, float(spot), strike_interval,
                    entry_date=current_trig, expiry=parent_expiry, index=index_str,
                )
                if new_strike is None:
                    return None  # Strike not resolvable — fall back to Python

                # Build a single-leg spec for the re-entry and price its entry.
                re_spec = {
                    "trade_id": leg["trade_id"],
                    "leg_id": leg["leg_id"],
                    "index": leg["index"],
                    "entry_date": current_trig,
                    "exit_date": cycle_exit,
                    "expiry": parent_expiry,
                    "strike": new_strike,
                    "option_type": leg["option_type"],
                    "position": base_position,
                    "lots": leg["lots"],
                    "lot_size": leg["lot_size"],
                    "slippage_pct": slippage_val,
                }
                try:
                    priced_re = algotest_native.simulate_trades_batch([re_spec])
                except Exception as exc:
                    logger.warning("[ENGINE_RUST] re-entry simulate failed: %s", exc)
                    return None
                if not priced_re:
                    break
                priced_re_row = priced_re[0]

                # Run SL/Target/Trail check on the re-entry leg.
                sl_leg_cfg = _build_leg_config_for_sl(priced_re_row, leg_src)
                try:
                    re_sl_res = algotest_native.check_leg_stop_loss_target(
                        _normalize_iso(current_trig),
                        _normalize_iso(cycle_exit),
                        parent_expiry,
                        float(priced_re_row.get("entry_spot") or spot),
                        [sl_leg_cfg],
                        index_str,
                        trading_calendar,
                        str(square_off_mode or "partial"),
                        slippage,
                    )
                except Exception as exc:
                    logger.warning("[ENGINE_RUST] re-entry SL check failed: %s", exc)
                    return None
                re_exit = cycle_exit
                re_reason = "EXPIRY"
                if isinstance(re_sl_res, list) and re_sl_res:
                    r0 = re_sl_res[0]
                    if isinstance(r0, dict) and r0.get("triggered"):
                        re_exit = _normalize_iso(r0.get("exit_date") or cycle_exit)
                        re_reason = (r0.get("exit_reason") or "").upper()

                # If overall SL fires before the re-entry's own exit, clamp.
                if overall_date is not None and re_exit >= overall_date:
                    re_exit = overall_date
                    re_reason = overall_reasons.get(trade_id, "OVERALL_SL")

                re_spec["exit_date"] = re_exit
                re_spec["_entry_date_key"] = str(current_trig)
                reentry_specs.append(re_spec)
                reentry_reason_map[(int(leg["trade_id"]), int(leg["leg_id"]), str(current_trig))] = re_reason or "EXPIRY"

                # Cascade: if this re-entry also SL'd/TP'd AND budget remains,
                # loop. Otherwise stop.
                current_trig = re_exit
                current_reason = re_reason

    # Step 4: build a NEW spec list with adjusted exit_dates for triggered legs.
    # Then apply Python-engine overlap prevention before re-pricing.
    #
    # Note on overlap key: Python's engine uses the SCHEDULED exit_date (not
    # the SL-adjusted actual exit) as `prev_exit_ts` — see
    # engines/generic_algotest_engine.py:3614,3642. So a trade that exits
    # early via SL/Target still blocks the next candidate as if it ran to
    # scheduled exit. Mirror that exactly.
    adjusted_specs: List[Dict[str, Any]] = []
    adjusted_reason_by_date: Dict[Tuple[int, int, str], str] = {}  # (trade_id, leg_id, entry_date) → exit_reason
    trade_scheduled_exit: Dict[int, str] = {}  # trade_id → max scheduled leg exit
    for trade_id, legs in by_trade.items():
        legs.sort(key=lambda r: r["leg_id"])
        leg_overrides = overrides.get(trade_id, [None] * len(legs))
        leg_results_for_reason = per_leg_results_by_trade.get(trade_id, [])
        overall_date = overall_overrides.get(trade_id)
        latest_scheduled = ""
        for i, leg in enumerate(legs):
            override = leg_overrides[i]
            final_exit = override or leg["exit_date"]
            # Determine exit reason (cascades in same order as final_exit)
            if override:
                lr = leg_results_for_reason[i] if i < len(leg_results_for_reason) else {}
                reason = str(lr.get("exit_reason") or "SL").upper() or "SL"
            else:
                reason = "EXPIRY"
            # Slice 4b: SL-with-Buffer. The engine's exit_price_override IS
            # honored now (_recalc_leg_pnl is skipped when it's set). We stash
            # the override here keyed by (trade_id, leg_id, date) so the
            # downstream re-pricing step can swap it in.
            slb = slb_overrides.get((leg["trade_id"], leg["leg_id"]))
            if slb is not None:
                slb_date, _slb_price = slb
                if not override or slb_date < override:
                    final_exit = slb_date
                    reason = "SL_WITH_BUFFER"
            # Slice 5: Overall SL/Target. Matches _apply_overall_sl_to_per_leg —
            # overall trigger overrides leg whose current exit_date is on or
            # after the overall trigger date. Earlier per-leg exits win.
            if overall_date is not None and final_exit >= overall_date:
                final_exit = overall_date
                reason = overall_reasons.get(trade_id, "OVERALL_SL")
            # Slice 7a: Spot adjustment exit always clamps the final exit when
            # the SL/Overall date is later than the spot-adj trigger.
            spot_adj_clamp = spot_adj_overrides.get(trade_id)
            if spot_adj_clamp and final_exit >= spot_adj_clamp:
                final_exit = spot_adj_clamp
                reason = spot_adj_reasons.get(trade_id, "SPOT_ADJ")
            adjusted_reason_by_date[(int(leg["trade_id"]), int(leg["leg_id"]), str(leg["entry_date"]))] = reason
            sched_exit = leg["exit_date"]
            if sched_exit > latest_scheduled:
                latest_scheduled = sched_exit
            adjusted_specs.append({
                "trade_id": leg["trade_id"],
                "leg_id": leg["leg_id"],
                "index": leg["index"],
                "entry_date": leg["entry_date"],
                "exit_date": final_exit,
                "expiry": leg["expiry"],
                "strike": leg["strike"],
                "option_type": leg["option_type"],
                "position": leg["position"],
                "lots": leg["lots"],
                "lot_size": leg["lot_size"],
                "slippage_pct": leg["slippage_pct"],
            })
        trade_scheduled_exit[trade_id] = latest_scheduled

    # Step 5: Overlap prevention — mirrors engines/generic_algotest_engine.py:3680
    # If a trade's scheduled entry_date is on-or-before the previous trade's
    # ACTUAL exit (which may be earlier than scheduled due to SL/Target/Trail
    # firing), skip the trade.
    # Note: fixed_late_entry is a no-op in the Python engine (py:3747) — accepted
    # for backwards compat but ignored here too.

    # Walk trades chronologically.
    trade_ids_sorted = sorted(
        by_trade.keys(),
        key=lambda tid: by_trade[tid][0]["entry_date"],
    )
    kept_trades: set = set()
    prev_exit: Optional[str] = None
    for tid in trade_ids_sorted:
        entry_date = by_trade[tid][0]["entry_date"]
        if prev_exit is not None and entry_date < prev_exit:
            # Strict overlap (entry before prev scheduled exit). Same-day
            # chaining (entry == prev_exit) is allowed — mirrors Python's
            # `entry_ts < _dte_last_exit` at generic_algotest_engine.py:4016.
            continue
        kept_trades.add(tid)
        prev_exit = trade_scheduled_exit[tid]

    # Filter adjusted_specs to only kept trades.
    adjusted_specs = [s for s in adjusted_specs if s["trade_id"] in kept_trades]
    # Slice 6: re-entry specs piggyback on the parent trade_id, so they survive
    # the overlap filter exactly when the parent does.
    adjusted_specs.extend(s for s in reentry_specs if s["trade_id"] in kept_trades)
    if not adjusted_specs:
        return []

    final_priced = list(algotest_native.simulate_trades_batch(adjusted_specs))

    # Inject exit_reason into each priced row. Priority (highest wins):
    # spot_adj > overall > SLB > per-leg SL/Target > EXPIRY.
    # Re-entry rows are identified by (trade_id, leg_id, entry_date) triplet.
    for row in final_priced:
        key = (int(row.get("trade_id") or 0), int(row.get("leg_id") or 1), str(row.get("entry_date") or ""))
        row["exit_reason"] = (
            reentry_reason_map.get(key)
            or adjusted_reason_by_date.get(key)
            or "EXPIRY"
        )

    # SL-with-Buffer post-process: when a leg's final exit date matches the
    # buffer trigger date, swap the close-based exit price for the buffer
    # override (capped at day high/low in Rust). Mirrors the Python flow where
    # step 8C-2 writes exit_price_override into raw/market exit premium and
    # `_recalc_leg_pnl` is skipped when an override exists.
    if slb_overrides:
        for row in final_priced:
            key = (row.get("trade_id"), row.get("leg_id"))
            slb = slb_overrides.get(key)
            if slb is None:
                continue
            slb_date, slb_price = slb
            if _normalize_iso(row.get("exit_date")) != _normalize_iso(slb_date):
                continue
            position = str(row.get("position") or "SELL").upper()
            slip = float(row.get("slippage_pct") or 0.0)
            # Mirror _apply_slippage(side='exit'): SELL exit pays UP, BUY exit pays DOWN.
            if slip > 0:
                factor = (1.0 + slip / 100.0) if position == "SELL" else (1.0 - slip / 100.0)
                adjusted_exit = max(float(slb_price) * factor, 0.0)
            else:
                adjusted_exit = float(slb_price)
            adjusted_exit = round(adjusted_exit, 2)
            row["raw_exit_price"] = round(float(slb_price), 4)
            row["exit_price"] = round(float(adjusted_exit), 4)
            entry_px = float(row.get("entry_price") or 0.0)
            # net_pnl is in PREMIUM POINTS, matching simulate_trades_batch (no qty multiply).
            per_leg_pnl_points = (entry_px - adjusted_exit) if position == "SELL" else (adjusted_exit - entry_px)
            row["net_pnl"] = round(float(per_leg_pnl_points), 4)
    return final_priced
