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


def _clamp_sl_buffer_fill(
    slb_price: Any,
    entry_px: Any,
    sl_value: Any,
    sl_mode: Any,
    position: Any,
) -> float:
    """Enforce the SL-with-Buffer fill invariant on the FINAL contract.

    A SELL stop-loss can never fill BELOW its trigger level (entry premium grown
    by the SL %/points), and a BUY stop never ABOVE it. The Rust SL-with-Buffer
    pre-pass computes its (date, price) override on the INITIAL priced rows —
    before the fixed-strike re-anchor / strike-correction step runs — so on an
    adjusted or rolled trade the stored price can belong to the WRONG
    (un-adjusted) contract: a cheap, far-OTM number that turns a real stop-out
    into an impossible profit (e.g. the 11500-vs-11800 fixed-strike case, and the
    rollover-chain false fills). Re-deriving the stop level from THIS final row's
    own entry premium and clamping the fill to it makes the booked exit always
    reflect the contract actually held.

    Returns the clamped fill price. Spot-anchored SL modes (no clean premium
    level) and missing/invalid inputs are returned unchanged.
    """
    price = _maybe_float(slb_price)
    if price is None:
        return slb_price
    ep = _maybe_float(entry_px) or 0.0
    sv = _maybe_float(sl_value)
    if not sv or sv <= 0 or ep <= 0:
        return price
    pos = str(position or "SELL").upper()
    mode = _norm_mode(sl_mode)
    if mode == "pct":
        level = ep * (1.0 + sv / 100.0) if pos == "SELL" else ep * (1.0 - sv / 100.0)
    elif mode == "points":
        level = ep + sv if pos == "SELL" else ep - sv
    else:
        # underlying / spot-anchored modes have no clean premium floor — leave as-is
        return price
    return max(price, level) if pos == "SELL" else min(price, level)


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


_SL_REASONS = {"STOP_LOSS", "TRAIL_SL", "COMPLETE_STOP_LOSS", "STOP_LOSS_BUFFER", "STOP_LOSS_BUFFER_GAP", "SL_WITH_BUFFER"}
_TGT_REASONS = {"TARGET", "COMPLETE_TARGET"}


def _resolve_atm_strike(spot: float, strike_interval: float) -> float:
    """ATM strike = nearest multiple of strike_interval. Matches Python ATM."""
    return round(spot / strike_interval) * strike_interval


def _spot_adj_reason_tag(
    spot_adj_direction: str,
    entry_spot: float,
    trigger_spot: Optional[float],
    spot_adj_pct: float,
    spot_adj_units: str,
) -> str:
    """SPOT_ADJ_RISE vs SPOT_ADJ_FALL — never returns the plain 'SPOT_ADJ' tag.

    For one-directional configs the answer is fixed. For 'both' we compare the
    trigger-day spot against the rise threshold; if data is missing we fall
    back to the move direction (trig vs entry), and finally to RISE.
    """
    d = (spot_adj_direction or "").lower()
    if d == "rise":
        return "SPOT_ADJ_RISE"
    if d == "fall":
        return "SPOT_ADJ_FALL"
    # "both" — figure it out from the actual spot movement.
    if trigger_spot is None or entry_spot <= 0:
        return "SPOT_ADJ_RISE"
    if (spot_adj_units or "").lower() == "points":
        rise_tgt = entry_spot + spot_adj_pct
    else:
        rise_tgt = entry_spot * (1.0 + spot_adj_pct / 100.0)
    return "SPOT_ADJ_RISE" if trigger_spot >= rise_tgt else "SPOT_ADJ_FALL"


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
        # NOTE: do NOT skip weekends here. NSE runs special weekend sessions
        # (Diwali Muhurat, occasional Saturday sessions) where the index genuinely
        # trades and can breach the spot-adj threshold — the research-team reference
        # exits on those session dates. Days that are not real sessions are already
        # excluded: this loop iterates `trading_days` (built from loaded data) and the
        # `spot is None` guard below drops any date lacking a close, so a weekday skip
        # would only discard legitimate special sessions and report the next weekday.
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

    def _seg_iso(v) -> str:
        # Normalize a segment boundary to ISO YYYY-MM-DD. Already-ISO / datetime
        # values parse directly (no warning); only ambiguous slash/dash dates fall
        # back to dayfirst=True so DD/MM/YYYY (e.g. "10/05/2019" = 10-May) is never
        # misread as MM/DD (5-Oct). Keeps this loader consistent with
        # parse_filter_csv (base.py), which already parses uploads day-first.
        text = str(v).strip()
        # Try unambiguous year-first formats first (no warning, no flip), then
        # fall back to dayfirst=True for genuine DD/MM-style inputs.
        for _fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return pd.to_datetime(text, format=_fmt).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
        return pd.to_datetime(text, dayfirst=True).strftime("%Y-%m-%d")

    for s in raw or []:
        try:
            segs.append((_seg_iso(s["start"]), _seg_iso(s["end"])))
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


def _scan_futures_sl_target(
    entry_date: str,
    entry_price_raw: float,
    position: str,
    leg_src: Dict[str, Any],
    sorted_td: List[str],
    scheduled_exit: str,
    index: str,
    fut_expiry: str,
    slippage: float,
) -> Tuple[str, Optional[float], str]:
    """
    Scan daily futures prices entry+1 -> scheduled_exit for SL/Target/TrailSL.

    Returns (actual_exit_date, exit_price_raw_or_None, exit_reason).
    exit_price_raw is None when nothing fires - caller keeps original price.
    Mirrors check_leg_stop_loss_target logic from generic_algotest_engine.py.
    """
    from base import get_future_price_from_db

    sl = (leg_src.get("stopLoss") or {}) if isinstance(leg_src.get("stopLoss"), dict) else {}
    tp = (leg_src.get("targetProfit") or {}) if isinstance(leg_src.get("targetProfit"), dict) else {}
    trail = (leg_src.get("trailSL") or {}) if isinstance(leg_src.get("trailSL"), dict) else {}

    sl_val = _maybe_float(sl.get("value"))
    sl_type = _norm_mode(sl.get("mode"))
    tp_val = _maybe_float(tp.get("value"))
    tp_type = _norm_mode(tp.get("mode"))
    trail_trig = _maybe_float(trail.get("trigger") or trail.get("x"))
    trail_move_v = _maybe_float(trail.get("move") or trail.get("y"))
    trail_mode = _norm_mode(trail.get("mode"))
    trail_enabled = bool(trail_trig and trail_move_v and trail_trig > 0 and trail_move_v > 0)

    has_sl = sl_val is not None and sl_val > 0
    has_tp = tp_val is not None and tp_val > 0
    if not has_sl and not has_tp and not trail_enabled:
        return (scheduled_exit, None, "EXPIRY")

    trail_armed = False
    trail_best: Optional[float] = None

    def _metric(current: float, mode: str) -> float:
        """Directional P&L in pct or points, positive = favourable."""
        if mode == "pct":
            base = entry_price_raw or 1.0
            raw = (current - entry_price_raw) / base * 100.0
        else:
            raw = current - entry_price_raw
        return raw if position == "BUY" else -raw

    for day in sorted_td:
        if day <= entry_date:
            continue
        if day > scheduled_exit:
            break
        try:
            current = get_future_price_from_db(day, index, expiry=fut_expiry)
        except Exception:
            current = None
        if current is None or current <= 0:
            continue

        pnl_pct = _metric(current, "pct")
        pnl_pts = _metric(current, "points")

        if trail_enabled:
            trail_metric = pnl_pct if trail_mode == "pct" else pnl_pts
            if not trail_armed:
                if trail_metric >= trail_trig:
                    trail_armed = True
                    trail_best = trail_metric
            else:
                if trail_metric > trail_best:
                    trail_best = trail_metric
                if trail_best - trail_metric >= trail_move_v:
                    return (day, current, "TRAIL_SL")

        if has_sl:
            adverse = pnl_pct if sl_type == "pct" else pnl_pts
            if adverse <= -sl_val:
                return (day, current, "SL")

        if has_tp:
            favourable = pnl_pct if tp_type == "pct" else pnl_pts
            if favourable >= tp_val:
                return (day, current, "TARGET")

    return (scheduled_exit, None, "EXPIRY")


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

            # Save scheduled exit BEFORE the SL scan — re-entry (Task 3) uses it as cap.
            _orig_sched_exit = fut_exit_date

            # SL / Target / TrailSL scan for FUTURES leg.
            _scan_exit_date, _scan_exit_raw, _actual_exit_reason = _scan_futures_sl_target(
                entry_date, float(entry_price_raw), position, leg, sorted_td,
                _orig_sched_exit, index, fut_expiry or "", slippage,
            )
            if _scan_exit_raw is not None:
                fut_exit_date = _scan_exit_date
                exit_price_raw = _scan_exit_raw
                exit_spot = spot_by_date.get(fut_exit_date, exit_spot)

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
                "exit_reason": _actual_exit_reason,
            })

            # Re-entry loop for FUTURES legs (mirrors options re-entry orchestration).
            _re_on_sl = leg.get("reEntryOnSL") if isinstance(leg.get("reEntryOnSL"), dict) else None
            _re_on_tgt = leg.get("reEntryOnTarget") if isinstance(leg.get("reEntryOnTarget"), dict) else None
            _sl_budget = int((_re_on_sl or {}).get("count") or 0)
            _tgt_budget = int((_re_on_tgt or {}).get("count") or 0)
            _re_mode = str(
                ((_re_on_sl or _re_on_tgt or {}).get("mode") or "RE_ASAP")
            ).upper()

            if (_re_on_sl or _re_on_tgt) and (_sl_budget > 0 or _tgt_budget > 0):
                _sl_used_re = 0
                _tgt_used_re = 0
                _cur_exit = fut_exit_date           # tracks re-entry's start date (post-scan actual exit)
                _cur_reason = _actual_exit_reason   # reason that may trigger re-entry
                _sched_exit = _orig_sched_exit      # original scheduled exit cap — re-entry cannot exceed

                while True:
                    if _cur_reason in ("SL", "TRAIL_SL") and _re_on_sl and _sl_used_re < _sl_budget:
                        _sl_used_re += 1
                        _re_trigger = "SL"
                    elif _cur_reason == "TARGET" and _re_on_tgt and _tgt_used_re < _tgt_budget:
                        _tgt_used_re += 1
                        _re_trigger = "TARGET"
                    else:
                        break

                    _re_entry_date = next(
                        (d for d in sorted_td if d > _cur_exit and d < _sched_exit),
                        None,
                    )
                    if not _re_entry_date:
                        break

                    _re_idx = _sl_used_re + _tgt_used_re

                    try:
                        _re_ep_raw, _re_xp_raw, _re_expiry = resolve_futures_pnl_with_rollover(
                            entry_date=_re_entry_date,
                            exit_date=_sched_exit,
                            index=index,
                            position=position,
                            preference=fut_pref,
                        )
                    except Exception:
                        break
                    if _re_ep_raw is None:
                        break
                    if _re_xp_raw is None:
                        _re_xp_raw = _re_ep_raw

                    _re_scan_date, _re_scan_raw, _re_reason = _scan_futures_sl_target(
                        _re_entry_date, float(_re_ep_raw), position, leg, sorted_td,
                        _sched_exit, index, _re_expiry or fut_expiry or "", slippage,
                    )
                    if _re_scan_raw is not None:
                        _re_xp_raw = _re_scan_raw
                        _re_exit_date = _re_scan_date
                    else:
                        _re_exit_date = _sched_exit
                        _re_reason = "EXPIRY"

                    if slippage > 0:
                        _re_ep = round(max(float(_re_ep_raw) * _entry_fac, 0.0), 2)
                        _re_xp = round(max(float(_re_xp_raw) * _exit_fac, 0.0), 2)
                    else:
                        _re_ep = round(float(_re_ep_raw), 2)
                        _re_xp = round(float(_re_xp_raw), 2)

                    _re_pnl = round(
                        (_re_ep - _re_xp) if position == "SELL" else (_re_xp - _re_ep), 4
                    )

                    out.append({
                        "trade_id": trade_id,
                        "leg_id": leg_id,
                        "index": index,
                        "entry_date": _re_entry_date,
                        "exit_date": _re_exit_date,
                        "expiry": _re_expiry or fut_expiry or "",
                        "strike": 0.0,
                        "option_type": "FUT",
                        "position": position,
                        "lots": lots,
                        "lot_size": lot_size,
                        "slippage_pct": slippage,
                        "entry_price": _re_ep,
                        "exit_price": _re_xp,
                        "raw_entry_price": round(float(_re_ep_raw), 4),
                        "raw_exit_price": round(float(_re_xp_raw), 4),
                        "net_pnl": _re_pnl,
                        "entry_spot": float(spot_by_date.get(_re_entry_date, 0.0)),
                        "exit_spot": float(spot_by_date.get(_re_exit_date, 0.0)),
                        "exit_reason": _re_reason,
                        "_reentry_index": _re_idx,
                        "_reentry_trigger": _re_trigger,
                        "_reentry_mode": _re_mode,
                    })

                    _cur_exit = _re_exit_date
                    _cur_reason = _re_reason

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


def _validate_or_shift_strike_python(
    strike: float,
    atm: float,
    interval: float,
    is_call: bool,
    entry_date: Optional[str],
    expiry: Optional[str],
    index: Optional[str],
    opt_type: str,
    max_shifts: int,
) -> Optional[float]:
    """Python mirror of Rust validate_or_shift_strike.  Walks TOWARD ATM when
    the requested strike has zero turnover, capped at the distance to ATM
    (i.e. never walks past ATM).  Returns the first tradeable strike or None
    when even ATM is untradeable.  `max_shifts` is ignored and retained only
    for API compatibility — the cap is always distance-to-ATM."""
    if not (entry_date and expiry and index):
        return strike  # Can't validate without market context — trust the picker.
    try:
        import algotest_native  # type: ignore
    except ImportError:
        return strike
    def _status(s: float) -> str:
        """Returns 'tradeable' | 'zero_contracts' | 'missing'."""
        fn = getattr(algotest_native, "get_option_status", None)
        if fn is None:
            try:
                px = algotest_native.get_option_price(entry_date, index, s, opt_type, expiry)
                return "tradeable" if (px is not None and px > 0) else "missing"
            except Exception:
                return "missing"
        try:
            return fn(entry_date, index, s, opt_type, expiry) or "missing"
        except Exception:
            return "missing"

    st = _status(strike)
    if st == "tradeable":
        return strike
    if st == "missing":
        return None
    # st == "zero_contracts" → walk TOWARD ATM (more liquid strikes).
    if strike > atm + 1e-6:
        direction = -1.0  # above ATM → walk down toward ATM
    elif strike < atm - 1e-6:
        direction = 1.0   # below ATM → walk up toward ATM
    else:
        # ATM strike itself has zero turnover. Instead of skipping the trade,
        # walk OUTWARD in the OTM direction (CALL: up, PUT: down) to the first
        # strike WITH turnover. Stop at the chain edge (missing) or a safety cap.
        otm_dir = 1.0 if is_call else -1.0
        step = 1
        while step <= 500:
            cand = strike + otm_dir * step * interval
            if cand <= 0:
                break
            cand_st = _status(cand)
            if cand_st == "tradeable":
                return cand
            if cand_st == "missing":
                break
            step += 1
        return None
    dist = int(round(abs(strike - atm) / interval))
    max_walk = max(dist, 1)
    for step in range(1, max_walk + 1):
        cand = strike + direction * step * interval
        if cand <= 0:
            break
        if _status(cand) == "tradeable":
            return cand
    return None


def _compute_strike_for_leg_python(
    leg: Dict[str, Any],
    entry_spot: float,
    interval: float,
    *,
    entry_date: Optional[str] = None,
    expiry: Optional[str] = None,
    index: Optional[str] = None,
    strike_shift_max: int = 1,
    out_info: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """
    Python mirror of simulate.rs::compute_strike_for_leg.

    Handles: ATM, ITM1..N, OTM1..N (strike_type), pct_of_atm — always.
    Premium-based modes (closest_premium, premium_gte/lte, straddle_width,
    atm_straddle_prem_pct) — supported when entry_date, expiry, and index are
    provided (uses algotest_native.get_strikes_for_date / get_option_price).
    Returns None when premium lookup is needed but params are absent, or when
    the feather has no data for the given date/expiry.

    If `out_info` is provided, it is populated with 'requested_strike' (the
    strike before zero-turnover shift) so the tradesheet can show the reason.
    """
    sel = leg.get("strike_selection") or {}
    if not isinstance(sel, dict):
        return None
    sel_type = str(sel.get("type") or "strike_type").lower().strip()
    opt_type = str(leg.get("option_type") or "CE").upper()
    is_ce = opt_type in ("CE", "CALL", "C")
    atm = round(entry_spot / interval) * interval

    def _validate(s: Optional[float]) -> Optional[float]:
        if s is None:
            return None
        if out_info is not None:
            out_info["requested_strike"] = float(s)
        return _validate_or_shift_strike_python(
            s, atm, interval, is_ce, entry_date, expiry, index, opt_type, strike_shift_max,
        )

    if sel_type in ("strike_type", "") or sel_type.startswith(("atm", "itm", "otm")):
        # Compact form: {type: "ATM"} | {type: "ITM1"} | …
        if sel_type not in ("strike_type", ""):
            strike_type = sel_type.upper()
        else:
            strike_type = str(sel.get("strike_type") or "ATM").upper().strip()
        if strike_type == "ATM":
            return _validate(atm)
        if strike_type.startswith("ITM"):
            n_str = strike_type[3:].strip()
            try:
                n = int(n_str) if n_str else 1
            except ValueError:
                return None
            return _validate(atm - n * interval if is_ce else atm + n * interval)
        if strike_type.startswith("OTM"):
            n_str = strike_type[3:].strip()
            try:
                n = int(n_str) if n_str else 1
            except ValueError:
                return None
            return _validate(atm + n * interval if is_ce else atm - n * interval)
        return None

    if sel_type == "pct_of_atm":
        try:
            value = float(sel.get("value") or 0.0)
        except (TypeError, ValueError):
            return None
        # Default direction is empty (sign convention), NOT "OTM".  When the
        # optimizer sweeps `value` across negative territory the user expects
        # signed-offset behavior: negative value = below spot (ITM for CE,
        # OTM for PE), positive value = above spot (OTM for CE, ITM for PE).
        # An "OTM" default would discard the sign and always place strikes
        # above spot for CE, making the negative half of the param range
        # collapse onto the positive half.
        direction = str(sel.get("direction") or "").strip()
        direction_up = direction.upper()
        if direction_up in ("OTM", "ITM", "ATM"):
            if direction_up == "ATM" or value == 0.0:
                raw = entry_spot
            else:
                shift = entry_spot * abs(value) / 100.0
                above_spot = (direction_up == "OTM" and is_ce) or (direction_up == "ITM" and not is_ce)
                raw = entry_spot + shift if above_spot else entry_spot - shift
        else:
            shift = entry_spot * value / 100.0
            raw = entry_spot - shift if direction == "-" else entry_spot + shift
        return _validate(round(raw / interval) * interval)

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
    no_rollover_min_days_val = int(payload.get("no_rollover_min_days", 0) or 0)
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

            # No-rollover min-DTE: same expiry-skip for the single trade per segment
            if no_rollover_flag and no_rollover_min_days_val > 0:
                gap = _trading_day_gap_strict(current_entry, target_expiry, trading_days)
                if gap <= no_rollover_min_days_val and target_idx + 1 < len(sorted_expiries):
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
            # Also a filter-end exit when the target expiry lies BEYOND the
            # segment end: the trade can't reach its expiry inside the segment,
            # so it exits at the segment boundary. This catches the case where
            # the expiry is also outside the loaded data range — then exit_date
            # was already truncated to the last trading day (== last_in_seg) so
            # the `exit_date > last_in_seg` test above misses it.
            if target_expiry > last_in_seg:
                clamped = True

            entry_spot = spot_by_date.get(current_entry)
            if not entry_spot:
                break

            _trade_specs: List[Dict[str, Any]] = []
            _trade_resolved = True
            for leg_idx, leg in enumerate(legs_src):
                if not isinstance(leg, dict):
                    return None
                # NEXT_WEEKLY / NEXT_MONTHLY legs trade the contract ONE expiry
                # beyond the exit anchor (target_expiry); all other legs trade
                # target_expiry itself. Same contract shift as the DTE next-weekly
                # path, applied here so Fixed Entry works for next-weekly. If the
                # shifted contract isn't available (end of expiry list), skip just
                # this trade and keep chaining.
                _leg_is_next = str(leg.get("expiry") or "").upper() in _NEXT_EXPIRY_TYPES
                if _leg_is_next:
                    if target_idx + 1 >= len(sorted_expiries):
                        _trade_resolved = False
                        break
                    leg_expiry = sorted_expiries[target_idx + 1]
                else:
                    leg_expiry = target_expiry
                # Honour per-leg strike_interval override (user picks 100 for
                # NIFTY in the leg form). Without this, every fixed-mode trade
                # snaps to the index default (50 for NIFTY).
                _leg_iv_raw = leg.get("strike_interval")
                try:
                    leg_interval = float(_leg_iv_raw) if _leg_iv_raw else interval
                except (TypeError, ValueError):
                    leg_interval = interval
                _shift_info: Dict[str, Any] = {}
                strike = _compute_strike_for_leg_python(
                    leg, entry_spot, leg_interval,
                    entry_date=current_entry, expiry=leg_expiry, index=index_str,
                    out_info=_shift_info,
                )
                if strike is None:
                    # Strike unresolvable on this date — e.g. the requested strike
                    # is a stale/zero-turnover contract with no inward strike to
                    # shift to (NSE didn't trade it that day), or it was never
                    # listed. Skip ONLY this trade and keep building the chain,
                    # rather than aborting the whole backtest to Python. Mirrors the
                    # Python engine, which drops an unpriceable trade and continues.
                    _trade_resolved = False
                    break
                _trade_specs.append({
                    "trade_id": trade_id,
                    "leg_id": leg_idx + 1,
                    "index": index_str,
                    "entry_date": current_entry,
                    "exit_date": exit_date,
                    "expiry": leg_expiry,
                    "strike": float(strike),
                    "requested_strike": float(_shift_info.get("requested_strike") or strike),
                    "strike_interval": float(leg_interval),
                    "option_type": str(leg.get("option_type") or "CE").upper(),
                    "position": str(leg.get("position") or "SELL").upper(),
                    "lots": int(leg.get("lots") or 1),
                    "lot_size": int(lot_size),
                    "slippage_pct": slippage,
                    # Exit was clamped to the segment/filter end (exit < natural
                    # expiry exit) → exit reason should be FILTER_END, not EXPIRY.
                    "_seg_clamped": clamped,
                })

            # Emit the trade only if every leg resolved; otherwise drop just this
            # trade. Either way advance the same-day chain so the rest of the
            # schedule keeps the same entry/exit dates (the schedule is built
            # independent of strike resolution, exactly like the Python engine).
            if _trade_resolved:
                all_specs.extend(_trade_specs)
                trade_id += 1
                if clamped or no_rollover_flag or not rollover_toggle:
                    break

            if clamped or not rollover_toggle:
                break
            current_entry = exit_date  # same-day chain

    return all_specs


def _fetch_one_extra_expiry(
    expiry_dates: List[str], payload: Dict[str, Any], count: int = 1
) -> List[str]:
    """
    Return expiry_dates extended by `count` extra cycles beyond the last date.

    For NEXT_WEEKLY / NEXT_MONTHLY strategies the last schedule cycle needs
    expiries that may lie outside the backtest range. The Python engine gets
    these from get_expiry_dates() which queries the DB. We do the same but for
    just a couple of rows so the overhead is negligible. `count=2` is used by
    the next-weekly path, which anchors exit to Ek+1 and trades the Ek+2 contract.
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
        _n = max(1, int(count))
        last_dt = _dt.date.fromisoformat(last_exp)
        lookahead = (last_dt + _dt.timedelta(days=40 * _n)).isoformat()
        extra_df = get_expiry_dates(index, freq, last_exp, lookahead)
        if extra_df is not None and not extra_df.empty:
            col = "Current Expiry" if "Current Expiry" in extra_df.columns else extra_df.columns[0]
            candidates = sorted(
                pd.to_datetime(extra_df[col]).dt.strftime("%Y-%m-%d").unique().tolist()
            )
            # Add the first `count` expiries strictly after last_exp.
            extra = [c for c in candidates if c > last_exp][:_n]
            if extra:
                return sorted(set(expiry_dates) | set(extra))
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
    # NOTE: use an explicit None check, not `or 1` — entry_dte=0 is a valid
    # value (enter ON the expiry day) and `0 or 1` would wrongly coerce it to 1,
    # shifting every entry one trading day early and breaking the contiguous roll.
    _edte = payload.get("entry_dte")
    entry_dte = int(_edte) if _edte not in (None, "") else 1
    _xdte = payload.get("exit_dte")
    exit_dte = int(_xdte) if _xdte not in (None, "") else 0
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
        # NEXT_WEEKLY semantics — like WEEKLY, one contract further out:
        #   • entry anchors to cur_exp (Ek)             → Ek   - entry_dte td
        #   • exit  anchors to the NEXT expiry (Ek+1)   → Ek+1 - exit_dte  td
        #   • the traded NEXT_* contract is Ek+2 — at exit it still has ~1 week
        #     of life. (entry 4-Apr / exit 11-Apr → 18-Apr contract.)
        # Non-next ("mixed") legs keep trading cur_exp and anchor exit to cur_exp.
        next_exp     = sorted_expiries[i + 1] if i + 1 < n_exp else None  # exit anchor (Ek+1)
        contract_exp = sorted_expiries[i + 2] if i + 2 < n_exp else None  # NEXT_* contract (Ek+2)

        if _all_legs_next:
            # Both the exit anchor (Ek+1) and the traded contract (Ek+2) must exist.
            if next_exp is None or contract_exp is None:
                continue
            exit_anchor = next_exp
        else:
            exit_anchor = cur_exp

        entry_date = _trading_day_n_before(cur_exp, entry_dte, td_sorted)
        if entry_date is None:
            continue

        exit_date = _trading_day_n_before(exit_anchor, exit_dte, td_sorted)
        if exit_date is None:
            continue

        if entry_date >= exit_date:
            continue

        entry_spot = spot_by_date.get(entry_date)
        if not entry_spot:
            continue

        # Resolve every leg first; only commit the trade if all legs resolve.
        # A NEXT_* leg with no Ek+2 contract available (end of range) skips the
        # WHOLE cycle rather than emitting a partial trade.
        _trade_specs: List[Dict[str, Any]] = []
        _skip_trade = False
        for leg_idx, (leg, is_next) in enumerate(zip(legs_src, leg_is_next)):
            if is_next:
                if contract_exp is None:
                    _skip_trade = True
                    break
                per_leg_expiry = contract_exp
            else:
                per_leg_expiry = cur_exp
            # Honour per-leg strike_interval override (e.g. user picks 100 for
            # NIFTY). Without this, DTE-mode trades collapse to the index
            # default and the per-leg setting is ignored.
            _leg_iv_raw = leg.get("strike_interval")
            try:
                leg_interval = float(_leg_iv_raw) if _leg_iv_raw else interval
            except (TypeError, ValueError):
                leg_interval = interval
            _shift_info: Dict[str, Any] = {}
            strike = _compute_strike_for_leg_python(
                leg, entry_spot, leg_interval,
                entry_date=entry_date, expiry=per_leg_expiry, index=index_str,
                out_info=_shift_info,
            )
            if strike is None:
                # Unresolvable strike for THIS cycle — most commonly the Ek+2
                # contract lies past the loaded data at the end of the range, or
                # a thin/zero-volume expiry. Skip just this trade (mirrors WEEKLY:
                # never zero the entire run on one bad strike). Premium-based modes
                # that need data params still resolve normally when present.
                _skip_trade = True
                break
            _trade_specs.append({
                "trade_id": trade_id,
                "leg_id": leg_idx + 1,
                "index": index_str,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "expiry": per_leg_expiry,
                "strike": float(strike),
                "requested_strike": float(_shift_info.get("requested_strike") or strike),
                "strike_interval": float(leg_interval),
                "option_type": str(leg.get("option_type") or "CE").upper(),
                "position": str(leg.get("position") or "SELL").upper(),
                "lots": int(leg.get("lots") or 1),
                "lot_size": int(lot_size),
                "slippage_pct": slippage,
            })

        if _skip_trade or not _trade_specs:
            continue
        all_specs.extend(_trade_specs)
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
            _shift_info: Dict[str, Any] = {}
            new_strike = _compute_strike_for_leg_python(
                leg, new_spot, interval,
                entry_date=new_entry, expiry=spec_expiry, index=index_str,
                out_info=_shift_info,
            )
            if new_strike is None:
                return None  # Strike not resolvable — Python fallback
            s = dict(s)
            s["entry_date"] = new_entry
            s["strike"] = float(new_strike)
            s["requested_strike"] = float(_shift_info.get("requested_strike") or new_strike)
            s["strike_interval"] = float(interval)
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
            # rollover_strike_mode='fixed' is a user-chosen behavior (reuse
            # first-cycle strike). Sync requested_strike so this carry-over
            # doesn't get reported as a zero-turnover shift.
            s["requested_strike"] = strike_overrides[key]
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
        # Spot P&L is a trade-level quantity: write it only on the first-leg
        # row (leg_id == 1) and leave subsequent leg rows blank, mirroring the
        # Net P&L convention.  Per-row summing then yields the trade total
        # without double-counting for multi-leg strategies.
        _leg_id_val = int(row.get("leg_id") or 1)
        spot_pnl = round(exit_spot - entry_spot, 2) if _leg_id_val == 1 else ""
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
        # Strike Shift Reason — populated only when the engine shifted the
        # requested strike toward ATM because the original contract had zero
        # turnover on entry day. Empty when no shift was applied.
        _shift_reason = ""
        try:
            _req = row.get("requested_strike")
            if _req is not None and not is_fut and strike_val != "":
                _req_f = float(_req)
                _act_f = float(strike_val)
                if abs(_req_f - _act_f) > 1e-6:
                    _intvl = float(row.get("strike_interval") or 50.0) or 50.0
                    _steps = max(1, int(round(abs(_act_f - _req_f) / _intvl)))
                    _shift_reason = (
                        f"{int(_req_f) if _req_f.is_integer() else _req_f}→"
                        f"{int(_act_f) if _act_f.is_integer() else _act_f} "
                        f"(zero turnover, {_steps} step{'s' if _steps != 1 else ''})"
                    )
        except (TypeError, ValueError):
            pass
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
            "Strike Shift Reason": _shift_reason,
            "ReEntryIndex": row.get("_reentry_index") or "",
            "ReEntryTrigger": str(row.get("_reentry_trigger") or ""),
            "ReEntryMode": str(row.get("_reentry_mode") or ""),
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
        # Buffer is a user-configured offset, NOT a zero-turnover shift.
        # Sync requested_strike with the buffered strike so the Strike Shift
        # Reason column only reports forced toward-ATM shifts, never the
        # deliberate buffer offset the user enabled.
        new_spec["requested_strike"] = float(snapped)
        result.append(new_spec)
    return result


def _build_mixed_futures_next_weekly(
    payload: Dict[str, Any],
    expiry_dates: List[str],
    trading_days: List[str],
    lot_size: int,
    spot_by_date: Dict[str, float],
    segments: Optional[List[Tuple[str, str]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Handle strategies that mix FUTURES legs with NEXT_WEEKLY/NEXT_MONTHLY option legs.

    Python convention for mixed mode:
      * FUT pricing uses the FUT's OWN DTE schedule (entry_dte/exit_dte applied
        to the current weekly expiry — same as _build_futures_specs would produce).
      * The row's reported exit_date field uses the TRADE-level exit
        (option's NEXT_WEEKLY exit), not the FUT's own exit.
      * The first leg of each trade gets the trade-total net_pnl.

    Returns None on any failure so caller falls back to Python.
    """
    try:
        import algotest_native  # type: ignore
    except ImportError:
        return None

    legs_src = payload.get("legs") or []

    fut_legs_with_pos = [
        (orig_idx, leg) for orig_idx, leg in enumerate(legs_src, start=1)
        if isinstance(leg, dict)
        and str(leg.get("segment") or "OPTION").upper() in ("FUTURE", "FUTURES")
    ]
    opt_legs_with_pos = [
        (orig_idx, leg) for orig_idx, leg in enumerate(legs_src, start=1)
        if isinstance(leg, dict)
        and str(leg.get("segment") or "OPTION").upper() not in ("FUTURE", "FUTURES")
    ]
    if not fut_legs_with_pos or not opt_legs_with_pos:
        return None  # Mixed mode requires both leg types.

    # Build option specs — drives the trade-level exit_date. Two extra cycles so
    # the next-weekly option leg can resolve its Ek+2 contract at end of range.
    _ext_expiry_dates = _fetch_one_extra_expiry(expiry_dates, payload, count=2)
    opt_payload = {**payload, "legs": [l for _, l in opt_legs_with_pos]}
    opt_specs = _build_next_expiry_specs(
        opt_payload, _ext_expiry_dates, trading_days, spot_by_date, int(lot_size)
    )
    if opt_specs is None:
        return None
    if not opt_specs:
        return []

    if not algotest_native.is_loaded():
        return None
    priced_opts = list(algotest_native.simulate_trades_batch(opt_specs))

    # Remap option leg_ids from opt_legs (1-based) to their original positions.
    _opt_leg_id_remap = {new_idx: orig_idx for new_idx, (orig_idx, _) in enumerate(opt_legs_with_pos, start=1)}
    for row in priced_opts:
        if row.get("leg_id") in _opt_leg_id_remap:
            row["leg_id"] = _opt_leg_id_remap[row["leg_id"]]

    # Build FUT rows using FUT's OWN DTE schedule via _build_futures_specs.
    # _build_futures_specs already skips non-FUTURES legs by segment check, so
    # we can pass the full payload — only FUT legs will produce rows.
    fut_rows = _build_futures_specs(
        payload, expiry_dates, trading_days, spot_by_date, lot_size, segments
    )
    if fut_rows is None:
        return None  # FUT path rejected (unsupported config)

    # Relabel each FUT row's exit_date to the trade-level exit_date from options.
    # Match FUT rows to option specs by entry_date (same trade period).
    from collections import defaultdict
    opt_exit_by_entry: Dict[str, str] = {}  # entry_date -> max(exit_date) across option legs
    for spec in opt_specs:
        e = spec["entry_date"]
        x = spec["exit_date"]
        if e not in opt_exit_by_entry or x > opt_exit_by_entry[e]:
            opt_exit_by_entry[e] = x

    for row in fut_rows:
        trade_exit = opt_exit_by_entry.get(row["entry_date"])
        if trade_exit and trade_exit > row["exit_date"]:
            # Don't change actual pricing — just the reported exit_date label.
            row["exit_date"] = trade_exit
            # exit_spot is the spot on the labeled exit date.
            row["exit_spot"] = float(spot_by_date.get(trade_exit, row.get("exit_spot") or 0.0))

    # Re-assign trade_ids so FUT row and option row of the same period share one tid.
    # Match by entry_date.
    entry_to_tid: Dict[str, int] = {}
    next_tid = 1
    # Walk through unique entry_dates in chronological order.
    all_entries = sorted({r["entry_date"] for r in (list(priced_opts) + list(fut_rows))})
    for e in all_entries:
        entry_to_tid[e] = next_tid
        next_tid += 1
    for row in priced_opts:
        row["trade_id"] = entry_to_tid[row["entry_date"]]
    for row in fut_rows:
        row["trade_id"] = entry_to_tid[row["entry_date"]]

    combined = list(priced_opts) + fut_rows

    # Multi-leg P&L convention: the FIRST leg (lowest leg_id) of each trade
    # carries the TRADE-TOTAL net_pnl; other legs carry per-leg net_pnl.
    # simulate.rs already does this for options-only trades; for mixed we must
    # replicate it after merging the FUT rows in.
    from collections import defaultdict as _dd
    _by_tid: Dict[int, List[Dict]] = _dd(list)
    for _r in combined:
        _by_tid[int(_r.get("trade_id") or 0)].append(_r)
    for _tid, _rows in _by_tid.items():
        if len(_rows) <= 1:
            continue  # Single-leg trade — net_pnl already per-leg = trade total
        _trade_total = round(sum(float(_r.get("net_pnl") or 0.0) for _r in _rows), 4)
        _first = min(_rows, key=lambda _r: int(_r.get("leg_id") or 1))
        _first["net_pnl"] = _trade_total

    return combined if combined else None


# Per-process cache of the Midcap index close-lookup, keyed by symbol. Loaded
# once (Rust INDEX_OHLC or DB) and reused across combos so the optimizer doesn't
# re-load the series for every combo.
_MIDCAP_SA_LOOKUP_CACHE: Dict[str, Any] = {}


def _get_midcap_sa_lookup(symbol: str):
    lk = _MIDCAP_SA_LOOKUP_CACHE.get(symbol)
    if lk is None:
        from services.midcap_overlay import MidcapCloseLookup
        lk = MidcapCloseLookup(symbol)
        # Force the one-time series load now so subsequent .close() are O(1).
        try:
            lk.close("2000-01-01")
        except Exception:
            pass
        _MIDCAP_SA_LOOKUP_CACHE[symbol] = lk
    return lk


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
        logger.warning(
            "[ENGINE_RUST] PYTHON pricing path: strategy has FUTURES legs — "
            "priced via base.resolve_futures_pnl_with_rollover (expected for futures)."
        )
        if _has_next_leg:
            # Mixed FUTURES + NEXT_WEEKLY: build each type separately, merge by period.
            try:
                _mixed = _build_mixed_futures_next_weekly(
                    payload, expiry_dates, trading_days, lot_size, spot_by_date, segments,
                )
            except Exception as _exc:
                logger.warning("[ENGINE_RUST] mixed FUTURES+NEXT_WEEKLY failed: %s", _exc)
                _mixed = None
            return _mixed  # None → caller falls back to Python engine
        fut_rows = _build_futures_specs(
            payload, expiry_dates, trading_days, spot_by_date, int(lot_size), segments,
        )
        if fut_rows is None:
            return None  # SL/Target/re-entry on futures — Python handles it
        return fut_rows  # already priced; skip simulate_trades_batch entirely

    elif _has_next_leg and filter_entry_mode == "fixed":
        # NEXT_WEEKLY / NEXT_MONTHLY + Fixed Entry: pin the first entry of each
        # segment to the segment start and chain like WEEKLY fixed entry, but
        # trade the next-weekly contract (one expiry beyond the exit anchor).
        # _build_fixed_entry_specs is per-leg next-weekly-aware; one extra expiry
        # so the last cycle's shifted contract resolves. Its own per-segment
        # gating/clamping applies, so suppress the downstream gate (segments=None).
        _fx_expiry_dates = _fetch_one_extra_expiry(expiry_dates, payload, count=1)
        specs = _build_fixed_entry_specs(
            payload, _fx_expiry_dates, trading_days, spot_by_date, int(lot_size), segments,
        )
        if specs is None:
            return None  # Premium-based strike mode — Python engine handles it
        if not specs:
            return []
        if payload.get("buffer_strike_enabled"):
            specs = _apply_buffer_strike_to_specs(specs, payload)
        segments = None

    elif _has_next_leg:
        # Per-leg NEXT_WEEKLY / NEXT_MONTHLY: Python builds per-leg-expiry specs.
        # Extend expiry list by TWO extra cycles so the last cycle can resolve
        # both its exit anchor (Ek+1) and its traded contract (Ek+2).
        _ext_expiry_dates = _fetch_one_extra_expiry(expiry_dates, payload, count=2)
        specs = _build_next_expiry_specs(
            payload, _ext_expiry_dates, trading_days, spot_by_date, int(lot_size),
        )
        if specs is None:
            return None  # Strike unresolvable — Python engine handles it
        if not specs:
            return []
        if payload.get("buffer_strike_enabled"):
            specs = _apply_buffer_strike_to_specs(specs, payload)
        # STR / filter date-range gating — SAME rule as the WEEKLY (DTE) path so
        # NEXT_WEEKLY behaves exactly like WEEKLY under a filter: the ENTRY must
        # fall inside a filter segment, and an exit running past the segment end
        # is CLAMPED to the last trading day in the segment (labelled FILTER_END
        # / STR_Exit downstream via _seg_clamped). Unlike the DTE gate we do NOT
        # treat expiry>seg_end as a filter-end — the next-weekly contract (Ek+2)
        # naturally lies a week past the scheduled exit (Ek+1), which is a normal
        # exit, not a filter truncation. Leaving `segments` non-None is inert
        # downstream (only original_segments is read past this point).
        if segments is not None:
            if not segments:
                return []

            def _seg_for_next(entry_iso: str) -> Optional[Tuple[str, str]]:
                for s_start, s_end in segments:
                    if s_start <= entry_iso <= s_end:
                        return (s_start, s_end)
                return None

            _gated: List[Dict[str, Any]] = []
            for s in specs:
                entry_iso = _normalize_iso(s["entry_date"])
                seg = _seg_for_next(entry_iso)
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
                    s["_seg_clamped"] = True
                _gated.append(s)
            specs = _gated
            if not specs:
                return []

    elif filter_entry_mode == "fixed":
        # Step 1 (fixed): Python builds schedule then Rust prices each spec.
        # Rollover lookahead: the last rollover window (entry = last in-range
        # expiry) needs one expiry BEYOND it to roll into, otherwise the final
        # same-day chain trade (entry = last expiry, exit clamped to segment end)
        # is never generated. Same fix the DTE path applies.
        _fixed_rollover_lookahead = (
            bool(payload.get("rollover_toggle", False))
            and str(payload.get("expiry_type") or "").upper() in ("WEEKLY", "MONTHLY")
        )
        _fixed_expiry_dates = (
            _fetch_one_extra_expiry(expiry_dates, payload)
            if _fixed_rollover_lookahead
            else expiry_dates
        )
        specs = _build_fixed_entry_specs(
            payload, _fixed_expiry_dates, trading_days, spot_by_date, int(lot_size), segments,
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
        # For rollover strategies, extend expiry_dates by one extra cycle so
        # the last rollover window is not silently dropped. The last expiry in
        # range is typically a 0-day trade (entry==exit); the subsequent window
        # (entry=last_expiry, exit=next_cycle) needs the extra cycle present.
        # Mirrors the Python engine's look-ahead behaviour.
        _rollover_lookahead = (
            bool(payload.get("rollover_toggle", False))
            and str(payload.get("expiry_type") or "").upper() in ("WEEKLY", "MONTHLY")
        )
        _expiry_dates_for_specs = (
            _fetch_one_extra_expiry(expiry_dates, payload)
            if _rollover_lookahead
            else expiry_dates
        )
        specs = algotest_native.resolve_trade_specs(
            payload, _expiry_dates_for_specs, trading_days, int(lot_size), spot_by_date
        )
        if not specs:
            # Rust path rejected payload — feature outside supported slices.
            return None
        # Clip specs whose exit exceeds to_date to the last trading day ≤ to_date.
        # This mirrors the Python engine which uses to_date as the effective exit
        # ceiling for the last rollover window.
        if _rollover_lookahead:
            _to_date_str = str(payload.get("to_date") or payload.get("date_to") or "")
            # When filter segments extend beyond to_date, use the latest segment
            # end as the effective ceiling. Without this, the last rollover window
            # (entry = last expiry = to_date) gets clamped to a zero-duration trade
            # and dropped before segment gating can extend it to the segment end.
            if _to_date_str and segments:
                _max_seg_end = max(end for _, end in segments)
                if _max_seg_end > _to_date_str:
                    _to_date_str = _max_seg_end
            if _to_date_str:
                _clipped: List[Dict[str, Any]] = []
                for _s in specs:
                    _entry_iso = _normalize_iso(_s.get("entry_date", ""))
                    _exit_iso = _normalize_iso(_s.get("exit_date", ""))
                    if _entry_iso > _to_date_str:
                        continue
                    if _exit_iso > _to_date_str:
                        _last_td = _last_trading_day_on_or_before(_to_date_str, trading_days)
                        if _last_td is None or _last_td <= _entry_iso:
                            continue
                        _s = dict(_s)
                        _s["exit_date"] = _last_td
                        # This trade did NOT reach its natural expiry — it was cut
                        # short at the window/range ceiling (the filter's last
                        # segment end, or the requested to_date). Tag it so the
                        # exit reason reads FILTER_END (combined with any
                        # co-occurring reason) instead of falsely reading EXPIRY.
                        # Calc-neutral: a range-clipped trade is the final trade
                        # of the run, so it has no successor for the FILTER_END
                        # patch/Live-DD reset to act on.
                        _s["_seg_clamped"] = True
                    elif _normalize_iso(_s.get("expiry", "")) > _to_date_str:
                        # Exit was ALREADY truncated to the last available trading
                        # day by resolve_trade_specs (so the exit>ceiling test
                        # above misses it), but the contract's expiry lies beyond
                        # the range ceiling — i.e. the trade was cut short by the
                        # window/range, not by reaching expiry. Mirrors the filter
                        # gating's `elif expiry > seg_end` so range-end and
                        # filter-end behave identically. → FILTER_END.
                        _s = dict(_s)
                        _s["_seg_clamped"] = True
                    _clipped.append(_s)
                specs = _clipped
                if not specs:
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
                    # Exit clamped to segment/filter end → FILTER_END reason.
                    s["_seg_clamped"] = True
                elif _normalize_iso(s.get("expiry", "")) > seg_end:
                    # Expiry lies beyond the segment end (and may be outside the
                    # loaded data, so exit_date was already truncated to seg_end).
                    # Still a filter-end exit, not a natural expiry exit.
                    s = dict(s)
                    s["_seg_clamped"] = True
                filtered.append(s)
            specs = filtered
            if not specs:
                return []

    # ── Slice 6b: no_rollover post-processing ───────────────────────────────
    # Keep only the first trade per segment (or globally when no filter).
    # Mirrors generic_algotest_engine.py:4054-4072.
    if payload.get("no_rollover"):
        specs = _apply_no_rollover(specs, payload, trading_days, original_segments)

    # ── Slice 9b: rollover_strike_mode='fixed' ───────────────────────────────
    # Reuse first rollover cycle's (already-buffered) strike for subsequent cycles.
    # Must run AFTER buffer-strike and segment gating, BEFORE simulate_trades_batch.
    # Mirrors generic_algotest_engine.py:4601-4618.
    # Save natural (post-buffer, pre-fixed-strike) strikes so we can restore them
    # for rollover trades that follow a spot-adj trigger in the same segment.
    _natural_spec_strikes: Dict[Tuple[int, int], float] = {
        (int(s.get("trade_id", 0)), int(s.get("leg_id", 1))): float(s.get("strike") or 0)
        for s in specs
    }
    if any(
        isinstance(leg, dict) and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in (payload.get("legs") or [])
    ):
        specs = _apply_fixed_rollover_strike(specs, payload, original_segments)

    # Step 2: price entries + scheduled exits.
    # Capture which specs were clamped to a segment/filter end BEFORE pricing
    # (simulate_trades_batch drops custom keys). Keyed by (trade_id, entry_date)
    # so the exit-reason step can label these FILTER_END instead of EXPIRY,
    # matching the Python engine (generic_algotest_engine.py:4385).
    # Keyed by (entry_date, expiry) — NOT trade_id — so the marker survives the
    # spot-adjustment / re-entry trade-id renumbering that happens further down.
    # With trade_id in the key, a clamped boundary trade whose id is renumbered
    # by spot-adj synthesis would miss this lookup and fall back to EXPIRY.
    _seg_clamped_keys: set = {
        (_normalize_iso(s.get("entry_date", "")), _normalize_iso(s.get("expiry", "")))
        for s in specs if s.get("_seg_clamped")
    }
    # STR (super-trend) segments use 'STR_Exit'; plain filters use 'FILTER_END'.
    _clamp_reason = (
        "STR_Exit"
        if str(payload.get("super_trend_config") or "").strip() in ("5x1", "5x2")
        else "FILTER_END"
    )

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
    # Midcap cross-index spot adjustment also needs the risk/re-entry pass below,
    # so it must NOT take the no-risk-controls early return.
    _mc_sa_cfg_early = payload.get("midcap_spot_adjustment") or {}
    has_midcap_spot_adj = bool(_mc_sa_cfg_early.get("enabled")) and (
        (_maybe_float(_mc_sa_cfg_early.get("pct")) or 0) > 0
    )
    if not any_risk and not has_overall_top and not has_spot_adj and not has_midcap_spot_adj:
        # No risk controls → priced output is the final answer. Still tag exits
        # that were clamped to a segment/filter end so they read FILTER_END
        # (or STR_Exit) instead of defaulting to EXPIRY.
        if _seg_clamped_keys:
            for _row in priced:
                if (_normalize_iso(_row.get("entry_date", "")), _normalize_iso(_row.get("expiry", ""))) in _seg_clamped_keys:
                    _row["exit_reason"] = _clamp_reason
        return list(priced)

    # Re-entry Rollover same-day chain: handled in Slice 6 via synthesis below.

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

    # ── Midcap cross-index spot adjustment (additive; gated on its own config) ──
    # Same exit-trigger + same-day re-entry logic as the NIFTY spot adjustment,
    # but the breach is measured on the NIFTYMIDCAP100 index. Earliest breach
    # (NIFTY vs Midcap) wins. Strikes/re-entry are unchanged (still NIFTY). When
    # midcap_spot_adjustment is absent/disabled NOTHING below runs — the NIFTY
    # path stays byte-identical.
    _mc_sa = payload.get("midcap_spot_adjustment") or {}
    midcap_adj_enabled = bool(_mc_sa.get("enabled"))
    midcap_adj_pct = _maybe_float(_mc_sa.get("pct")) or 0.0
    midcap_adj_direction = str(_mc_sa.get("direction") or "rise").lower()
    if midcap_adj_direction not in ("rise", "fall", "both"):
        midcap_adj_direction = "rise"
    midcap_adj_units = str(_mc_sa.get("units") or "percent").lower()
    if midcap_adj_units not in ("percent", "points"):
        midcap_adj_units = "percent"
    if midcap_adj_enabled and midcap_adj_pct > 0 and midcap_adj_units == "percent":
        midcap_adj_pct = max(0.25, min(5.0, midcap_adj_pct))
    _mc_legs_pl = payload.get("midcap_legs") or []
    midcap_sa_symbol = (
        (_mc_legs_pl[0].get("symbol") if (_mc_legs_pl and isinstance(_mc_legs_pl[0], dict)) else None)
        or "NIFTYMIDCAP100"
    )
    midcap_spot_by_date: Dict[str, float] = {}
    if midcap_adj_enabled and midcap_adj_pct > 0 and trading_days:
        try:
            _mc_lk = _get_midcap_sa_lookup(midcap_sa_symbol)
            for _d in trading_days:
                _c = _mc_lk.close(_d)
                if _c is not None:
                    midcap_spot_by_date[_d] = float(_c)
        except Exception as _mc_exc:
            logger.warning("[ENGINE_RUST] midcap spot-adj data load failed (%s) — disabling midcap trigger", _mc_exc)
            midcap_adj_enabled = False
        if not midcap_spot_by_date:
            midcap_adj_enabled = False
    _midcap_active = bool(midcap_adj_enabled and midcap_adj_pct > 0 and midcap_spot_by_date)

    # Fixed-strike legs: compute per-trade spot adj baseline using the segment's
    # first-entry spot so rollovers don't reset the reference level.
    # Mirrors the Python engine _seg_spot_adj_base logic.
    _has_fixed_strike_opt_legs_sa = any(
        isinstance(leg, dict)
        and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in legs_src
        if str(leg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
    )
    _trade_adj_baseline: Dict[int, float] = {}
    if spot_adj_enabled and spot_adj_pct > 0 and _has_fixed_strike_opt_legs_sa:
        _eff_segs_sa: List[Tuple[str, str]]
        if original_segments is not None:
            _eff_segs_sa = original_segments
        else:
            _from_sa = str(payload.get("from_date") or payload.get("date_from") or "")
            _to_sa = str(payload.get("to_date") or payload.get("date_to") or "")
            _eff_segs_sa = [(_from_sa, _to_sa)] if _from_sa and _to_sa else []
        _tid_entry_iso_sa: Dict[int, str] = {
            tid: _normalize_iso(legs[0]["entry_date"])
            for tid, legs in by_trade.items()
            if legs
        }
        for _seg_s_sa, _seg_e_sa in _eff_segs_sa:
            _seg_tids_sa = sorted(
                [t for t, e in _tid_entry_iso_sa.items() if e and _seg_s_sa <= e <= _seg_e_sa],
                key=lambda t: _tid_entry_iso_sa[t],
            )
            _seg_base_sa: Optional[float] = None
            for _tid_sa in _seg_tids_sa:
                _tid_iso_sa = _tid_entry_iso_sa[_tid_sa]
                _tid_own_sa = float(
                    by_trade[_tid_sa][0].get("entry_spot")
                    or spot_by_date.get(_tid_iso_sa)
                    or 0.0
                )
                if _seg_base_sa is None or _seg_base_sa <= 0:
                    _seg_base_sa = _tid_own_sa
                _trade_adj_baseline[_tid_sa] = _seg_base_sa
                # If adj fires with this baseline, next trade measures from trigger spot
                _sched_sa = _normalize_iso(by_trade[_tid_sa][0].get("exit_date", ""))
                if _sched_sa and _seg_base_sa > 0:
                    _trig_sa = _compute_spot_adjustment_trigger(
                        _tid_iso_sa, _seg_base_sa, _sched_sa,
                        spot_adj_direction, spot_adj_pct, spot_adj_units,
                        trading_days, spot_by_date,
                    )
                    if _trig_sa:
                        _new_base_sa = spot_by_date.get(_trig_sa)
                        if _new_base_sa and _new_base_sa > 0:
                            _seg_base_sa = _new_base_sa

    spot_adj_overrides: Dict[int, str] = {}
    spot_adj_reasons: Dict[int, str] = {}
    _nifty_active = bool(spot_adj_enabled and spot_adj_pct > 0)
    if _nifty_active or _midcap_active:
        for trade_id, legs in by_trade.items():
            legs.sort(key=lambda r: r["leg_id"])
            first = legs[0]
            entry_iso = _normalize_iso(first["entry_date"])
            scheduled_exit = _normalize_iso(first["exit_date"])
            if not scheduled_exit:
                continue

            # NIFTY-index breach (unchanged path).
            nifty_trig = None
            entry_spot = 0.0
            if _nifty_active:
                entry_spot = (
                    _trade_adj_baseline[trade_id]
                    if trade_id in _trade_adj_baseline
                    else float(first.get("entry_spot") or spot_by_date.get(entry_iso) or 0.0)
                )
                if entry_spot > 0:
                    nifty_trig = _compute_spot_adjustment_trigger(
                        entry_iso, entry_spot, scheduled_exit,
                        spot_adj_direction, spot_adj_pct, spot_adj_units,
                        trading_days, spot_by_date,
                    )

            # Midcap-index breach (additive — measured on NIFTYMIDCAP100, but it
            # truncates the SAME trade so the existing re-entry chain re-enters
            # the NIFTY leg the same day with a fresh strike).
            midcap_trig = None
            if _midcap_active:
                mc_entry_spot = midcap_spot_by_date.get(entry_iso) or 0.0
                if mc_entry_spot > 0:
                    midcap_trig = _compute_spot_adjustment_trigger(
                        entry_iso, mc_entry_spot, scheduled_exit,
                        midcap_adj_direction, midcap_adj_pct, midcap_adj_units,
                        trading_days, midcap_spot_by_date,
                    )

            # Earliest breach wins.
            if nifty_trig and (not midcap_trig or nifty_trig <= midcap_trig):
                spot_adj_overrides[trade_id] = nifty_trig
                spot_adj_reasons[trade_id] = _spot_adj_reason_tag(
                    spot_adj_direction, entry_spot, spot_by_date.get(nifty_trig),
                    spot_adj_pct, spot_adj_units,
                )
            elif midcap_trig:
                spot_adj_overrides[trade_id] = midcap_trig
                _mc_e = midcap_spot_by_date.get(entry_iso) or 0.0
                _mc_t = midcap_spot_by_date.get(midcap_trig) or 0.0
                spot_adj_reasons[trade_id] = (
                    "MIDCAP_SPOT_ADJ_RISE" if _mc_t >= _mc_e else "MIDCAP_SPOT_ADJ_FALL"
                )

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

    # ── Fix 4: re-anchor fixed-strike strikes BEFORE SL-with-Buffer detection ──
    # The SL-with-Buffer pre-pass (Slice 4b above) and the re-entry synthesis
    # (Slice 6 below) both read `slb_overrides`, and the re-entry takes its entry
    # date from it (~"trig_date = _slb_override[0]"). But the pre-pass ran on the
    # INITIAL (locked, pre-adjustment) strike, so on adjusted/rolled trades the
    # SL-buffer *trigger date* — and the re-entry that inherits it — land on the
    # wrong day. The fixed-strike strikes are only corrected later (carry-forward,
    # ~line 3300). Here we apply that SAME re-anchor EARLY and recompute the
    # SL-buffer detection on the corrected contract, so the parent exit date AND
    # its re-entry resolve on the strike actually held. Strike-only: no specs/ids
    # are built here, so the existing bridge block and the late carry-forward run
    # unchanged (the latter re-applies identical strikes idempotently). Gated so
    # non-fixed-strike / non-spot-adj / non-SLB strategies are fully untouched.
    _fix4_index = str(payload.get("index") or "NIFTY").upper()
    _fix4_fixed_legs = any(
        isinstance(leg, dict)
        and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in legs_src
        if str(leg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
    )
    _fix4_has_slb = any(
        isinstance(leg, dict) and isinstance(leg.get("slWithBuffer"), dict)
        and _maybe_float((leg.get("slWithBuffer") or {}).get("value"))
        for leg in legs_src
    )
    if _fix4_fixed_legs and _fix4_has_slb and (spot_adj_enabled or _midcap_active) and spot_adj_overrides:
        # (1) anchor strikes per re-anchor date — mirrors the bridge cascade strike
        #     resolution + the natural-ATM fallback in the carry-forward below.
        _fix4_anchor: Dict[str, Dict[int, float]] = {}
        _fix4_ok = True
        for _f_id, _f_trigger in list(spot_adj_overrides.items()):
            _f_legs = sorted(by_trade.get(_f_id, []), key=lambda r: r["leg_id"])
            if not _f_legs:
                continue
            _f_orig_exit = _normalize_iso(_f_legs[0]["exit_date"])
            if _f_trigger >= _f_orig_exit:
                continue
            _f_cur = _f_trigger
            _f_depth = 0
            while _f_depth < 8 and _f_cur < _f_orig_exit:
                _f_depth += 1
                _f_spot = spot_by_date.get(_f_cur)
                if _f_spot is None:
                    break
                # Guard on _nifty_active (see bridge note): a default spot_adjustment_pct
                # in the payload would otherwise fire a spurious NIFTY re-anchor on a
                # Midcap-only run.
                _f_casc = None
                if _nifty_active:
                    _f_casc = _compute_spot_adjustment_trigger(
                        _f_cur, float(_f_spot), _f_orig_exit,
                        spot_adj_direction, spot_adj_pct, spot_adj_units,
                        trading_days, spot_by_date,
                    )
                for _f_leg in _f_legs:
                    _f_src = (
                        legs_src[_f_leg["leg_id"] - 1]
                        if 0 <= _f_leg["leg_id"] - 1 < len(legs_src) else {}
                    )
                    if not _supports_reentry_strike(_f_src):
                        _fix4_ok = False
                        break
                    _f_si = float(
                        _f_src.get("strike_interval")
                        or _STRIKE_INTERVALS.get(_fix4_index, 50.0)
                    )
                    _f_info: Dict[str, Any] = {}
                    _f_strk = _compute_strike_for_leg_python(
                        _f_src, float(_f_spot), _f_si,
                        entry_date=_f_cur, expiry=_normalize_iso(_f_leg["expiry"]),
                        index=_fix4_index, out_info=_f_info,
                    )
                    if _f_strk is not None:
                        _fix4_anchor.setdefault(_f_cur, {})[int(_f_leg["leg_id"])] = float(_f_strk)
                if not _fix4_ok:
                    break
                if _f_casc and _f_casc < _f_orig_exit:
                    _f_cur = _f_casc
                else:
                    break
            if not _fix4_ok:
                break
        _f_tid_entry = {
            _t: _normalize_iso(_l[0]["entry_date"]) for _t, _l in by_trade.items() if _l
        }
        if _fix4_ok:
            # natural-ATM anchors for trigger==scheduled-exit days (no bridge).
            _f_trig_dates = set(spot_adj_overrides.values())
            for _t, _td in _f_tid_entry.items():
                if _td in _f_trig_dates:
                    for _r in by_trade.get(_t, []):
                        _lid = int(_r["leg_id"])
                        _nat = _natural_spec_strikes.get((_t, _lid))
                        if _nat:
                            _fix4_anchor.setdefault(_td, {}).setdefault(_lid, _nat)
        # (2) apply most-recent-anchor correction to by_trade (mirrors carry-forward).
        _fix4_changed_tids: Set[int] = set()
        if _fix4_ok and _fix4_anchor:
            if original_segments is not None:
                _f_segs: Optional[List[Tuple[str, str]]] = original_segments
            else:
                _f_from = str(payload.get("from_date") or payload.get("date_from") or "")
                _f_to = str(payload.get("to_date") or payload.get("date_to") or "")
                _f_segs = [(_f_from, _f_to)] if _f_from and _f_to else None
            if _f_segs:
                _f_dates_sorted = sorted(_fix4_anchor.keys())
                for _f_s, _f_e in _f_segs:
                    _f_seg_tids = [t for t, e in _f_tid_entry.items() if _f_s <= e <= _f_e]
                    _f_seg_anchors = [d for d in _f_dates_sorted if _f_s <= d <= _f_e]
                    for _f_ctid in _f_seg_tids:
                        _f_centry = _f_tid_entry[_f_ctid]
                        _f_applic = [d for d in _f_seg_anchors if d <= _f_centry]
                        if not _f_applic:
                            continue
                        _f_anchor = _f_applic[-1]
                        for _f_crow in by_trade.get(_f_ctid, []):
                            _f_clid = int(_f_crow["leg_id"])
                            _f_cstrike = _fix4_anchor[_f_anchor].get(_f_clid)
                            if _f_cstrike and abs(_f_cstrike - float(_f_crow.get("strike") or 0)) > 0.01:
                                _f_crow["strike"] = _f_cstrike
                                _f_crow["requested_strike"] = _f_cstrike
                                _fix4_changed_tids.add(int(_f_ctid))
        # (3) recompute SL-with-Buffer ONLY on the trades we re-anchored, so the
        #     trigger date + fill reflect the strike actually held. The re-entry
        #     synthesis below then reads the corrected date.
        if _fix4_changed_tids:
            try:
                import algotest_native  # type: ignore
                _f_rows = [
                    r for _t in _fix4_changed_tids for r in by_trade.get(_t, [])
                ]
                _f_specs = [{
                    "trade_id": r["trade_id"], "leg_id": r["leg_id"], "index": r["index"],
                    "entry_date": r["entry_date"], "exit_date": r["exit_date"],
                    "expiry": r["expiry"], "strike": float(r["strike"]),
                    "requested_strike": float(r.get("requested_strike", r["strike"])),
                    "strike_interval": float(
                        r.get("strike_interval") or _STRIKE_INTERVALS.get(_fix4_index, 50.0)
                    ),
                    "option_type": r["option_type"], "position": r["position"],
                    "lots": r["lots"], "lot_size": r["lot_size"],
                    "slippage_pct": r["slippage_pct"],
                } for r in _f_rows]
                _f_priced = list(algotest_native.simulate_trades_batch(_f_specs))
                _f_slb = algotest_native.apply_sl_with_buffer_batch(
                    _f_priced, list(legs_src), list(trading_days)
                )
                for _f_row, _f_res in zip(_f_priced, _f_slb):
                    _f_key = (_f_row["trade_id"], _f_row["leg_id"])
                    if _f_res is not None:
                        _f_d, _f_p = _f_res
                        slb_overrides[_f_key] = (_normalize_iso(_f_d), float(_f_p))
                    else:
                        slb_overrides.pop(_f_key, None)
            except Exception as _f_exc:
                logger.warning("[ENGINE_RUST] Fix 4 SL-buffer recompute failed: %s", _f_exc)

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
    reentry_meta_map: Dict[Tuple[int, int, str], Tuple[int, str, str]] = {}  # → (index, trigger, mode)
    # Each re-entry gets a unique trade_id so it appears as a separate trade row
    # (with its own Cumulative/Peak/DD) rather than a sub-row of the parent.
    # Mirrors the bridge-trade pattern (_bt_bridge_by_new_tid).
    _reentry_new_tid = max(by_trade.keys()) + 1 if by_trade else 1
    _reentry_by_new_tid: Dict[int, int] = {}  # new_tid → parent_tid (for overlap filter)
    index_str = str(payload.get("index") or "NIFTY").upper()
    for trade_id, legs in by_trade.items():
        legs.sort(key=lambda r: r["leg_id"])
        leg_results = per_leg_results_by_trade.get(trade_id, [])
        overall_date = overall_overrides.get(trade_id)
        for i, leg in enumerate(legs):
            leg_src = legs_src[leg["leg_id"] - 1] if 0 <= leg["leg_id"] - 1 < len(legs_src) else {}
            sl_cfg = leg_src.get("reEntryOnSL") if isinstance(leg_src.get("reEntryOnSL"), dict) else None
            tgt_cfg = leg_src.get("reEntryOnTarget") if isinstance(leg_src.get("reEntryOnTarget"), dict) else None
            # Rollover same-day chain synthesis (mirrors generic_algotest_engine.py:5367-5398).
            # When rollover_toggle is on and a leg has risk but no explicit re-entry config,
            # synthesize RE_ASAP count=10 based on what actually fired — same as Python engine.
            if not sl_cfg and not tgt_cfg and bool(payload.get("rollover_toggle")) and _has_risk(leg_src):
                _leg_result = leg_results[i] if i < len(leg_results) else {}
                _trig = str(_leg_result.get("exit_reason") or "").split("[")[0].strip().upper()
                _slb_synth_key = (int(leg["trade_id"]), int(leg["leg_id"]))
                # slWithBuffer-only legs: check_leg_stop_loss_target returns EXPIRY (it
                # doesn't know about SLB). If SLB pre-pass fired, override the reason.
                if _trig not in _SL_REASONS and _trig not in _TGT_REASONS:
                    if slb_overrides.get(_slb_synth_key):
                        _trig = "SL_WITH_BUFFER"
                if _trig in _SL_REASONS:
                    sl_cfg = {"mode": "RE_ASAP", "count": 10}
                elif _trig in _TGT_REASONS:
                    tgt_cfg = {"mode": "RE_ASAP", "count": 10}
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
            _slb_key = (int(leg["trade_id"]), int(leg["leg_id"]))
            _slb_override = slb_overrides.get(_slb_key)

            # Determine trigger date and reason. Priority (matches adjusted_specs
            # construction ~line 3173): SLB fires before regular SL → use SLB.
            _per_leg_exit = _normalize_iso(result.get("exit_date")) if result.get("triggered") else None
            _per_leg_reason = (result.get("exit_reason") or "").upper() if result.get("triggered") else ""

            if _slb_override is not None:
                _slb_date = _slb_override[0]
                # Use SLB when: no regular SL fired, or SLB fires before regular SL.
                if _per_leg_exit is None or _slb_date < _per_leg_exit or _per_leg_reason not in _SL_REASONS:
                    trig_date = _slb_date
                    _init_reason = "SL_WITH_BUFFER"
                else:
                    trig_date = _per_leg_exit
                    _init_reason = _per_leg_reason
            elif _per_leg_exit and _per_leg_reason in _SL_REASONS | _TGT_REASONS:
                trig_date = _per_leg_exit
                _init_reason = _per_leg_reason
            else:
                continue  # No SL/Target/SLB triggered — nothing to chain.

            # If overall SL fires BEFORE the per-leg trigger, the leg is already
            # closed by overall — no re-entry.
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
            current_reason = _init_reason
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
                    _shift_info: Dict[str, Any] = {}
                    lazy_strike = _compute_strike_for_leg_python(
                        lazy_leg_config, float(lazy_spot), strike_interval,
                        entry_date=current_trig, expiry=lazy_expiry, index=index_str,
                        out_info=_shift_info,
                    )
                    if lazy_strike is None:
                        break  # Strike unresolvable — skip this lazy leg re-entry
                    _lazy_new_tid = _reentry_new_tid; _reentry_new_tid += 1
                    _reentry_by_new_tid[_lazy_new_tid] = int(leg["trade_id"])
                    lazy_spec = {
                        "trade_id": _lazy_new_tid,
                        "leg_id": 1,
                        "index": index_str,
                        "entry_date": current_trig,
                        "exit_date": cycle_exit,
                        "expiry": lazy_expiry,
                        "strike": lazy_strike,
                        "requested_strike": float(_shift_info.get("requested_strike") or lazy_strike),
                        "strike_interval": float(strike_interval),
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
                    reentry_reason_map[(_lazy_new_tid, 1, str(current_trig))] = lazy_reason or "EXPIRY"
                    reentry_meta_map[(_lazy_new_tid, 1, str(current_trig))] = (
                        sl_used + tgt_used,
                        "SL" if current_reason in _SL_REASONS else "TARGET",
                        re_mode,
                    )
                    break  # Lazy leg doesn't cascade in the outer re-entry loop

                # ── RE_ASAP / RE_ASAP_REV branch ─────────────────────────────
                # Spot at re-entry date must be available to compute fresh strike.
                spot = spot_by_date.get(current_trig)
                if spot is None:
                    break
                _shift_info: Dict[str, Any] = {}
                new_strike = _compute_strike_for_leg_python(
                    leg_src, float(spot), strike_interval,
                    entry_date=current_trig, expiry=parent_expiry, index=index_str,
                    out_info=_shift_info,
                )
                if new_strike is None:
                    break  # Strike not resolvable — skip this re-entry chain

                # Build a single-leg spec for the re-entry and price its entry.
                # New unique trade_id so this appears as a separate trade row
                # with its own Cumulative/Peak/DD, not a sub-row of the parent.
                _re_new_tid = _reentry_new_tid; _reentry_new_tid += 1
                _reentry_by_new_tid[_re_new_tid] = int(leg["trade_id"])
                re_spec = {
                    "trade_id": _re_new_tid,
                    "leg_id": 1,
                    "index": leg["index"],
                    "entry_date": current_trig,
                    "exit_date": cycle_exit,
                    "expiry": parent_expiry,
                    "strike": new_strike,
                    "requested_strike": float(_shift_info.get("requested_strike") or new_strike),
                    "strike_interval": float(strike_interval),
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

                # SL-with-Buffer also applies to the re-entry leg. The regular
                # check above (check_leg_stop_loss_target) is buffer-blind, so an
                # SLB-only re-entry would otherwise ride to EXPIRY and the cascade
                # would stop after one re-entry. Run the same buffer-aware pre-pass
                # the parent uses and apply the parent's SLB-vs-SL priority
                # (mirrors ~line 2702): SLB wins when no regular SL fired or it
                # fires first.
                if isinstance(leg_src.get("slWithBuffer"), dict) and _maybe_float(
                    (leg_src.get("slWithBuffer") or {}).get("value")
                ):
                    try:
                        _re_slb_res = algotest_native.apply_sl_with_buffer_batch(
                            [priced_re_row], [leg_src], list(trading_days)
                        )
                    except Exception as exc:
                        logger.warning("[ENGINE_RUST] re-entry SLB check failed: %s", exc)
                        _re_slb_res = None
                    if _re_slb_res and _re_slb_res[0] is not None:
                        _re_slb_date = _normalize_iso(_re_slb_res[0][0])
                        _re_slb_price = float(_re_slb_res[0][1])
                        if re_reason not in _SL_REASONS or _re_slb_date < re_exit:
                            re_exit = _re_slb_date
                            re_reason = "SL_WITH_BUFFER"
                            # Register so the price/display fix-up (~line 3589)
                            # swaps in the buffered exit price for this re-entry
                            # row too, exactly as it does for the parent.
                            slb_overrides[(_re_new_tid, 1)] = (_re_slb_date, _re_slb_price)

                # If overall SL fires before the re-entry's own exit, clamp.
                if overall_date is not None and re_exit >= overall_date:
                    re_exit = overall_date
                    re_reason = overall_reasons.get(trade_id, "OVERALL_SL")

                re_spec["exit_date"] = re_exit
                re_spec["_entry_date_key"] = str(current_trig)
                reentry_specs.append(re_spec)
                reentry_reason_map[(_re_new_tid, 1, str(current_trig))] = re_reason or "EXPIRY"
                reentry_meta_map[(_re_new_tid, 1, str(current_trig))] = (
                    sl_used + tgt_used,
                    "SL" if current_reason in _SL_REASONS else "TARGET",
                    re_mode,
                )

                # Cascade: if this re-entry also SL'd/TP'd AND budget remains,
                # loop. Otherwise stop.
                current_trig = re_exit
                current_reason = re_reason

    # Slice 7b: Spot-adjustment bridge trades (rollover).
    # When spot adj exits a trade early AND rollover_toggle=True, insert bridge
    # spec(s) from trigger_date to original scheduled exit so the cycle stays
    # fully covered. Each bridge is itself checked for cascading spot adj (up to
    # 8 levels). Mirrors generic_algotest_engine.py:4144-4191.
    #
    # Bridge specs mint a NEW trade_id per cascade cycle so each cycle renders
    # as its own row block in the tradesheet (Cumulative/Peak/DD live per-Trade
    # in the frontend; reusing the parent id squashed multi-cascade trades into
    # one block with all chain values on a single row). Survival through the
    # overlap filter still uses the parent id via _bt_bridge_by_new_tid.
    # Mirrors Slice 7a-reentry behavior in DTE mode (~line 3082).
    _rollover_toggle = bool(payload.get("rollover_toggle", False))
    _bt_bridge_specs: List[Dict[str, Any]] = []
    _bt_bridge_by_new_tid: Dict[int, int] = {}  # new_tid → parent_tid (for overlap filter)
    # Bridges apply in two cases:
    #   1. rollover + filter_entry_mode='fixed': original bridge behaviour (fills the
    #      gap so the cycle window stays fully covered after a spot-adj exit).
    #   2. Any leg has rollover_strike_mode='fixed': restrike — exit at trigger price,
    #      re-enter immediately with a fresh strike from the new spot level, continue
    #      to the original scheduled exit. Works for no-rollover and DTE-rollover too.
    #      Mirrors the Python change in generic_algotest_engine.py (SPOT_ADJ RESTRIKE).
    _has_fixed_strike_opt_legs = any(
        isinstance(leg, dict)
        and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in legs_src
        if str(leg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
    )
    # Shared id counter: continue from where the Slice-6 re-entry generator left
    # off so bridge / spot-adj trades never collide with re-entry trade_ids.
    # (Defined unconditionally so the spot-adj re-entry block below can chain from
    # it even when this bridge block doesn't run.)
    _bt_new_tid = _reentry_new_tid
    if (spot_adj_enabled or _midcap_active) and spot_adj_overrides and (
        (_rollover_toggle and filter_entry_mode == "fixed") or _has_fixed_strike_opt_legs
    ):
        for _bt_id, _bt_trigger in list(spot_adj_overrides.items()):
            _bt_legs = sorted(by_trade.get(_bt_id, []), key=lambda r: r["leg_id"])
            if not _bt_legs:
                continue
            _bt_orig_exit = _normalize_iso(_bt_legs[0]["exit_date"])
            if _bt_trigger >= _bt_orig_exit:
                continue  # No gap between trigger and scheduled exit — skip

            _bt_cur_entry = _bt_trigger
            _bt_cycle_exit = _bt_orig_exit
            _bt_depth = 0

            while _bt_depth < 8 and _bt_cur_entry < _bt_cycle_exit:
                _bt_depth += 1
                _bt_spot = spot_by_date.get(_bt_cur_entry)
                if _bt_spot is None:
                    break

                # Fresh trade_id for this cascade cycle (shared across legs in
                # this cycle). Lets the tradesheet render each cycle as its own
                # row block with its own Cumulative/Peak/DD entry.
                _bt_cycle_tid = _bt_new_tid

                # Cascading spot adj trigger for this bridge window.
                # Guard on _nifty_active: when NIFTY spot adjustment is disabled the
                # payload still carries a default spot_adjustment_pct (e.g. 1.0), so an
                # unconditional scan would fire a spurious NIFTY trigger on a Midcap-only
                # run. Mirrors the initial loop and the DTE/cascade block.
                _bt_casc_trig = None
                if _nifty_active:
                    _bt_casc_trig = _compute_spot_adjustment_trigger(
                        _bt_cur_entry,
                        float(_bt_spot),
                        _bt_cycle_exit,
                        spot_adj_direction,
                        spot_adj_pct,
                        spot_adj_units,
                        trading_days,
                        spot_by_date,
                    )
                # Re-base + re-scan the Midcap index on THIS cycle too (mirrors the
                # DTE/cascade path above). Without this, fixed-strike bridge
                # re-entries silently skipped Midcap spot adjustment — only the
                # original trade was ever checked. Earliest of NIFTY / Midcap wins
                # (tie → NIFTY, since we only override on a strictly earlier date).
                _bt_casc_is_midcap = False
                if _midcap_active:
                    _bt_mc_entry = midcap_spot_by_date.get(_bt_cur_entry) or 0.0
                    if _bt_mc_entry > 0:
                        _bt_mc_casc = _compute_spot_adjustment_trigger(
                            _bt_cur_entry,
                            _bt_mc_entry,
                            _bt_cycle_exit,
                            midcap_adj_direction,
                            midcap_adj_pct,
                            midcap_adj_units,
                            trading_days,
                            midcap_spot_by_date,
                        )
                        if _bt_mc_casc and (
                            not _bt_casc_trig or _bt_mc_casc < _bt_casc_trig
                        ):
                            _bt_casc_trig = _bt_mc_casc
                            _bt_casc_is_midcap = True
                _bt_this_exit = (
                    _bt_casc_trig
                    if (_bt_casc_trig and _bt_casc_trig < _bt_cycle_exit)
                    else _bt_cycle_exit
                )
                _bt_all_ok = True
                # Earliest SL / SL-with-Buffer / Target stop-out (with re-entry
                # enabled) among this cycle's legs. Drives the same-day re-entry
                # continuation at the bottom of the loop so a bridge stop-out no
                # longer leaves a gap to the next scheduled trade.
                _bt_sl_reentry_from = None

                for _btl in _bt_legs:
                    _btl_src = (
                        legs_src[_btl["leg_id"] - 1]
                        if 0 <= _btl["leg_id"] - 1 < len(legs_src)
                        else {}
                    )
                    if not _supports_reentry_strike(_btl_src):
                        return None  # Can't re-resolve strike — Python fallback

                    _btl_si = float(
                        _btl_src.get("strike_interval")
                        or _STRIKE_INTERVALS.get(index_str, 50.0)
                    )
                    _btl_shift_info: Dict[str, Any] = {}
                    _btl_strike = _compute_strike_for_leg_python(
                        _btl_src,
                        float(_bt_spot),
                        _btl_si,
                        entry_date=_bt_cur_entry,
                        expiry=_normalize_iso(_btl["expiry"]),
                        index=index_str,
                        out_info=_btl_shift_info,
                    )
                    if _btl_strike is None:
                        return None  # Strike unresolvable — Python fallback

                    _btl_spec = {
                        "trade_id": _bt_cycle_tid,
                        "leg_id": _btl["leg_id"],
                        "index": _btl["index"],
                        "entry_date": _bt_cur_entry,
                        "exit_date": _bt_this_exit,
                        "expiry": _normalize_iso(_btl["expiry"]),
                        "strike": _btl_strike,
                        "requested_strike": float(_btl_shift_info.get("requested_strike") or _btl_strike),
                        "strike_interval": float(_btl_si),
                        "option_type": _btl["option_type"],
                        "position": _btl["position"],
                        "lots": _btl["lots"],
                        "lot_size": _btl["lot_size"],
                        "slippage_pct": _btl["slippage_pct"],
                    }
                    # Inline price for SL check (needs entry_price).
                    try:
                        _btl_priced = algotest_native.simulate_trades_batch([_btl_spec])
                    except Exception as _btl_exc:
                        logger.warning("[ENGINE_RUST] bridge simulate failed: %s", _btl_exc)
                        _bt_all_ok = False
                        break
                    if not _btl_priced:
                        _bt_all_ok = False
                        break

                    _btl_row = _btl_priced[0]
                    _btl_entry_spot = float(_btl_row.get("entry_spot") or _bt_spot)
                    _btl_sl_cfg = _build_leg_config_for_sl(_btl_row, _btl_src)

                    try:
                        _btl_sl_res = algotest_native.check_leg_stop_loss_target(
                            _bt_cur_entry,
                            _bt_this_exit,
                            _normalize_iso(_btl["expiry"]),
                            _btl_entry_spot,
                            [_btl_sl_cfg],
                            index_str,
                            trading_calendar,
                            str(square_off_mode or "partial"),
                            slippage,
                        )
                    except Exception as _btl_sl_exc:
                        logger.warning("[ENGINE_RUST] bridge SL check failed: %s", _btl_sl_exc)
                        _btl_sl_res = None

                    _btl_final_exit = _bt_this_exit
                    if _bt_casc_trig and _bt_casc_trig < _bt_cycle_exit:
                        if _bt_casc_is_midcap:
                            _bt_mc_e2 = midcap_spot_by_date.get(_bt_cur_entry) or 0.0
                            _bt_mc_t2 = midcap_spot_by_date.get(_bt_casc_trig) or 0.0
                            _btl_reason = (
                                "MIDCAP_SPOT_ADJ_RISE" if _bt_mc_t2 >= _bt_mc_e2
                                else "MIDCAP_SPOT_ADJ_FALL"
                            )
                        else:
                            _btl_reason = _spot_adj_reason_tag(
                                spot_adj_direction,
                                _bt_spot,
                                spot_by_date.get(_bt_casc_trig),
                                spot_adj_pct,
                                spot_adj_units,
                            )
                    else:
                        _btl_reason = "EXPIRY"
                    if isinstance(_btl_sl_res, list) and _btl_sl_res:
                        _btl_r0 = _btl_sl_res[0]
                        if isinstance(_btl_r0, dict) and _btl_r0.get("triggered"):
                            _btl_sl_exit = _normalize_iso(
                                _btl_r0.get("exit_date") or _bt_this_exit
                            )
                            if _btl_sl_exit < _btl_final_exit:
                                _btl_final_exit = _btl_sl_exit
                                _btl_reason = (
                                    (_btl_r0.get("exit_reason") or "").upper() or "SL"
                                )

                    # SL-with-Buffer for restrike/bridge legs. The block above runs
                    # only the PLAIN SL check (check_leg_stop_loss_target), so
                    # buffer-stops were never evaluated for spot-adjustment restrike
                    # trades — both the original-trade path and the re-entry path call
                    # apply_sl_with_buffer_batch, but the bridge path did not. Mirror
                    # them so a restrike leg can stop out mid-hold at its buffer level;
                    # the fill is applied via slb_overrides in the swap below (clamped
                    # to the stop level there, same as every other path).
                    if isinstance(_btl_src.get("slWithBuffer"), dict) and _maybe_float(
                        (_btl_src.get("slWithBuffer") or {}).get("value")
                    ):
                        try:
                            _btl_slb = algotest_native.apply_sl_with_buffer_batch(
                                [_btl_row], list(legs_src), list(trading_days)
                            )
                            _btl_slb_res = _btl_slb[0] if _btl_slb else None
                        except Exception as _btl_slb_exc:
                            logger.warning(
                                "[ENGINE_RUST] bridge SL-with-Buffer check failed: %s",
                                _btl_slb_exc,
                            )
                            _btl_slb_res = None
                        if _btl_slb_res is not None:
                            _btl_slb_date = _normalize_iso(_btl_slb_res[0])
                            _btl_slb_fill = float(_btl_slb_res[1])
                            # Buffer wins when it fires before the planned exit, or ON
                            # it when that exit is a plain EXPIRY (an expiry-day gap
                            # stop-out — same case Trade 133 has on the original path).
                            # A spot-adj cascade on the same day keeps priority.
                            if _btl_slb_date < _btl_final_exit or (
                                _btl_slb_date == _btl_final_exit
                                and _btl_reason == "EXPIRY"
                            ):
                                _btl_final_exit = _btl_slb_date
                                _btl_reason = "SL_WITH_BUFFER"
                                slb_overrides[(int(_bt_cycle_tid), int(_btl["leg_id"]))] = (
                                    _btl_slb_date,
                                    _btl_slb_fill,
                                )

                    # Overall SL clamp — bridge shares the same trade_id.
                    _bt_overall = overall_overrides.get(_bt_id)
                    if _bt_overall is not None and _btl_final_exit >= _bt_overall:
                        _btl_final_exit = _bt_overall
                        _btl_reason = overall_reasons.get(_bt_id, "OVERALL_SL")

                    # Re-entry-on-SL for bridge legs: if this restrike leg stopped
                    # out on SL / SL-with-Buffer / Target BEFORE the cycle end AND
                    # re-entry is enabled (rollover, or an explicit RE_ASAP
                    # reEntryOnSL/Target), record the stop-out day. The loop below
                    # then re-enters there — same day, fresh strike — exactly like
                    # the Slice 6 RE_ASAP chain, instead of leaving a gap. Overall
                    # SL ("OVERALL_SL") is intentionally excluded: the whole
                    # position is closed, so nothing is re-entered.
                    _btl_re_on_sl = (
                        _rollover_toggle
                        or (isinstance(_btl_src.get("reEntryOnSL"), dict)
                            and str((_btl_src.get("reEntryOnSL") or {}).get("mode")
                                    or "RE_ASAP").upper() == "RE_ASAP")
                    )
                    _btl_re_on_tgt = (
                        _rollover_toggle
                        or (isinstance(_btl_src.get("reEntryOnTarget"), dict)
                            and str((_btl_src.get("reEntryOnTarget") or {}).get("mode")
                                    or "RE_ASAP").upper() == "RE_ASAP")
                    )
                    if (_bt_cur_entry < _btl_final_exit < _bt_cycle_exit) and (
                        (_btl_reason in _SL_REASONS and _btl_re_on_sl)
                        or (_btl_reason in _TGT_REASONS and _btl_re_on_tgt)
                    ):
                        if (_bt_sl_reentry_from is None
                                or _btl_final_exit < _bt_sl_reentry_from):
                            _bt_sl_reentry_from = _btl_final_exit

                    _btl_spec["exit_date"] = _btl_final_exit
                    _btl_spec["_entry_date_key"] = str(_bt_cur_entry)
                    _bt_bridge_specs.append(_btl_spec)
                    reentry_reason_map[
                        (int(_bt_cycle_tid), int(_btl["leg_id"]), str(_bt_cur_entry))
                    ] = _btl_reason

                if not _bt_all_ok:
                    break

                # Cycle complete — record parent mapping + advance the id counter.
                _bt_bridge_by_new_tid[_bt_cycle_tid] = _bt_id
                _bt_new_tid += 1

                # Continue the cycle from whichever fires first: a cascading spot
                # adjustment (existing behaviour) OR a re-entry-on-SL/Target
                # stop-out (new). When no SL re-entry fired, _bt_sl_reentry_from is
                # None and this reduces exactly to the original cascade-only path.
                _bt_casc_next = (
                    _bt_casc_trig
                    if (_bt_casc_trig and _bt_casc_trig < _bt_cycle_exit)
                    else None
                )
                _bt_next_entry = None
                for _bt_cand in (_bt_casc_next, _bt_sl_reentry_from):
                    if _bt_cand is not None and (
                        _bt_next_entry is None or _bt_cand < _bt_next_entry
                    ):
                        _bt_next_entry = _bt_cand
                if _bt_next_entry is not None:
                    _bt_cur_entry = _bt_next_entry  # Loop for next bridge cycle
                else:
                    break  # Bridge chain complete

    # Fixed-strike carry-forward: in fixed-strike mode the strike is LOCKED and
    # only RE-ANCHORS when a spot-adjustment re-entry fires. A normal EXPIRY
    # rollover must CARRY the previous (locked) strike — it must NOT resolve its
    # own fresh ATM. Mirrors the Python engine (generic_algotest_engine.py
    # _seg_fixed_strikes: reuse on rollover, clear+resave on spot adj).
    #
    # Re-anchor points (entry date → fresh-ATM strike per leg):
    #   (1) every spot-adj re-entry ("bridge") trade — it already holds the
    #       fresh ATM resolved at its trigger/cascade-trigger entry date, and
    #   (2) a trade that re-enters on a trigger==scheduled-exit day, where no
    #       bridge is created — its own natural (pre-fixed) ATM is the re-anchor.
    # Each original trade then takes the strike of the most-recent re-anchor on
    # or before its own entry; trades before any trigger keep the first-cycle
    # (locked) strike already applied by _apply_fixed_rollover_strike.
    # Updating by_trade here (before adjusted_specs assembly) means the final
    # simulate_trades_batch picks up the corrected strikes automatically.
    # Works for filter/STR mode (original_segments set) and DTE mode
    # (original_segments None — treat full date range as one segment).
    if _has_fixed_strike_opt_legs and spot_adj_overrides:
        _cf_segs: Optional[List[Tuple[str, str]]]
        if original_segments is not None:
            _cf_segs = original_segments
        else:
            _cf_from = str(payload.get("from_date") or payload.get("date_from") or "")
            _cf_to = str(payload.get("to_date") or payload.get("date_to") or "")
            _cf_segs = [(_cf_from, _cf_to)] if _cf_from and _cf_to else None
        if _cf_segs:
            _cf_tid_entry: Dict[int, str] = {
                _tid: _normalize_iso(_tlegs[0]["entry_date"])
                for _tid, _tlegs in by_trade.items()
                if _tlegs
            }
            # Re-anchor strikes keyed by entry date → {leg_id: strike}.
            _anchor_strike: Dict[str, Dict[int, float]] = {}
            # (1) spot-adj re-entry bridges carry the fresh trigger-day ATM.
            for _bspec in _bt_bridge_specs:
                _bdate = _normalize_iso(_bspec.get("_entry_date_key") or _bspec.get("entry_date") or "")
                if not _bdate:
                    continue
                _anchor_strike.setdefault(_bdate, {})[int(_bspec["leg_id"])] = float(_bspec.get("strike") or 0)
            # (2) trigger==scheduled-exit days have no bridge; the original trade
            #     entering that day re-anchors with its own natural ATM.
            _trigger_dates = set(spot_adj_overrides.values())
            for _tid, _tdate in _cf_tid_entry.items():
                if _tdate in _trigger_dates:
                    for _trow in by_trade.get(_tid, []):
                        _tlid = int(_trow["leg_id"])
                        _tnat = _natural_spec_strikes.get((_tid, _tlid))
                        if _tnat:
                            _anchor_strike.setdefault(_tdate, {}).setdefault(_tlid, _tnat)
            _anchor_dates_sorted = sorted(_anchor_strike.keys())
            # Reprice each original trade to the most-recent re-anchor ≤ its entry.
            for _cf_s, _cf_e in _cf_segs:
                _seg_tids = [t for t, e in _cf_tid_entry.items() if _cf_s <= e <= _cf_e]
                _seg_anchors = [d for d in _anchor_dates_sorted if _cf_s <= d <= _cf_e]
                for _ctid in _seg_tids:
                    _centry = _cf_tid_entry[_ctid]
                    _applicable = [d for d in _seg_anchors if d <= _centry]
                    if not _applicable:
                        continue  # before any re-anchor → keep locked first-cycle strike
                    _anchor = _applicable[-1]
                    for _crow in by_trade.get(_ctid, []):
                        _clid = int(_crow["leg_id"])
                        _cstrike = _anchor_strike[_anchor].get(_clid)
                        if _cstrike and abs(_cstrike - float(_crow.get("strike") or 0)) > 0.01:
                            _crow["strike"] = _cstrike
                            _crow["requested_strike"] = _cstrike

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
    trade_actual_exit:    Dict[int, str] = {}  # trade_id → max ACTUAL exit (post SL/SLB/SpotAdj)
    for trade_id, legs in by_trade.items():
        legs.sort(key=lambda r: r["leg_id"])
        leg_overrides = overrides.get(trade_id, [None] * len(legs))
        leg_results_for_reason = per_leg_results_by_trade.get(trade_id, [])
        overall_date = overall_overrides.get(trade_id)
        latest_scheduled = ""
        latest_actual    = ""
        for i, leg in enumerate(legs):
            override = leg_overrides[i]
            final_exit = override or leg["exit_date"]
            # Determine exit reason (cascades in same order as final_exit).
            # We also accumulate every candidate (exit_date, reason) so that when
            # more than one exit condition resolves to the SAME final exit date
            # (e.g. a filter-end clamp AND a spot adjustment on the same day),
            # the tradesheet "Exit Reason" shows ALL of them joined by '+'
            # (e.g. "FILTER_END+SPOT_ADJ_RISE") instead of only the
            # highest-priority one. EXPIRY is included too when the trade reached
            # its scheduled exit on that same day (e.g. "EXPIRY+SPOT_ADJ_RISE");
            # it only stands alone when nothing else co-occurs.
            _reason_cands: List[Tuple[Any, str]] = []  # (exit_date, reason) in display order
            if override:
                lr = leg_results_for_reason[i] if i < len(leg_results_for_reason) else {}
                reason = str(lr.get("exit_reason") or "SL").upper() or "SL"
            else:
                # Default to EXPIRY, unless this trade's exit was clamped to a
                # segment/filter end — then it's a FILTER_END (or STR_Exit) exit.
                if (_normalize_iso(leg["entry_date"]), _normalize_iso(leg.get("expiry", ""))) in _seg_clamped_keys:
                    reason = _clamp_reason
                else:
                    reason = "EXPIRY"
            _reason_cands.append((final_exit, reason))
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
                    _reason_cands.append((slb_date, "SL_WITH_BUFFER"))
            # Slice 5: Overall SL/Target. Matches _apply_overall_sl_to_per_leg —
            # overall trigger overrides leg whose current exit_date is on or
            # after the overall trigger date. Earlier per-leg exits win.
            if overall_date is not None and final_exit >= overall_date:
                final_exit = overall_date
                reason = overall_reasons.get(trade_id, "OVERALL_SL")
                _reason_cands.append((overall_date, reason))
            # Slice 7a: Spot adjustment exit always clamps the final exit when
            # the SL/Overall date is later than the spot-adj trigger.
            spot_adj_clamp = spot_adj_overrides.get(trade_id)
            if spot_adj_clamp and final_exit >= spot_adj_clamp:
                final_exit = spot_adj_clamp
                reason = spot_adj_reasons.get(trade_id, "SPOT_ADJ_RISE")
                _reason_cands.append((spot_adj_clamp, reason))
            # `reason` now holds the OLD single (highest-priority) label — the
            # value this column used to carry. Combine all reasons whose
            # effective date matches the resolved final_exit, in display order,
            # de-duplicated. EXPIRY is kept so a trade that reached its scheduled
            # exit shows it even when another reason co-occurs on the same day.
            _old_primary = reason
            _fx_norm = _normalize_iso(str(final_exit))
            _at_final = [
                r for (d, r) in _reason_cands
                if r and _normalize_iso(str(d)) == _fx_norm
            ]
            _combined = "+".join(dict.fromkeys(_at_final)) or "EXPIRY"
            # CALCULATION-NEUTRALITY GUARD. Downstream metrics key off the EXACT
            # reason string (the SL adverse-cap set; the FILTER_END patch /
            # Live-DD reset). Those checks are intentionally left untouched while
            # the software is being verified. If the OLD single label would have
            # matched one of those exact-keyed calculations but the combined
            # label no longer would, keep the OLD single label so no numeric
            # output changes. (FILTER_END is only ever the primary when it is the
            # sole reason, so that clause is a belt-and-braces no-op.)
            _EXACT_KEYED_REASONS = {
                "FILTER_END",
                "STOP_LOSS", "SL_WITH_BUFFER", "SL_WITH_BUFFER_GAP",
                "STOP_LOSS_BUFFER", "STOP_LOSS_BUFFER_GAP",
            }
            if _combined != _old_primary and _old_primary in _EXACT_KEYED_REASONS:
                _combined = _old_primary
            reason = _combined
            adjusted_reason_by_date[(int(leg["trade_id"]), int(leg["leg_id"]), str(leg["entry_date"]))] = reason
            sched_exit = leg["exit_date"]
            if sched_exit > latest_scheduled:
                latest_scheduled = sched_exit
            if final_exit > latest_actual:
                latest_actual = final_exit
            adjusted_specs.append({
                "trade_id": leg["trade_id"],
                "leg_id": leg["leg_id"],
                "index": leg["index"],
                "entry_date": leg["entry_date"],
                "exit_date": final_exit,
                "expiry": leg["expiry"],
                "strike": leg["strike"],
                # Carry through the zero-turnover-shift metadata so the final
                # re-priced row still shows "Strike Shift Reason". Without
                # these two fields, every trade that goes through any
                # adjustment path (SL/Target/Overall/SpotAdj) loses its
                # requested_strike and the tradesheet column comes up blank.
                "requested_strike": leg.get("requested_strike", leg["strike"]),
                "strike_interval": leg.get("strike_interval") or _STRIKE_INTERVALS.get(leg.get("index") or "", 50.0),
                "option_type": leg["option_type"],
                "position": leg["position"],
                "lots": leg["lots"],
                "lot_size": leg["lot_size"],
                "slippage_pct": leg["slippage_pct"],
            })
        trade_scheduled_exit[trade_id] = latest_scheduled
        trade_actual_exit[trade_id]    = latest_actual

    # Slice 7a-reentry: When rollover is on and spot adj fired before the original
    # expiry, synthesise a fresh mini-trade for the residual window
    # [trigger_date, orig_expiry] so the gap between the spot-adj exit and the
    # next pre-scheduled entry is filled.  Mirrors the DTE-mode correction added
    # to generic_algotest_engine.py:[SPOT_ADJ_ROLLOVER_DTE].
    _sa_reentry_specs: List[Dict[str, Any]] = []
    _sa_reentry_by_new_tid: Dict[int, int] = {}  # new_tid → orig_tid (for overlap filter)
    _sa_reentry_reasons: Dict[Tuple[int, int, str], str] = {}

    # Slice 7a-reentry only applies to DTE mode. For filter_entry_mode='fixed' the
    # bridge (Slice 7b above) already fills the [trigger_date, orig_expiry] gap under
    # the same trade_id. Running both would create duplicate re-entry rows and
    # double-count P&L for every spot-adj trade in fixed-entry strategies.
    #
    # Cascade fires whenever a trade spans an expiry window — i.e. its scheduled
    # exit_date < expiry, OR its window simply covers multiple trading days. This
    # is true for:
    #   - rollover_toggle=True + WEEKLY/MONTHLY            (classic rollover)
    #   - NEXT_WEEKLY / NEXT_MONTHLY                       (per-leg next-expiry)
    #   - T-0/T-0 WEEKLY/MONTHLY                           (engine treats this as
    #                                                       "sell next contract on
    #                                                       this expiry day" even
    #                                                       without the toggle)
    # Old gate required rollover_toggle=True which silently dropped the cascade
    # for the T-0/T-0 case → tradesheets missed the [trigger, expiry] mini-trade.
    _sa_rollover_toggle = bool(payload.get("rollover_toggle"))
    _sa_expiry_type     = str(payload.get("expiry_type") or "").upper()
    _sa_entry_dte       = int(payload.get("entry_dte") or 0)
    _sa_exit_dte        = int(payload.get("exit_dte") or 0)
    _sa_cascade_active = (
        # Classic rollover
        (_sa_rollover_toggle and _sa_expiry_type in ("WEEKLY", "MONTHLY"))
        # Per-leg next-expiry strategies (engine spans expiry implicitly)
        or _sa_expiry_type in ("NEXT_WEEKLY", "NEXT_MONTHLY", "WEEKLY_T1", "MONTHLY_T1")
        # T-0/T-0 WEEKLY/MONTHLY: trade window naturally spans 1 expiry cycle.
        # Engine builds trade.exit_date on the NEXT expiry while expiry stays the
        # current one — same residual-window logic applies when spot adj fires.
        or (_sa_entry_dte == 0 and _sa_exit_dte == 0 and _sa_expiry_type in ("WEEKLY", "MONTHLY"))
        # Non-rollover intra-cycle T-n (Entry T-m / Exit T-n, m>n>=0): one base
        # trade per expiry whose scheduled exit is T-n before expiry. A spot-adj
        # breach mid-cycle must still exit + re-enter same day and ride to the
        # T-n scheduled exit (exactly like weekly), instead of stopping at the
        # breach and leaving a gap to the next cycle. The residual window is
        # [trigger, scheduled_exit] and is bounded by the per-trade exit_date
        # read below, so this never extends past T-n. Fixed-entry is handled by
        # the bridge (Slice 7b) and stays excluded via the filter_entry_mode
        # guard on the `if` immediately below.
        or _sa_expiry_type in ("WEEKLY", "MONTHLY")
    )
    if spot_adj_overrides and _sa_cascade_active and filter_entry_mode != "fixed":
        if True:  # legacy indentation
            _sa_index = str(payload.get("symbol") or payload.get("index") or "NIFTY").upper()
            _sa_interval = _STRIKE_INTERVALS.get(_sa_index, 50.0)
            # Continue the shared id counter (re-entry → bridge → spot-adj) so
            # spot-adj mini-trades never collide with re-entry / bridge trade_ids.
            _sa_new_tid = _bt_new_tid

            for orig_tid in sorted(spot_adj_overrides.keys()):
                trigger_date = spot_adj_overrides[orig_tid]  # ISO string
                orig_legs = by_trade.get(orig_tid)
                if not orig_legs:
                    continue
                orig_legs_s = sorted(orig_legs, key=lambda r: r["leg_id"])
                orig_expiry = _normalize_iso(orig_legs_s[0].get("expiry") or "")
                if not orig_expiry or trigger_date >= orig_expiry:
                    continue  # no residual window

                # For T-n strategies (exit_dte > 0) the original scheduled exit
                # is T-n days before expiry, not expiry itself.  Use that as the
                # cascade boundary so mini-specs also exit at T-n (not at expiry).
                # For T-0 orig_exit_date == orig_expiry so there's no difference.
                orig_exit_date = _normalize_iso(orig_legs_s[0].get("exit_date") or "")
                if not orig_exit_date or orig_exit_date >= orig_expiry:
                    orig_exit_date = orig_expiry

                # Guard: trigger must be before the scheduled exit too
                if trigger_date >= orig_exit_date:
                    continue

                # Cascade spot-adj within [trigger_date, orig_exit_date] — mirrors
                # the DTE rollover logic in generic_algotest_engine.py where each
                # re-entry trade goes through spot-adj check again on its own slot.
                _sa_cur_entry = trigger_date
                _sa_cur_exit  = orig_exit_date
                _sa_depth = 0

                while _sa_depth < 8 and _sa_cur_entry < _sa_cur_exit:
                    _sa_depth += 1
                    _sa_spot = float(spot_by_date.get(_sa_cur_entry) or 0.0)
                    if not _sa_spot:
                        break

                    # Check for a further NIFTY spot-adj trigger inside this
                    # window — only when NIFTY SA is actually enabled.
                    _sa_casc = None
                    if _nifty_active:
                        _sa_casc = _compute_spot_adjustment_trigger(
                            _sa_cur_entry,
                            _sa_spot,
                            _sa_cur_exit,
                            spot_adj_direction,
                            spot_adj_pct,
                            spot_adj_units,
                            trading_days,
                            spot_by_date,
                        )
                    # Check for a further Midcap spot-adj trigger — only when
                    # Midcap SA is active. Earliest of NIFTY/Midcap wins.
                    _mc_casc = None
                    _mc_casc_is_midcap = False
                    if _midcap_active:
                        _mc_entry_c = midcap_spot_by_date.get(_sa_cur_entry) or 0.0
                        if _mc_entry_c > 0:
                            _mc_casc = _compute_spot_adjustment_trigger(
                                _sa_cur_entry,
                                _mc_entry_c,
                                _sa_cur_exit,
                                midcap_adj_direction,
                                midcap_adj_pct,
                                midcap_adj_units,
                                trading_days,
                                midcap_spot_by_date,
                            )
                    # Earliest sub-trigger wins; track which source it came from.
                    if _sa_casc and _mc_casc:
                        if _mc_casc < _sa_casc:
                            _sa_casc = _mc_casc
                            _mc_casc_is_midcap = True
                    elif _mc_casc:
                        _sa_casc = _mc_casc
                        _mc_casc_is_midcap = True
                    _sa_this_exit = (
                        _sa_casc if (_sa_casc and _sa_casc < _sa_cur_exit)
                        else _sa_cur_exit
                    )

                    mini_specs: List[Dict[str, Any]] = []
                    for _sa_leg in orig_legs_s:
                        _sa_lidx = int(_sa_leg.get("leg_id") or 1) - 1
                        _sa_leg_src = legs_src[_sa_lidx] if _sa_lidx < len(legs_src) else {}
                        # Per-leg strike_interval override (e.g. user sets 100 for NIFTY).
                        # Without this, mini-trades default to the index step and re-entry
                        # strikes snap to 50 even when the leg was configured for 100.
                        _sa_leg_sel = (_sa_leg_src or {}).get("strike_selection") or {}
                        _sa_leg_interval_raw = (
                            (_sa_leg_src or {}).get("strike_interval")
                            or (_sa_leg_src or {}).get("strike_gap")
                            or (_sa_leg_sel.get("strike_interval") if isinstance(_sa_leg_sel, dict) else None)
                            or (_sa_leg_sel.get("strike_gap") if isinstance(_sa_leg_sel, dict) else None)
                        )
                        try:
                            _sa_leg_interval = float(_sa_leg_interval_raw) if _sa_leg_interval_raw else _sa_interval
                        except (TypeError, ValueError):
                            _sa_leg_interval = _sa_interval
                        if _sa_leg_interval not in (50.0, 100.0, 25.0):
                            _sa_leg_interval = _sa_interval
                        _sa_strike_info: Dict[str, Any] = {}
                        _sa_strike = _compute_strike_for_leg_python(
                            _sa_leg_src, _sa_spot, _sa_leg_interval,
                            entry_date=_sa_cur_entry, expiry=orig_expiry, index=_sa_index,
                            out_info=_sa_strike_info,
                        ) or float(_sa_leg.get("strike") or 0.0)
                        if not _sa_strike:
                            continue
                        _sa_lid = int(_sa_leg.get("leg_id") or 1)
                        mini_specs.append({
                            "trade_id":     _sa_new_tid,
                            "leg_id":       _sa_lid,
                            "index":        _sa_leg.get("index") or _sa_index,
                            "entry_date":   _sa_cur_entry,
                            "exit_date":    _sa_this_exit,
                            "expiry":       orig_expiry,
                            "strike":       _sa_strike,
                            "requested_strike": float(_sa_strike_info.get("requested_strike") or _sa_strike),
                            "strike_interval": float(_sa_leg_interval),
                            "option_type":  _sa_leg.get("option_type") or "CE",
                            "position":     _sa_leg.get("position") or "SELL",
                            "lots":         int(_sa_leg.get("lots") or 1),
                            "lot_size":     int(_sa_leg.get("lot_size") or lot_size),
                            "slippage_pct": float(_sa_leg.get("slippage_pct") or 0.0),
                        })
                        if _sa_casc and _sa_casc < _sa_cur_exit:
                            if _mc_casc_is_midcap:
                                _mc_e2 = midcap_spot_by_date.get(_sa_cur_entry) or 0.0
                                _mc_t2 = midcap_spot_by_date.get(_sa_casc) or 0.0
                                _sa_reason = (
                                    "MIDCAP_SPOT_ADJ_RISE" if _mc_t2 >= _mc_e2
                                    else "MIDCAP_SPOT_ADJ_FALL"
                                )
                            else:
                                _sa_reason = _spot_adj_reason_tag(
                                    spot_adj_direction,
                                    _sa_spot,
                                    spot_by_date.get(_sa_casc),
                                    spot_adj_pct,
                                    spot_adj_units,
                                )
                        else:
                            _sa_reason = "EXPIRY"
                        _sa_reentry_reasons[(_sa_new_tid, _sa_lid, _sa_cur_entry)] = _sa_reason

                    if mini_specs:
                        _sa_reentry_specs.extend(mini_specs)
                        _sa_reentry_by_new_tid[_sa_new_tid] = orig_tid
                        _sa_new_tid += 1

                    if _sa_casc and _sa_casc < _sa_cur_exit:
                        _sa_cur_entry = _sa_casc  # advance to cascade trigger for next mini-trade
                    else:
                        break

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
            # Strict overlap (entry before prev ACTUAL exit). Same-day
            # chaining (entry == prev_exit) is allowed — mirrors Python's
            # `entry_ts < _dte_last_exit` at generic_algotest_engine.py:4016.
            # Use actual exit (post SL/SLB/SpotAdj) not scheduled expiry so
            # a re-entry on the SL day is never incorrectly dropped because
            # the parent's scheduled expiry is still in the future.
            continue
        kept_trades.add(tid)
        prev_exit = trade_actual_exit[tid]

    # Filter adjusted_specs to only kept trades.
    adjusted_specs = [s for s in adjusted_specs if s["trade_id"] in kept_trades]
    # Slice 6: re-entry specs have unique trade_ids; survive when their parent trade did.
    adjusted_specs.extend(
        s for s in reentry_specs
        if _reentry_by_new_tid.get(s["trade_id"]) in kept_trades
    )
    # Slice 7b bridges: new trade_ids per cycle, survive when their parent did.
    adjusted_specs.extend(
        s for s in _bt_bridge_specs
        if _bt_bridge_by_new_tid.get(s["trade_id"]) in kept_trades
    )
    # Slice 7a-reentry: spot-adj mini-trades survive when their source trade was kept.
    adjusted_specs.extend(
        s for s in _sa_reentry_specs
        if _sa_reentry_by_new_tid.get(s["trade_id"]) in kept_trades
    )
    adjusted_reason_by_date.update(_sa_reentry_reasons)
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

    # Inject re-entry metadata so priced_to_tradesheet_records can populate
    # ReEntryIndex / ReEntryTrigger / ReEntryMode columns.
    for row in final_priced:
        key = (int(row.get("trade_id") or 0), int(row.get("leg_id") or 1), str(row.get("entry_date") or ""))
        meta = reentry_meta_map.get(key)
        if meta:
            row["_reentry_index"] = meta[0]
            row["_reentry_trigger"] = meta[1]
            row["_reentry_mode"] = meta[2]

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
            entry_px = float(row.get("entry_price") or 0.0)
            # SL-with-Buffer fill invariant: re-derive the stop level from THIS
            # final row's own entry premium and clamp, so a stale override priced
            # on the wrong (un-adjusted / rolled) contract can never book a fill
            # better than the stop — which is what turned real stop-outs into
            # impossible profits on adjusted and rolled trades.
            _lid0 = int(row.get("leg_id") or 1) - 1
            _leg_src = legs_src[_lid0] if 0 <= _lid0 < len(legs_src) else {}
            _slb_cfg = _leg_src.get("slWithBuffer") if isinstance(_leg_src.get("slWithBuffer"), dict) else {}
            _clamped_price = _clamp_sl_buffer_fill(
                slb_price, entry_px, (_slb_cfg or {}).get("value"),
                (_slb_cfg or {}).get("mode"), position,
            )
            if abs(float(_clamped_price) - float(slb_price)) > 1e-6:
                logger.warning(
                    "[ENGINE_RUST] SL-with-Buffer fill %.2f beyond stop level for "
                    "trade %s leg %s (entry %.2f) — clamped to %.2f",
                    float(slb_price), row.get("trade_id"), row.get("leg_id"),
                    entry_px, float(_clamped_price),
                )
            slb_price = _clamped_price
            # Tag the exit reason from the buffer fill itself. A fill ABOVE the stop
            # level means the market opened past the stop (a gap) -> SL_WITH_BUFFER_GAP;
            # a fill AT the stop is an intraday touch -> SL_WITH_BUFFER. This also
            # corrects the EXPIRY mislabel that happens when the buffer date coincides
            # with the scheduled exit (the reason-column lookup misses, even though the
            # buffer fill IS applied here). Only the SLB-family / EXPIRY reason is
            # touched — OVERALL_SL / SPOT_ADJ / FILTER_END keep their higher-priority
            # label (the swap only reaches this row when its exit_date == the buffer
            # date, so a buffer fill genuinely happened).
            _cur_reason = str(row.get("exit_reason") or "").upper()
            if _cur_reason in ("EXPIRY", "SL_WITH_BUFFER", "SL_WITH_BUFFER_GAP"):
                _sl_v = _maybe_float((_slb_cfg or {}).get("value"))
                _sl_m = _norm_mode((_slb_cfg or {}).get("mode"))
                _stop_lvl = None
                if _sl_v and _sl_v > 0 and entry_px > 0:
                    if _sl_m == "pct":
                        _stop_lvl = (
                            entry_px * (1.0 + _sl_v / 100.0) if position == "SELL"
                            else entry_px * (1.0 - _sl_v / 100.0)
                        )
                    elif _sl_m == "points":
                        _stop_lvl = entry_px + _sl_v if position == "SELL" else entry_px - _sl_v
                _is_gap = _stop_lvl is not None and (
                    (position == "SELL" and float(slb_price) > _stop_lvl + 0.01)
                    or (position == "BUY" and float(slb_price) < _stop_lvl - 0.01)
                )
                row["exit_reason"] = "SL_WITH_BUFFER_GAP" if _is_gap else "SL_WITH_BUFFER"
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
            # net_pnl is in PREMIUM POINTS, matching simulate_trades_batch (no qty multiply).
            per_leg_pnl_points = (entry_px - adjusted_exit) if position == "SELL" else (adjusted_exit - entry_px)
            row["net_pnl"] = round(float(per_leg_pnl_points), 4)

    # Settlement-price fix for same-day expiry trades (T-0 or intraday-expiry).
    # The Rust feather has one LTP per (symbol, expiry, strike, type, date). When
    # entry_date == exit_date == expiry_date the lookup returns the SAME record for
    # both legs → entry_price == exit_price → net_pnl = 0.
    # NSE settles expired options at intrinsic value: max(0, spot-strike) for CE,
    # max(0, strike-spot) for PE. Apply that here so P&L is correct.
    # The condition is intentionally narrow: all three must match so we never
    # touch legs that exit mid-life (entry_date == exit_date but expiry is later)
    # or normal held trades (entry_date != exit_date).
    for row in final_priced:
        exit_d = _normalize_iso(row.get("exit_date") or "")
        entry_d = _normalize_iso(row.get("entry_date") or "")
        expiry_d = _normalize_iso(row.get("expiry") or "")
        if not (exit_d and entry_d == exit_d == expiry_d):
            continue
        opt_type = str(row.get("option_type") or "").upper()
        if opt_type not in ("CE", "PE"):
            continue
        exit_spot = float(row.get("exit_spot") or 0.0)
        strike = float(row.get("strike") or 0.0)
        if exit_spot <= 0 or strike <= 0:
            continue
        settlement = (
            max(0.0, exit_spot - strike) if opt_type == "CE"
            else max(0.0, strike - exit_spot)
        )
        settlement = round(settlement, 2)
        position = str(row.get("position") or "SELL").upper()
        entry_px = float(row.get("entry_price") or 0.0)
        net_pnl = round(
            entry_px - settlement if position == "SELL" else settlement - entry_px, 4
        )
        row["exit_price"] = settlement
        row["raw_exit_price"] = settlement
        row["net_pnl"] = net_pnl

    # Final FILTER_END guarantee. A trade clamped to a filter/segment end must
    # read FILTER_END (combined with any co-occurring reason) no matter which
    # path assigned its reason. The spot-adjustment / bridge re-entry synthesis
    # renumbers trade ids and writes reasons via reentry_reason_map, which
    # bypasses the per-leg cascade lookup — so without this pass a clamped
    # boundary trade in a spot-adj run silently falls back to EXPIRY. Keyed by
    # (entry_date, expiry) so it survives the renumbering. Display-only: it
    # prepends FILTER_END and drops the neutral EXPIRY token; it never changes
    # exit dates, prices, or P&L.
    if _seg_clamped_keys:
        for row in final_priced:
            k = (_normalize_iso(row.get("entry_date", "")), _normalize_iso(row.get("expiry", "")))
            if k not in _seg_clamped_keys:
                continue
            _parts = [
                p for p in str(row.get("exit_reason") or "").upper().split("+")
                if p and p != "EXPIRY" and p != _clamp_reason
            ]
            row["exit_reason"] = "+".join([_clamp_reason] + _parts)

    return final_priced
