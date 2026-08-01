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
import os
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


def _leg_slippage_pct(leg_src: Any) -> float:
    """Per-leg slippage percentage. There is no strategy-level slippage_pct
    anymore — each leg carries its own (frontend field: slippage_pct, set by
    the leg's own Slippage % input). Absent/invalid defaults to 0 (no
    slippage), not a fallback to any global value."""
    if not isinstance(leg_src, dict):
        return 0.0
    try:
        return max(0.0, float(leg_src.get("slippage_pct") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _apply_per_leg_slippage(specs: List[Dict[str, Any]], legs_src: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rust's resolve_trade_specs bakes ONE payload-level slippage_pct onto
    every spec it builds. Overwrite it per-spec with that spec's own leg's
    slippage_pct, since slippage is configured per leg now, not globally."""
    for spec in specs:
        try:
            leg_idx = int(spec.get("leg_id") or 0) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= leg_idx < len(legs_src):
            spec["slippage_pct"] = _leg_slippage_pct(legs_src[leg_idx])
    return specs


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


def _compute_confirm_trigger(
    entry_date: str,
    scheduled_exit: str,
    nifty_base: float,
    midcap_base: float,
    nifty_direction: str,
    nifty_pct: float,
    nifty_units: str,
    midcap_direction: str,
    midcap_pct: float,
    midcap_units: str,
    n_days: int,
    trading_days: List[str],
    nifty_by_date: Dict[str, float],
    midcap_by_date: Dict[str, float],
) -> Tuple[Optional[str], Optional[str]]:
    """
    "Confirm within N days, SAME direction" spot-adjustment trigger.

    Optional combine mode used ONLY when both NIFTY and Midcap spot adjustment are
    active and ``spot_adjustment_combine_mode == 'confirm'``. Differs from the
    default "earliest" behaviour (``_compute_spot_adjustment_trigger`` per index,
    whichever fires first).

    Breach is evaluated DAILY (not latched): on each trading day d in
    (entry_date, scheduled_exit] an index is "breached RISE/FALL" if its close vs
    its own entry-day base crosses its own band THAT day. Scanning forward, on day
    d we look at the rolling window of d and the previous ``n_days`` trading days;
    if within that window NIFTY has a breach in direction X *and* Midcap has a
    breach in the SAME direction X, we confirm and return (d, X). n_days is counted
    in trading days; n_days=0 means both must breach on the same day. Returns
    (None, None) if no confirmation occurs in the window.
    """
    if nifty_base <= 0 or midcap_base <= 0 or nifty_pct <= 0 or midcap_pct <= 0:
        return None, None

    def _band(base: float, pct: float, units: str) -> Tuple[float, float]:
        if units == "points":
            return base + pct, base - pct
        return base * (1.0 + pct / 100.0), base * (1.0 - pct / 100.0)

    n_rt, n_ft = _band(nifty_base, nifty_pct, nifty_units)
    m_rt, m_ft = _band(midcap_base, midcap_pct, midcap_units)
    n_wr = nifty_direction in ("rise", "both")
    n_wf = nifty_direction in ("fall", "both")
    m_wr = midcap_direction in ("rise", "both")
    m_wf = midcap_direction in ("fall", "both")

    def _bdir(close: Optional[float], rt: float, ft: float, wr: bool, wf: bool) -> Optional[str]:
        if close is None:
            return None
        if wr and close >= rt:
            return "RISE"
        if wf and close <= ft:
            return "FALL"
        return None

    scan = [d for d in trading_days if entry_date < d <= scheduled_exit]
    if not scan:
        return None, None
    n_bd = {d: _bdir(nifty_by_date.get(d), n_rt, n_ft, n_wr, n_wf) for d in scan}
    m_bd = {d: _bdir(midcap_by_date.get(d), m_rt, m_ft, m_wr, m_wf) for d in scan}
    posn = {d: i for i, d in enumerate(scan)}
    for d in scan:
        lo = posn[d] - n_days
        win = [w for w in scan if lo <= posn[w] <= posn[d]]
        for direction in ("RISE", "FALL"):
            if any(n_bd[w] == direction for w in win) and any(m_bd[w] == direction for w in win):
                return d, direction
    return None, None


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
    from services.leg_filter import seg_iso as _seg_iso
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
            segs.append((_seg_iso(s["start"]), _seg_iso(s["end"])))
        except Exception:
            continue
    segs.sort()
    return segs


def _last_trading_day_on_or_before(target: str, trading_days: List[str]) -> Optional[str]:
    """Return the latest trading day <= target, or None.

    Delegates to services.leg_filter so the per-leg filter module and the engine
    snap boundaries through ONE implementation (see leg_filter.resolve_leg_window).
    """
    from services.leg_filter import last_trading_day_on_or_before

    return last_trading_day_on_or_before(target, trading_days)


def _next_trading_day_on_or_after(trading_days: List[str], date_str: str) -> Optional[str]:
    """Return the first trading day >= date_str, or None."""
    if not trading_days or not date_str:
        return None
    idx = bisect.bisect_left(trading_days, date_str)
    return trading_days[idx] if idx < len(trading_days) else None


# Reasons that must NOT be overridden by FILTER_END (they feed downstream
# exact-match calcs — SL adverse-cap, etc.). Mirrors _EXACT_KEYED_REASONS used
# in the per-leg cascade combination.
_FILTER_END_SKIP_REASONS: frozenset = frozenset({
    "STOP_LOSS", "SL_WITH_BUFFER", "SL_WITH_BUFFER_GAP",
    "STOP_LOSS_BUFFER", "STOP_LOSS_BUFFER_GAP",
    # A leg truncated by its OWN filter file. _apply_filter_end_last_per_patch
    # tags the last trade of each STRATEGY patch; without this it would strip
    # this per-leg tag from every trade that is not that patch's last one.
    "LEG_FILTER_END",
})


def _apply_filter_end_last_per_patch(
    rows: List[Dict[str, Any]],
    segs: Optional[List[Tuple[str, str]]],
    clamp_reason: str,
) -> None:
    """Tag FILTER_END (or STR_Exit) on the LAST trade of each filter patch and
    strip a stale clamp tag from every other trade — IN PLACE on `rows`.

    The patch's last trade = the trade with the maximum exit date among trades
    whose entry falls inside the segment. This is the user's rule: 'the last
    exit of each patch is the filter end'. Anchoring on the last *exit* (not on
    the original base trade's entry+expiry) correctly handles two cases the old
    marker missed: (a) spot-adjustment splitting the boundary trade — the marker
    used to stick to the FIRST split piece; (b) the patch end landing exactly on
    a contract expiry — the boundary trade was never 'clamped' so it got no
    marker at all.

    Display-only: changes ONLY exit_reason. It never overrides a genuine SL exit
    (keeps SL adverse-cap calcs untouched). Downstream patch / Live-DD resets
    correctly follow the corrected FILTER_END position.
    """
    if not segs:
        return
    cu = clamp_reason.upper()
    # Per trade_id: earliest entry and latest exit (aggregate across legs).
    t_entry: Dict[int, str] = {}
    t_exit: Dict[int, str] = {}
    for r in rows:
        tid = int(r.get("trade_id") or 0)
        e = _normalize_iso(r.get("entry_date", ""))
        x = _normalize_iso(r.get("exit_date", ""))
        if tid not in t_entry or e < t_entry[tid]:
            t_entry[tid] = e
        if tid not in t_exit or x > t_exit[tid]:
            t_exit[tid] = x
    # Last trade (max exit, tie-broken by latest entry) per patch.
    last_tids: set = set()
    for s_start, s_end in segs:
        ss = _normalize_iso(s_start)
        se = _normalize_iso(s_end)
        best_key = None
        best_tid = None
        for tid, ent in t_entry.items():
            if ss <= ent <= se:
                key = (t_exit[tid], ent)
                if best_key is None or key > best_key:
                    best_key = key
                    best_tid = tid
        if best_tid is not None:
            last_tids.add(best_tid)
    for r in rows:
        tid = int(r.get("trade_id") or 0)
        toks = [p for p in str(r.get("exit_reason") or "").split("+") if p]
        toks_wo_clamp = [p for p in toks if p.upper() != cu]
        if tid in last_tids:
            rest = [
                p for p in toks_wo_clamp
                if p.upper() not in ("EXPIRY", "SCHEDULED_EXIT")
            ]
            # Genuine SL exit on the patch's last day → leave it (calc-neutral).
            if any(p.upper() in _FILTER_END_SKIP_REASONS for p in rest):
                r["exit_reason"] = "+".join(toks_wo_clamp) or "EXPIRY"
            else:
                r["exit_reason"] = "+".join([clamp_reason] + rest)
        else:
            # Not the patch boundary → drop any stale clamp tag.
            r["exit_reason"] = "+".join(toks_wo_clamp) or "EXPIRY"


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

# ── SAME-INDEX MIXED EXPIRY (weekly cadence + monthly leg) ────────────────────
# A leg COARSER than the run cadence (MONTHLY leg while the cadence is WEEKLY)
# holds its own contract across several cadence re-books — structurally the same
# thing YEARLY already does with its pinned December (see _pin at the leg loop
# below). Everything here is inert unless such a leg exists, so a run whose legs
# all match the cadence takes byte-identical paths to before.
_MONTHLY_LEG_TYPES: frozenset = frozenset({"MONTHLY", "NEXT_MONTHLY", "MONTHLY_T1"})
# FULL PER-LEG SPOT-ADJ INDEPENDENCE. When True, a leg carrying its OWN adjustment
# config keeps BOTH its anchor and its strike when the trade is cut by ANOTHER
# leg's breach — that breach only BREAKS THE TRADE UP. Reset happens solely on the
# leg's own breach, its own contract roll, or a patch start. Applies to weekly /
# monthly / yearly alike, keyed on the leg's own expiry changing.
# Kept as a module flag so the parity harness can obtain a genuine "before", and so
# the behaviour can be switched off without a code change if it proves wrong.
_PER_LEG_INDEPENDENT: bool = True
_WEEKLY_CADENCE_TYPES: frozenset = frozenset({"WEEKLY", "NEXT_WEEKLY", "WEEKLY_T1"})


def _has_monthly_pinned_leg(payload: Dict[str, Any]) -> bool:
    """True when a leg is pinned to a contract COARSER than the run cadence
    (a MONTHLY leg under a WEEKLY cadence). Such a leg holds one contract across
    several cadence re-books, so its strike epoch and its spot-adj re-anchor must
    both follow the CONTRACT, not the weekly trade."""
    if str(payload.get("expiry_type") or "").upper() not in _WEEKLY_CADENCE_TYPES:
        return False
    return any(
        isinstance(_l, dict)
        and str(_l.get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
        and str(_l.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
        for _l in (payload.get("legs") or [])
    )


def _resolve_monthly_pin(
    monthly_expiries: List[str],
    sched_exit: str,
    offset: int = 0,
    trading_days: Optional[List[str]] = None,
    exit_dte: int = 0,
) -> Optional[str]:
    """Contract for a MONTHLY-pinned leg: first monthly expiry >= `sched_exit`.

    `sched_exit` is the trade's exit AS KNOWN AT SPEC-BUILD TIME: after segment /
    filter-end clamping, but BEFORE the spot-adjustment / SL cascade truncates
    anything. Both halves matter.

    * Clamping is already applied and that is correct — it is derived purely from
      the filter segment, so it is deterministic. A trade clamped to 31-Jul should
      hold the July contract, not August, since it genuinely ends on 31-Jul.
    * The cascade must NOT be used. It runs after specs are built, so at this
      point the engine cannot know the real exit. Resolving off it would let the
      same trade pick a different contract depending on whether a breach happened
      to fire — an irreproducible tradesheet.

    ">=" (not ">") keeps the leg on a contract that is still alive on the exit
    day. On the monthly-expiry week that means the pinned leg and the cadence leg
    land on the SAME contract for one cycle — the calendar spread collapses to a
    single expiry. That is the deliberate "ride to expiry" reading; switching to
    early-roll is a one-character change here (">=" -> ">"), which is why the rule
    lives in this one function.

    `offset` shifts further out for NEXT_MONTHLY. Returns None when the list runs
    out, so the caller drops just that trade and keeps chaining.
    """
    for i, m in enumerate(monthly_expiries):
        # Holdable through the trade's exit UNDER ITS OWN T-n. The cadence leg
        # already rolls at T-n — it abandons a contract once that contract's T-n
        # exit has passed — so a pinned leg testing the raw expiry was rolling on a
        # different rule from its own basket. Where a patch boundary lands exactly
        # on a monthly expiry they disagreed about the SAME date: NIFTY 27-Mar-2024
        # trade exiting 28-Mar, the weekly leg rolled to 04-Apr while the monthly
        # leg stayed on the 28-Mar contract and died that day, losing 219 pts
        # (335.85 -> 116.60) to terminal decay.
        # With exit_dte = 0 the T-n exit IS the expiry, so this reduces exactly to
        # the previous `m >= sched_exit` test and every T-0 run is untouched.
        _m_exit = (
            _trading_day_n_before(m, exit_dte, trading_days)
            if (trading_days and exit_dte > 0)
            else m
        )
        if _m_exit is None:
            continue
        if _m_exit >= sched_exit:
            j = i + offset
            return monthly_expiries[j] if j < len(monthly_expiries) else None
    return None

# ── YEARLY expiry ─────────────────────────────────────────────────────────────
# YEARLY trades NSE's long-dated DECEMBER contract (26-Dec-2019 → 31-Dec-2020 →
# …) while the position is re-booked on the weekly/monthly cadence. Contract and
# cadence are therefore two different calendars — everywhere else in the engine
# they are the same list.
#
# The December expiries are already rows of the *monthly* expiry_calendar (the
# long-dated contract and the December monthly expiry are the SAME contract; it
# has simply been listed ~1826 days ahead), so no new table or ingestion.
#
# Only NIFTY has long-dated December contracts (verified: NIFTY 24 spanning
# 2010-2030; BANKNIFTY/MIDCPNIFTY/FINNIFTY have zero) — index_metadata gates it.
_YEARLY_CADENCES: frozenset = frozenset({"weekly", "monthly"})


def _opens_new_epoch(
    mode: str, prev_entry: str, entry: str, new_cycle: bool, leg_is_yearly: bool = True
) -> bool:
    """
    True when this entry must resolve a FRESH strike rather than carry the epoch's.

    Python mirror of simulate.rs::opens_new_epoch — the two MUST agree. This one
    serves the FIXED-ENTRY builder (_build_fixed_entry_specs); the Rust one serves
    the DTE rollover schedule. A strategy hits exactly one of them depending on
    filter_entry_mode, so a rule changed in only one place silently applies to
    half the users — which is exactly what happened before this was made
    leg-aware.

    The reset trigger is PER LEG:
      * FIXED (any leg)            — new yearly cycle only.
      * FRESH, weekly/monthly leg  — EVERY entry.
      * FRESH, yearly leg          — first entry of each calendar MONTH.

    Previously the month-hold applied to every leg whenever the CADENCE was
    weekly, so a weekly CE leg sat on one strike for a whole month while spot ran
    away. Measured on NIFTY 2019 (yearly Dec contract, weekly cadence, fixed
    entry): the CE leg held 11300 from 03-Oct to 31-Oct while ATM moved
    11300 -> 11900, i.e. a 600-point ITM call nobody configured.

    `leg_is_yearly` defaults to True so any caller that has not been updated keeps
    the old month-hold rather than silently switching behaviour.
    """
    if new_cycle:
        return True
    if str(mode or "fresh").lower() == "fixed":
        return False
    if leg_is_yearly:
        return entry[:7] != prev_entry[:7]
    return True


def _cycle_for_exit(
    cycles: Optional[List[Dict[str, str]]], exit_date: str
) -> Optional[Dict[str, str]]:
    """
    The December contract a segment ending on `exit_date` should hold: the FIRST
    cycle whose T-n boundary (`end`) is at or after that exit.

    T-n is a THRESHOLD, not an exact exit date. A segment must be holdable for
    its WHOLE cadence period without breaching T-n — so if the current December
    would force a mid-segment exit, the segment opens on the NEXT December
    instead. The yearly roll therefore always lands on a real cadence boundary
    (a monthly/weekly expiry) and never creates a 1-day stub.

    Keying on the EXIT (not the entry) is the whole trick: at the 26-Nov monthly
    roll the engine looks ahead to the 31-Dec exit, sees Dec-2020's T-1 boundary
    (27-Nov) falls before it, and opens on Dec-2021 right there.

    Python mirror of simulate.rs::cycle_for_exit.
    """
    if not cycles:
        return None
    for c in cycles:
        if c["end"] >= exit_date:
            return c
    return None


def _expiry_date_list(index: str, expiry_type: str, from_date: str, to_date: str) -> List[str]:
    """
    Sorted unique ISO expiry dates for (index, expiry_type) in range.

    Exact reproduction of the block previously inlined at
    algotest_job.py:334-349 — same get_expiry_dates args, same column choice,
    same sort/unique — so non-YEARLY callers get a byte-identical list.
    """
    import pandas as pd
    from base import get_expiry_dates  # type: ignore

    df = get_expiry_dates(index, expiry_type, from_date, to_date)
    if df is None or df.empty:
        return []
    col = "Current Expiry" if "Current Expiry" in df.columns else df.columns[0]
    return (
        pd.to_datetime(df[col]).sort_values().dt.strftime("%Y-%m-%d").unique().tolist()
    )


def _build_yearly_cycles(
    december_expiries: List[str],
    n_months: int,
    from_date: str,
    trading_days: List[str],
    cadence_expiries: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    Build the pinned-contract cycles consumed by simulate.rs.

    Each cycle is {contract, start, end} over the half-open window [start, end)
    keyed by a segment's *entry* date. `end` is the T-n exit: n counts MONTHS
    back from the December expiry, so n=0 means hold to expiry (the default).

    Cycle N's `start` is cycle N-1's `end`. That shared boundary is what makes
    the yearly roll — exit the old December and re-enter the new one the same
    day — fall out of the schedule builder's existing same-day chain with no
    special case.

    `december_expiries` must be widened one year on BOTH sides of the backtest:
    the first real cycle needs the PRIOR December as its start anchor, and the
    last needs the NEXT December as its contract.
    """
    import pandas as pd

    cycles: List[Dict[str, str]] = []
    prev_end: Optional[str] = None
    for contract in december_expiries:  # ascending
        if n_months <= 0:
            # T=0 → hold to expiry. Returned UNSNAPPED so the default can never
            # perturb the schedule via a trading-day snap.
            end = contract
        else:
            _tgt = pd.Timestamp(contract) - pd.DateOffset(months=n_months)
            target = _tgt.strftime("%Y-%m-%d")
            # T-n means "roll in the MONTH n months before the contract's month".
            # So take the LAST cadence boundary inside that month — never the
            # merely-nearest date, which can sit in the following month.
            #
            # Nearest-date snapping was wrong at month ends. Dec-2025 (30-Dec) at
            # T-1 targets 30-Nov; on WEEKLY cadence the neighbours are 25-Nov
            # (5 days) and 02-Dec (2 days), so "nearest" picked 02-Dec — a
            # DECEMBER boundary — and T-1 stopped meaning "roll in November".
            # MONTHLY cadence hid this: with one boundary per month the two rules
            # always agreed, which is why 2019/2020/2023/2024 were unaffected and
            # still resolve to the same dates under this rule.
            _tgt_month = _tgt.strftime("%Y-%m")
            _in_month = [c for c in (cadence_expiries or []) if c[:7] == _tgt_month]
            if _in_month:
                end = max(_in_month)
            else:
                # No cadence boundary in the target month: the target lies outside
                # the loaded cadence range — the widened PRIOR December (long
                # elapsed, dropped by the start >= end guard below) or the final
                # December beyond to_date. Fall back to the trading calendar, as
                # before, so edge cycles behave exactly as they did.
                end = _last_trading_day_on_or_before(target, trading_days)
            if not end:
                continue
        start = prev_end or from_date
        prev_end = end
        if start >= end:
            # Cycle already elapsed before the backtest starts (typically the
            # widened prior December). It still seeds the next cycle's start.
            continue
        cycles.append({"contract": contract, "start": start, "end": end})
    return cycles


def resolve_expiry_inputs(
    index: str,
    payload: Dict[str, Any],
    from_date: str,
    to_date: str,
    trading_days: List[str],
) -> Tuple[List[str], Optional[List[Dict[str, str]]]]:
    """
    Resolve (expiry_dates, yearly_cycles) for the Rust engine.

    Non-YEARLY → (the same list as before, None). Byte-identical by delegating
    to _expiry_date_list.

    YEARLY → (cadence expiries, pinned December cycles). Rust owns the schedule;
    Python only resolves dates, exactly as it already does for weekly/monthly —
    the December contract is an expiry_calendar row and the T-n offset needs the
    trading calendar, neither of which Rust carries on the EOD path.
    """
    import pandas as pd

    etype = str(payload.get("expiry_type") or "weekly").upper()

    # CALLER-SUPPLIED cycles (multi-index sync cadence). The {contract,start,end}
    # schedule has already been built from the MERGED roll boundaries across every
    # leg's own index (whichever expires first ends the cycle for all legs), so
    # honour it verbatim rather than deriving a December/yearly schedule.
    # `sync_cadence_expiry_type` names the base leg's OWN expiry basis and is used
    # only for the expiry_dates list (NEXT_WEEKLY / LAZY resolution).
    # Purely additive: nothing else pre-supplies yearly_cycles, so every existing
    # path (including real YEARLY) still falls through to the builder below.
    if etype == "YEARLY" and payload.get("yearly_cycles"):
        # `sync_cadence_expiries` (the MERGED roll boundaries across every leg's
        # index) is the cadence when supplied: the cadence list is what drives
        # entry/exit in Rust, so the earliest-expiry-wins boundary has to arrive
        # here, not just in the cycles (which only pin the contract).
        explicit = payload.get("sync_cadence_expiries")
        if explicit:
            return ([str(d) for d in explicit], list(payload["yearly_cycles"]))
        return (
            _expiry_date_list(
                index,
                payload.get("sync_cadence_expiry_type") or "weekly",
                from_date,
                to_date,
            ),
            list(payload["yearly_cycles"]),
        )

    if etype != "YEARLY":
        return (
            _expiry_date_list(
                index, payload.get("expiry_type", "weekly"), from_date, to_date
            ),
            None,
        )

    cadence = str(payload.get("rollover_cadence") or "monthly").lower()
    if cadence not in _YEARLY_CADENCES:
        raise ValueError(
            f"expiry_type=YEARLY needs rollover_cadence in {sorted(_YEARLY_CADENCES)}, "
            f"got {cadence!r}"
        )
    cadence_expiries = _expiry_date_list(index, cadence, from_date, to_date)

    wide_from = (pd.Timestamp(from_date) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    wide_to = (pd.Timestamp(to_date) + pd.DateOffset(years=1)).strftime("%Y-%m-%d")

    # Which long-dated expiry MONTHS to roll through. Default December-only, so
    # any run that doesn't set this is byte-identical to before. Selecting more
    # (e.g. ["03","12"]) makes the position alternate: the cycle builder below
    # already handles a multi-month sorted list, holding each contract until its
    # own T-n and rolling into the next selected long-dated expiry. Only the 4
    # months that actually have long-dated NIFTY contracts are allowed — the
    # other 8 months have no long-dated series to pin to.
    _LONGDATED_MONTHS = {"03", "06", "09", "12"}
    roll_months_raw = payload.get("yearly_roll_months") or ["12"]
    roll_months = {str(m).zfill(2) for m in roll_months_raw}
    _bad = roll_months - _LONGDATED_MONTHS
    if _bad:
        raise ValueError(
            f"expiry_type=YEARLY: yearly_roll_months must be a subset of "
            f"{sorted(_LONGDATED_MONTHS)} (March/June/September/December — the only "
            f"months with long-dated contracts); got invalid {sorted(_bad)}."
        )
    december = [
        d for d in _expiry_date_list(index, "monthly", wide_from, wide_to)
        if d[5:7] in roll_months
    ]
    if not december:
        raise ValueError(
            f"expiry_type=YEARLY: no long-dated contract found for {index} "
            f"(months {sorted(roll_months)}) in {wide_from}..{wide_to}."
        )

    try:
        n_months = int(payload.get("yearly_exit_months_before") or 0)
    except (TypeError, ValueError):
        raise ValueError("yearly_exit_months_before must be an integer (months; 0 = hold to expiry)")
    if n_months < 0 or n_months > 11:
        raise ValueError(
            f"yearly_exit_months_before must be 0..11 months, got {n_months}"
        )

    # cadence_expiries lets the T-n boundary snap to a date the position can
    # actually roll on — see _build_yearly_cycles.
    cycles = _build_yearly_cycles(
        december, n_months, from_date, trading_days, cadence_expiries=cadence_expiries
    )
    if not cycles:
        raise ValueError(
            f"expiry_type=YEARLY: no yearly cycle covers {from_date}..{to_date} "
            f"(December contracts: {december[:3]}…, T-{n_months} months)"
        )
    return (cadence_expiries, cycles)


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


# ── Native futures data helpers (NO Postgres reads) ─────────────────────────
# Futures pricing + contract-expiry resolution + rollover, all sourced from the
# in-memory Rust FUTIDX cache (native get_future_price) plus an in-memory expiry
# index built ONCE per process from the futures feather. Replaces the per-trade
# Postgres reads (base.resolve_futures_pnl_with_rollover / get_future_price_from_db
# / _resolve_futures_expiry_by_preference) that made futures backtests slow.
# Behaviour mirrors those functions exactly for trade-by-trade parity.
_FUT_EXPIRY_INDEX: Dict[str, Dict[str, List[str]]] = {}
# symbol -> date -> expiry -> (high, low)  — feather-batch source for futures MAE/MFE.
_FUT_OHLC_INDEX: Dict[str, Dict[str, Dict[str, Tuple[float, float]]]] = {}


def _ensure_fut_expiry_index(symbol: str) -> Dict[str, List[str]]:
    """Per-process {date -> sorted[expiry]} of FUTIDX contracts trading each day,
    built once from the futures feather. Mirrors the per-date contract set that
    base.get_all_futures_for_date returns (used by the expiry-preference resolvers).
    Also populates _FUT_OHLC_INDEX (date->expiry->(high,low)) from the same feather
    read, for the feather-batch futures MAE/MFE (no per-row DB query)."""
    symu = str(symbol).upper()
    idx = _FUT_EXPIRY_INDEX.get(symu)
    if idx is not None:
        return idx
    idx = {}
    ohlc: Dict[str, Dict[str, Tuple[float, float]]] = {}
    try:
        from services.futures_cache_store import ensure_futures_loaded, build_futures_feather
        ensure_futures_loaded(symu)  # guarantees native price cache + fresh feather
        import pyarrow.feather as _pf
        from collections import defaultdict as _dd
        path = build_futures_feather(symu)
        if path is not None:
            tbl = _pf.read_table(str(path))
            _cols = tbl.column_names
            dates = tbl.column("Date").to_pylist()
            exps = tbl.column("ExpiryDate").to_pylist()
            highs = tbl.column("High").to_pylist() if "High" in _cols else [None] * len(dates)
            lows = tbl.column("Low").to_pylist() if "Low" in _cols else [None] * len(dates)
            tmp: Dict[str, set] = _dd(set)
            for d, e, h, l in zip(dates, exps, highs, lows):
                ds = str(d)[:10]
                es = str(e)[:10]
                tmp[ds].add(es)
                if h is not None and l is not None:
                    ohlc.setdefault(ds, {})[es] = (float(h), float(l))
            idx = {d: sorted(s) for d, s in tmp.items()}
    except Exception as _exc:
        logger.warning("[ENGINE_RUST] futures expiry index build failed for %s: %s", symu, _exc)
    _FUT_EXPIRY_INDEX[symu] = idx
    _FUT_OHLC_INDEX[symu] = ohlc
    return idx


def _fut_leg_mae_mfe(symbol, entry_date, exit_date, expiry, entry_price, position,
                     entry_spot, sorted_td, exit_reason=None, exit_price=None):
    """Feather-batch MAE/MFE for a FUTURES leg: scan the in-memory FUTIDX high/low
    over (entry+1 .. exit) on the held contract, then reuse the SAME extremes math
    the option path uses (cap-adverse-at-SL + _calculate_mae_mfe_from_extremes) so
    futures MAE/MFE is directionally identical to options. No per-row DB query."""
    _ensure_fut_expiry_index(symbol)  # populates _FUT_OHLC_INDEX
    ohlc = _FUT_OHLC_INDEX.get(str(symbol).upper()) or {}
    ed = str(entry_date)[:10]
    xd = str(exit_date)[:10]
    es = str(expiry)[:10]
    highs: List[float] = []
    lows: List[float] = []
    for d in sorted_td:
        if d <= ed:
            continue
        if d > xd:
            break
        hl = ohlc.get(d, {}).get(es)
        if hl:
            highs.append(hl[0])
            lows.append(hl[1])
    if not highs:  # same-day trade fallback: use the entry day itself
        hl = ohlc.get(ed, {}).get(es)
        if hl:
            highs = [hl[0]]
            lows = [hl[1]]
    if not highs or not lows:
        return None, None
    try:
        from engines.generic_algotest_engine import _cap_adverse_extreme_for_sl, _calculate_mae_mfe_from_extremes
        _hi, _lo = _cap_adverse_extreme_for_sl(max(highs), min(lows), position, exit_reason, exit_price)
        # FUTURES MAE/MFE is normalized by the FUTURES entry price (f_entry), not the
        # index entry spot — matching the research-verified workbook convention
        # (midcap_overlay.py ÷f_entry). The shared helper divides by its `entry_spot`
        # arg, so pass entry_price there; both the reference point and denominator are
        # then f_entry. (The options path still passes the real entry_spot, unchanged.)
        return _calculate_mae_mfe_from_extremes(
            entry_price=entry_price, position=position, entry_spot=entry_price,
            max_high=_hi, min_low=_lo,
        )
    except Exception:
        return None, None


def _fut_resolve_expiry(symbol: str, date, preference: str = "monthly") -> Optional[str]:
    """Mirror base._cached_futures_expiry_by_preference, from the in-memory index."""
    ds = str(date)[:10]
    exps = _ensure_fut_expiry_index(symbol).get(ds)
    if not exps:
        return None
    filtered = [e for e in exps if e >= ds] or exps
    if not filtered:
        return None
    if str(preference or "monthly").lower() == "next_monthly" and len(filtered) >= 2:
        return filtered[1]
    return filtered[0]


def _fut_resolve_expiry_for_hold(symbol: str, entry_date, exit_date, preference: str = "monthly") -> Optional[str]:
    """Contract to HOLD for a unit-exit trade (mixed options+futures): at entry,
    the nearest contract whose expiry survives to exit_date. On a normal weekly
    cycle (entry & exit in the same month) this equals _fut_resolve_expiry — the
    current month — so existing behaviour is UNCHANGED. It differs only when the
    entry lands ON/after a monthly expiry: then it rolls to the next month
    instead of selecting a contract that expires before the exit (which would
    otherwise price the future on an already-expired contract → flat P&L)."""
    ed = str(entry_date)[:10]
    xd = str(exit_date)[:10]
    exps = _ensure_fut_expiry_index(symbol).get(ed)
    if not exps:
        return None
    covering = [e for e in exps if e >= xd]           # survives to exit
    if not covering:
        covering = [e for e in exps if e >= ed] or exps  # degrade to old behaviour
    if not covering:
        return None
    if str(preference or "monthly").lower() == "next_monthly" and len(covering) >= 2:
        return covering[1]
    return covering[0]


def _fut_nearest_expiry(symbol: str, date) -> Optional[str]:
    """Mirror base._cached_nearest_future_expiry (filter expiry>=date, take first)."""
    ds = str(date)[:10]
    exps = _ensure_fut_expiry_index(symbol).get(ds)
    if not exps:
        return None
    filtered = [e for e in exps if e >= ds]
    return filtered[0] if filtered else None


def _fut_nearest_expiry_after(symbol: str, date, min_expiry) -> Optional[str]:
    """Mirror base._cached_nearest_future_expiry_after (filter expiry>min, first)."""
    ds = str(date)[:10]
    me = str(min_expiry)[:10]
    exps = _ensure_fut_expiry_index(symbol).get(ds)
    if not exps:
        return None
    filtered = [e for e in exps if e > me]
    return filtered[0] if filtered else None


def _fut_price(symbol: str, date, expiry) -> Optional[float]:
    """Native FUTIDX close for (symbol,date,expiry) from the Rust cache, with the
    same ±1 expiry-day tolerance base.get_future_price_from_db applies."""
    from services import rust_fast_path as _rf
    from datetime import datetime as _dtc, timedelta as _td
    ds = str(date)[:10]
    es = str(expiry)[:10]
    v = _rf.get_future_price(symbol, ds, es)
    if v is not None:
        return float(v)
    for _delta in (1, -1):
        try:
            e2 = (_dtc.strptime(es, "%Y-%m-%d") + _td(days=_delta)).strftime("%Y-%m-%d")
        except Exception:
            continue
        v = _rf.get_future_price(symbol, ds, e2)
        if v is not None:
            return float(v)
    return None


def _resolve_futures_pnl_native(entry_date, exit_date, symbol, position, preference="monthly"):
    """Native-priced equivalent of base.resolve_futures_pnl_with_rollover — handles
    monthly-contract rollover when the hold spans an expiry. Returns
    (entry_price, exit_price, final_expiry_str). Reads ONLY the Rust cache."""
    from datetime import datetime as _dtc, timedelta as _td
    ed = str(entry_date)[:10]
    xd = str(exit_date)[:10]
    entry_expiry = _fut_resolve_expiry(symbol, ed, preference)
    if not entry_expiry:
        return None, None, None
    entry_price = _fut_price(symbol, ed, entry_expiry)
    if entry_price is None:
        return None, None, None
    if entry_expiry >= xd:
        exit_price = _fut_price(symbol, xd, entry_expiry)
        if exit_price is None:
            exit_price = entry_price
        return entry_price, exit_price, entry_expiry
    # ── rollover: entry contract expires mid-hold ──
    roll_date = entry_expiry
    roll_price_old = _fut_price(symbol, roll_date, entry_expiry)
    if roll_price_old is None:
        _prev = (_dtc.strptime(roll_date, "%Y-%m-%d") - _td(days=1)).strftime("%Y-%m-%d")
        roll_price_old = _fut_price(symbol, _prev, entry_expiry)
    if roll_price_old is None:
        roll_price_old = entry_price
    next_expiry = _fut_nearest_expiry_after(symbol, roll_date, entry_expiry)
    if not next_expiry:
        return entry_price, roll_price_old, entry_expiry
    roll_price_new = _fut_price(symbol, roll_date, next_expiry)
    if roll_price_new is None:
        roll_price_new = roll_price_old
    if next_expiry >= xd:
        exit_price = _fut_price(symbol, xd, next_expiry)
        if exit_price is None:
            exit_price = roll_price_new
        return entry_price, exit_price, next_expiry
    final_expiry = _fut_nearest_expiry(symbol, xd)
    if not final_expiry:
        return entry_price, roll_price_new, next_expiry
    exit_price = _fut_price(symbol, xd, final_expiry)
    if exit_price is None:
        exit_price = roll_price_new
    return entry_price, exit_price, final_expiry


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
    Prices come from the native Rust FUTIDX cache (_fut_price), not Postgres.
    """
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
            current = _fut_price(index, day, fut_expiry)
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


def _apply_leg_filter_mask(
    leg: Dict[str, Any],
    entry_date: str,
    exit_date: str,
    sorted_td: List[str],
) -> Tuple[bool, str, bool]:
    """Per-leg individual filter mask, shared by both FUTURES builders.

    Futures rows are priced INSIDE their builders and never pass through the
    options apply_leg_filters post-pass, so this must run before pricing, not
    after — see leg_filter.leg_window for the underlying rule.

    Returns (taken, exit_date, truncated):
      * taken=False    -> caller must `continue` (leg absent from this trade).
      * exit_date      -> unchanged, or snapped back to the last trading day
                          on/before the mask's own end when truncated.
      * truncated=True -> exit_date came from the leg's own filter, not the
                          trade's schedule; caller must tag exit_reason with
                          LEG_FILTER_END.
    """
    from services.leg_filter import resolve_leg_window

    return resolve_leg_window(leg, entry_date, exit_date, sorted_td)


def _reject_leg_filter_unsupported(payload: Dict[str, Any], path: str) -> None:
    """Hard-fail when a per-leg filter file reaches a path that cannot honour it.

    Rust-only, NO silent degradation: a path that would price legs outside the
    window their uploaded file allows must RAISE, never quietly ignore the mask
    and hand back wrong numbers. Cheap and exact — a run with no individual
    filter never trips this, so unfiltered behaviour is untouched.
    """
    from services.leg_filter import leg_segments

    if any(leg_segments(l) for l in (payload.get("legs") or [])):
        raise RuntimeError(
            "Per-leg Individual Filter is not supported on the %s path: option "
            "legs there bypass the per-leg mask, so the uploaded file would be "
            "silently ignored. Remove the individual filter from this strategy "
            "or run it without that leg combination." % path
        )


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
    # Futures priced from the native Rust FUTIDX cache (no Postgres) — see
    # _resolve_futures_pnl_native / _fut_price above.
    index = str(payload.get("index") or "NIFTY").upper()
    legs_src = payload.get("legs") or []
    entry_dte = int(payload.get("entry_dte") or 0)
    exit_dte = int(payload.get("exit_dte") or 0)

    # Rollover: under rollover a futures position HOLDS and ROLLS across expiries
    # rather than round-tripping inside a single cycle. Applies to WEEKLY/MONTHLY
    # (next_* futures route to the mixed-next-weekly builder, not here).
    rollover_toggle = bool(payload.get("rollover_toggle", False))
    _etype_rk = str(payload.get("expiry_type") or payload.get("expiry_window") or "").upper()
    _rollover_mode = rollover_toggle and "NEXT" not in _etype_rk

    sorted_td = sorted(trading_days)
    sorted_exp = sorted(expiry_dates)
    _n_exp = len(sorted_exp)

    # Per-leg individual filter file. Futures rows are priced INSIDE this
    # builder and never pass through apply_leg_filters (the options post-pass),
    # so the mask has to run here, before pricing, or a truncated exit would
    # leave the P&L computed over the wrong window. See _apply_leg_filter_mask.
    from services.leg_filter import LEG_FILTER_END

    out: List[Dict[str, Any]] = []
    prev_sched_exit: Optional[str] = None  # overlap-prevention sentinel

    for _exp_i, exp_str in enumerate(sorted_exp):
        trade_id = _exp_i + 1
        entry_date = _trading_day_n_before(exp_str, entry_dte, sorted_td)
        exit_date = _trading_day_n_before(exp_str, exit_dte, sorted_td)
        if not entry_date or not exit_date:
            continue

        # ── Rollover re-anchor (mirrors the Python schedule,
        # generic_algotest_engine ~3688-3736) ──
        # Under rollover, entry_dte >= exit_dte puts entry on/after the exit WITHIN
        # one cycle → a same-day round trip at a single price → 0 P&L (the exact
        # symptom of the "entry=1/exit=1 rollover" report). Re-anchor the exit to
        # the NEXT expiry (T-exit_dte) so the trade holds and rolls across the
        # expiry, matching AlgoTest ("exit anchors to next expiry; roll to the
        # next contract"). Non-rollover behaviour is left untouched.
        _rolled = False
        if _rollover_mode and entry_date >= exit_date:
            _nxt = sorted_exp[_exp_i + 1] if _exp_i + 1 < _n_exp else None
            if _nxt is not None:
                _re_exit = _trading_day_n_before(_nxt, exit_dte, sorted_td)
            else:
                # Last cycle, no further expiry: clamp the hold to the last trading
                # day in range (Python clamps to the segment/to_date end).
                _re_exit = sorted_td[-1] if sorted_td else None
            if _re_exit and _re_exit > entry_date:
                exit_date = _re_exit
                _rolled = True
            else:
                continue  # cannot form a real holding period

        # Non-rollover, entry_dte == exit_dte → entry and exit fall on the same day
        # → a same-day round trip at one price → ~0 P&L. Skip it (mirrors the Python
        # engine, generic_algotest_engine ~3742). The frontend already blocks this
        # combination unless rollover is on, so this only guards direct-API payloads.
        if not _rolled and entry_date == exit_date:
            continue

        entry_spot = spot_by_date.get(entry_date)
        if entry_spot is None:
            continue
        exit_spot = spot_by_date.get(exit_date, 0.0)

        # Overlap prevention: skip if entry before previous scheduled exit.
        # Rollover chains same-day (entry == prev exit) — allowed (strict <).
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
            _leg_slip = _leg_slippage_pct(leg)

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

            if _rolled:
                # Rolled hold: exit is the re-anchored next-expiry date (do NOT
                # clamp it back to this cycle's own expiry via _futures_get_exit_date
                # — that would collapse the hold to a same-day round trip again).
                # Price BOTH ends on the single futures contract that survives to
                # the exit (the next-expiry contract) so the P&L is a clean
                # single-contract move — no near/far basis mixing.
                fut_exit_date = effective_exit

                # Per-leg individual filter — must run before pricing (see
                # _apply_leg_filter_mask).
                _lf_taken, fut_exit_date, _leg_filter_end_row = _apply_leg_filter_mask(
                    leg, entry_date, fut_exit_date, sorted_td
                )
                if not _lf_taken:
                    continue
                # The boundary the mask imposed. The SL/Target scan below can
                # still exit EARLIER; only an exit that actually lands here was
                # bound by the filter and may carry the LEG_FILTER_END tag.
                _lf_bound = fut_exit_date

                # Resolve the rolled contract from the RE-ANCHORED next-cycle exit
                # (exit_date), NOT the filter-clamped fut_exit_date. On a filter /
                # patch boundary the clamp shortens the hold back into the current-
                # month window, which would pick the CURRENT contract even though
                # the trade rolled into the NEXT one (the paired option leg holds
                # the next). Using exit_date keeps the futures on the same month as
                # the option; for a non-clamped trade exit_date == fut_exit_date so
                # nothing changes.
                _hold_expiry = _fut_resolve_expiry_for_hold(
                    index, entry_date, exit_date, fut_pref
                )
                if not _hold_expiry:
                    continue
                entry_price_raw = _fut_price(index, entry_date, _hold_expiry)
                exit_price_raw = _fut_price(index, fut_exit_date, _hold_expiry)
                fut_expiry = _hold_expiry
                if entry_price_raw is None:
                    continue
                if exit_price_raw is None:
                    exit_price_raw = entry_price_raw
            else:
                fut_exit_trigger = _futures_get_exit_date(exp_str, exit_mode_raw, n_days, sorted_td)
                fut_exit_date = min(fut_exit_trigger, effective_exit)

                # Per-leg individual filter — must run before pricing (see
                # _apply_leg_filter_mask).
                _lf_taken, fut_exit_date, _leg_filter_end_row = _apply_leg_filter_mask(
                    leg, entry_date, fut_exit_date, sorted_td
                )
                if not _lf_taken:
                    continue
                # The boundary the mask imposed. The SL/Target scan below can
                # still exit EARLIER; only an exit that actually lands here was
                # bound by the filter and may carry the LEG_FILTER_END tag.
                _lf_bound = fut_exit_date

                entry_price_raw, exit_price_raw, fut_expiry = _resolve_futures_pnl_native(
                    entry_date=entry_date,
                    exit_date=fut_exit_date,
                    symbol=index,
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
                _orig_sched_exit, index, fut_expiry or "", _leg_slip,
            )
            if _scan_exit_raw is not None:
                fut_exit_date = _scan_exit_date
                exit_price_raw = _scan_exit_raw
                exit_spot = spot_by_date.get(fut_exit_date, exit_spot)

            # Futures rows carry their exit reason directly on the row dict
            # (unlike options rows, which are labelled later in the pipeline —
            # see the _leg_filter_end_keys join at engine_rust.py:8736).
            if _leg_filter_end_row and fut_exit_date == _lf_bound:
                if not _actual_exit_reason or _actual_exit_reason == "EXPIRY":
                    _actual_exit_reason = LEG_FILTER_END
                elif LEG_FILTER_END not in _actual_exit_reason:
                    _actual_exit_reason = _actual_exit_reason + "+" + LEG_FILTER_END

            # Slippage — mirrors _apply_slippage in generic_algotest_engine.py
            if _leg_slip > 0:
                _entry_fac = (1.0 - _leg_slip / 100.0) if position == "SELL" else (1.0 + _leg_slip / 100.0)
                _exit_fac = (1.0 + _leg_slip / 100.0) if position == "SELL" else (1.0 - _leg_slip / 100.0)
                entry_price = round(max(float(entry_price_raw) * _entry_fac, 0.0), 2)
                exit_price = round(max(float(exit_price_raw) * _exit_fac, 0.0), 2)
            else:
                entry_price = round(float(entry_price_raw), 2)
                exit_price = round(float(exit_price_raw), 2)

            # P&L = POINTS x LOTS. lot_size is NOT a factor — it feeds only the
            # display Qty column. Mirrors native/src/simulate.rs:1652.
            _lots = float(leg.get("lots") or 1)
            net_pnl = round(
                ((entry_price - exit_price) if position == "SELL" else (exit_price - entry_price))
                * _lots,
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
                "slippage_pct": _leg_slip,
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
                        _re_ep_raw, _re_xp_raw, _re_expiry = _resolve_futures_pnl_native(
                            entry_date=_re_entry_date,
                            exit_date=_sched_exit,
                            symbol=index,
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
                        _sched_exit, index, _re_expiry or fut_expiry or "", _leg_slip,
                    )
                    if _re_scan_raw is not None:
                        _re_xp_raw = _re_scan_raw
                        _re_exit_date = _re_scan_date
                    else:
                        _re_exit_date = _sched_exit
                        _re_reason = "EXPIRY"

                    # Re-entry rows inherit the truncated exit via _sched_exit
                    # (= _orig_sched_exit, already masked above) so their P&L
                    # window is already right — but the tag applied to the
                    # primary row (above) only runs once, before this loop, so
                    # it must be repeated here for each re-entry sub-row.
                    if _leg_filter_end_row and _re_exit_date == _lf_bound:
                        if not _re_reason or _re_reason == "EXPIRY":
                            _re_reason = LEG_FILTER_END
                        elif LEG_FILTER_END not in _re_reason:
                            _re_reason = _re_reason + "+" + LEG_FILTER_END

                    if _leg_slip > 0:
                        _re_ep = round(max(float(_re_ep_raw) * _entry_fac, 0.0), 2)
                        _re_xp = round(max(float(_re_xp_raw) * _exit_fac, 0.0), 2)
                    else:
                        _re_ep = round(float(_re_ep_raw), 2)
                        _re_xp = round(float(_re_xp_raw), 2)

                    # P&L = POINTS x LOTS. lot_size is NOT a factor — it feeds only the
                    # display Qty column. Mirrors native/src/simulate.rs:1652.
                    _re_pnl = round(
                        ((_re_ep - _re_xp) if position == "SELL" else (_re_xp - _re_ep))
                        * _lots,
                        4,
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
                        "slippage_pct": _leg_slip,
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


def _liquidity_walk_step(index: Optional[str], interval: float) -> Tuple[float, bool]:
    """Step size the liquidity-shift walk uses to find a tradeable strike —
    Python mirror of Rust `liquidity_walk_step`. Legacy 25/50/100 gaps walk by
    the gap itself (unchanged). A COARSE 500 gap walks by a finer per-index step
    so it lands on a liquid listed strike instead of skipping a whole 500-pt
    gap: NIFTY->100, MIDCPNIFTY->50 (others default to 100). The 500 gap still
    governs ATM snap + offset stepping. Returns (walk_step, is_coarse)."""
    if interval <= 100.0:
        return interval, False
    step = {
        "NIFTY": 100.0, "MIDCPNIFTY": 50.0, "FINNIFTY": 50.0,
        "BANKNIFTY": 100.0, "SENSEX": 100.0, "BANKEX": 100.0,
    }.get((index or "").upper(), 100.0)
    return step, True


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

    walk_step, _coarse = _liquidity_walk_step(index, interval)
    interval = walk_step  # coarse (500) gaps walk by the fine per-index step
    st = _status(strike)
    if st == "tradeable":
        return strike
    # Any non-tradeable status ("zero_contracts" or "missing") is no-liquidity
    # and shifts toward a tradeable strike, for EVERY gap and every strike mode.
    # (Previously a "missing"/unlisted strike dropped the trade; now we always
    # walk toward ATM to find a tradeable strike.)
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


def _validate_or_shift_straddle_strike_python(
    strike: float,
    atm: float,
    interval: float,
    entry_date: Optional[str],
    expiry: Optional[str],
    index: Optional[str],
) -> Optional[float]:
    """Joint CE+PE liquidity validation/shift for straddle_width legs — mirrors
    Rust validate_or_shift_straddle_strike. Both legs share the same requested
    strike; if either side is illiquid there, BOTH move together to the first
    strike where CE AND PE are simultaneously tradeable."""
    if not (entry_date and expiry and index):
        return strike
    try:
        import algotest_native  # type: ignore
    except ImportError:
        return strike

    def _tradeable(s: float, opt_type: str) -> bool:
        fn = getattr(algotest_native, "get_option_status", None)
        if fn is not None:
            try:
                return (fn(entry_date, index, s, opt_type, expiry) or "missing") == "tradeable"
            except Exception:
                return False
        try:
            px = algotest_native.get_option_price(entry_date, index, s, opt_type, expiry)
            return px is not None and px > 0
        except Exception:
            return False

    def _joint_ok(s: float) -> bool:
        return _tradeable(s, "CE") and _tradeable(s, "PE")

    walk_step, _coarse = _liquidity_walk_step(index, interval)
    interval = walk_step  # coarse gaps walk by the fine per-index step

    if _joint_ok(strike):
        return strike

    if strike > atm + 1e-6:
        direction = -1.0
    elif strike < atm - 1e-6:
        direction = 1.0
    else:
        # Requested strike IS atm and jointly illiquid — no single CE/PE-
        # favored direction applies to both legs; walk outward alternating.
        step = 1
        while step <= 500:
            for cand in (strike + step * interval, strike - step * interval):
                if cand > 0 and _joint_ok(cand):
                    return cand
            step += 1
        return None

    dist = int(round(abs(strike - atm) / interval))
    max_walk = max(dist, 1)
    for step in range(1, max_walk + 1):
        cand = strike + direction * step * interval
        if cand <= 0:
            break
        if _joint_ok(cand):
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
    resolved_strikes: Optional[Dict[int, float]] = None,
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

    if sel_type == "rel_leg":
        # Relative-to-leg (Iron Condor wing). Mirror of Rust StrikeSel::RelToLeg:
        #   wing = parent_strike + offset*interval (CALL) / − (PUT).
        # `resolved_strikes` maps 1-based leg number → that leg's final strike;
        # the parent MUST be an earlier leg (resolved before this one).
        try:
            ref = int(sel.get("ref_leg") or 0)
            off = float(sel.get("offset") or 0.0)
        except (TypeError, ValueError):
            return None
        parent = (resolved_strikes or {}).get(ref)
        if parent is None:
            return None
        shift = off * interval
        return _validate(parent + shift if is_ce else parent - shift)

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
        # Fast path: the ENTIRE computation (formula, tradeable check,
        # gap-widening fallback, joint zero-turnover walk) now lives natively
        # in Rust (compute_straddle_leg_strike) as ONE call, replacing a chain
        # of many small Python↔Rust FFI round-trips per leg — this is what
        # made straddle_width (especially on thin-liquidity indices like
        # MIDCPNIFTY) much slower than every other strike mode. Falls through
        # to the pure-Python mirror below only if the native function is
        # unavailable (older compiled extension) or raises.
        _native_fn = getattr(algotest_native, "compute_straddle_leg_strike", None)
        if _native_fn is not None:
            try:
                _native_result = _native_fn(
                    leg, entry_date, expiry, index_up, entry_spot,
                    int(strike_shift_max), dict(resolved_strikes or {}),
                )
            except Exception:
                _native_result = None
            if _native_result is not None:
                _n_final, _n_requested, _n_atm, _n_ce, _n_pe, _n_source = _native_result
                if out_info is not None:
                    out_info["requested_strike"] = float(_n_requested)
                    if _n_source:
                        out_info["straddle_price_source"] = _n_source
                return float(_n_final)
        try:
            # Use the *tradeable* variant (filters zero-turnover/stale close
            # prices), not get_option_price — a straddle price built from a
            # dead, untraded contract's stale close silently corrupts the
            # shift formula (seen on MIDCPNIFTY: a 0-contract PE's stale
            # close of 1223.85 vs a real ~330 elsewhere).
            ce_px = algotest_native.get_option_price_tradeable(entry_date, index_up, atm, "CE", expiry)
            pe_px = algotest_native.get_option_price_tradeable(entry_date, index_up, atm, "PE", expiry)
            _straddle_price_source = ""
            if ce_px is None or pe_px is None:
                # ATM straddle price illiquid at the leg's own strike gap —
                # widen the GAP used only to source a liquid CE+PE price
                # (gap, 2×gap, 3×gap, 4×gap), same rule for every index. The
                # leg's own ATM/strike-gap ("atm", "interval" above) and the
                # existing final-strike zero-turnover walk are untouched —
                # this only replaces a bad price input to the shift formula.
                _missing = ("CE" if ce_px is None else "") + ("PE" if pe_px is None else "")
                _widened = None
                for _mult_n in (2, 3, 4):
                    _w_gap = interval * _mult_n
                    _w_atm = round(entry_spot / _w_gap) * _w_gap
                    _w_ce = algotest_native.get_option_price_tradeable(entry_date, index_up, _w_atm, "CE", expiry)
                    _w_pe = algotest_native.get_option_price_tradeable(entry_date, index_up, _w_atm, "PE", expiry)
                    if _w_ce is not None and _w_pe is not None:
                        _widened = (_w_gap, _w_ce, _w_pe)
                        break
                if _widened is None:
                    return None
                _w_gap, ce_px, pe_px = _widened
                _straddle_price_source = (
                    f"{interval:g}→{_w_gap:g} (ATM {_missing} zero turnover)"
                )
            if out_info is not None and _straddle_price_source:
                out_info["straddle_price_source"] = _straddle_price_source
        except Exception:
            return None
        if ce_px is None or pe_px is None:
            return None
        if sel_type == "straddle_width":
            # NOTE: `or 0.5`, not `.get(..., 0.5)`, would silently turn a
            # deliberate multiplier=0 (pure ATM) into 0.5 — 0 is falsy in
            # Python. Explicit None-check preserves 0 correctly.
            _mult_raw = sel.get("straddle_multiplier")
            mult = float(_mult_raw) if _mult_raw is not None else 0.5
            direction = str(sel.get("straddle_direction") or "+").strip()
            shift = mult * (float(ce_px) + float(pe_px))
            # Raw +/- sign applied identically regardless of option_type: "+"
            # always adds the shift, "-" always subtracts. Both legs of a
            # straddle land on the same strike for the same direction.
            raw = atm - shift if direction == "-" else atm + shift
            req = round(raw / interval) * interval
            if out_info is not None:
                out_info["requested_strike"] = float(req)
            # Joint CE+PE liquidity walk ONLY when another straddle_width leg
            # in this trade actually shares this same strike (same multiplier
            # + direction — see _straddle_use_joint_shift annotation in
            # run_rust_engine_pipeline). Otherwise this leg's strike is its
            # own, unrelated to any sibling leg's contract, so it must shift
            # on its OWN option_type's liquidity only, like every other mode.
            if bool(leg.get("_straddle_use_joint_shift", False)):
                return _validate_or_shift_straddle_strike_python(req, atm, interval, entry_date, expiry, index)
            return _validate(req)
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

    # YEARLY: `expiry_dates` is the CADENCE list (weekly/monthly) and only drives
    # entry/exit; the CONTRACT is the pinned December from `yearly_cycles`.
    # Without this the fixed-entry path uses each cadence element AS the
    # contract — silently trading weeklies while the UI says "Yearly".
    _yearly_cycles: Optional[List[Dict[str, str]]] = (
        payload.get("yearly_cycles")
        if str(payload.get("expiry_type") or "").upper() == "YEARLY"
        else None
    )
    if _yearly_cycles:
        # Same reason as the rollover path: min-DTE advances the contract to the
        # next CADENCE element, which would swap December for a weekly.
        rollover_min_days = 0
        no_rollover_min_days_val = 0

    # SAME-INDEX MIXED EXPIRY: a MONTHLY leg while the cadence is WEEKLY.
    # Gated on the cadence actually being weekly — under a MONTHLY cadence a
    # MONTHLY leg IS the cadence leg and must keep resolving to target_expiry
    # exactly as before, so this stays None and the pin branch never runs.
    _cadence_weekly = (
        str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
    )
    _has_monthly_pin_leg = _cadence_weekly and any(
        isinstance(_l, dict)
        and str(_l.get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
        and str(_l.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
        for _l in legs_src
    )
    _monthly_expiries: Optional[List[str]] = None
    if _has_monthly_pin_leg:
        # Authoritative monthly calendar, not "last weekly of each month" derived
        # from `expiry_dates` — that list is bounded by the backtest range, so a
        # run ending mid-month would treat a plain weekly as the month's expiry.
        _from_mp = str(payload.get("from_date") or payload.get("date_from") or "")
        _to_mp = str(payload.get("to_date") or payload.get("date_to") or "")
        if _from_mp and _to_mp:
            # Reach past the range end: the last cadence cycle's scheduled exit can
            # fall beyond it, and its pin must still resolve (else the tail trade is
            # silently dropped). ~3 months of slack covers NEXT_MONTHLY at the edge.
            try:
                import datetime as _dt_mp

                _to_mp_ext = (
                    _dt_mp.date.fromisoformat(_to_mp) + _dt_mp.timedelta(days=100)
                ).isoformat()
            except Exception:
                _to_mp_ext = _to_mp
            _monthly_expiries = _expiry_date_list(
                index_str, "monthly", _from_mp, _to_mp_ext
            )
        if not _monthly_expiries:
            # No monthly calendar → cannot honour the pin. Fail loudly rather than
            # silently handing the leg a weekly contract (the exact bug the UI
            # gate existed to prevent).
            raise RuntimeError(
                "Mixed expiry: a MONTHLY leg is present under a WEEKLY cadence but "
                f"no monthly expiry calendar was found for {index_str} "
                f"({_from_mp}..{_to_mp}). Refusing to fall back to weekly contracts."
            )
        # Identical hazard to YEARLY: min-DTE advances the contract to the next
        # CADENCE element, which would swap the pinned monthly for a weekly.
        rollover_min_days = 0
        no_rollover_min_days_val = 0
    # Fresh's "re-strike" marker depends on the cadence:
    #   * MONTHLY — re-strike at EVERY monthly roll, so key on the cadence expiry
    #     (target_expiry). Consecutive monthly expiries are in different months,
    #     and a mid-month filter start vs the month's roll are two different
    #     expiries — both re-strike correctly.
    #   * WEEKLY — hold within a CALENDAR month and re-strike at month-end, so
    #     key on the entry date. A 28-Mar weekly entry (rolling into the w/c
    #     04-Apr) is still March by the calendar, so it holds; the first April
    #     entry re-strikes.
    _yearly_cadence = str(payload.get("rollover_cadence") or "monthly").lower()

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
        # YEARLY strike epochs (pinned path only). Fresh/Fixed are applied for
        # weekly/monthly by the Python post-process `_apply_fixed_rollover_strike`,
        # which early-returns under YEARLY. So the carry policy lives here.
        #
        # SEGMENT-WISE (user's rule "fixed should work segment wise"): the fixed
        # strike is re-baselined to ATM at the start of every filter segment and
        # held within it — matching weekly/monthly Fixed, which also takes its
        # override from each segment's first trade. Resetting here (inside the
        # loop) is the segment reset; `_opens_new_epoch` additionally re-strikes
        # at a yearly roll via `new_cycle`, so a segment that spans a December
        # roll re-strikes there too (the position re-enters fresh on the new
        # contract). Untouched when _yearly_cycles is None.
        _epoch_strike: Dict[int, float] = {}
        _epoch_prev_cadence: Optional[str] = None
        _epoch_prev_contract: Optional[str] = None

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

            # YEARLY: the contract is resolved AFTER the 0-day loop settles
            # exit_date (see below) — it depends on the exit, not the entry.
            _pin: Optional[Dict[str, str]] = None

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

            # YEARLY: resolve the pinned December from the SETTLED exit. Must be
            # after the 0-day loop, which re-assigns exit_date to the next
            # cadence expiry (with exit_dte=0 the entry always lands ON a cadence
            # expiry, so that loop always fires).
            #
            # The exit is NOT truncated: T-n is a threshold, so a segment simply
            # opens on whichever December it can hold for its whole cadence
            # period. The roll therefore lands on a real cadence boundary and no
            # 1-day stub is produced.
            #
            # Resolved AFTER the segment clamp below — a filter-shortened segment
            # is held for less time, so it may keep the NEARER December. Pinning
            # off the unclamped exit would put the filter-end tail on a contract
            # a whole year too far out.
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
            #
            # EXCEPTION (rollover): when the cycle reached its natural T-n exit
            # STRICTLY BEFORE the filter/segment end, it's a real EXPIRY exit,
            # not a filter-end one. Leaving clamped=False here lets the chain
            # roll once more so a final stub trade — entered on this T-n exit
            # date in the NEXT contract and clamped to the filter end — fills
            # the [T-n exit, filter end] tail. This mirrors weekly, which
            # already produces that stub via its denser roll. Non-rollover, or
            # an exit that already lands at/after the filter end (incl. the
            # expiry-beyond-data truncation above), keep the original filter-end
            # behaviour untouched.
            if target_expiry > last_in_seg:
                if not (rollover_toggle and not no_rollover_flag and exit_date < last_in_seg):
                    clamped = True

            # YEARLY: resolve the pinned December from the FINAL (clamped) exit —
            # see the note above the clamp. A filter-shortened segment is held for
            # less time, so it may keep the NEARER December.
            if _yearly_cycles is not None:
                _pin = _cycle_for_exit(_yearly_cycles, exit_date)
                if _pin is None:
                    break

            entry_spot = spot_by_date.get(current_entry)
            if not entry_spot:
                break

            _trade_specs: List[Dict[str, Any]] = []
            _trade_resolved = True
            _resolved_strikes: Dict[int, float] = {}
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
                # PER-LEG contract under YEARLY. Only a leg whose OWN expiry is
                # YEARLY gets the pinned December; a weekly/monthly leg in the
                # same strategy keeps trading its cadence contract. That is what
                # lets a mixed basket work — e.g. CE SELL weekly + PE BUY yearly
                # — with every leg re-booking on the shared cadence but holding
                # its own contract. When no leg is yearly the pin is inert, so
                # non-yearly strategies are untouched.
                _leg_is_yearly = str(leg.get("expiry") or "").upper() == "YEARLY"
                # SAME-INDEX MIXED EXPIRY. A MONTHLY / NEXT_MONTHLY leg under a
                # WEEKLY cadence pins to its own monthly contract, exactly as a
                # YEARLY leg pins to December, while still re-booking on the shared
                # weekly cadence. Checked BEFORE `_leg_is_next` because
                # NEXT_MONTHLY is in _NEXT_EXPIRY_TYPES and would otherwise be
                # handed sorted_expiries[target_idx+1] — the next WEEKLY, not the
                # next monthly. `_monthly_expiries` is None unless such a leg
                # exists under a weekly cadence, so every other run is untouched.
                _leg_exp_raw = str(leg.get("expiry") or "").upper()
                if (
                    _monthly_expiries is not None
                    and _leg_exp_raw in _MONTHLY_LEG_TYPES
                    and not _leg_is_yearly
                ):
                    # exit_date here is the SCHEDULED cadence exit — specs are built
                    # before the spot-adj / SL cascade truncates anything.
                    leg_expiry = _resolve_monthly_pin(
                        _monthly_expiries,
                        exit_date,
                        1 if _leg_exp_raw in ("NEXT_MONTHLY", "MONTHLY_T1") else 0,
                        trading_days,
                        exit_dte,
                    )
                    if leg_expiry is None:
                        _trade_resolved = False
                        break
                elif _pin is not None and _leg_is_yearly:
                    leg_expiry = _pin["contract"]
                elif _leg_is_next:
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
                # YEARLY carry policy. Outside yearly `_pin is None`, so this is
                # skipped entirely and the strike resolves fresh per trade exactly
                # as before (weekly/monthly Fixed is applied downstream by
                # _apply_fixed_rollover_strike).
                # MONTHLY keys on the cadence expiry (re-strike every roll, incl.
                # a mid-month filter start); WEEKLY keys on the entry date (hold
                # within a calendar month). See _yearly_cadence above.
                _epoch_marker = target_expiry if _yearly_cadence == "monthly" else current_entry
                _carried = None
                if _pin is not None and _epoch_prev_cadence is not None:
                    _new_cycle = _epoch_prev_contract != _pin["contract"]
                    _leg_is_yearly = str(
                        leg.get("expiry") or leg.get("expiry_type") or ""
                    ).upper() == "YEARLY"
                    if not _opens_new_epoch(
                        leg.get("rollover_strike_mode"), _epoch_prev_cadence,
                        _epoch_marker, _new_cycle, _leg_is_yearly,
                    ):
                        _carried = _epoch_strike.get(leg_idx + 1)

                if _carried is not None:
                    # Re-validate the carried strike for liquidity against THIS
                    # entry date and THIS December contract — never reuse blindly.
                    # Over a ~12-month carry on a long-dated contract the strike
                    # can go unlisted/illiquid.
                    _atm = round(entry_spot / leg_interval) * leg_interval
                    _is_ce = str(leg.get("option_type") or "CE").upper() in ("CE", "CALL", "C")
                    strike = _validate_or_shift_strike_python(
                        _carried, _atm, leg_interval, _is_ce, current_entry,
                        leg_expiry, index_str, str(leg.get("option_type") or "CE").upper(), 1,
                    )
                    _shift_info["requested_strike"] = float(_carried)
                else:
                    strike = _compute_strike_for_leg_python(
                        leg, entry_spot, leg_interval,
                        entry_date=current_entry, expiry=leg_expiry, index=index_str,
                        out_info=_shift_info, resolved_strikes=_resolved_strikes,
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
                _resolved_strikes[leg_idx + 1] = float(strike)
                _trade_specs.append({
                    "trade_id": trade_id,
                    "leg_id": leg_idx + 1,
                    "index": index_str,
                    "entry_date": current_entry,
                    "exit_date": exit_date,
                    "expiry": leg_expiry,
                    # The CADENCE contract this trade was scheduled on, which is
                    # the leg's own contract for every leg EXCEPT a pinned one.
                    # WOW buckets on this so both legs of a mixed trade land in the
                    # same week; for non-mixed runs it equals "expiry", so WOW
                    # output is unchanged by construction.
                    "_cadence_expiry": target_expiry,
                    "strike": float(strike),
                    "requested_strike": float(_shift_info.get("requested_strike") or strike),
                    "straddle_price_source": _shift_info.get("straddle_price_source") or "",
                    "strike_interval": float(leg_interval),
                    "option_type": str(leg.get("option_type") or "CE").upper(),
                    "position": str(leg.get("position") or "SELL").upper(),
                    "lots": int(leg.get("lots") or 1),
                    "lot_size": int(lot_size),
                    "slippage_pct": _leg_slippage_pct(leg),
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
                # Advance the epoch only for trades that actually emitted — a
                # dropped trade must not consume the month boundary, or the next
                # real trade in a new month would wrongly carry the old strike.
                if _pin is not None:
                    for _s in _trade_specs:
                        _epoch_strike[int(_s["leg_id"])] = float(_s["strike"])
                    _epoch_prev_cadence = (
                        target_expiry if _yearly_cadence == "monthly" else current_entry
                    )
                    _epoch_prev_contract = _pin["contract"]
                if clamped or no_rollover_flag or not rollover_toggle:
                    break

            if clamped or not rollover_toggle:
                break
            current_entry = exit_date  # same-day chain

    return all_specs


def _build_fixed_entry_futures_specs(
    payload: Dict[str, Any],
    expiry_dates: List[str],
    trading_days: List[str],
    spot_by_date: Dict[str, float],
    lot_size: int,
    segments: Optional[List[Tuple[str, str]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Fixed-entry ('fixed' filter_entry_mode) support for a FUTURES leg.

    A futures leg is otherwise always routed to _build_futures_specs (DTE-based
    entry scheduling), so Fixed Entry was silently ignored — with a date-list CSV
    filter the DTE entry + segment clamp collapses every trade to zero. This
    builder uses the SAME fixed-entry while-loop as _build_fixed_entry_specs
    (enter on the segment start, exit at target_expiry - exit_dte clamped to the
    segment end, chain within the segment when rollover is on) but prices the
    leg with the native FUTIDX cache exactly like _build_futures_specs. Returns
    fully-priced rows (bypass simulate_trades_batch). None if a leg is malformed.
    """
    rollover_toggle = bool(payload.get("rollover_toggle", False))
    no_rollover_flag = bool(payload.get("no_rollover", False))
    rollover_min_days = int(payload.get("rollover_min_days_to_expiry", 0) or 0)
    no_rollover_min_days_val = int(payload.get("no_rollover_min_days", 0) or 0)
    exit_dte = int(payload.get("exit_dte", 0) or 0)
    legs_src = payload.get("legs") or []
    index = str(payload.get("index") or "NIFTY").upper()

    # Per-leg individual filter file. Futures rows are priced INSIDE this
    # builder and never pass through apply_leg_filters (the options post-pass),
    # so the mask has to run here, before pricing, or a truncated exit would
    # leave the P&L computed over the wrong window. See _apply_leg_filter_mask.
    from services.leg_filter import LEG_FILTER_END

    sorted_td = sorted(trading_days)
    sorted_expiries = sorted(expiry_dates)

    if segments is not None:
        effective_segs = segments
    else:
        from_date = str(payload.get("from_date") or payload.get("date_from") or "")
        to_date = str(payload.get("to_date") or payload.get("date_to") or "")
        effective_segs = [(from_date, to_date)] if from_date and to_date else []

    out: List[Dict[str, Any]] = []
    trade_id = 1

    for seg_start, seg_end in effective_segs:
        current_entry = _next_trading_day_on_or_after(sorted_td, seg_start)
        if current_entry is None or current_entry > seg_end:
            continue
        last_in_seg = _last_trading_day_on_or_before(seg_end, sorted_td)
        if last_in_seg is None:
            continue

        max_iters = max(20, len(sorted_expiries) * 4)
        iter_count = 0

        while current_entry <= seg_end and iter_count < max_iters:
            iter_count += 1
            if current_entry < seg_start:
                break

            target_idx = bisect.bisect_left(sorted_expiries, current_entry)
            if target_idx >= len(sorted_expiries):
                break
            target_expiry = sorted_expiries[target_idx]

            if rollover_toggle and rollover_min_days > 0:
                gap = _trading_day_gap_strict(current_entry, target_expiry, sorted_td)
                if gap <= rollover_min_days and target_idx + 1 < len(sorted_expiries):
                    target_idx += 1
                    target_expiry = sorted_expiries[target_idx]
            if no_rollover_flag and no_rollover_min_days_val > 0:
                gap = _trading_day_gap_strict(current_entry, target_expiry, sorted_td)
                if gap <= no_rollover_min_days_val and target_idx + 1 < len(sorted_expiries):
                    target_idx += 1
                    target_expiry = sorted_expiries[target_idx]

            exit_date = _trading_day_n_before(target_expiry, exit_dte, sorted_td)
            if exit_date is None:
                break

            # 0-day cycle: advance target expiry until exit > entry (mirror options).
            while exit_date <= current_entry:
                if target_idx + 1 >= len(sorted_expiries):
                    next_td = _next_trading_day_on_or_after(sorted_td, sorted_expiries[-1] + "x")
                    if next_td is None or next_td > seg_end:
                        exit_date = current_entry
                        break
                    current_entry = next_td
                    exit_date = current_entry
                    break
                target_idx += 1
                target_expiry = sorted_expiries[target_idx]
                _new = _trading_day_n_before(target_expiry, exit_dte, sorted_td)
                exit_date = _new if _new else current_entry

            if exit_date <= current_entry:
                if current_entry > seg_end:
                    break
                continue

            clamped = False
            if exit_date > last_in_seg:
                if last_in_seg <= current_entry:
                    break
                exit_date = last_in_seg
                clamped = True
            if target_expiry > last_in_seg:
                if not (rollover_toggle and not no_rollover_flag and exit_date < last_in_seg):
                    clamped = True

            entry_spot = spot_by_date.get(current_entry)
            if not entry_spot:
                break
            exit_spot = spot_by_date.get(exit_date, 0.0)

            _emitted = False
            for leg_id, leg in enumerate(legs_src, start=1):
                if not isinstance(leg, dict):
                    return None
                if str(leg.get("segment") or "OPTION").upper() not in ("FUTURE", "FUTURES"):
                    continue

                position = str(leg.get("position") or "SELL").upper()
                lots = int(leg.get("lots") or 1)
                fut_pref_raw = str(leg.get("expiry") or "monthly").lower()
                fut_pref = "next_monthly" if fut_pref_raw in ("next_monthly", "next_month", "mid_month") else "monthly"
                _leg_slip = _leg_slippage_pct(leg)

                # Per-leg individual filter — must run before pricing (see
                # _apply_leg_filter_mask).
                _lf_taken, _fe_exit_date, _leg_filter_end_row = _apply_leg_filter_mask(
                    leg, current_entry, exit_date, sorted_td
                )
                if not _lf_taken:
                    continue

                # Native-priced, exactly like _build_futures_specs (no Postgres).
                entry_price_raw, exit_price_raw, fut_expiry = _resolve_futures_pnl_native(
                    entry_date=current_entry, exit_date=_fe_exit_date,
                    symbol=index, position=position, preference=fut_pref,
                )
                if entry_price_raw is None:
                    continue
                if exit_price_raw is None:
                    exit_price_raw = entry_price_raw

                fut_exit_date = _fe_exit_date
                _sc_date, _sc_raw, _reason = _scan_futures_sl_target(
                    current_entry, float(entry_price_raw), position, leg, sorted_td,
                    _fe_exit_date, index, fut_expiry or "", _leg_slip,
                )
                exit_reason = _reason
                _exit_spot = exit_spot
                if _sc_raw is not None:
                    fut_exit_date = _sc_date
                    exit_price_raw = _sc_raw
                    _exit_spot = spot_by_date.get(fut_exit_date, exit_spot)
                elif clamped:
                    # Exit clamped to the segment/filter end (not a natural expiry).
                    exit_reason = "FILTER_END"

                if _leg_filter_end_row and fut_exit_date == _fe_exit_date:
                    if not exit_reason or exit_reason == "EXPIRY":
                        exit_reason = LEG_FILTER_END
                    elif LEG_FILTER_END not in exit_reason:
                        exit_reason = exit_reason + "+" + LEG_FILTER_END

                if _leg_slip > 0:
                    _ef = (1.0 - _leg_slip / 100.0) if position == "SELL" else (1.0 + _leg_slip / 100.0)
                    _xf = (1.0 + _leg_slip / 100.0) if position == "SELL" else (1.0 - _leg_slip / 100.0)
                    entry_price = round(max(float(entry_price_raw) * _ef, 0.0), 2)
                    exit_price = round(max(float(exit_price_raw) * _xf, 0.0), 2)
                else:
                    entry_price = round(float(entry_price_raw), 2)
                    exit_price = round(float(exit_price_raw), 2)

                # P&L = POINTS x LOTS (see native/src/simulate.rs:1652).
                _lots = float(leg.get("lots") or 1)
                net_pnl = round(
                    ((entry_price - exit_price) if position == "SELL" else (exit_price - entry_price))
                    * _lots,
                    4,
                )

                out.append({
                    "trade_id": trade_id,
                    "leg_id": leg_id,
                    "index": index,
                    "entry_date": current_entry,
                    "exit_date": fut_exit_date,
                    "expiry": fut_expiry or "",
                    "strike": 0.0,
                    "option_type": "FUT",
                    "position": position,
                    "lots": lots,
                    "lot_size": lot_size,
                    "slippage_pct": _leg_slip,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "raw_entry_price": round(float(entry_price_raw), 4),
                    "raw_exit_price": round(float(exit_price_raw), 4),
                    "net_pnl": net_pnl,
                    "entry_spot": float(entry_spot),
                    "exit_spot": float(_exit_spot),
                    "exit_reason": exit_reason,
                })
                _emitted = True

            if _emitted:
                trade_id += 1

            # Fixed-entry chaining: one trade per segment unless rollover keeps the
            # chain alive within the segment (same rule as _build_fixed_entry_specs).
            if clamped or no_rollover_flag or not rollover_toggle:
                break
            current_entry = exit_date

    return out


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
        if expiry_type_raw == "YEARLY":
            # YEARLY has no calendar of its own — the roll CADENCE is what this
            # list holds, so the extra expiry must come from that calendar.
            # Falling through to "weekly" would append a weekly expiry to a
            # monthly cadence list and roll the position on a bogus date.
            freq = str(payload.get("rollover_cadence") or "monthly").lower()
            if freq not in ("weekly", "monthly"):
                freq = "monthly"
        elif expiry_type_raw in ("MONTHLY", "NEXT_MONTHLY", "MONTHLY_T1"):
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
        _resolved_strikes: Dict[int, float] = {}
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
                out_info=_shift_info, resolved_strikes=_resolved_strikes,
            )
            if strike is None:
                # Unresolvable strike for THIS cycle — most commonly the Ek+2
                # contract lies past the loaded data at the end of the range, or
                # a thin/zero-volume expiry. Skip just this trade (mirrors WEEKLY:
                # never zero the entire run on one bad strike). Premium-based modes
                # that need data params still resolve normally when present.
                _skip_trade = True
                break
            _resolved_strikes[leg_idx + 1] = float(strike)
            _trade_specs.append({
                "trade_id": trade_id,
                "leg_id": leg_idx + 1,
                "index": index_str,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "expiry": per_leg_expiry,
                "strike": float(strike),
                "requested_strike": float(_shift_info.get("requested_strike") or strike),
                "straddle_price_source": _shift_info.get("straddle_price_source") or "",
                "strike_interval": float(leg_interval),
                "option_type": str(leg.get("option_type") or "CE").upper(),
                "position": str(leg.get("position") or "SELL").upper(),
                "lots": int(leg.get("lots") or 1),
                "lot_size": int(lot_size),
                "slippage_pct": _leg_slippage_pct(leg),
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

    # Rebuild specs list with entry_date overrides (and recomputed strikes).
    # Per-trade resolved-strike buffer so a Relative-to-Leg wing re-offsets from
    # its parent's recomputed strike at the shifted entry. Specs are in leg order
    # within a trade, so a parent (lower leg_id) is resolved before its wing.
    result: List[Dict[str, Any]] = []
    _md_resolved_by_tid: Dict[int, Dict[int, float]] = {}
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
            _md_rb = _md_resolved_by_tid.setdefault(tid, {})
            new_strike = _compute_strike_for_leg_python(
                leg, new_spot, interval,
                entry_date=new_entry, expiry=spec_expiry, index=index_str,
                out_info=_shift_info, resolved_strikes=_md_rb,
            )
            if new_strike is None:
                return None  # Strike not resolvable — Python fallback
            _md_rb[int(s.get("leg_id", 1))] = float(new_strike)
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

    # YEARLY resolves the carry policy in Rust (simulate.rs `opens_new_epoch`),
    # where the epoch resets per yearly cycle and — for Fresh — per month, and
    # where a carried strike is re-validated for liquidity against the December
    # contract.
    #
    # This post-process MUST NOT also run there. Its `effective_segs` falls back
    # to [(from_date, to_date)] — one segment for the whole backtest — so it
    # would treat every year as a single epoch and pin the 2019 strike onto the
    # 2021 contract. That is a wrong tradesheet, not a redundant no-op.
    if str(payload.get("expiry_type") or "").upper() == "YEARLY":
        return specs

    legs_src = payload.get("legs") or []
    fixed_leg_ids: Set[int] = {
        idx + 1
        for idx, leg in enumerate(legs_src)
        if isinstance(leg, dict) and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
    }
    # SAME-INDEX MIXED EXPIRY: legs PINNED to a coarser contract than the cadence
    # (MONTHLY leg under a WEEKLY cadence). A Fixed pinned leg must hold its
    # strike for the life of ONE contract and re-strike when the pin rolls —
    # exactly what a Fixed YEARLY leg does via `opens_new_epoch`'s new_cycle.
    # Without this it carries the segment's very first strike onto every later
    # contract (observed: a 24300 PE held onto the July contract with spot
    # ~25500 — a strike nobody chose).
    #
    # FIXED-ONLY on purpose, and the `rollover_strike_mode` test below is what
    # enforces it. Without that test BOTH modes fell through to the per-contract
    # epoch and produced byte-identical tradesheets: a Fixed pinned leg is
    # excluded from the segment-lock branch by `not in pinned_ids`, and a Fresh
    # pinned leg was swept into the epoch branch, so neither branch ever read the
    # mode. Measured on NIFTY weekly cadence + MONTHLY CE ATM, Jul-Oct 2022:
    # Fresh and Fixed returned identical 31-row (spot-adj on) and 18-row
    # (spot-adj off) sheets, Fresh holding 16600 from 27-Jul to 17-Aug while spot
    # ran 16,641 -> 17,944 (a 1,344-pt ITM short call). A Fresh pinned leg must
    # re-strike at EVERY cadence re-book, i.e. be left alone here.
    pinned_ids: Set[int] = set()
    if str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES:
        pinned_ids = {
            idx + 1
            for idx, leg in enumerate(legs_src)
            if isinstance(leg, dict)
            and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
            and str(leg.get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
            and str(leg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
        }

    # No-op only when there is neither a Fixed leg NOR a pinned leg.
    if not fixed_leg_ids and not pinned_ids:
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
            # Pinned legs are handled per-contract below, not per-segment.
            if leg_id in fixed_leg_ids and leg_id not in pinned_ids:
                seg_first_strikes[leg_id] = float(s.get("strike") or 0.0)

        for tid in seg_tids[1:]:
            for leg_id, saved_strike in seg_first_strikes.items():
                strike_overrides[(tid, leg_id)] = saved_strike

        # Pinned Fixed legs: one epoch per CONTRACT inside the segment. The first
        # trade on each contract keeps its own freshly-resolved strike (no
        # override) and defines the epoch; later trades on that same contract
        # carry it. When the pin rolls, the next trade re-strikes naturally.
        if pinned_ids:
            epoch_strike: Dict[Tuple[int, str], float] = {}
            for tid in seg_tids:
                for s in trade_groups.get(tid, []):
                    leg_id = int(s.get("leg_id", 1))
                    if leg_id not in pinned_ids:
                        continue
                    ckey = _normalize_iso(s.get("expiry")) or ""
                    if (leg_id, ckey) in epoch_strike:
                        strike_overrides[(tid, leg_id)] = epoch_strike[(leg_id, ckey)]
                    else:
                        epoch_strike[(leg_id, ckey)] = float(s.get("strike") or 0.0)

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


def _reanchor_yearly_fresh_on_segments(
    specs: List[Dict[str, Any]],
    payload: Dict[str, Any],
    segments: Optional[List[Tuple[str, str]]],
    spot_by_date: Dict[str, float],
) -> List[Dict[str, Any]]:
    """
    Re-anchor YEARLY 'Fresh' option legs on the SURVIVING (segment-gated) trades.

    Rust resolves the yearly month-epoch on the CONTINUOUS, pre-filter schedule
    (simulate.rs::opens_new_epoch — one strike per calendar month, held across
    every roll inside it). When a filter drops a month's early trades and the
    strategy re-enters mid-month, the surviving trade inherits a strike anchored
    on a trade that never made the tradesheet. Measured on this desk's
    weekly-CE + yearly-PE run: NIFTY 30-Apr-2024 held the yearly PE at 22000 —
    the epoch set by a 01-Apr phantom at spot ~22,450 — while 30-Apr's OWN ATM
    was 23000 and the 23000 Dec PE was fully liquid (2,281 contracts). The
    fixed-entry builder never hit this because it rebuilds the chain from each
    segment's first SURVIVING entry (_build_fixed_entry_specs:2104); the DTE path
    resolves the epoch before gating, so it needs this correction after.

    Re-run the month-epoch over the gated specs, resetting the epoch at each
    segment (patch) start as well as each calendar month and yearly-cycle change,
    so the first surviving trade of each patch/month re-strikes to its own fresh
    ATM and the rest of that month/patch hold it. Only YEARLY-expiry_type runs
    with a live filter and a yearly Fresh option leg are touched; a row is
    rewritten only when its strike actually changes, so every already-correct
    trade is left byte-for-byte as Rust produced it.
    """
    if not segments:
        return specs
    if str(payload.get("expiry_type") or "").upper() != "YEARLY":
        return specs
    legs_src = payload.get("legs") or []
    index_str = str(payload.get("index") or "NIFTY").upper()
    default_iv = _STRIKE_INTERVALS.get(index_str, 50.0)

    target_legs: Dict[int, Dict[str, Any]] = {}
    for _idx, _leg in enumerate(legs_src):
        if not isinstance(_leg, dict):
            continue
        if str(_leg.get("segment") or "OPTION").upper() in ("FUTURE", "FUTURES"):
            continue
        _exp = str(_leg.get("expiry") or _leg.get("expiry_type") or "").upper()
        _mode = str(_leg.get("rollover_strike_mode") or "fresh").lower()
        if _exp == "YEARLY" and _mode != "fixed":
            target_legs[_idx + 1] = _leg
    if not target_legs:
        return specs

    def _seg_index(entry_iso: str) -> Optional[int]:
        for _i, (_s0, _s1) in enumerate(segments):
            if _s0 <= entry_iso <= _s1:
                return _i
        return None

    by_leg: Dict[int, List[Dict[str, Any]]] = {}
    for _s in specs:
        _lid = int(_s.get("leg_id", 1))
        if _lid in target_legs:
            by_leg.setdefault(_lid, []).append(_s)

    for _lid, _leg in target_legs.items():
        _rows = sorted(
            by_leg.get(_lid, []),
            key=lambda s: _normalize_iso(s.get("entry_date") or ""),
        )
        _iv_raw = _leg.get("strike_interval")
        try:
            _leg_iv = float(_iv_raw) if _iv_raw else default_iv
        except (TypeError, ValueError):
            _leg_iv = default_iv
        _opt = str(_leg.get("option_type") or "CE").upper()
        _is_ce = _opt in ("CE", "CALL", "C")
        _prev_seg: Optional[int] = None
        _prev_month: Optional[str] = None
        _prev_contract: Optional[str] = None
        _held: Optional[float] = None
        for _s in _rows:
            _entry = _normalize_iso(_s.get("entry_date") or "")
            _expiry = _normalize_iso(_s.get("expiry") or "")
            _seg = _seg_index(_entry)
            _month = _entry[:7]
            _entry_spot = float(
                _s.get("entry_spot") or spot_by_date.get(_entry) or 0.0
            )
            if _entry_spot <= 0:
                _prev_seg, _prev_month, _prev_contract = _seg, _month, _expiry
                continue
            _atm = round(_entry_spot / _leg_iv) * _leg_iv
            _new_epoch = (
                _held is None
                or _seg != _prev_seg
                or _month != _prev_month
                or _expiry != _prev_contract
            )
            if _new_epoch:
                _info: Dict[str, Any] = {}
                _fresh = _compute_strike_for_leg_python(
                    _leg, _entry_spot, _leg_iv,
                    entry_date=_entry, expiry=_expiry, index=index_str,
                    out_info=_info,
                )
                if _fresh is not None:
                    _held = float(_fresh)
                    _new_strike = float(_fresh)
                    _new_req = float(_info.get("requested_strike") or _fresh)
                else:
                    _new_strike = None
                    _new_req = None
            elif _held is not None:
                _validated = _validate_or_shift_strike_python(
                    _held, _atm, _leg_iv, _is_ce, _entry, _expiry,
                    index_str, _opt, 1,
                )
                _new_strike = float(_validated if _validated is not None else _held)
                _new_req = float(_held)
            else:
                _new_strike = None
                _new_req = None
            if _new_strike is not None:
                _cur = float(_s.get("strike") or 0.0)
                if abs(_new_strike - _cur) > 1e-6:
                    _s["strike"] = _new_strike
                    _s["requested_strike"] = _new_req
            _prev_seg, _prev_month, _prev_contract = _seg, _month, _expiry
    return specs


def _payload_uses_straddle_width(payload: Dict[str, Any]) -> bool:
    """True when ANY leg selects its strike via the ``straddle_width`` mode.

    Drives whether the ATM-straddle context columns (ATM Strike / ATM Call /
    ATM Put / ATM Call+Put) are added to the tradesheet — they are hidden for
    every other strike mode. Purely a display gate: no calculation depends on
    it.
    """
    for leg in (payload.get("legs") or []):
        if not isinstance(leg, dict):
            continue
        sel = leg.get("strike_selection") or {}
        if isinstance(sel, dict) and str(sel.get("type") or "").lower().strip() == "straddle_width":
            return True
    return False


def _atm_straddle_prices(
    native: Any,
    cache: Dict[tuple, Optional[tuple]],
    entry_date: Optional[str],
    index: str,
    entry_spot: float,
    interval: float,
    expiry: Optional[str],
) -> Optional[tuple]:
    """Return ``(atm_strike, ce_px, pe_px, ce+pe, source_reason)`` at trade
    entry, or None.

    Display-only: re-reads the SAME ATM CE/PE prices the straddle_width strike
    selection already used — including the same liquidity check and gap-
    widening fallback (gap, 2x, 3x, 4x) as _compute_strike_for_leg_python, so
    this never shows a stale zero-turnover close price (e.g. a dead PE's
    stale close of 1223.85) when the strike math itself used a corrected,
    widened price. Cached per (entry_date, expiry, atm) so a multi-leg
    straddle only hits the lookup once. Never affects strike/P&L.
    """
    if native is None or not (entry_date and expiry and interval and entry_spot and entry_spot > 0):
        return None
    atm = round(entry_spot / interval) * interval
    key = (entry_date, expiry, atm)
    if key in cache:
        return cache[key]
    try:
        ce = native.get_option_price_tradeable(entry_date, index, atm, "CE", expiry)
        pe = native.get_option_price_tradeable(entry_date, index, atm, "PE", expiry)
        source = ""
        if ce is None or pe is None:
            missing = ("CE" if ce is None else "") + ("PE" if pe is None else "")
            widened = None
            for mult_n in (2, 3, 4):
                w_gap = interval * mult_n
                w_atm = round(entry_spot / w_gap) * w_gap
                w_ce = native.get_option_price_tradeable(entry_date, index, w_atm, "CE", expiry)
                w_pe = native.get_option_price_tradeable(entry_date, index, w_atm, "PE", expiry)
                if w_ce is not None and w_pe is not None:
                    widened = (w_gap, w_ce, w_pe)
                    break
            if widened is None:
                cache[key] = None
                return None
            w_gap, ce, pe = widened
            source = f"{interval:g}→{w_gap:g} (ATM {missing} zero turnover)"
    except Exception:
        cache[key] = None
        return None
    if ce is None or pe is None:
        cache[key] = None
        return None
    res = (float(atm), round(float(ce), 2), round(float(pe), 2), round(float(ce) + float(pe), 2), source)
    cache[key] = res
    return res


def _apply_carry_slippage_guard(priced: List[Dict[str, Any]]) -> None:
    """
    Charge slippage only when a leg actually transacts.

    Slippage is a real transaction cost — it should be incurred on the trade
    where a leg is opened, rolled, or shifted, NOT on every row where the leg
    is merely carried forward. A long-held leg (e.g. a yearly hedge) is marked
    across each short-dated re-entry, but it is not re-traded: its exit price on
    one row equals its entry price on the next. Charging round-trip slippage on
    every such carry row is unrealistic (the "not feasible" case flagged by the
    analyst) and inflates cost.

    Rule (universal, per-leg — nothing yearly-specific), comparing each row to
    the SAME leg's neighbours in chronological (entry-date) order:
      * ENTRY slippage on the row that OPENS a contract — strike/expiry differs
        from the PREVIOUS row (or first row). Otherwise entry uses the RAW price.
      * EXIT slippage on the row that CLOSES a contract — strike/expiry differs
        from the NEXT row (or final row). Otherwise exit uses the RAW price.
      * A carried contract is therefore bought once (its open row's entry) and
        sold once (its close row's exit); every marked row in between is raw on
        both sides.

    Non-breaking by construction:
      * Legs that change strike/expiry every trade (ordinary weekly / monthly
        backtests) are OPEN and CLOSE on every row, so entry+exit stay slipped
        exactly as Rust produced them — output is bit-identical.
      * When nothing moves, the pass returns without mutating a single row.
      * With slippage off (raw == slipped) restoring raw is a no-op.

    Mutates ``priced`` in place. This is the single list that both the backtest
    (run_rust_engine_pipeline) and the optimizer per-combo/master funnel through
    priced_to_tradesheet_records, so backtest == optim == master stays identical.
    """
    def _tid(r: Dict[str, Any]) -> int:
        try:
            return int(r.get("trade_id") or 0)
        except (TypeError, ValueError):
            return 0

    # Chronological per leg by ENTRY DATE — NOT trade_id. Spot-adjustment /
    # cascade re-entries are appended with out-of-order trade_ids (e.g. a
    # 12-Mar-2019 re-entry can carry trade_id 68, sorting after the 2020 epoch),
    # so ordering by trade_id would compare a carry row against a later epoch's
    # contract and wrongly flag it as a change (double-charging slippage). Entry
    # date is the true chronology; trade_id/leg_id only break ties.
    def _edate(r: Dict[str, Any]) -> str:
        return _normalize_iso(r.get("entry_date") or "") or str(r.get("entry_date") or "")
    order = sorted(
        range(len(priced)),
        key=lambda i: (_edate(priced[i]), _tid(priced[i]), int(priced[i].get("leg_id") or 0)),
    )
    # Group chronological indices per leg to find each contract's OPEN and CLOSE
    # rows. Slippage is a real transaction cost, charged once per side of a
    # contract's life:
    #   * ENTRY slippage on the row that OPENS a contract (strike/expiry differs
    #     from the leg's PREVIOUS row, or the first row) — the buy.
    #   * EXIT slippage on the row that CLOSES a contract (strike/expiry differs
    #     from the leg's NEXT row, or the final row) — the sell.
    #   * Pure carry rows (contract unchanged on both sides) get NEITHER; their
    #     entry/exit are marks, not trades.
    # A contract is thus bought once and sold once — one clean round-trip. Legs
    # whose contract changes every row (ordinary weekly/monthly) are OPEN and
    # CLOSE on every row, so entry+exit stay slipped exactly as Rust produced
    # them → byte-identical output for those strategies.
    from collections import defaultdict as _dd
    _by_leg: Dict[int, List[int]] = _dd(list)
    for i in order:
        _by_leg[int(priced[i].get("leg_id") or 0)].append(i)

    def _slipped(raw: Any, cur: Any, position: str, side: str, pct: Any) -> Optional[float]:
        # The slipped fill for one side. Prefer Rust's already-slipped value
        # (byte-identical for ordinary strategies), else compute it from raw —
        # the yearly path only slips epoch-boundary rows, so a close row's exit
        # may still be raw here and must be slipped explicitly.
        if raw is None:
            return round(float(cur), 2) if cur is not None else None
        raw = float(raw)
        if cur is not None and abs(float(cur) - raw) > 1e-9:
            return round(float(cur), 2)  # Rust already slipped this side
        try:
            s = float(pct or 0.0) / 100.0
        except (TypeError, ValueError):
            s = 0.0
        if s <= 0.0:
            return round(raw, 2)
        is_sell = str(position or "SELL").strip().upper() == "SELL"
        if side == "entry":
            f = (1.0 - s) if is_sell else (1.0 + s)
        else:
            f = (1.0 + s) if is_sell else (1.0 - s)
        return round(max(raw * f, 0.0), 2)

    def _raw2(v: Any, fallback: Any) -> Optional[float]:
        if v is not None:
            return round(float(v), 2)
        return round(float(fallback), 2) if fallback is not None else None

    affected: set = set()
    for _leg, idxs in _by_leg.items():
        keys = [(str(priced[i].get("strike")), str(priced[i].get("expiry"))) for i in idxs]
        n = len(idxs)
        for pos, i in enumerate(idxs):
            r = priced[i]
            k = keys[pos]
            is_open = (pos == 0) or (k != keys[pos - 1])
            is_close = (pos == n - 1) or (k != keys[pos + 1])
            _posn = r.get("position")
            _pct = r.get("slippage_pct")
            cur_e = r.get("entry_price")
            cur_x = r.get("exit_price")
            # ENTRY: slipped on an open (buy), raw on a carried/held row.
            new_e = (_slipped(r.get("raw_entry_price"), cur_e, _posn, "entry", _pct)
                     if is_open else _raw2(r.get("raw_entry_price"), cur_e))
            # EXIT: slipped on a close (sell), raw on a carried/held row.
            new_x = (_slipped(r.get("raw_exit_price"), cur_x, _posn, "exit", _pct)
                     if is_close else _raw2(r.get("raw_exit_price"), cur_x))
            if new_e is not None and cur_e is not None and round(float(new_e), 2) != round(float(cur_e), 2):
                r["entry_price"] = round(float(new_e), 2)
                affected.add(_tid(r))
            if new_x is not None and cur_x is not None and round(float(new_x), 2) != round(float(cur_x), 2):
                r["exit_price"] = round(float(new_x), 2)
                affected.add(_tid(r))

    if not affected:
        return  # every row is a full open+close → ordinary strategies untouched

    # Recompute net_pnl ONLY for trades whose prices moved, so every unaffected
    # trade keeps its Rust value byte-for-byte. Convention mirrors the
    # mixed-futures merge (points × lots; lowest leg_id carries the trade total).
    by_tid: Dict[int, List[Dict[str, Any]]] = _dd(list)
    for r in priced:
        if _tid(r) in affected:
            by_tid[_tid(r)].append(r)
    for _tid_key, rows in by_tid.items():
        for r in rows:
            ep = float(r.get("entry_price") or 0.0)
            xp = float(r.get("exit_price") or 0.0)
            pos = str(r.get("position") or "SELL").upper()
            lots = float(r.get("lots") or 1)
            r["net_pnl"] = round(((ep - xp) if pos == "SELL" else (xp - ep)) * lots, 4)
        if len(rows) > 1:
            total = round(sum(float(r.get("net_pnl") or 0.0) for r in rows), 4)
            min(rows, key=lambda r: int(r.get("leg_id") or 1))["net_pnl"] = total


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
    # T-n scheduled-exit label. When the run exits N>0 trading days before the
    # contract expiry (exit_dte > 0) the trade rides to its SCHEDULED exit, not
    # the actual expiry — so the neutral "EXPIRY" reason reads "SCHEDULED_EXIT"
    # (alone or within a combo, e.g. "SCHEDULED_EXIT+SPOT_ADJ_RISE"). T-0 runs
    # (exit ON expiry) keep "EXPIRY". This converter is the single point EVERY
    # engine return path (simple/full/futures) and EVERY output (backtest +
    # optimizer ZIP/single-combo/master) funnels through, so the relabel applies
    # uniformly across weekly / next-weekly / monthly / next-monthly. Purely
    # cosmetic: no exit date, price, or P&L changes; no downstream calc keys off
    # the "EXPIRY" string (SL-cap / FILTER_END resets use other tokens).
    _tn_run = int(payload.get("exit_dte") or 0) > 0
    # ATM-straddle context columns: only computed when the strategy actually
    # uses the straddle_width strike mode (otherwise hidden entirely). Zero
    # overhead + no extra keys for every other strategy.
    _uses_sw = _payload_uses_straddle_width(payload)
    _sw_native = None
    _sw_cache: Dict[tuple, Optional[tuple]] = {}
    # Per-leg strike gap sourced from the ORIGINAL payload leg config, not
    # row.get("strike_interval") — the Rust-simulated row can normalize/
    # overwrite that field to the index default, silently dropping a leg's
    # own override (e.g. MIDCPNIFTY leg explicitly set to 50 while the index
    # default is 25) and making the display ATM Strike land on a different
    # grid than the actually-traded Strike.
    _sw_leg_intervals: Dict[int, float] = {}
    # The ATM-straddle context (ATM Strike/Call/Put/Sum) is a display fact for the
    # OPTIONS leg that uses straddle_width — NOT necessarily leg 1. When a FUTURES
    # leg is added first it becomes leg 1 and would wrongly carry these columns.
    # Anchor them on the first straddle_width leg, else the first non-FUTURES leg.
    _atm_anchor_leg_id = 1
    if _uses_sw:
        try:
            import algotest_native as _sw_native  # type: ignore
        except ImportError:
            _sw_native = None
        _first_opt_leg = None
        _first_sw_leg = None
        for _li, _lg in enumerate((payload.get("legs") or []), start=1):
            if not isinstance(_lg, dict):
                continue
            _liv = _lg.get("strike_interval")
            try:
                _sw_leg_intervals[_li] = float(_liv) if _liv else _STRIKE_INTERVALS.get(
                    str(_lg.get("index") or index_str).upper(), 50.0
                )
            except (TypeError, ValueError):
                _sw_leg_intervals[_li] = _STRIKE_INTERVALS.get(index_str, 50.0)
            if str(_lg.get("segment") or "").upper() not in ("FUTURES", "FUTURE") and _first_opt_leg is None:
                _first_opt_leg = _li
            _sel = _lg.get("strike_selection") or {}
            if (_first_sw_leg is None and isinstance(_sel, dict)
                    and str(_sel.get("type") or "").lower().strip() == "straddle_width"):
                _first_sw_leg = _li
        _atm_anchor_leg_id = _first_sw_leg or _first_opt_leg or 1
    # Carry-aware slippage: strip slippage from rows where a leg was merely
    # carried forward (same strike + expiry as its own previous trade). No-op
    # for ordinary strategies whose legs change every trade. Runs here so both
    # the backtest and the optimizer (which share this converter) apply it
    # identically.
    _apply_carry_slippage_guard(priced)
    # Spot P&L is a trade-level quantity and rides ONE row per trade. That row
    # is the trade's LOWEST PRESENT leg — not literally leg 1, because a leg
    # can be absent: an individual per-leg filter file removes it from the
    # trade. This mirrors native/src/simulate.rs:1793-1803, which places the
    # Net P&L total on `lowest_leg` computed the same way. Before this, the
    # gate was `leg_id == 1`, so a trade whose leg 1 was filtered out reported
    # a BLANK Spot P&L.
    # A trade can carry TWO rows with the same leg_id (e.g. a futures primary
    # row + its re-entry row, see _build_futures_specs :1623-1624 / :1733-1734)
    # — both would match the lowest-leg check below and double the Spot P&L
    # sum. `_spot_assigned` guards that, mirroring simulate.rs:1799-1805's
    # `total_assigned` HashSet: the FIRST matching row (input order) wins.
    _lowest_leg_by_trade: Dict[Any, int] = {}
    for _r in priced:
        _tid = _r.get("trade_id")
        _lid = int(_r.get("leg_id") or 1)
        if _tid not in _lowest_leg_by_trade or _lid < _lowest_leg_by_trade[_tid]:
            _lowest_leg_by_trade[_tid] = _lid
    _spot_assigned: set = set()
    out: List[Dict[str, Any]] = []
    for row in priced:
        opt_type = (row.get("option_type") or "").upper()
        position = (row.get("position") or "SELL").upper()
        entry_spot = float(row.get("entry_spot") or 0.0)
        exit_spot = float(row.get("exit_spot") or 0.0)
        # Spot P&L is a trade-level quantity: write it only on the row for the
        # trade's lowest PRESENT leg (see _lowest_leg_by_trade above) and leave
        # the rest blank. Per-row summing then yields the trade total without
        # double-counting for multi-leg strategies.
        _leg_id_val = int(row.get("leg_id") or 1)
        _row_tid = row.get("trade_id")
        if (_leg_id_val == _lowest_leg_by_trade.get(_row_tid, 1)
                and _row_tid not in _spot_assigned):
            spot_pnl = round(exit_spot - entry_spot, 2)
            _spot_assigned.add(_row_tid)
        else:
            spot_pnl = ""
        net_pnl = float(row.get("net_pnl") or 0.0)
        # CE/PE P&L are PER-LEG values. The simulate.rs post-process puts the
        # trade total in the parent row's `net_pnl`, so we cannot read per-leg
        # P&L back from that column. Recompute it from entry/exit prices —
        # this matches Python's tradesheet builder which stores per-leg P&L
        # in CE P&L / PE P&L and then aggregates them in compute_analytics.
        entry_px = float(row.get("entry_price") or 0.0)
        exit_px = float(row.get("exit_price") or 0.0)
        is_fut = opt_type == "FUT"
        # P&L = POINTS x LOTS (see native/src/simulate.rs:1652). Uses THIS leg's
        # lots so ratio spreads (leg 1 = 2 lots, leg 2 = 1 lot) price correctly.
        _leg_lots = float(row.get("lots") or 1)
        per_leg_pnl = round(
            ((entry_px - exit_px) if position == "SELL" else (exit_px - entry_px)) * _leg_lots, 4
        )
        ce_pnl = per_leg_pnl if opt_type == "CE" else 0
        pe_pnl = per_leg_pnl if opt_type == "PE" else 0
        fut_pnl = per_leg_pnl if is_fut else 0
        pct_pnl = round(net_pnl / entry_spot * 100.0, 4) if entry_spot else 0.0
        _row_lots_int = int(row.get("lots") or 1)
        qty = _row_lots_int * int(row.get("lot_size") or lot_size or 1)
        # FUTURES: Strike = '' (matches Python engine convention); options: float.
        strike_val = "" if is_fut else float(row.get("strike") or 0.0)
        # Strike Shift Reason — populated whenever the engine moved the
        # requested strike to a tradeable one because the original contract had
        # no liquidity on entry day. Shows the ACTUAL cause (zero turnover vs
        # strike-not-listed), the from→to strikes, the number of walk steps
        # (using the finer per-index step for coarse 500 gaps), and the walk
        # direction. Applies to EVERY strike-selection mode (the shift runs on
        # the final strike of all modes) and every index. Empty when no shift.
        _shift_reason = ""
        try:
            _req = row.get("requested_strike")
            if _req is not None and not is_fut and strike_val != "":
                _req_f = float(_req)
                _act_f = float(strike_val)
                if abs(_req_f - _act_f) > 1e-6:
                    _intvl = float(row.get("strike_interval") or 50.0) or 50.0
                    _walk_step, _ = _liquidity_walk_step(index_str, _intvl)
                    _steps = max(1, int(round(abs(_act_f - _req_f) / _walk_step)))
                    _cause = "zero turnover"  # historical default (safe)
                    try:
                        import algotest_native  # type: ignore
                        _stfn = getattr(algotest_native, "get_option_status", None)
                        if _stfn is not None:
                            _st = _stfn(
                                _normalize_iso(row.get("entry_date")), index_str,
                                _req_f, opt_type, _normalize_iso(row.get("expiry")),
                            )
                            if _st == "missing":
                                _cause = "strike not listed"
                            elif _st == "zero_contracts":
                                _cause = "zero turnover"
                    except Exception:
                        pass
                    _atm_f = (round(entry_spot / _intvl) * _intvl) if entry_spot else _act_f
                    _dir = "toward ATM" if abs(_act_f - _atm_f) <= abs(_req_f - _atm_f) else "outward"
                    _fmt = lambda x: int(x) if float(x).is_integer() else round(x, 2)
                    _shift_reason = (
                        f"{_fmt(_req_f)}→{_fmt(_act_f)} "
                        f"({_cause}, {_steps} step{'s' if _steps != 1 else ''} {_dir})"
                    )
        except (TypeError, ValueError):
            pass
        _exit_reason = str(row.get("exit_reason") or "EXPIRY")
        if _tn_run and "EXPIRY" in _exit_reason.upper():
            _exit_reason = "+".join(
                "SCHEDULED_EXIT" if p.upper() == "EXPIRY" else p
                for p in _exit_reason.split("+")
            )
        _rec = {
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
            # This row's own lots (lot_size excluded) — needed downstream to
            # scale MAE/MFE into the same leveraged-percentage unit as % P&L
            # (Task 7, see algotest_job.py MAE/MFE write site). Not part of
            # excel_builder._build_key_order's explicit whitelist, so it never
            # reaches Excel output.
            "lots": _row_lots_int,
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
            # Cadence contract for the trade. Falls back to the leg's own expiry
            # when the builder did not stamp one (every non-fixed-entry path), so
            # this column is always populated and always equals "Expiry" unless a
            # leg is actually pinned to a coarser contract.
            "Cadence Expiry": _normalize_iso(
                row.get("_cadence_expiry") or row.get("expiry")
            ),
            "CE P&L": ce_pnl,
            "PE P&L": pe_pnl,
            "FUT P&L": fut_pnl,
            "FUT Entry Price": entry_px if is_fut else "",
            "FUT Exit Price": exit_px if is_fut else "",
            "Net P&L": net_pnl,
            "% P&L": pct_pnl,
            "Exit Reason": _exit_reason,
            "Strike Shift Reason": _shift_reason,
            "ATM Straddle Price Source": str(row.get("straddle_price_source") or ""),
            "ReEntryIndex": row.get("_reentry_index") or "",
            "ReEntryTrigger": str(row.get("_reentry_trigger") or ""),
            "ReEntryMode": str(row.get("_reentry_mode") or ""),
            "Is Lazy Leg": False,
            "Lazy Leg Name": "",
            "Lazy Entry Date": "",
            "Lazy Exit Date": "",
        }
        # Straddle-width only: surface the ATM strike + its CE/PE prices (and
        # their sum) that the strike selection was derived from. Trade-level
        # entry fact → written on the first-leg row only, blank on the rest.
        if _uses_sw:
            sw = {"ATM Strike": "", "ATM Call Price": "", "ATM Put Price": "", "ATM Call+Put Price": ""}
            if _leg_id_val == _atm_anchor_leg_id and _sw_native is not None and entry_spot > 0:
                _sw_interval = _sw_leg_intervals.get(_leg_id_val) or float(row.get("strike_interval") or 50.0)
                _atm = _atm_straddle_prices(
                    _sw_native, _sw_cache, _normalize_iso(row.get("entry_date")),
                    index_str, entry_spot, _sw_interval,
                    _normalize_iso(row.get("expiry")),
                )
                if _atm is not None:
                    sw = {
                        "ATM Strike": _atm[0], "ATM Call Price": _atm[1],
                        "ATM Put Price": _atm[2], "ATM Call+Put Price": _atm[3],
                    }
                    # Prefer the strike-selection's own reason (row-sourced);
                    # fall back to this display recompute's reason so the two
                    # independent lookups never disagree in what's shown.
                    if not _rec["ATM Straddle Price Source"] and _atm[4]:
                        _rec["ATM Straddle Price Source"] = _atm[4]
            _rec.update(sw)
        out.append(_rec)

    # Cadence Expiry must be IDENTICAL across every leg of a trade — that is the
    # whole point of the column (WOW buckets on it, so a mixed trade's two legs
    # have to land in one week). Only the fixed-entry builder stamps
    # `_cadence_expiry`; spot-adj re-entry mini-trades come from the Phase 3
    # cascade and carry none, which left their legs disagreeing.
    #
    # Derive it per trade instead of trusting every builder: the cadence contract
    # is the NEAREST expiry in the trade, because a pinned leg is by definition on
    # a contract further out. For a non-mixed trade every leg shares one expiry, so
    # this is exactly "Expiry" and WOW output is unchanged.
    _cad_by_trade: Dict[Any, str] = {}
    for _r in out:
        _t = _r.get("Trade")
        _e = _r.get("Cadence Expiry") or _r.get("Expiry")
        if not _e:
            continue
        _prev = _cad_by_trade.get(_t)
        if _prev is None or _e < _prev:
            _cad_by_trade[_t] = _e
    for _r in out:
        _e = _cad_by_trade.get(_r.get("Trade"))
        if _e:
            _r["Cadence Expiry"] = _e
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
        # This sum is ALREADY lots-scaled (each leg's own lots, including the FUT
        # re-entry rows from _build_futures_specs). Do NOT multiply by lots again
        # here or the trade-total leg would be scaled twice (lots^2).
        _trade_total = round(sum(float(_r.get("net_pnl") or 0.0) for _r in _rows), 4)
        _first = min(_rows, key=lambda _r: int(_r.get("leg_id") or 1))
        _first["net_pnl"] = _trade_total

    # Order rows by (trade, leg) ascending so the row carrying Spot P&L + the
    # trade total — the trade's LOWEST PRESENT leg, not necessarily leg 1 (see
    # priced_to_tradesheet_records) — sorts FIRST within each trade. This fixes
    # the optim's `Spot P&L: first` aggregation picking a leading blank option row.
    combined.sort(key=lambda _r: (int(_r.get("trade_id") or 0), int(_r.get("leg_id") or 0)))
    return combined if combined else None


def _build_mixed_futures_options(
    payload: Dict[str, Any],
    *,
    expiry_dates: List[str],
    trading_days: List[str],
    lot_size: int,
    spot_by_date: Dict[str, float],
    square_off_mode: str,
) -> Optional[List[Dict[str, Any]]]:
    """
    General mixed OPTIONS + FUTURES with UNIT-EXIT semantics: both legs enter and
    exit together on the strategy/option trade window — mirroring the Python engine,
    which anchors a mixed trade's exit to the option's current expiry
    (``effective_to_date = ... else trade_curr_expiry``).

    Additive design — reuses ALL existing option machinery:
      * OPTION legs flow through the FULL normal pipeline via a recursive call on a
        futures-stripped payload, so SL/Target, spot-adjustment, midcap, buffer
        strike and segment gating behave exactly as an options-only run.
      * FUTURES legs are RUST-PRICED (native ``get_future_price`` from the FUTIDX
        Arrow cache) and aligned to each option trade's ``(entry_date -> trade
        exit)``.  Contract-expiry RESOLUTION stays Python (calendar orchestration).
        This is the agreed "Rust prices, Python orchestrates" split — no Python
        pricing, no Python-engine fallback.

    Returns:
      * ``None``  → not the mixed case, or an unsupported / failed configuration
                    (caller keeps existing behavior — nothing breaks).
      * ``[]``    → option sub-run produced no trades.
      * ``[row]`` → merged, priced option+futures rows (pre-tradesheet).
    """
    legs_src = payload.get("legs") or []
    fut_legs = [
        (i, l) for i, l in enumerate(legs_src, start=1)
        if isinstance(l, dict)
        and str(l.get("segment") or "OPTION").upper() in ("FUTURE", "FUTURES")
    ]
    opt_legs = [
        (i, l) for i, l in enumerate(legs_src, start=1)
        if isinstance(l, dict)
        and str(l.get("segment") or "OPTION").upper() not in ("FUTURE", "FUTURES")
    ]
    if not fut_legs or not opt_legs:
        return None  # mixed mode requires BOTH leg types

    index = str(payload.get("index") or "NIFTY").upper()

    # Native futures pricing source must be loaded for this index (self-refreshing).
    try:
        from services.futures_cache_store import ensure_futures_loaded
        if not ensure_futures_loaded(index):
            logger.warning("[ENGINE_RUST] mixed-fut: FUTIDX cache not loaded for %s — bailing", index)
            return None
    except Exception as _exc:
        logger.warning("[ENGINE_RUST] mixed-fut: ensure_futures_loaded failed: %s", _exc)
        return None

    # ── 1) OPTION legs through the full normal pipeline (futures stripped) ──────
    opt_payload = {**payload, "legs": [l for _, l in opt_legs]}
    opt_rows = run_rust_engine_pipeline(
        opt_payload,
        expiry_dates=expiry_dates,
        trading_days=trading_days,
        lot_size=lot_size,
        spot_by_date=spot_by_date,
        square_off_mode=square_off_mode,
    )
    if opt_rows is None:
        return None
    if not opt_rows:
        return []

    # Remap option leg_ids (1..M in the sub-payload) back to original positions.
    _opt_remap = {new: orig for new, (orig, _) in enumerate(opt_legs, start=1)}
    for r in opt_rows:
        if r.get("leg_id") in _opt_remap:
            r["leg_id"] = _opt_remap[r["leg_id"]]

    # ── 2) Trade windows from option rows ──────────────────────────────────────
    # Unit-exit: the futures leg exits at the option trade's exit. When option legs
    # disagree (multi-leg), the LATEST exit per entry_date is the trade exit.
    trade_exit_by_entry: Dict[str, str] = {}
    # Also track the option leg's EXPIRY cycle per entry — the futures leg holds the
    # SAME contract month as the option, so a roll-in stub whose exit is clamped to a
    # filter/patch boundary (exit in the CURRENT month while the option already rolled
    # to NEXT) doesn't drop the futures back to the near contract.
    opt_expiry_by_entry: Dict[str, str] = {}
    for r in opt_rows:
        e = r.get("entry_date")
        x = r.get("exit_date")
        if not e or not x:
            continue
        if e not in trade_exit_by_entry or x > trade_exit_by_entry[e]:
            trade_exit_by_entry[e] = x
        _ex = r.get("expiry")
        if _ex and (e not in opt_expiry_by_entry or str(_ex) > opt_expiry_by_entry[e]):
            opt_expiry_by_entry[e] = str(_ex)
    if not trade_exit_by_entry:
        return opt_rows  # no datable option trades — nothing to hedge

    # ── 3) RUST-PRICED futures rows aligned to each option trade window ─────────
    fut_rows: List[Dict[str, Any]] = []
    for entry_date in sorted(trade_exit_by_entry.keys()):
        trade_exit = trade_exit_by_entry[entry_date]
        entry_spot = float(spot_by_date.get(entry_date) or 0.0)
        exit_spot = float(spot_by_date.get(trade_exit) or 0.0)
        for leg_id, leg in fut_legs:
            position = str(leg.get("position") or "SELL").upper()
            lots = int(leg.get("lots") or 1)
            pref_raw = str(leg.get("expiry") or "monthly").lower()
            pref = "next_monthly" if pref_raw in ("next_monthly", "next_month", "mid_month") else "monthly"
            _leg_slip = _leg_slippage_pct(leg)

            # Contract must survive to the unit-exit date; on an expiry-day entry
            # this rolls to next month (normal weekly cycles resolve identically).
            # Anchor the contract to the OPTION leg's EXPIRY cycle (not the possibly
            # filter/patch-clamped trade_exit) so both legs hold the SAME month: on a
            # roll-in stub the option has rolled to next month but trade_exit is
            # clamped back into the current month, which would otherwise pick the near
            # futures contract. opt_expiry >= trade_exit always, so the chosen contract
            # still survives the actual hold. Falls back to trade_exit if unknown.
            _contract_anchor = opt_expiry_by_entry.get(entry_date) or trade_exit
            fut_expiry = _fut_resolve_expiry_for_hold(index, entry_date, _contract_anchor, pref)
            if not fut_expiry:
                logger.warning("[ENGINE_RUST] mixed-fut: no %s contract for %s @ %s", pref, index, entry_date)
                return None
            ep_raw = _fut_price(index, entry_date, fut_expiry)
            xp_raw = _fut_price(index, trade_exit, fut_expiry)
            if ep_raw is None:
                logger.warning("[ENGINE_RUST] mixed-fut: no entry fut price %s %s exp=%s", index, entry_date, fut_expiry)
                return None
            if xp_raw is None:
                xp_raw = ep_raw  # exit-day close missing → priced flat (matches Python guard)
            ep_raw = float(ep_raw)
            xp_raw = float(xp_raw)

            if _leg_slip > 0:
                _ef = (1.0 - _leg_slip / 100.0) if position == "SELL" else (1.0 + _leg_slip / 100.0)
                _xf = (1.0 + _leg_slip / 100.0) if position == "SELL" else (1.0 - _leg_slip / 100.0)
                entry_price = round(max(ep_raw * _ef, 0.0), 2)
                exit_price = round(max(xp_raw * _xf, 0.0), 2)
            else:
                entry_price = round(ep_raw, 2)
                exit_price = round(xp_raw, 2)

            fut_rows.append({
                "trade_id": 0,          # assigned in step 4
                "leg_id": leg_id,
                "index": index,
                "entry_date": entry_date,
                "exit_date": trade_exit,
                "expiry": fut_expiry,
                "strike": 0.0,
                "option_type": "FUT",
                "position": position,
                "lots": lots,
                "lot_size": lot_size,
                "slippage_pct": _leg_slip,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "raw_entry_price": round(ep_raw, 4),
                "raw_exit_price": round(xp_raw, 4),
                "net_pnl": 0.0,         # set in step 5
                "entry_spot": entry_spot,
                "exit_spot": exit_spot,
                "exit_reason": "EXPIRY",
            })

    # ── 4) Shared trade_ids by entry_date so opt+fut of one period share a tid ──
    combined = list(opt_rows) + fut_rows
    _all_entries = sorted({r["entry_date"] for r in combined})
    _entry_to_tid = {e: i for i, e in enumerate(_all_entries, start=1)}
    for r in combined:
        r["trade_id"] = _entry_to_tid[r["entry_date"]]

    # ── 5) Net-P&L convention (matches priced_to_tradesheet_records/simulate.rs) ─
    # P&L is POINTS x LOTS (lot_size excluded — display Qty only).
    # The FIRST leg (lowest leg_id) of each trade carries the TRADE-TOTAL; other
    # legs carry their per-leg value. Recompute from PRICES so the merge is robust
    # to whatever net_pnl convention the recursive option rows arrived with.
    from collections import defaultdict as _dd
    for r in combined:
        _ep = float(r.get("entry_price") or 0.0)
        _xp = float(r.get("exit_price") or 0.0)
        _pos = str(r.get("position") or "SELL").upper()
        _r_lots = float(r.get("lots") or 1)
        r["net_pnl"] = round(((_ep - _xp) if _pos == "SELL" else (_xp - _ep)) * _r_lots, 4)
    _by_tid: Dict[int, List[Dict]] = _dd(list)
    for r in combined:
        _by_tid[int(r.get("trade_id") or 0)].append(r)
    for _tid, _rows in _by_tid.items():
        if len(_rows) <= 1:
            continue
        # _total sums the per-leg net_pnl values just scaled above — it is
        # ALREADY lots-scaled (each leg's own lots). Do NOT multiply by lots
        # again here or the trade-total leg would be scaled twice (lots^2).
        _total = round(sum(float(r.get("net_pnl") or 0.0) for r in _rows), 4)
        min(_rows, key=lambda r: int(r.get("leg_id") or 1))["net_pnl"] = _total

    # Order rows by (trade, leg) ascending so the row carrying Spot P&L and the
    # trade-total net_pnl — the trade's LOWEST PRESENT leg, not necessarily leg 1
    # (see priced_to_tradesheet_records) — is FIRST within each trade. The
    # optimizer's `Spot P&L: first` aggregation (and any first-row logic) then
    # picks the real value instead of a leading option row whose Spot P&L is
    # intentionally blank.
    combined.sort(key=lambda _r: (int(_r.get("trade_id") or 0), int(_r.get("leg_id") or 0)))
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
    return_specs_only: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """
    Run the Rust-accelerated pipeline end-to-end.

    `return_specs_only` (multi-index FUSED / Path B, opt-in): build + gate the
    trade specs exactly as this pipeline would, but return them RIGHT BEFORE
    `simulate_trades_batch` instead of pricing. The caller (services.
    multi_index_feature._run_sync_fused_groups) merges TWO symbols' spec lists
    into ONE simulate call so both legs can be priced together (each on its own
    index). No SL/Target/spot-adj/re-entry post-processing runs in this mode —
    those are Phase 2/3. Purely additive: the default False path is unchanged.

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
        # No caller in this codebase actually falls back to Python (Rust-only
        # rule) — returning None here means the caller rejects this payload
        # (raises/hard-fails for the optimizer, returns None/no-result for a
        # regular backtest). This just signals "cache wasn't ready," not that
        # any Python computation happens next.
        logger.warning("[ENGINE_RUST] Rust cache not loaded — rejecting payload (no Python fallback exists)")
        return None

    # ── YEARLY blockers ────────────────────────────────────────────────────────
    # YEARLY pins the option contract to a December expiry while the cadence
    # list drives entry/exit. Anything that assumes "contract == cadence element"
    # must be rejected LOUDLY here rather than silently producing a
    # plausible-but-wrong tradesheet. (simulate.rs separately rejects YEARLY
    # without yearly_cycles, and YEARLY + rollover_min_days_to_expiry.)
    if str(payload.get("expiry_type") or "").upper() == "YEARLY":
        if not payload.get("yearly_cycles"):
            raise ValueError(
                "expiry_type=YEARLY reached the engine without 'yearly_cycles'. "
                "Resolve the payload through engine_rust.resolve_expiry_inputs()."
            )
        # _build_futures_specs gates on `\"NEXT\" not in expiry_type`, which is
        # True for YEARLY — futures would roll across the *cadence* list. Futures
        # have their own monthly contracts and no December pin, so v1 blocks it
        # instead of silently rolling them wrong.
        # Same predicate _build_futures_specs uses to select its legs (:1241).
        if any(
            isinstance(_leg, dict)
            and str(_leg.get("segment") or "OPTION").upper() in ("FUTURE", "FUTURES")
            for _leg in (payload.get("legs") or [])
        ):
            raise ValueError(
                "expiry_type=YEARLY does not support FUTURES legs yet: futures have "
                "no long-dated December contract to pin to, and the futures rollover "
                "builder would roll them across the option cadence."
            )
        # NEXT_* is fine now that the pin is PER-LEG: a NEXT_WEEKLY leg is simply
        # not a yearly leg, so it never touches the December pin and resolves off
        # the cadence list exactly as it would under a weekly basis. (Previously
        # the pin was applied to every leg, so NEXT_* had to be rejected.)
        # At least one leg must actually be YEARLY, though — otherwise the run is
        # a weekly/monthly strategy wearing a YEARLY label and the December
        # contract would never be used. EXCEPT multi-index SYNC cadence
        # (run_sync_weekly_cadence), which drives its merged roll boundaries by
        # setting expiry_type="YEARLY" + yearly_cycles on the cadence-index's OWN
        # weekly/monthly legs — none of which are individually "YEARLY" by
        # design (the December pin is irrelevant there; sync_cadence_expiries is
        # what actually drives entry/exit). Gate on the sync cadence itself
        # rather than on "YEARLY", matching the same carve-out already used for
        # the spot-adjustment re-entry gate below, so a genuine yearly strategy
        # keeps its existing behaviour.
        if not payload.get("sync_cadence_expiries") and not any(
            isinstance(_l, dict) and str(_l.get("expiry") or "").upper() == "YEARLY"
            for _l in (payload.get("legs") or [])
        ):
            raise ValueError(
                "expiry_type=YEARLY but no leg has expiry=YEARLY. Set at least one "
                "leg to Yearly, or switch the strategy basis to weekly/monthly."
            )

    # Sorted expiry list used by NEXT_WEEKLY and LAZY_LEG expiry resolution.
    _sorted_expiries: List[str] = sorted(expiry_dates)

    # ── straddle_width joint-shift eligibility ──────────────────────────────────
    # Annotate each leg with whether it should use the JOINT CE+PE liquidity
    # walk (both legs shift together) vs the standard per-option-type walk.
    # Joint shifting is only correct when two straddle_width legs actually
    # resolve to the SAME strike (same multiplier + direction) — with
    # different multipliers each leg lands on a different strike, so forcing
    # a joint check makes one leg's shift depend on an unrelated contract the
    # OTHER leg doesn't even trade. Mutated in place on payload["legs"] (the
    # same list object every downstream builder/native call reads) so this
    # single pass covers every code path — DTE-mode Rust resolve_trade_specs,
    # every Python schedule builder, and the native compute_straddle_leg_strike
    # fast path — without threading a new parameter through each call site.
    _sw_legs = payload.get("legs") or []
    if isinstance(_sw_legs, list):
        _sw_configs: Dict[int, tuple] = {}
        for _si, _sleg in enumerate(_sw_legs, start=1):
            if not isinstance(_sleg, dict):
                continue
            _ssel = _sleg.get("strike_selection") or {}
            if not isinstance(_ssel, dict) or str(_ssel.get("type") or "").lower().strip() != "straddle_width":
                continue
            # Explicit None-check — `or 0.5` would silently turn a deliberate
            # multiplier=0 into 0.5 (0 is falsy in Python), which would then
            # wrongly "match" a genuinely different 0.5-multiplier sibling leg.
            _smult_raw = _ssel.get("straddle_multiplier")
            try:
                _smult = round(float(_smult_raw), 6) if _smult_raw is not None else 0.5
            except (TypeError, ValueError):
                _smult = 0.5
            _sdir = "-" if str(_ssel.get("straddle_direction") or "+").strip() == "-" else "+"
            _sw_configs[_si] = (_smult, _sdir)
        for _si, _sleg in enumerate(_sw_legs, start=1):
            if _si in _sw_configs and isinstance(_sleg, dict):
                _cfg = _sw_configs[_si]
                _sleg["_straddle_use_joint_shift"] = any(
                    _oi != _si and _ocfg == _cfg for _oi, _ocfg in _sw_configs.items()
                )

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

    # SAME-INDEX MIXED EXPIRY guard. Only the 'fixed' builder resolves the
    # per-leg monthly pin. The 'dte' / 'min_days' builders pick every leg's
    # contract out of the CADENCE list, so a MONTHLY leg there would silently
    # receive a WEEKLY contract — internally consistent, completely wrong, and
    # invisible in the tradesheet. Fail loudly instead; never fall back.
    # The UI blocks the combination too, but the optimizer and the API build
    # payloads server-side and never touch it, so this is the real guard.
    segments = _load_filter_segments(payload)

    if filter_entry_mode != "fixed" and str(
        payload.get("expiry_type") or ""
    ).upper() in _WEEKLY_CADENCE_TYPES:
        _mixed_legs = [
            i + 1
            for i, _l in enumerate(payload.get("legs") or [])
            if isinstance(_l, dict)
            and str(_l.get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
            and str(_l.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
        ]
        if _mixed_legs and not segments:
            # NO filter active — the run is a plain date range. The fixed builder
            # treats that as one segment [(from_date, to_date)], which is exactly
            # what "no filter" means, so promote instead of refusing. Without this
            # mixed expiry would be unreachable unless the user enabled the Filter
            # purely to select an entry mode.
            #
            # Only ever reached by a MIXED payload, which had no working behaviour
            # before (it raised), so nothing existing changes. It does mean mixed
            # strategies always use FIXED-ENTRY scheduling — entries anchored to the
            # range start and chained same-day, not N days before each expiry —
            # because only that builder resolves the per-leg pin.
            logger.info(
                "[MIXED_EXPIRY] leg(s) %s are MONTHLY under a WEEKLY cadence and no "
                "filter is active — promoting filter_entry_mode '%s' -> 'fixed' "
                "(the only builder that resolves the monthly pin).",
                _mixed_legs, filter_entry_mode,
            )
            filter_entry_mode = "fixed"
        elif _mixed_legs:
            # A filter IS active and the caller explicitly chose a non-fixed entry
            # mode. Silently switching would change which trades the filter yields,
            # so refuse and let them choose.
            raise RuntimeError(
                f"Mixed expiry (leg(s) {_mixed_legs} are MONTHLY under a WEEKLY "
                f"cadence) requires filter_entry_mode='fixed'; got "
                f"'{filter_entry_mode}' with an active filter. The DTE/min_days "
                "schedulers would hand those legs a weekly contract."
            )

    # A YEARLY leg is only pinned to its long-dated December contract when
    # expiry_type == 'YEARLY' supplies `yearly_cycles`. Under any other cadence
    # `_pin` is None and the leg falls through to the cadence contract — i.e. a
    # leg the user marked YEARLY silently trades a WEEKLY/MONTHLY option, with the
    # tradesheet showing that near expiry as if it were intended. Same failure
    # class as the mixed-expiry guard above, so refuse it the same way.
    #
    # Does NOT touch the legitimate mixed basket (weekly CE + yearly PE): that
    # runs with expiry_type='YEARLY' and rollover_cadence='weekly', so it never
    # enters this branch.
    if str(payload.get("expiry_type") or "").upper() != "YEARLY":
        _stray_yearly = [
            i + 1
            for i, _l in enumerate(payload.get("legs") or [])
            if isinstance(_l, dict)
            and str(_l.get("expiry") or "").upper() == "YEARLY"
            and str(_l.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
        ]
        if _stray_yearly:
            raise RuntimeError(
                f"Leg(s) {_stray_yearly} are YEARLY but expiry_type is "
                f"'{payload.get('expiry_type')}'. A YEARLY leg is pinned to its "
                "long-dated December contract only when expiry_type='YEARLY' "
                "(which resolves `yearly_cycles`); under any other cadence it "
                "would silently trade the cadence contract instead."
            )
    # Save before dispatch branches nullify segments so _apply_fixed_rollover_strike
    # can still use the original segment boundaries as grouping keys.
    original_segments = segments

    if _has_futures_leg:
        # FUTURES legs are priced from the native Rust FUTIDX cache
        # (_resolve_futures_pnl_native / _fut_price — no Postgres), then built
        # into complete priced rows directly, bypassing simulate_trades_batch.
        logger.info(
            "[ENGINE_RUST] RUST pricing path: strategy has FUTURES legs — "
            "priced from the native FUTIDX cache (no Postgres reads)."
        )
        # ── General mixed OPTIONS + FUTURES (unit-exit), Rust-priced — ADDITIVE ──
        # Gate MIXED_FUT_RUST: unset/0 → existing behavior (unchanged); '1'/'on' →
        # Rust-authoritative mixed path; 'shadow' → build it and log, but keep the
        # existing path live (safe rollout). Only the general case (an option leg
        # present AND no NEXT_WEEKLY/NEXT_MONTHLY leg — that has its own path below).
        _mixed_gate = os.getenv("MIXED_FUT_RUST", "").strip().lower()
        _has_opt_leg = any(
            isinstance(_l, dict)
            and str(_l.get("segment") or "OPTION").upper() not in ("FUTURE", "FUTURES")
            for _l in (payload.get("legs") or [])
        )
        if _has_opt_leg and not _has_next_leg and _mixed_gate in ("1", "on", "true", "shadow"):
            # Futures legs here are priced INLINE with no _apply_leg_filter_mask
            # call, so an uploaded per-leg file would be ignored on them. Raise
            # OUTSIDE the try below — the except there would swallow it and fall
            # back, which is exactly the silent degradation the rule forbids.
            _reject_leg_filter_unsupported(payload, "mixed FUTURES+OPTIONS (MIXED_FUT_RUST)")
            try:
                _mixed_rows = _build_mixed_futures_options(
                    payload,
                    expiry_dates=expiry_dates,
                    trading_days=trading_days,
                    lot_size=lot_size,
                    spot_by_date=spot_by_date,
                    square_off_mode=square_off_mode,
                )
            except Exception as _mx_exc:
                logger.warning("[ENGINE_RUST] mixed-fut build failed: %s", _mx_exc)
                _mixed_rows = None
            if _mixed_gate == "shadow":
                logger.warning(
                    "[ENGINE_RUST] MIXED_FUT_RUST=shadow: built %d mixed rows "
                    "(not returned; existing path serves live)", len(_mixed_rows or [])
                )
                # fall through to existing behavior — live output unchanged
            elif _mixed_rows is not None:
                return _mixed_rows
        if _has_next_leg:
            # Mixed FUTURES + NEXT_WEEKLY: build each type separately, merge by period.
            # Option legs here go through _build_next_expiry_specs and are priced
            # directly, returning before apply_leg_filters ever runs — the futures
            # legs ARE masked, the option legs are NOT. Raise outside the try so
            # the except cannot turn it into a silent fallback.
            _reject_leg_filter_unsupported(payload, "mixed FUTURES+NEXT_WEEKLY")
            try:
                _mixed = _build_mixed_futures_next_weekly(
                    payload, expiry_dates, trading_days, lot_size, spot_by_date, segments,
                )
            except Exception as _exc:
                logger.warning("[ENGINE_RUST] mixed FUTURES+NEXT_WEEKLY failed: %s", _exc)
                _mixed = None
            return _mixed  # None → caller falls back to Python engine
        # Fixed Entry for a futures-only strategy: schedule entries ON the filter
        # segment starts (not by DTE) so a date-list CSV filter enters on its dates
        # instead of collapsing to zero. Mixed opt+fut keeps its existing path.
        if filter_entry_mode == "fixed" and not _has_opt_leg:
            _fut_fx = _build_fixed_entry_futures_specs(
                payload, expiry_dates, trading_days, spot_by_date, int(lot_size), segments,
            )
            if _fut_fx is None:
                return None
            return _fut_fx
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
        # _build_fixed_entry_specs is per-leg next-weekly-aware; fetch TWO extra
        # expiries (same as the DTE next-weekly path) so the FINAL filter-end
        # stub can resolve its Ek+2 contract — the stub's exit anchor can itself
        # be the first extra expiry, and its traded contract is one beyond that.
        # (count=1 dropped that last clamped trade, e.g. 19-May→21-May / 02-Jun.)
        # Its own per-segment gating/clamping applies, so suppress the downstream
        # gate (segments=None).
        _fx_expiry_dates = _fetch_one_extra_expiry(expiry_dates, payload, count=2)
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
        # YEARLY needs the lookahead too: the cadence list is bounded by the
        # backtest range, so without one extra expiry the chain has no boundary
        # to exit into and simply stops at the last cadence date — dropping the
        # filter-end tail (e.g. 25-Nov..30-Nov silently missing).
        _fixed_rollover_lookahead = (
            bool(payload.get("rollover_toggle", False))
            and str(payload.get("expiry_type") or "").upper() in ("WEEKLY", "MONTHLY", "YEARLY")
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
        specs = _apply_per_leg_slippage(specs, payload.get("legs") or [])
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
        specs = _apply_per_leg_slippage(specs, payload.get("legs") or [])
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

            # YEARLY Fresh legs: the Rust epoch was resolved on the CONTINUOUS
            # pre-filter schedule, so a patch that re-enters mid-month inherited a
            # strike anchored on a dropped (phantom) trade. Re-anchor on the
            # SURVIVING trades, per segment. No-op unless expiry_type=YEARLY with a
            # yearly Fresh option leg; only genuinely-changed rows are rewritten.
            specs = _reanchor_yearly_fresh_on_segments(
                specs, payload, segments, spot_by_date,
            )

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
    # ANY Fixed leg (original condition) OR any PINNED leg — a pinned leg needs
    # per-contract strike epochs even under Fresh. The function no-ops for
    # anything neither Fixed nor pinned, so non-mixed strategies are unaffected.
    _sr_legs = payload.get("legs") or []
    if any(
        isinstance(leg, dict) and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in _sr_legs
    ) or _has_monthly_pinned_leg(payload):
        specs = _apply_fixed_rollover_strike(specs, payload, original_segments)

    # ── Per-leg individual filter files ─────────────────────────────────────
    # A leg may carry its own uploaded date file. It is purely SUBTRACTIVE: the
    # strategy filter above already decided which trades exist; this only drops
    # a leg from a trade or ends its hold early (earliest of window-end and
    # trade-exit wins). No-op — same list object — when no leg has a file, so
    # every existing strategy is byte-identical.
    # See docs/superpowers/specs/2026-07-31-per-leg-filter-design.md.
    from services.leg_filter import LEG_FILTER_END, apply_leg_filters

    # trading_days is threaded in so a window ending on a non-trading day snaps
    # back to the last real session — an unsnapped exit prices to nothing and
    # books a zero-P&L phantom row (simulate.rs sets missing=true; nobody reads it).
    specs = apply_leg_filters(specs, payload.get("legs") or [], trading_days)
    if not specs:
        return []

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
    # Per-leg truncation markers. Keyed by (expiry, leg_id) -> the set of
    # truncated boundary exit dates for that leg in that cycle.
    #
    # NOT trade_id (the same warning as _seg_clamped_keys above: re-entry,
    # bridge and spot-adjustment synthesis allocate FRESH trade ids at :6546,
    # :6961, :7863 and :8423, so a trade_id key silently loses every derived
    # row) and NOT entry_date either (a re-entry row enters on a LATER date than
    # the spec it descends from, while inheriting the same truncated boundary).
    # (expiry, leg_id) survives both, and the mask is per-leg-per-cycle so any
    # row of that leg in that cycle landing on the boundary IS filter-ended.
    #
    # The exit-date component is what makes the tag truthful: a row whose
    # realised exit came EARLIER, from its own SL/Target, was never bound by
    # the filter and must keep its own reason. Tagging it "STOP_LOSS+
    # LEG_FILTER_END" would wrongly drop a legitimate exit out of the trade's
    # exit anchor (apply_exit_anchor_exclusion matches on "contains").
    _leg_filter_end_keys: Dict[Tuple[str, int], Set[str]] = {}
    for _s in specs:
        if not _s.get("_leg_filter_end"):
            continue
        _lfk = (_normalize_iso(_s.get("expiry", "")), int(_s.get("leg_id") or 1))
        _leg_filter_end_keys.setdefault(_lfk, set()).add(
            _normalize_iso(_s.get("exit_date", ""))
        )

    def _leg_filter_bounds(row: Dict[str, Any]) -> Optional[Set[str]]:
        """The truncation boundaries configured for THIS row's leg + cycle."""
        if not _leg_filter_end_keys:
            return None
        return _leg_filter_end_keys.get(
            (_normalize_iso(row.get("expiry") or ""), int(row.get("leg_id") or 1))
        ) or None

    def _is_leg_filter_ended(row: Dict[str, Any], exit_override: str = "") -> bool:
        """True when THIS row's realised exit landed on its own filter boundary.

        For LABELLING only. A row that exited EARLIER on its own SL/Target was
        not bound by the filter and must keep its own reason.
        """
        _b = _leg_filter_bounds(row)
        if not _b:
            return False
        return _normalize_iso(exit_override or row.get("exit_date") or "") in _b

    def _leg_was_truncated(row: Dict[str, Any]) -> bool:
        """True when this row's leg is truncated in this cycle AT ALL.

        For SAFETY GUARDS, deliberately WIDER than _is_leg_filter_ended: it
        ignores where the row actually exited. A leg that was truncated but
        exited early on SL/Target still must not be resurrected past its own
        window end — mini-specs never pass back through apply_leg_filters, so
        an exit-equality test here would let exactly that through.
        """
        return _leg_filter_bounds(row) is not None

    if return_specs_only:
        # The mask itself is applied above, but the LEG_FILTER_END tag is not:
        # this returns before the tagger (:8846) and before both cascade guards,
        # and simulate_trades_batch drops the `_leg_filter_end` spec key, so the
        # truncated row would come back labelled EXPIRY. That row could then
        # hijack the trade's Exit Date (apply_exit_anchor_exclusion can only see
        # the tag) and the fused cascade in multi_index_feature.py could re-enter
        # the leg past its own boundary. Wrong numbers, silently — so raise.
        if _leg_filter_end_keys:
            raise RuntimeError(
                "Per-leg Individual Filter is not supported on the multi-index "
                "FUSED path: the LEG_FILTER_END tag does not survive the fused "
                "pricing hand-off, so the truncated leg would be reported as a "
                "normal expiry exit. Remove the individual filter from this "
                "strategy or run the indices separately."
            )
        # Path B (multi-index FUSED): hand the fully-resolved, gated specs back
        # so the caller can concatenate a SECOND symbol's specs and price both
        # in one simulate_trades_batch call. Stops here — no pricing, no
        # SL/Target/spot-adj/re-entry (Phase 2/3).
        return specs

    priced = algotest_native.simulate_trades_batch(specs)
    if not priced:
        return []

    # simulate_trades_batch rebuilds rows from the fields Rust knows, so any
    # Python-only spec key is dropped. Re-attach the cadence contract here, keyed
    # by (trade_id, leg_id), so the tradesheet's "Cadence Expiry" column is the
    # real cadence and not a fallback to the leg's own expiry — which for a PINNED
    # leg is a different contract entirely, and would put the two legs of one
    # trade in different WOW weeks. Purely additive: specs built by any other
    # path carry no _cadence_expiry, so nothing is stamped and the column falls
    # back to "Expiry" exactly as before.
    _cad_by_key = {
        (int(_s.get("trade_id") or 0), int(_s.get("leg_id") or 1)): _s["_cadence_expiry"]
        for _s in specs
        if isinstance(_s, dict) and _s.get("_cadence_expiry")
    }
    if _cad_by_key:
        for _pr in priced:
            _ck = _cad_by_key.get(
                (int(_pr.get("trade_id") or 0), int(_pr.get("leg_id") or 1))
            )
            if _ck:
                _pr["_cadence_expiry"] = _ck

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
    # MIDCPNIFTY spot adjustment likewise needs the risk/re-entry pass below. Omitting
    # it here made the trigger work ONLY when some other risk control happened to be
    # on: with NIFTY spot-adj enabled the run fell through and MIDCPNIFTY fired, but
    # MIDCPNIFTY on its own took this early return and produced ZERO triggers.
    _mn_sa_cfg_early = payload.get("midcpnifty_spot_adjustment") or {}
    has_midcp_spot_adj = bool(_mn_sa_cfg_early.get("enabled")) and (
        (_maybe_float(_mn_sa_cfg_early.get("pct")) or 0) > 0
    )
    # Per-leg spot adjustment also needs the risk/re-entry pass below. Without
    # this a payload whose adjustment lives ONLY on the legs takes the no-risk
    # early return and produces zero triggers — the same failure the MIDCPNIFTY
    # clause above documents.
    _has_leg_spot_adj_early = any(
        isinstance(_l, dict)
        and isinstance(_l.get("spot_adjustment"), dict)
        and _l["spot_adjustment"].get("enabled")
        and (_maybe_float(_l["spot_adjustment"].get("pct")) or 0) > 0
        for _l in legs_src
    )
    if (not any_risk and not has_overall_top and not has_spot_adj
            and not has_midcap_spot_adj and not has_midcp_spot_adj
            and not _has_leg_spot_adj_early):
        # No risk controls → priced output is the final answer. Tag the LAST
        # trade of each filter patch as FILTER_END (the user's rule), not just
        # the (entry,expiry)-clamped trade — so a boundary trade that expired
        # exactly on the window end is also covered.
        _apply_filter_end_last_per_patch(priced, original_segments, _clamp_reason)
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

    # ── Per-leg spot adjustment (additive; gated on each leg's own config) ─────
    # A leg carrying spot_adjustment={enabled,pct,direction,units} measures its
    # OWN breach with those values; a leg without one falls back to the
    # strategy-level values above. This ADDS a scope, it does not replace the
    # strategy-level knob — a payload with no per-leg config resolves every leg
    # to the strategy values and the trigger scan below is bit-for-bit the old
    # one. Lets e.g. a weekly CE leg adjust on 2% while a yearly PE leg adjusts
    # on 300 points.
    def _resolve_leg_sa(_lg: Any) -> Optional[Dict[str, Any]]:
        _c = (_lg or {}).get("spot_adjustment") if isinstance(_lg, dict) else None
        if not isinstance(_c, dict) or not _c.get("enabled"):
            return None
        _p = _maybe_float(_c.get("pct")) or 0.0
        if _p <= 0:
            return None
        _u = str(_c.get("units") or "percent").lower()
        if _u not in ("percent", "points"):
            _u = "percent"
        _d = str(_c.get("direction") or "rise").lower()
        if _d not in ("rise", "fall", "both"):
            _d = "rise"
        if _u == "percent":
            _p = max(0.25, min(5.0, _p))  # same clamp as the strategy-level knob
        return {"pct": _p, "units": _u, "direction": _d}

    _per_leg_sa: Dict[int, Dict[str, Any]] = {}
    for _li, _lg in enumerate(legs_src, start=1):
        _r = _resolve_leg_sa(_lg)
        if _r is not None:
            _per_leg_sa[_li] = _r
    # When empty, every code path below takes its original branch untouched.
    _has_per_leg_sa = bool(_per_leg_sa)

    def _sa_leg_label(_lid: int) -> str:
        """'Leg 2 PE Yearly' — appended to a per-leg spot-adj Exit Reason so the
        tradesheet says WHICH leg breached. The canonical token stays the prefix
        (SPOT_ADJ_RISE ...), and the '+' combiner still splits cleanly, so the
        downstream `"FILTER_END" in reason.split("+")` checks are unaffected."""
        _s = legs_src[_lid - 1] if 0 <= _lid - 1 < len(legs_src) else {}
        # `_sa_label_expiry` is the leg's REAL cadence. The multi-index sync path
        # re-flags every leg expiry="YEARLY" purely as a Rust contract-pin marker
        # (the leg still trades the weekly/monthly cadence), so trust the stamped
        # real cadence over the marker when present.
        _exp = str(
            (_s or {}).get("_sa_label_expiry")
            or (_s or {}).get("expiry")
            or (_s or {}).get("expiry_type")
            or ""
        ).upper()
        # A leg can only be genuinely YEARLY when the RUN itself is yearly. A
        # stale per-leg 'yearly' — left over when a strategy is switched off a
        # yearly basis to weekly/monthly — is inert for trading (every yearly-pin
        # path gates on payload expiry_type), but without this guard it would
        # mislabel a monthly/weekly leg's Exit Reason as "Yearly". Fall back to
        # the strategy cadence so the label matches the contract actually traded.
        if _exp == "YEARLY" and str(payload.get("expiry_type") or "").upper() != "YEARLY":
            _exp = str(payload.get("expiry_type") or "").upper()
        _bits = [
            str((_s or {}).get("option_type") or "").upper(),
            _exp.title(),
        ]
        _bits = [b for b in _bits if b]
        return "Leg %d%s" % (_lid, (" " + " ".join(_bits)) if _bits else "")

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

    # ── MIDCPNIFTY spot adjustment (additive; gated on its own config) ─────────
    # Same shape as the Midcap100 block above, but the reference index is a
    # TRADEABLE one that the strategy actually holds a leg in (multi-index
    # NIFTY + MIDCPNIFTY). Its close series lives in spot_data, not index_ohlc —
    # MidcapCloseLookup falls back to it. Close-only is sufficient: the trigger
    # reads one value per day and never touches high/low.
    # When midcpnifty_spot_adjustment is absent/disabled NOTHING below runs.
    _mn_sa = payload.get("midcpnifty_spot_adjustment") or {}
    midcp_adj_enabled = bool(_mn_sa.get("enabled"))
    midcp_adj_pct = _maybe_float(_mn_sa.get("pct")) or 0.0
    midcp_adj_direction = str(_mn_sa.get("direction") or "rise").lower()
    if midcp_adj_direction not in ("rise", "fall", "both"):
        midcp_adj_direction = "rise"
    midcp_adj_units = str(_mn_sa.get("units") or "percent").lower()
    if midcp_adj_units not in ("percent", "points"):
        midcp_adj_units = "percent"
    if midcp_adj_enabled and midcp_adj_pct > 0 and midcp_adj_units == "percent":
        midcp_adj_pct = max(0.25, min(5.0, midcp_adj_pct))
    midcp_sa_symbol = str(_mn_sa.get("symbol") or "MIDCPNIFTY").upper()
    midcp_spot_by_date: Dict[str, float] = {}
    if midcp_adj_enabled and midcp_adj_pct > 0 and trading_days:
        _mn_lk = _get_midcap_sa_lookup(midcp_sa_symbol)   # raises -> hard fail
        for _d in trading_days:
            _c = _mn_lk.close(_d)
            if _c is not None:
                midcp_spot_by_date[_d] = float(_c)
        # Rust-only/no-fallback rule: a silently never-triggering adjustment is
        # indistinguishable from a broken feature, so refuse the run instead.
        # MIDCPNIFTY's spot starts 2020-01-01; anything earlier has no reference.
        if not midcp_spot_by_date:
            raise RuntimeError(
                f"[ENGINE_RUST] {midcp_sa_symbol} spot adjustment is enabled but no "
                f"{midcp_sa_symbol} spot exists for {trading_days[0]}..{trading_days[-1]}. "
                "Start the backtest from a date this index has data for."
            )
        _missing = [d for d in trading_days if d not in midcp_spot_by_date]
        if _missing and _missing[0] <= trading_days[0]:
            _have = sorted(midcp_spot_by_date)
            raise RuntimeError(
                f"[ENGINE_RUST] {midcp_sa_symbol} spot adjustment is enabled but its "
                f"spot only starts {_have[0]} — the run begins {trading_days[0]}, so "
                f"{len(_missing)} early session(s) have no reference level. "
                f"Start the backtest on or after {_have[0]}."
            )
    _midcp_active = bool(midcp_adj_enabled and midcp_adj_pct > 0 and midcp_spot_by_date)

    # ── Combine mode (additive; only meaningful when BOTH NIFTY & Midcap active) ──
    # 'earliest' (default) = current behaviour: whichever index breaches first
    #            triggers the adjustment (see _compute_spot_adjustment_trigger).
    # 'confirm'  = BOTH indices must breach the SAME direction within N trading
    #            days of each other (daily breach, rolling window); the adjustment
    #            fires on the day the pair completes (see _compute_confirm_trigger).
    # When the mode is absent/'earliest' NOTHING below changes — the existing
    # earliest path stays byte-identical.
    _combine_mode = str(payload.get("spot_adjustment_combine_mode") or "earliest").lower()
    if _combine_mode not in ("earliest", "confirm"):
        _combine_mode = "earliest"
    try:
        _confirm_days = max(0, int(payload.get("spot_adjustment_confirm_days") or 0))
    except (TypeError, ValueError):
        _confirm_days = 0
    # _confirm_mode requires BOTH adjustments active; finalized after _nifty_active
    # is computed below.

    # Fixed-strike legs: compute per-trade spot adj baseline using the segment's
    # first-entry spot so rollovers don't reset the reference level.
    # Mirrors the Python engine _seg_spot_adj_base logic.
    _has_fixed_strike_opt_legs_sa = any(
        isinstance(leg, dict)
        and str(leg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        for leg in legs_src
        if str(leg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
    )
    # Segment anchor, shared by the trade-level ("overall") and per-leg ("own")
    # paths so the two cannot drift. A fixed-strike leg holds ONE strike across
    # the filter segment, so its adjustment is measured from the segment's first
    # entry spot, carried forward and re-based whenever a breach fires. Fresh
    # strikes get no anchor at all and fall back to each trade's own entry spot.
    #
    # This was previously inlined under `if spot_adj_enabled ...` — the TRADE-LEVEL
    # toggle — so an own-adjustment run (trade level off) never built it and
    # silently measured from the trade's own spot instead. Single leg, same
    # threshold, Fixed strikes: overall gave 58 trades/10 adjustments, own gave
    # 53/6, diverging at the 27-10-2022 trade (overall fired 07-11 off the
    # 29-07-2022 segment anchor of 17,158.25; own ran to expiry off 17,736.95).
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

    def _segment_carry_baseline(
        _sc_direction: str, _sc_pct: float, _sc_units: str,
        _sc_leg_id: Optional[int] = None, _sc_rollover_reset: bool = False,
    ) -> Dict[int, float]:
        """Per-segment anchor carried across trades, re-based on each breach.

        When _sc_rollover_reset is True (a FIXED YEARLY leg), the anchor ALSO resets
        to the trade's own entry spot at a CONTRACT ROLLOVER — the leg's expiry
        changing vs the previous trade in the segment. A yearly roll re-anchors the
        strike to fresh ATM, so its 1000-pt reference must reset to the roll spot too,
        exactly like a patch reset. Without this the counter kept measuring from the
        last pre-roll breach and fired ~1 day late (e.g. 15-Dec-2023 instead of
        14-Dec: baseline should reset to the 29-Nov roll spot 20,096 → threshold
        21,096, crossed 14-Dec at 21,182). Keyed on _sc_leg_id's OWN expiry so a
        weekly/monthly leg's routine expiry change never triggers it.
        """
        _out: Dict[int, float] = {}
        for _seg_s_sa, _seg_e_sa in _eff_segs_sa:
            _seg_tids_sa = sorted(
                [t for t, e in _tid_entry_iso_sa.items() if e and _seg_s_sa <= e <= _seg_e_sa],
                key=lambda t: _tid_entry_iso_sa[t],
            )
            _seg_base_sa: Optional[float] = None
            _prev_exp_sa: Optional[str] = None
            for _tid_sa in _seg_tids_sa:
                _tid_iso_sa = _tid_entry_iso_sa[_tid_sa]
                _tid_own_sa = float(
                    by_trade[_tid_sa][0].get("entry_spot")
                    or spot_by_date.get(_tid_iso_sa)
                    or 0.0
                )
                # Contract rollover reset (yearly fixed leg only).
                if _sc_rollover_reset:
                    _lrow_sa = (
                        next(
                            (r for r in by_trade[_tid_sa]
                             if int(r.get("leg_id") or 0) == _sc_leg_id),
                            None,
                        )
                        if _sc_leg_id is not None
                        else by_trade[_tid_sa][0]
                    )
                    _cur_exp_sa = _normalize_iso((_lrow_sa or {}).get("expiry") or "")
                    if (
                        _prev_exp_sa is not None
                        and _cur_exp_sa
                        and _cur_exp_sa != _prev_exp_sa
                        and _tid_own_sa > 0
                    ):
                        _seg_base_sa = _tid_own_sa  # roll → measure from roll spot
                    _prev_exp_sa = _cur_exp_sa
                if _seg_base_sa is None or _seg_base_sa <= 0:
                    _seg_base_sa = _tid_own_sa
                _out[_tid_sa] = _seg_base_sa
                # If adj fires with this baseline, next trade measures from trigger spot
                _sched_sa = _normalize_iso(by_trade[_tid_sa][0].get("exit_date", ""))
                if _sched_sa and _seg_base_sa > 0:
                    _trig_sa = _compute_spot_adjustment_trigger(
                        _tid_iso_sa, _seg_base_sa, _sched_sa,
                        _sc_direction, _sc_pct, _sc_units,
                        trading_days, spot_by_date,
                    )
                    if _trig_sa:
                        _new_base_sa = spot_by_date.get(_trig_sa)
                        if _new_base_sa and _new_base_sa > 0:
                            _seg_base_sa = _new_base_sa
        return _out

    _trade_adj_baseline: Dict[int, float] = {}
    if spot_adj_enabled and spot_adj_pct > 0 and _has_fixed_strike_opt_legs_sa:
        _trade_adj_baseline = _segment_carry_baseline(
            spot_adj_direction, spot_adj_pct, spot_adj_units
        )

    # ── Per-leg spot-adj baseline ─────────────────────────────────────────────
    # Each configured leg measures from ITS OWN contract cycle, not from every
    # trade's entry:
    #   · a YEARLY leg holds one pinned December/March contract across many
    #     cadence trades, so its reference is the spot at that CYCLE's first
    #     entry, carried through the cycle and re-based whenever the leg itself
    #     breaches (a 300-point move is measured against where the contract was
    #     opened, not against last week's re-book).
    #   · any other leg re-books every cadence trade, so its reference is that
    #     trade's own entry spot — today's behaviour.
    # Mirrors the existing _trade_adj_baseline carry/re-base shape, with the
    # window being the yearly cycle instead of the filter segment. Skipped
    # entirely when no leg carries its own config.
    # Every leg — yearly included — measures from the SAME anchor the trade-level
    # ("overall") knob uses, so `own` and `overall` at the same threshold produce
    # identical triggers. Mirrors the trade-level read at the _trade_adj_baseline
    # site below: use that baseline when one exists (fixed-strike legs get a
    # segment anchor), otherwise the trade's own entry spot.
    #
    # A yearly leg previously anchored on the CONTRACT CYCLE's entry spot and
    # ratcheted to the trigger spot after each breach, which made `own` a
    # cycle-wide trailing rule while `overall` stayed per-trade. Same number in
    # the box, different rule: measured on NIFTY BUY PE ATM YEARLY T0/T0 at
    # "rise 1000 pts", the trade entering 2022-10-27 @17,736.95 ran to expiry
    # under overall (needed 18,736.95, spot reached 18,484.10) but triggered on
    # 2022-11-07 under own, because it was still measuring off the 2022-07-29
    # cycle anchor of 17,158.25 (18,202.80 - 17,158.25 = 1,044.55 >= 1000).
    # Whole runs diverged from that point: 53 trades/6 adjustments vs 57/9.
    _leg_adj_baseline: Dict[Tuple[int, int], float] = {}
    # (trade_id, leg_id) -> cycle key, and (leg_id, cycle key) -> that cycle's seed
    # anchor. Populated only for the multi-leg yearly branch; the compare loop uses
    # them to carry a LIVE anchor that advances on a real win, not on a guess.
    _leg_cycle_of: Dict[Tuple[int, int], str] = {}
    _leg_cycle_seed: Dict[Tuple[int, str], float] = {}
    if _has_per_leg_sa:
        # Anchor rule:
        #   · SINGLE leg — use the same anchor `overall` uses, so selecting `own`
        #     on the only leg reproduces the trade-level knob exactly (fixed-strike
        #     legs get the segment carry, fresh strikes the trade's own entry spot).
        #   · MULTI leg — a YEARLY leg is a long-held position sitting beside the
        #     weekly/monthly legs, so its threshold measures from the CONTRACT
        #     CYCLE's first entry spot, carried across the cycle and re-based
        #     whenever the leg itself breaches. Verified against the desk sheet:
        #     NIFTY weekly-CE + yearly-PE, the 02-11-2022 trade breaches on
        #     07-11-2022 at 18,202.80 - 17,158.25 = 1,044.55 pts off the
        #     29-07-2022 cycle anchor; a per-trade anchor yields only 119.95 and
        #     never fires, which is exactly what the desk reported as "yearly
        #     adjustment not working".
        _pl_cycles: List[Dict[str, str]] = list(payload.get("yearly_cycles") or [])
        _multi_leg = len(legs_src) > 1

        def _cycle_containing(_d: str) -> Optional[Dict[str, str]]:
            for _c in (_pl_cycles or []):
                if str(_c.get("start")) <= _d < str(_c.get("end")):
                    return _c
            return None

        _tids_pl = sorted(_tid_entry_iso_sa, key=lambda t: _tid_entry_iso_sa[t])
        for _leg_id, _lcfg in _per_leg_sa.items():
            _lg_src = legs_src[_leg_id - 1] if 0 <= _leg_id - 1 < len(legs_src) else {}
            _leg_yearly = str((_lg_src or {}).get("expiry") or "").upper() == "YEARLY"
            # SAME-INDEX MIXED EXPIRY: a MONTHLY leg pinned under a WEEKLY cadence
            # is structurally the same as a yearly leg — ONE contract held across
            # many cadence re-books — so it must anchor to its CONTRACT, not to
            # each mini-trade's entry spot. Routing it through the cycle machinery
            # gives it `_leg_cycle_of`, which is what makes the cascade use the
            # leg's MARK at :7654 instead of `_sa_spot`. Without it the leg
            # measured from every re-entry's own spot, so with a fast leg breaching
            # constantly it almost never fired: measured on NIFTY weekly-CE(1%) +
            # monthly-PE(3%), Sep-2022 contract anchored 17,604.95 (band
            # 17,076.80..18,133.10) — spot reached 17,016.30 on 26-09 (-3.34%) and
            # the PE never breached, holding K17600 for the whole contract. This is
            # the identical failure the comment below records for the yearly leg.
            # The seeding body is generic (keys on the leg's OWN expiry), and
            # yearly keeps its `_pl_cycles` requirement, so yearly is untouched.
            _leg_pinned_cyc = (
                str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
                and str((_lg_src or {}).get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
                and str((_lg_src or {}).get("segment", "OPTIONS")).upper()
                not in ("FUTURES", "FUTURE")
            )
            # FULL PER-LEG INDEPENDENCE. Every leg carrying its OWN spot-adj config
            # anchors to ITS OWN CONTRACT, so another leg's breach only BREAKS THE
            # TRADE UP — it never re-anchors (or re-strikes) a leg that did not
            # breach. One rule covers all expiry types because the reset is keyed on
            # the leg's own expiry changing:
            #   * weekly  — contract rolls almost every trade, so this reduces to
            #     per-trade anchoring EXCEPT across a mid-cycle cut caused by
            #     another leg, which is exactly the case that was wrong.
            #   * monthly — one contract across ~4 re-books.
            #   * yearly  — one contract for a year (unchanged: still requires
            #     `_pl_cycles`, so the existing yearly path is untouched).
            # Measured on NIFTY CE-wk(1%) + PE-wk(1%) + PE-monthly(500pt), Aug-2022:
            # leg 3's 500pt breach on 11-Aug reset the weeklies' anchor
            # 17,534.75 -> 17,659.00, discarding +0.71% of accumulated move. Their 1%
            # threshold moved 17,710.10 -> 17,835.59 and 16-Aug's 17,825.25 missed by
            # 10.3 pts, so the weeklies never fired.
            if _multi_leg and (_pl_cycles if _leg_yearly else _PER_LEG_INDEPENDENT):
                # Seed the cycle anchor ONLY — no ratchet here. This pass used to
                # ask "would this leg breach inside this trade?" and advance the
                # anchor whenever the answer was yes. But the trade's ACTUAL exit
                # is decided later by the earliest-wins compare, where the weekly
                # leg's 1% is a far shorter distance than the yearly's 1000 pts
                # and almost always triggers first. So the yearly was charged for
                # breaches it never took: measured on NIFTY weekly-CE + yearly-PE,
                # 8 yearly triggers were computed, 7 lost the compare to the CE,
                # and all 8 moved the anchor — carrying it to 26,175.75 when it
                # should have sat near 19,800, permanently out of reach. One
                # yearly adjustment reached the tradesheet in a 7-year run.
                # The anchor is now advanced in the compare loop below, and only
                # when this leg actually wins. Cycle-boundary resets (a new
                # contract re-anchors to its first entry spot) are unchanged —
                # those were always correct.
                for _tid in _tids_pl:
                    _e_iso = _tid_entry_iso_sa[_tid]
                    _own_spot = float(
                        by_trade[_tid][0].get("entry_spot") or spot_by_date.get(_e_iso) or 0.0
                    )
                    # Mark the spot when THIS LEG'S EXPIRY CHANGES — the yearly
                    # roll — and hold that mark for the whole contract. Keying on
                    # the payload's yearly_cycles windows instead meant the mark
                    # was taken whenever the cycle window turned over, which is not
                    # the same date the leg actually rolls Dec-N -> Dec-N+1. On the
                    # desk's file the leg rolls on 23-11-2022, 29-11-2023,
                    # 27-11-2024 at spots 18,267.25 / 20,096.60 / 24,274.90; the
                    # first 1000-pt crossings from those marks are 04-07-2023,
                    # 14-12-2023 and 26-06-2025, while the engine reported
                    # 20-07-2023, 16-02-2024 and 08-07-2025 — up to two months late.
                    _lrow = next(
                        (r for r in by_trade[_tid]
                         if int(r.get("leg_id") or 0) == _leg_id),
                        None,
                    )
                    _ckey = _normalize_iso((_lrow or {}).get("expiry") or "") or ""
                    # First trade on a given expiry defines that contract's mark.
                    _seed = _leg_cycle_seed.setdefault((_leg_id, _ckey), _own_spot)
                    _leg_cycle_of[(_tid, _leg_id)] = _ckey
                    _leg_adj_baseline[(_tid, _leg_id)] = _seed
                continue
            # Segment-carry baseline is ONLY correct for a FIXED-strike leg (it
            # holds one strike across the segment, so its adjustment measures from
            # the segment's carried anchor). A FRESH leg re-strikes every trade and
            # MUST measure its adjustment from its OWN per-trade entry spot. The
            # gate here was `_has_fixed_strike_opt_legs_sa` — true whenever ANY leg
            # is fixed — so a fresh weekly CE beside a fixed yearly PE wrongly
            # measured its 1% off the carried anchor and "breached" on <1% moves
            # (phantom SPOT_ADJ_RISE: e.g. entry 17,388 exiting SPOT_ADJ on a day
            # spot was only 17,525, +0.79%). Gate on THIS leg's own mode instead.
            _this_leg_fixed = str(
                (_lg_src or {}).get("rollover_strike_mode") or "fresh"
            ).lower() == "fixed"
            # A fixed YEARLY leg also re-anchors its 1000-pt baseline at each contract
            # rollover (not just the patch reset) — see _segment_carry_baseline.
            #
            # SAME-INDEX MIXED EXPIRY: a MONTHLY leg PINNED under a WEEKLY cadence is
            # the same shape — it holds ONE contract across several cadence re-books,
            # and _apply_fixed_rollover_strike now re-strikes it to fresh ATM at each
            # pin roll. Its spot-adj reference must reset at that same roll, or the
            # counter keeps measuring from a pre-roll breach on a contract the leg no
            # longer holds. Measured on NIFTY weekly CE + fixed monthly PE, rise
            # 200pts, May-Jul 2025: without the reset the 28-May roll is ignored and
            # 06-Jun (spot 25,003.05) misses a stale 25,124.70 threshold; with it the
            # anchor resets to the roll spot 24,752.45 -> threshold 24,952.45 and the
            # leg breaches correctly.
            #
            # Safe for every existing run: _segment_carry_baseline keys the reset on
            # THIS leg's OWN expiry changing, and _leg_monthly_pinned is only true
            # when the cadence is weekly AND the leg is monthly — i.e. exactly the
            # legs whose contract does NOT change every trade. A plain cadence leg
            # keeps _sc_rollover_reset=False and is untouched.
            _leg_monthly_pinned = (
                str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
                and str((_lg_src or {}).get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
                and str((_lg_src or {}).get("segment", "OPTIONS")).upper()
                not in ("FUTURES", "FUTURE")
            )
            # A PINNED leg takes the carried anchor whether Fresh or Fixed.
            #
            # The "Fresh must measure per-trade" rule above assumes a Fresh leg is
            # a genuinely NEW position every trade — new strike AND new contract.
            # That holds while every leg rolls with the cadence. A monthly leg
            # PINNED under a weekly cadence breaks it: it re-strikes weekly but
            # keeps ONE contract across ~4 re-books, and the spot it tracks is
            # continuous. Its adjustment measures SPOT movement, which is
            # independent of the strike, so resetting the spot counter because the
            # strike changed conflates two different things.
            #
            # Worse, the cut is trade-level: a WEEKLY leg's breach ends the trade
            # and therefore reset the monthly leg's counter too. Measured on NIFTY
            # weekly CE + monthly PE, rise 1%: the monthly position opened 27-11-2019
            # at 12,100.70 (threshold 12,221.71) and by 18-12 had reached 12,221.65
            # — 0.06 short. Four intervening re-books (two weekly rolls, one
            # weekly-leg breach on 13-12, one more roll) each restarted the count,
            # so on 19-12 the live threshold was 12,343.87 off the 18-12 entry and
            # spot 12,259.70 missed by 84. A monthly-only run of the same rule fires
            # that day. With the carried anchor the pinned leg holds 12,221.71 and
            # fires on 19-12 too — matching monthly-only exactly.
            #
            # Blast radius: this block only runs for legs carrying their OWN per-leg
            # spot-adj config, and `_leg_monthly_pinned` needs a weekly cadence with
            # a monthly leg. For every other leg the condition collapses to
            # `_this_leg_fixed`, byte-identical to before.
            _leg_seg_base = (
                _segment_carry_baseline(
                    _lcfg["direction"], _lcfg["pct"], _lcfg["units"],
                    _sc_leg_id=_leg_id,
                    _sc_rollover_reset=_leg_yearly or _leg_monthly_pinned,
                )
                if (_this_leg_fixed or _leg_monthly_pinned)
                else {}
            )
            for _tid, _e_iso in _tid_entry_iso_sa.items():
                _own_spot = float(
                    by_trade[_tid][0].get("entry_spot") or spot_by_date.get(_e_iso) or 0.0
                )
                _leg_adj_baseline[(_tid, _leg_id)] = _leg_seg_base.get(_tid) or _own_spot

    spot_adj_overrides: Dict[int, str] = {}
    spot_adj_reasons: Dict[int, str] = {}
    # trade_id → leg_id that actually caused the breach, when a per-leg config won
    # the earliest-wins compare. Drives the re-entry strike: ONLY the breaching leg
    # re-strikes, the others hold. Absent for trade-level (NIFTY/MIDCAP/MIDCPNIFTY)
    # breaches, where every leg re-strikes as before.
    spot_adj_trigger_leg: Dict[int, int] = {}
    # ALL legs that breached on the winning cut date (winner + same-day co-triggers),
    # per trade. The re-strike/hold decision uses this SET, not the single winner:
    # a leg re-strikes if it is in the set, holds if not. Without it a same-day
    # co-breach (weekly wins the tie, yearly also crossed) left the yearly out —
    # its re-strike was neither anchored nor propagated, so a fixed yearly reverted
    # to its locked strike on the following base trades.
    spot_adj_breach_leg_set: Dict[int, Set[int]] = {}
    # SAME-INDEX MIXED EXPIRY: entry dates on which a PINNED leg breached on its
    # own account (including cascade SUB-HOPS, which never reach
    # spot_adj_overrides). Consumed by the final strike-epoch pass before pricing.
    _pinned_reanchor: Dict[int, Set[str]] = {}
    # Phase 3: breaches belonging to a leg that carries its own config, keyed by
    # (trade_id, leg_id). Only that leg's exit is clamped; the rest of the trade
    # runs to its own schedule. Empty without per-leg config, so every consumer
    # falls through to the trade-level maps above unchanged.
    spot_adj_leg_overrides: Dict[Tuple[int, int], str] = {}
    spot_adj_leg_reasons: Dict[Tuple[int, int], str] = {}
    _nifty_active = bool(spot_adj_enabled and spot_adj_pct > 0)

    # ── Mark timeline ────────────────────────────────────────────────────────
    # ONE chronological simulation of every cycle leg's mark. The engine decides
    # exits in one pass and builds re-entries in a second, but this mark has to
    # move forward in date order across BOTH — so it was computed twice and kept
    # falling through the seam: a reset inside a cascade never reached the next
    # trade, and the yearly re-fired without the threshold being met (03-Jul reset
    # to 19,322.55, then fired again on 06-Jul at 19,497.30, only +174.75).
    # Simulating it once here needs nothing from the bridge, so both passes can
    # simply read it.
    #   · mark set at the leg's expiry roll
    #   · rise past the threshold cuts the trade
    #   · mark resets to the spot at every adjustment, trade-level or cascade hop
    #   · resets again at the next roll
    _leg_mark_at_trade: Dict[Tuple[int, int], float] = {}
    _leg_mark_at_hop: Dict[Tuple[int, str, int], float] = {}
    # Every date each cycle (yearly) leg breaches its OWN threshold — winner OR
    # same-day co-trigger — across the WHOLE timeline, including breaches that fire
    # as a cascade sub-hop after another leg cut the trade first. block 6445 reads
    # this to anchor+propagate a fixed yearly's own re-strike onto the base trades
    # that follow it (spot_adj_overrides only holds the earliest breach per trade,
    # so a yearly that broke after the weekly was invisible to the re-anchor and
    # its strike reverted to the locked value, e.g. 19000->18000 on 05-07-2023).
    _leg_own_breach_dates: Dict[int, Set[str]] = {}
    _mark_seg_starts = {s for s, _e in (_eff_segs_sa or [])}
    if _has_per_leg_sa and _leg_cycle_of:
        _mark: Dict[Tuple[int, str], float] = dict(_leg_cycle_seed)
        for _mt in sorted(_tid_entry_iso_sa, key=lambda t: (_tid_entry_iso_sa[t], t)):
            _m_entry = _tid_entry_iso_sa[_mt]
            _m_sched = _normalize_iso(by_trade[_mt][0].get("exit_date") or "")
            if not _m_sched:
                continue
            _m_cur = _m_entry
            _m_spot = float(
                by_trade[_mt][0].get("entry_spot") or spot_by_date.get(_m_entry) or 0.0
            )
            # Patch reset: a filter-segment start re-anchors the fixed yearly
            # strike to fresh ATM, so its own spot-adjustment mark must re-base
            # to the patch-start spot as well. The live mark still also resets on
            # contract roll (new expiry key) and on every own breach below.
            if _m_entry in _mark_seg_starts and _m_spot > 0:
                for _m_lg0 in _per_leg_sa:
                    _m_ck0 = _leg_cycle_of.get((_mt, _m_lg0))
                    if _m_ck0 is not None:
                        _mark[(_m_lg0, _m_ck0)] = _m_spot
            _m_first = True
            _m_depth = 0
            while _m_depth < 250 and _m_cur < _m_sched:
                _m_depth += 1
                if not _m_spot:
                    break
                for _m_lg in _per_leg_sa:
                    _m_ck = _leg_cycle_of.get((_mt, _m_lg))
                    if _m_ck is None:
                        continue
                    _m_val = _mark.get((_m_lg, _m_ck), 0.0)
                    if _m_first:
                        _leg_mark_at_trade[(_mt, _m_lg)] = _m_val
                    _leg_mark_at_hop[(_mt, _m_cur, _m_lg)] = _m_val
                _m_first = False
                _m_cands: List[Tuple[str, Optional[int]]] = []
                if _nifty_active and _m_spot > 0:
                    _m_t = _compute_spot_adjustment_trigger(
                        _m_cur, _m_spot, _m_sched, spot_adj_direction,
                        spot_adj_pct, spot_adj_units, trading_days, spot_by_date,
                    )
                    if _m_t:
                        _m_cands.append((_m_t, None))
                for _m_lg, _m_cfg in _per_leg_sa.items():
                    _m_ck = _leg_cycle_of.get((_mt, _m_lg))
                    _m_base = (
                        _mark.get((_m_lg, _m_ck), 0.0) if _m_ck is not None else _m_spot
                    )
                    if not _m_base:
                        continue
                    _m_t = _compute_spot_adjustment_trigger(
                        _m_cur, _m_base, _m_sched, _m_cfg["direction"],
                        _m_cfg["pct"], _m_cfg["units"], trading_days, spot_by_date,
                    )
                    if _m_t:
                        _m_cands.append((_m_t, _m_lg))
                if not _m_cands:
                    break
                # Earliest wins; ties go to the trade-level source then leg order,
                # matching the _cands ordering in the compare loop below.
                _m_win_d, _m_win_lg = min(
                    _m_cands, key=lambda c: (c[0], 0 if c[1] is None else c[1])
                )
                # Whole-trade cut on _m_win_d: EVERY cycle leg whose threshold was
                # ALSO crossed on that same date is adjusted too, so reset all of
                # them — not just the single earliest-wins winner. When the weekly
                # (1%) and the yearly (1000pt) both cross the same day, the tie
                # handed the exit to the weekly and left the yearly mark stale,
                # which drifted its next reset to a later/higher spot (14-Dec
                # 21,182.70 -> wrongly 18-Dec 21,418.65) and made the yearly
                # under-fire for months. A leg whose trigger is LATER than the cut
                # (it hadn't crossed yet) is left untouched.
                _m_new = spot_by_date.get(_m_win_d)
                if _m_new and _m_new > 0:
                    for _m_cd, _m_cl in _m_cands:
                        if _m_cd != _m_win_d or _m_cl is None:
                            continue
                        _leg_own_breach_dates.setdefault(_m_cl, set()).add(_m_win_d)
                        _m_ck = _leg_cycle_of.get((_mt, _m_cl))
                        if _m_ck is not None:
                            _mark[(_m_cl, _m_ck)] = float(_m_new)
                _m_cur = _m_win_d
                _m_spot = float(spot_by_date.get(_m_win_d) or 0.0)

    # SAME-INDEX MIXED EXPIRY: the mark timeline records EVERY date a leg crossed
    # its own threshold — including a crossing that lands on the trade's SCHEDULED
    # EXIT. Such a crossing cannot truncate a trade that is already ending, so it
    # writes no SPOT_ADJ reason, but it DOES re-base the leg's reference. Leaving
    # the strike behind then strands the leg ~one threshold away from where its own
    # adjustment logic sits, and because the reference has moved the later move no
    # longer breaches either — so the strike is stuck for the rest of the contract.
    # Measured on NIFTY weekly-CE(1%) + monthly-PE(3%): 15-Oct-2019 trade, sched
    # exit 16-Oct, mark 11,126.40 (band 10,793..11,460), trigger 16-Oct. Reference
    # re-based to 11,464.00 but the strike stayed K11100 (ATM of the OLD mark) and
    # never moved again. Feed those dates into the strike-epoch pass so the next
    # trade re-strikes, keeping strike and reference consistent.
    for _pr_lid in list(_leg_own_breach_dates):
        _pr_src = legs_src[_pr_lid - 1] if 0 <= _pr_lid - 1 < len(legs_src) else {}
        if (
            str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
            and str((_pr_src or {}).get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
            and str((_pr_src or {}).get("segment", "OPTIONS")).upper()
            not in ("FUTURES", "FUTURE")
        ):
            _pinned_reanchor.setdefault(_pr_lid, set()).update(
                _leg_own_breach_dates.get(_pr_lid) or set()
            )

    # SINGLE-LEG / no-yearly_cycles fallback for _leg_own_breach_dates.
    # The cycle-based mark timeline above only runs for MULTI-leg configs carrying
    # payload yearly_cycles (gate at the `if _multi_leg and (...)` seeding), so for
    # a SINGLE-INDEX single-leg YEARLY leg it never populated
    # `_leg_own_breach_dates`. The carry-forward's step-3b then had no record of a
    # trade's INTERMEDIATE own breaches — only spot_adj_overrides' EARLIEST breach
    # per trade — so when the adjustment threshold is SMALLER than the strike gap
    # (e.g. rise 200pts on a 1000-gap leg) a single trade's cascade climbs several
    # strikes but only the first was anchored. The next SCHEDULED cadence trade then
    # reverted to that first (lower) anchor, dropping a rise-only strike mid-contract
    # (25000->24000 at the 28-May monthly roll) which the reason pass mislabelled as
    # SPOT_ADJ_FALL. Record EVERY own-breach date here, contract-keyed (the mark
    # re-bases on each breach and at a contract roll / patch reset), so step-3b
    # anchors each climb and the following scheduled trades carry the right strike.
    # Gated to the case the cycle timeline did NOT cover (`not _leg_cycle_of`) and to
    # FIXED yearly legs, so every multi-leg / multi-index run is byte-for-byte
    # unchanged (there `_leg_cycle_of` is populated and this block is skipped).
    if _has_per_leg_sa and not _leg_cycle_of:
        for _sl_lid, _sl_cfg in _per_leg_sa.items():
            _sl_src = legs_src[_sl_lid - 1] if 0 <= _sl_lid - 1 < len(legs_src) else {}
            if str((_sl_src or {}).get("expiry") or "").upper() != "YEARLY":
                continue
            if str((_sl_src or {}).get("rollover_strike_mode") or "fresh").lower() != "fixed":
                continue
            _sl_mark: Dict[str, float] = {}
            for _sl_tid in sorted(_tid_entry_iso_sa, key=lambda t: (_tid_entry_iso_sa[t], t)):
                _sl_entry = _tid_entry_iso_sa[_sl_tid]
                _sl_row = next(
                    (r for r in (by_trade.get(_sl_tid) or [])
                     if int(r.get("leg_id") or 0) == _sl_lid),
                    None,
                )
                if _sl_row is None:
                    continue
                _sl_sched = _normalize_iso(_sl_row.get("exit_date") or "")
                if not _sl_sched:
                    continue
                _sl_ck = _normalize_iso(_sl_row.get("expiry") or "")
                _sl_spot0 = float(_sl_row.get("entry_spot") or spot_by_date.get(_sl_entry) or 0.0)
                if _sl_spot0 <= 0:
                    continue
                # Re-base the mark at a contract roll (new expiry) or a patch start.
                if _sl_ck not in _sl_mark or _sl_entry in _mark_seg_starts:
                    _sl_mark[_sl_ck] = _sl_spot0
                # Feed the ADVANCING anchor into the trigger baseline. `_leg_adj_baseline`
                # (from `_segment_carry_baseline`) LAGGED for the single-leg path — it did
                # not advance on every intermediate own breach — so when the leg re-entered
                # at a later cadence roll its anchor was stale (too low) and the entry spot
                # was already past the threshold, re-firing immediately and fragmenting the
                # cadence cycle into spurious sub-threshold re-entries. This mark re-bases at
                # patch/contract and advances on EVERY own breach (mirrors the multi-leg mark
                # timeline at :5718), so it is the correct per-trade anchor. Gated to the
                # single-leg (`not _leg_cycle_of`) fixed-yearly case → multi-leg / multi-index
                # (which already advance via the timeline) and non-yearly legs are untouched.
                _leg_adj_baseline[(_sl_tid, _sl_lid)] = _sl_mark[_sl_ck]
                _sl_cur = _sl_entry
                _sl_depth = 0
                while _sl_depth < 250 and _sl_cur < _sl_sched:
                    _sl_depth += 1
                    _sl_base = _sl_mark.get(_sl_ck, 0.0)
                    if _sl_base <= 0:
                        break
                    _sl_t = _compute_spot_adjustment_trigger(
                        _sl_cur, _sl_base, _sl_sched, _sl_cfg["direction"],
                        _sl_cfg["pct"], _sl_cfg["units"], trading_days, spot_by_date,
                    )
                    if not _sl_t:
                        break
                    _sl_new = spot_by_date.get(_sl_t)
                    if not _sl_new or _sl_new <= 0:
                        break
                    _leg_own_breach_dates.setdefault(_sl_lid, set()).add(_sl_t)
                    _sl_mark[_sl_ck] = float(_sl_new)
                    _sl_cur = _sl_t

    # The strategy-level scan still covers every leg that has NO config of its
    # own. If every leg brought its own, the strategy-level knob no longer has a
    # leg to speak for and must not fire. With no per-leg config at all this is
    # exactly `_nifty_active`, so the existing path is untouched.
    _legs_without_own_sa = [
        _i for _i in range(1, len(legs_src) + 1) if _i not in _per_leg_sa
    ]
    _strategy_scope_active = bool(
        _nifty_active and (not _has_per_leg_sa or _legs_without_own_sa)
    )
    # Confirm mode only engages when BOTH adjustments are active AND the user chose
    # it. Otherwise everything below falls through to the existing earliest path.
    _confirm_mode = bool(_combine_mode == "confirm" and _nifty_active and _midcap_active)
    # `_has_per_leg_sa` admits a payload where ONLY legs carry a config and the
    # strategy-level knob is off — without it the scan is skipped and per-leg
    # breaches never compute. False when no leg has one, so this is a no-op for
    # every existing payload.
    if _nifty_active or _midcap_active or _midcp_active or _has_per_leg_sa:
        # Chronological: the live anchor carries forward, so the walk order is now
        # part of the result. by_trade is normally insertion-ordered by trade_id,
        # which is already chronological, but sort explicitly rather than rely on it.
        for trade_id, legs in sorted(
            by_trade.items(),
            key=lambda kv: (
                _normalize_iso(kv[1][0]["entry_date"]) if kv[1] else "", kv[0]
            ),
        ):
            legs.sort(key=lambda r: r["leg_id"])
            first = legs[0]
            entry_iso = _normalize_iso(first["entry_date"])
            scheduled_exit = _normalize_iso(first["exit_date"])
            if not scheduled_exit:
                continue

            # NIFTY-index breach (unchanged path).
            nifty_trig = None
            entry_spot = 0.0
            if _strategy_scope_active:
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

            # Per-leg breaches (additive). Each configured leg scans the SAME
            # spot series with its own threshold/unit/direction, measured from
            # its own baseline. Empty unless some leg carries a config.
            _leg_trigs: List[Tuple[str, int]] = []
            for _leg_id, _lcfg in _per_leg_sa.items():
                _lbase = _leg_adj_baseline.get((trade_id, _leg_id), 0.0)
                # Cycle-mode legs read the LIVE anchor, which only moves on a win.
                if _leg_cycle_of.get((trade_id, _leg_id)) is not None:
                    _lbase = _leg_mark_at_trade.get((trade_id, _leg_id), _lbase)
                if _lbase <= 0:
                    continue
                _ltrig = _compute_spot_adjustment_trigger(
                    entry_iso, _lbase, scheduled_exit,
                    _lcfg["direction"], _lcfg["pct"], _lcfg["units"],
                    trading_days, spot_by_date,
                )
                if _ltrig:
                    _leg_trigs.append((_ltrig, _leg_id))

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

            # MIDCPNIFTY breach (additive — measured on the MIDCPNIFTY index the
            # strategy actually holds a leg in). Truncates the SAME trade, so the
            # existing re-entry chain re-enters BOTH legs the same day; that is the
            # pair-trading rule, not a per-leg exit.
            midcp_trig = None
            if _midcp_active:
                mn_entry_spot = midcp_spot_by_date.get(entry_iso) or 0.0
                if mn_entry_spot > 0:
                    midcp_trig = _compute_spot_adjustment_trigger(
                        entry_iso, mn_entry_spot, scheduled_exit,
                        midcp_adj_direction, midcp_adj_pct, midcp_adj_units,
                        trading_days, midcp_spot_by_date,
                    )

            if _confirm_mode:
                # Both indices must breach the SAME direction within N trading days.
                # Confirm pairs exactly TWO series (NIFTY + Midcap100) — pairing three
                # is undefined — so MIDCPNIFTY does not participate here and is left
                # to the earliest-wins path below.
                _c_nbase = entry_spot if entry_spot > 0 else float(
                    first.get("entry_spot") or spot_by_date.get(entry_iso) or 0.0
                )
                _c_mbase = midcap_spot_by_date.get(entry_iso) or 0.0
                _c_trig, _c_dir = _compute_confirm_trigger(
                    entry_iso, scheduled_exit, _c_nbase, _c_mbase,
                    spot_adj_direction, spot_adj_pct, spot_adj_units,
                    midcap_adj_direction, midcap_adj_pct, midcap_adj_units,
                    _confirm_days, trading_days, spot_by_date, midcap_spot_by_date,
                )
                if _c_trig:
                    spot_adj_overrides[trade_id] = _c_trig
                    spot_adj_reasons[trade_id] = (
                        f"SPOT_ADJ_{_c_dir}+MIDCAP_SPOT_ADJ_{_c_dir}"
                    )
            else:
                # Earliest breach wins, across EVERY enabled reference index. This was
                # a hardcoded 2-way compare (NIFTY vs Midcap100); it is a list now so a
                # third source (MIDCPNIFTY) composes instead of displacing one. Order
                # matters only for ties: NIFTY first preserves the previous tie-break,
                # where NIFTY won on `nifty_trig <= midcap_trig`.
                _cands = []
                if nifty_trig:
                    _cands.append((nifty_trig, "NIFTY"))
                if midcap_trig:
                    _cands.append((midcap_trig, "MIDCAP"))
                if midcp_trig:
                    _cands.append((midcp_trig, "MIDCPNIFTY"))
                # A per-leg breach cuts the WHOLE TRADE, exactly like an index-level
                # one: its trigger joins the earliest-wins compare below rather than
                # being recorded per (trade, leg). Every leg then exits on that date
                # and re-enters together, so no trade row is ever left holding one
                # leg while the other runs on.
                #
                # What is per-leg is the STRIKE on the re-entry, not the exit: the
                # breaching short-dated leg re-strikes to ATM while a long-dated leg
                # keeps its month-wise epoch strike (see _opens_new_epoch).
                #
                # This deliberately replaces the per-leg-exit behaviour: that left
                # single-leg rows in the sheet (a CE-only trade 11 whose put was
                # still open on trade 10) which read as dropped legs. Appended after
                # the index sources so a tie keeps the existing NIFTY-first
                # tie-break.
                for _lt, _lid in _leg_trigs:
                    _cands.append((_lt, "LEG%d" % _lid))
                if _cands:
                    _win_date, _win_src = min(_cands, key=lambda c: c[0])
                    spot_adj_overrides[trade_id] = _win_date

                    def _src_reason(_src: str) -> str:
                        if _src == "NIFTY":
                            return _spot_adj_reason_tag(
                                spot_adj_direction, entry_spot,
                                spot_by_date.get(_win_date), spot_adj_pct, spot_adj_units,
                            )
                        if _src == "MIDCAP":
                            _mc_e = midcap_spot_by_date.get(entry_iso) or 0.0
                            _mc_t = midcap_spot_by_date.get(_win_date) or 0.0
                            return ("MIDCAP_SPOT_ADJ_RISE" if _mc_t >= _mc_e
                                    else "MIDCAP_SPOT_ADJ_FALL")
                        if _src.startswith("LEG"):
                            _wl = int(_src[3:])
                            _wck = _leg_cycle_of.get((trade_id, _wl))
                            _w_prebase = (
                                _leg_mark_at_trade.get(
                                    (trade_id, _wl),
                                    _leg_adj_baseline.get((trade_id, _wl), 0.0),
                                )
                                if _wck is not None
                                else _leg_adj_baseline.get((trade_id, _wl), 0.0)
                            )
                            _lcfg_r = _per_leg_sa.get(_wl) or {}
                            return "%s (%s)" % (
                                _spot_adj_reason_tag(
                                    _lcfg_r.get("direction") or "rise",
                                    _w_prebase, spot_by_date.get(_win_date),
                                    _lcfg_r.get("pct") or 0.0,
                                    _lcfg_r.get("units") or "percent",
                                ),
                                _sa_leg_label(_wl),
                            )
                        _mn_e = midcp_spot_by_date.get(entry_iso) or 0.0
                        _mn_t = midcp_spot_by_date.get(_win_date) or 0.0
                        return ("MIDCPNIFTY_SPOT_ADJ_RISE" if _mn_t >= _mn_e
                                else "MIDCPNIFTY_SPOT_ADJ_FALL")

                    # The winning leg drives the re-entry strike (unchanged).
                    if _win_src.startswith("LEG"):
                        spot_adj_trigger_leg[trade_id] = int(_win_src[3:])
                    # Whole-trade cut: every source that ALSO crossed on the cut
                    # date is adjusted too, so name them all — winner first, then
                    # co-triggers in _cands order — joined with " + ". Single-source
                    # trades are byte-identical to before.
                    _reason_srcs = [_win_src] + [
                        _cs for _cd, _cs in _cands
                        if _cd == _win_date and _cs != _win_src
                    ]
                    spot_adj_reasons[trade_id] = " + ".join(
                        _src_reason(_s) for _s in _reason_srcs
                    )
                    _brset = {int(_s[3:]) for _s in _reason_srcs if _s.startswith("LEG")}
                    if _brset:
                        spot_adj_breach_leg_set[trade_id] = _brset

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
        # check_leg_stop_loss_target scans ONE window for every leg, so under
        # per-leg spot adj the window must be the widest any leg still needs —
        # shrinking it to the earliest breach would hide a later leg's SL. Each
        # leg is still cut back to its own breach by the final_exit clamp below
        # (an SL after the breach resolves to the breach date either way), so
        # widening here cannot let a leg exit later than it should. Reduces to
        # the trade-level clamp when no leg carries its own config.
        if _per_leg_sa:
            # Widest window any leg still needs: a leg with its own config needs
            # only up to its own breach, but a leg WITHOUT one needs the full
            # scheduled window — so it contributes sl_cycle_exit and the max
            # leaves the window unshrunk. Taking max over only the per-leg
            # triggers would cut every leg back to the earliest breach, which
            # silently truncated legs that never opted in.
            _sl_win_cands = []
            for _lrow in legs:
                _lid_w = int(_lrow.get("leg_id") or 0)
                if _lid_w in _per_leg_sa:
                    _c_w = spot_adj_leg_overrides.get((trade_id, _lid_w))
                else:
                    _c_w = spot_adj_overrides.get(trade_id)
                _sl_win_cands.append(_c_w or sl_cycle_exit)
            spot_adj_clamp = max(_sl_win_cands) if _sl_win_cands else spot_adj_clamp
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
    if _fix4_fixed_legs and _fix4_has_slb and (spot_adj_enabled or _midcap_active or _midcp_active) and spot_adj_overrides:
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
            # Cap is a runaway-loop backstop only (was 8). This walk re-anchors
            # fixed-strike+SLB strikes at each spot-adj trigger; it MUST cover as
            # many triggers as the bridge cascade (also 250) or tail segments
            # beyond the 8th would reprice with a stale strike. Real terminator
            # is `_f_cur < _f_orig_exit`; each step strictly advances.
            while _f_depth < 250 and _f_cur < _f_orig_exit:
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
                # leg_id order so a Relative-to-Leg wing anchors off its parent's
                # re-anchored strike at this cascade date.
                _f_resolved: Dict[int, float] = {}
                for _f_leg in sorted(_f_legs, key=lambda _l: int(_l["leg_id"])):
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
                        resolved_strikes=_f_resolved,
                    )
                    if _f_strk is not None:
                        _f_resolved[int(_f_leg["leg_id"])] = float(_f_strk)
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
                # A Relative-to-Leg wing re-enters relative to its parent short's
                # strike. In per-leg re-entry only THIS leg re-enters; the parent
                # short is unchanged, so offset from its ORIGINAL trade strike.
                _re_resolved = {
                    int(_l["leg_id"]): float(_l["strike"])
                    for _l in legs if _l.get("strike") is not None
                }
                new_strike = _compute_strike_for_leg_python(
                    leg_src, float(spot), strike_interval,
                    entry_date=current_trig, expiry=parent_expiry, index=index_str,
                    out_info=_shift_info, resolved_strikes=_re_resolved,
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
    if (spot_adj_enabled or _midcap_active or _midcp_active) and spot_adj_overrides and (
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

            # Cap is a runaway-loop backstop only — far above any real cycle's
            # trading-day count (a monthly window is ~23 td). The loop really
            # terminates on `_bt_cur_entry < _bt_cycle_exit`; each step strictly
            # advances the cursor to a later trigger. The old cap of 8 silently
            # dropped the tail segments of cycles with >8 spot-adj triggers
            # (e.g. Oct-2022), leaving a flat gap + premature roll.
            while _bt_depth < 250 and _bt_cur_entry < _bt_cycle_exit:
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
                # Same re-base + re-scan for MIDCPNIFTY. Omitting this would truncate
                # the ORIGINAL trade on a MIDCPNIFTY breach but never re-check the
                # bridge re-entries — the exact defect the Midcap block above exists
                # to fix.
                _bt_casc_is_midcp = False
                if _midcp_active:
                    _bt_mn_entry = midcp_spot_by_date.get(_bt_cur_entry) or 0.0
                    if _bt_mn_entry > 0:
                        _bt_mn_casc = _compute_spot_adjustment_trigger(
                            _bt_cur_entry,
                            _bt_mn_entry,
                            _bt_cycle_exit,
                            midcp_adj_direction,
                            midcp_adj_pct,
                            midcp_adj_units,
                            trading_days,
                            midcp_spot_by_date,
                        )
                        if _bt_mn_casc and (
                            not _bt_casc_trig or _bt_mn_casc < _bt_casc_trig
                        ):
                            _bt_casc_trig = _bt_mn_casc
                            _bt_casc_is_midcap = False
                            _bt_casc_is_midcp = True
                # Confirm mode overrides the earliest-based trigger for this cycle:
                # both indices must breach the SAME direction within N trading days.
                _bt_confirm_dir = None
                if _confirm_mode:
                    _bt_mc_base = midcap_spot_by_date.get(_bt_cur_entry) or 0.0
                    _bt_casc_trig, _bt_confirm_dir = _compute_confirm_trigger(
                        _bt_cur_entry, _bt_cycle_exit, float(_bt_spot), _bt_mc_base,
                        spot_adj_direction, spot_adj_pct, spot_adj_units,
                        midcap_adj_direction, midcap_adj_pct, midcap_adj_units,
                        _confirm_days, trading_days, spot_by_date, midcap_spot_by_date,
                    )
                    _bt_casc_is_midcap = False
                    _bt_casc_is_midcp = False
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

                # Resolve legs in leg_id order so a Relative-to-Leg wing sees its
                # parent's re-anchored strike (Iron Condor wings re-offset from the
                # short's new bridge strike).
                _bt_resolved: Dict[int, float] = {}
                for _btl in sorted(_bt_legs, key=lambda _l: int(_l["leg_id"])):
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
                        resolved_strikes=_bt_resolved,
                    )
                    if _btl_strike is None:
                        # Bridge re-entry lands on a thin/illiquid strike with no
                        # matching option contract for that date/expiry (e.g. a
                        # NEXT_WEEKLY leg re-resolving during the Mar-2020 COVID
                        # crash week). Skip just THIS bridge cycle rather than
                        # rejecting the entire combo — mirrors the identical
                        # _bt_all_ok=False/break pattern used a few lines below
                        # for a simulate_trades_batch failure, and the same
                        # "never zero the entire run on one bad strike" rule
                        # _build_next_expiry_specs already applies elsewhere in
                        # this file. All OTHER trades/cycles for this combo are
                        # unaffected; the bridge chain for this one trade simply
                        # stops advancing at its last successfully-resolved cycle.
                        _bt_all_ok = False
                        break
                    _bt_resolved[int(_btl["leg_id"])] = float(_btl_strike)

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
                        if _confirm_mode and _bt_confirm_dir:
                            _btl_reason = (
                                f"SPOT_ADJ_{_bt_confirm_dir}+MIDCAP_SPOT_ADJ_{_bt_confirm_dir}"
                            )
                        elif _bt_casc_is_midcap:
                            _bt_mc_e2 = midcap_spot_by_date.get(_bt_cur_entry) or 0.0
                            _bt_mc_t2 = midcap_spot_by_date.get(_bt_casc_trig) or 0.0
                            _btl_reason = (
                                "MIDCAP_SPOT_ADJ_RISE" if _bt_mc_t2 >= _bt_mc_e2
                                else "MIDCAP_SPOT_ADJ_FALL"
                            )
                        elif _bt_casc_is_midcp:
                            _bt_mn_e2 = midcp_spot_by_date.get(_bt_cur_entry) or 0.0
                            _bt_mn_t2 = midcp_spot_by_date.get(_bt_casc_trig) or 0.0
                            _btl_reason = (
                                "MIDCPNIFTY_SPOT_ADJ_RISE" if _bt_mn_t2 >= _bt_mn_e2
                                else "MIDCPNIFTY_SPOT_ADJ_FALL"
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
    if (_has_fixed_strike_opt_legs or _has_monthly_pinned_leg(payload)) and spot_adj_overrides:
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
            # Parallel map: (anchor_date, leg_id) → the CONTRACT (expiry) that anchor
            # belongs to. A fixed leg's adjusted strike must NOT carry across a yearly
            # rollover — the new contract re-anchors to fresh ATM (Rust already does
            # this). Without this, the 23-Sep own-breach anchor (26000, Dec-2024
            # contract) was propagated across the 27-Nov Dec-2024→Dec-2025 roll,
            # overwriting Rust's correct fresh 24000 with the stale 26000.
            _anchor_leg_expiry: Dict[Tuple[str, int], str] = {}
            # (1) spot-adj re-entry bridges carry the fresh trigger-day ATM.
            for _bspec in _bt_bridge_specs:
                _bdate = _normalize_iso(_bspec.get("_entry_date_key") or _bspec.get("entry_date") or "")
                if not _bdate:
                    continue
                _anchor_strike.setdefault(_bdate, {})[int(_bspec["leg_id"])] = float(_bspec.get("strike") or 0)
                _anchor_leg_expiry[(_bdate, int(_bspec["leg_id"]))] = _normalize_iso(_bspec.get("expiry") or "")
            # (2) trigger==scheduled-exit days have no bridge; the original trade
            #     entering that day re-anchors with its own natural ATM.
            _trigger_dates = set(spot_adj_overrides.values())
            # Per-leg breach → only the BREACHING leg re-anchors; the other legs
            # (a FIXED yearly especially) HOLD their locked strike. A cross-leg
            # weekly 1% breach must never drag a fixed yearly PE off its patch
            # anchor. Trade-level breaches (NIFTY/Midcap, trigger_leg None) still
            # re-anchor every leg. If two overrides share a date but name different
            # legs, fall back to None (re-anchor all) to stay safe.
            # date → set of legs that breached that day (None = trade-level, all
            # legs). Uses the co-breach SET so a same-day weekly+yearly cut anchors
            # BOTH, not only the earliest-wins winner.
            _trigger_legs_by_date: Dict[str, Optional[Set[int]]] = {}
            for _gt_tid, _gt_date in spot_adj_overrides.items():
                _gt_set = spot_adj_breach_leg_set.get(_gt_tid)  # None => trade-level
                if _gt_date in _trigger_legs_by_date:
                    _ex = _trigger_legs_by_date[_gt_date]
                    if _ex is None or _gt_set is None:
                        _trigger_legs_by_date[_gt_date] = None
                    else:
                        _trigger_legs_by_date[_gt_date] = _ex | _gt_set
                else:
                    _trigger_legs_by_date[_gt_date] = set(_gt_set) if _gt_set is not None else None
            _cf_index = str(payload.get("symbol") or payload.get("index") or "NIFTY").upper()
            _cf_iv_default = _STRIKE_INTERVALS.get(_cf_index, 50.0)
            for _tid, _tdate in _cf_tid_entry.items():
                if _tdate in _trigger_dates:
                    _blset = _trigger_legs_by_date.get(_tdate)
                    for _trow in by_trade.get(_tid, []):
                        _tlid = int(_trow["leg_id"])
                        if _blset is not None and _tlid not in _blset:
                            continue  # non-breaching leg holds its locked strike
                        _tnat = None
                        # YEARLY fixed strikes are already locked by Rust before
                        # `_natural_spec_strikes` is captured, so on a
                        # trigger==scheduled-exit own adjustment that value can be
                        # the old patch anchor (e.g. 23000) instead of the fresh
                        # trigger-day ATM (25000). Recompute the breaching fixed
                        # leg's anchor from the trigger spot; trade-level triggers
                        # keep the existing natural-spec path below.
                        if _blset is not None and _tlid in _blset:
                            _tsrc = legs_src[_tlid - 1] if 0 <= _tlid - 1 < len(legs_src) else {}
                            _tiv_raw = (_tsrc or {}).get("strike_interval") or (_tsrc or {}).get("strike_gap")
                            try:
                                _tiv = float(_tiv_raw) if _tiv_raw else _cf_iv_default
                            except (TypeError, ValueError):
                                _tiv = _cf_iv_default
                            _tspot = float(spot_by_date.get(_tdate) or 0.0)
                            if _tspot > 0:
                                _tnat = _compute_strike_for_leg_python(
                                    _tsrc, _tspot, _tiv,
                                    entry_date=_tdate,
                                    expiry=_normalize_iso(_trow.get("expiry") or ""),
                                    index=_cf_index,
                                )
                        if _tnat is None:
                            _tnat = _natural_spec_strikes.get((_tid, _tlid))
                        if _tnat:
                            _anchor_strike.setdefault(_tdate, {}).setdefault(_tlid, _tnat)
                            _anchor_leg_expiry.setdefault((_tdate, _tlid), _normalize_iso(_trow.get("expiry") or ""))
            # (3) Per-leg ("own") adjustments re-enter through the Phase 3 cascade,
            #     which is assembled AFTER this block runs — so neither (1) nor (2)
            #     records an anchor for those triggers, and every FOLLOWING trade
            #     fell back to the locked first-cycle strike while `overall`
            #     propagated the re-anchor via its bridge. Measured on NIFTY BUY PE
            #     ATM YEARLY T0/T0, Fixed strikes, rise 1000pts: the re-entries
            #     themselves agreed (rows 18/21 = 19000/20000) but the trades after
            #     them read 18000 under own vs 19000/20000 under overall.
            #     Anchor every trigger date from the trigger-day ATM — the same
            #     value the bridge would have produced — independent of which
            #     builder created the re-entry. setdefault keeps (1)/(2) winning,
            #     so this is a no-op wherever they already registered the date.
            for _otid, _odate in spot_adj_overrides.items():
                _ospot = float(spot_by_date.get(_odate) or 0.0)
                if not _ospot:
                    continue
                _obl_set = spot_adj_breach_leg_set.get(_otid)
                for _orow in by_trade.get(_otid, []):
                    _olid = int(_orow["leg_id"])
                    if _obl_set is not None and _olid not in _obl_set:
                        continue  # non-breaching leg holds its locked strike
                    _osrc = legs_src[_olid - 1] if 0 <= _olid - 1 < len(legs_src) else {}
                    _oiv_raw = (_osrc or {}).get("strike_interval") or (_osrc or {}).get("strike_gap")
                    try:
                        _oiv = float(_oiv_raw) if _oiv_raw else _cf_iv_default
                    except (TypeError, ValueError):
                        _oiv = _cf_iv_default
                    _onat = _compute_strike_for_leg_python(
                        _osrc, _ospot, _oiv,
                        entry_date=_odate,
                        expiry=_normalize_iso(_orow.get("expiry") or ""),
                        index=_cf_index,
                    )
                    if _onat:
                        _anchor_strike.setdefault(_odate, {}).setdefault(_olid, float(_onat))
                        _anchor_leg_expiry.setdefault((_odate, _olid), _normalize_iso(_orow.get("expiry") or ""))
            # Only FIXED-strike legs are re-anchored/propagated here: their base
            # specs hold a locked strike that a spot-adj breach must update. FRESH
            # legs already carry the correct per-trade ATM (resolved in Rust) and
            # must NOT be overwritten — propagating a breaching CE's anchor pinned a
            # stale strike (23900 at spot 24,329, ATM 24300) onto later base trades.
            #
            # A PINNED leg qualifies only when it is ALSO Fixed. It used to join
            # "regardless of mode", to stop the trades after a breach reverting to
            # a stale pre-cascade epoch (the strike oscillated 24300/24900/24300).
            # That epoch only ever existed because `pinned_ids` was mode-blind and
            # force-held Fresh pinned legs; now that a Fresh pinned leg is left on
            # per-trade ATM there is no stale epoch to revert to, and propagating a
            # breach anchor onto it would re-introduce exactly the holding this
            # feature is meant to remove.
            _cf_fixed_lids = {
                _i + 1 for _i, _lg in enumerate(legs_src)
                if isinstance(_lg, dict)
                and str(_lg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
                and str(_lg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
            }
            # CONTRACT-LOCKED legs: those that hold ONE contract across many cadence
            # re-books — a YEARLY leg (pinned December) or a MONTHLY leg pinned under
            # a WEEKLY cadence. Only for these may a spot-adj re-anchor be REFUSED
            # when it comes from a different contract (see the consumer below): their
            # contract change IS a re-strike event, so a prior contract's adjusted
            # strike must not bleed across it.
            #
            # A plain cadence leg (WEEKLY leg under a WEEKLY cadence, MONTHLY under
            # MONTHLY, NEXT_*) changes contract on EVERY trade, so applying that same
            # refusal to it discards every re-anchor: the bridge re-entry got the new
            # strike and the very next scheduled trade reverted to the segment-locked
            # first-cycle strike. Measured on NIFTY WEEKLY SELL CE ATM Fixed, rise 1%,
            # Jul-Dec 2022: after the 18-Aug re-anchor to 18000 every later trade fell
            # back to the 06-Jul lock of 16000, ending 28-Dec holding a 16000 CE
            # against spot 18,122.50 (2,122 pts ITM). Correct behaviour — and what the
            # engine did before the guard was introduced — is 18000 carried forward
            # until the next breach.
            _cf_contract_locked_lids = {
                _i + 1 for _i, _lg in enumerate(legs_src)
                if isinstance(_lg, dict)
                and str(_lg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
                and (
                    str(_lg.get("expiry") or "").upper() == "YEARLY"
                    or (
                        str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
                        and str(_lg.get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
                    )
                )
            }
            # (3b) A FIXED leg's OWN breaches that fired as a cascade sub-hop (some
            # other leg cut the trade first) never reach spot_adj_overrides, so (1)-(3)
            # miss them and the following base trades revert to the locked strike. The
            # mark timeline recorded every own-breach date; anchor each to that day's
            # fresh ATM so the re-strike propagates. setdefault keeps (1)-(3) winning.
            for _fl in _cf_fixed_lids:
                _fl_src = legs_src[_fl - 1] if 0 <= _fl - 1 < len(legs_src) else {}
                _fl_iv_raw = (_fl_src or {}).get("strike_interval") or (_fl_src or {}).get("strike_gap")
                try:
                    _fl_iv = float(_fl_iv_raw) if _fl_iv_raw else _cf_iv_default
                except (TypeError, ValueError):
                    _fl_iv = _cf_iv_default
                _fl_rows = sorted(
                    (
                        (_normalize_iso(_r.get("entry_date") or ""), _normalize_iso(_r.get("expiry") or ""))
                        for _tl in by_trade.values() for _r in _tl
                        if int(_r.get("leg_id") or 0) == _fl
                    ),
                    key=lambda _x: _x[0],
                )
                for _bd in sorted(_leg_own_breach_dates.get(_fl, set())):
                    _bspot = float(spot_by_date.get(_bd) or 0.0)
                    if not _bspot:
                        continue
                    _bexp = ""
                    for _re_iso, _re_exp in _fl_rows:
                        if _re_iso and _re_iso <= _bd:
                            _bexp = _re_exp
                        else:
                            break
                    _bstk = _compute_strike_for_leg_python(
                        _fl_src, _bspot, _fl_iv, entry_date=_bd, expiry=_bexp, index=_cf_index,
                    )
                    if _bstk:
                        # This is the fixed leg's OWN breach, so it is the
                        # authoritative re-anchor for that leg/date. Do not use
                        # setdefault here: a same-date bridge/weekly anchor may
                        # already carry the old held strike, which caused the
                        # following base trades to revert to the patch anchor.
                        _anchor_strike.setdefault(_bd, {})[_fl] = float(_bstk)
                        _anchor_leg_expiry[(_bd, _fl)] = _bexp
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
                    for _crow in by_trade.get(_ctid, []):
                        _clid = int(_crow["leg_id"])
                        if _clid not in _cf_fixed_lids:
                            continue  # fresh leg keeps its own per-trade ATM
                        # Anchor dates are PER LEG (each leg breaches on its own
                        # days). Take the most-recent anchor that actually carries
                        # THIS leg — not the most-recent date overall. The latter
                        # picked a later weekly (leg-1) breach date that holds no
                        # leg-2 entry, returning None and letting a fixed yearly PE
                        # revert to its locked strike (18000 -> 17000 on 16-11-2022
                        # while 07-11's own-breach anchor of 18000 still stood).
                        # CONTRACT-LOCKED legs only: never carry an anchor across a
                        # contract rollover — accept only an anchor whose contract
                        # matches THIS trade's expiry. A yearly roll re-anchors to
                        # fresh ATM (Rust does this correctly); propagating the prior
                        # contract's adjusted strike would overwrite it (26000
                        # Dec-2024 anchor bleeding onto the 24000 Dec-2025 re-anchor
                        # at the 27-Nov roll).
                        #
                        # A plain cadence leg is EXEMPT — its contract changes every
                        # trade, so this test can never pass and would discard every
                        # re-anchor, reverting the leg to its segment lock. See
                        # `_cf_contract_locked_lids` above for the measured failure.
                        _cur_exp = _normalize_iso(_crow.get("expiry") or "")
                        _cf_locked = _clid in _cf_contract_locked_lids
                        _cstrike = None
                        for _d in reversed(_applicable):
                            _v = _anchor_strike.get(_d, {}).get(_clid)
                            if _v is None:
                                continue
                            _a_exp = _anchor_leg_expiry.get((_d, _clid))
                            if _cf_locked and _a_exp and _cur_exp and _a_exp != _cur_exp:
                                continue  # anchor from a different contract — skip
                            _cstrike = _v
                            break
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
            # Anchor the base candidate's DATE. A real SL/Target (override with a
            # non-EXPIRY reason) sits on its own override date. But a *plain*
            # EXPIRY base — whether from the no-override default OR from an
            # override whose SL scan found nothing (the Rust SL fn returns
            # "EXPIRY" at the scan-window END, which Slice 7a above truncates to
            # the spot-adj trigger) — means only "reached scheduled exit".
            # Anchoring it at leg["exit_date"] (the TRUE scheduled exit) instead
            # of the possibly-truncated `final_exit` lets the date-match below
            # DROP it when an earlier clamp (spot-adj/overall) actually wins — so
            # a trade that exits early via spot adjustment reads "SPOT_ADJ_RISE",
            # not a phantom "EXPIRY+SPOT_ADJ_RISE". When spot-adj lands ON the
            # scheduled exit day the dates match and both are still shown.
            _base_date = leg["exit_date"] if reason == "EXPIRY" else final_exit
            _reason_cands.append((_base_date, reason))
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
            # EVERY leg reads the TRADE-level map. A per-leg breach now joins the
            # earliest-wins compare (see _leg_trigs -> _cands above), so one leg
            # breaching clamps the whole trade and all legs exit together — that
            # is the whole-trade cut the sheet requires.
            #
            # This previously routed a leg WITH its own config to
            # spot_adj_leg_overrides. Once per-leg breaches were moved to the
            # trade-level map that branch read an empty dict, so a strategy where
            # EVERY leg carried its own config produced ZERO spot-adjustment
            # exits — 197 trades, all SCHEDULED_EXIT. The two halves must move
            # together: triggers and clamp read the same map.
            spot_adj_clamp = spot_adj_overrides.get(trade_id)
            _sa_clamp_reason = spot_adj_reasons.get(trade_id, "SPOT_ADJ_RISE")
            # A leg already ended by its OWN filter file cannot be re-exited
            # later by the spot-adjustment cascade — that would place it
            # outside the window its file allows. Skip only the clamp for
            # THIS row; the leg still gets appended below with its own
            # (already-resolved) exit, it just doesn't get overwritten.
            _sa_leg_filter_ended = _is_leg_filter_ended(leg, final_exit)
            if spot_adj_clamp and final_exit >= spot_adj_clamp and not _sa_leg_filter_ended:
                final_exit = spot_adj_clamp
                reason = _sa_clamp_reason
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
        # Multi-index SYNC cadence. run_sync_weekly_cadence drives its merged roll
        # boundaries by setting expiry_type="YEARLY" + yearly_cycles, so none of the
        # WEEKLY/MONTHLY tests above match and this whole block was skipped — a
        # spot-adj breach exited and never re-entered, leaving exactly the gap the
        # comment above says must not happen (measured: 15 breaches in a 133-trade
        # run, zero re-entries; single-index and the group-per-index multi path both
        # re-enter correctly). Gate on the sync cadence itself rather than on
        # "YEARLY", so a genuine yearly strategy keeps its existing behaviour.
        or bool(payload.get("sync_cadence_expiries"))
        # Genuine YEARLY. The clause above deliberately left real yearly runs out
        # while the feature was unverified, which gave them the SAME defect it was
        # added to fix: the breach exits and never re-enters. Measured on a 2024
        # NIFTY weekly-cadence yearly run — 9 breaches, 18 SPOT_ADJ exits, ZERO
        # re-entries (row count 102 -> 102), against 8 re-entries (104 -> 120) for
        # the identical weekly strategy. The residual-window body below is
        # expiry-agnostic: it rides `orig_exit_date` (the leg's SCHEDULED exit,
        # i.e. the weekly/monthly cadence exit under a yearly pin) and only falls
        # back to `orig_expiry` when the scheduled exit is later — so pinning the
        # contract to a far-off December never widens the window.
        # Gated on yearly_cycles so this admits only a properly-resolved yearly
        # run, matching the hard-fail contract at :3881.
        or (_sa_expiry_type == "YEARLY" and bool(payload.get("yearly_cycles")))
    )
    # Fixed entry used to be excluded here on the assumption that the bridge
    # (Slice 7b) re-enters those trades instead. That only holds when the bridge
    # actually runs — its own gate at :5710 is
    # `(_rollover_toggle and filter_entry_mode == "fixed") or _has_fixed_strike_opt_legs`
    # — so every OTHER fixed-entry config exited on a breach and never re-entered.
    # Measured on the NIFTY+MIDCPNIFTY sync-cadence run: 72 SPOT_ADJ exits, 0
    # genuine same-day re-entries (the 10 that looked like re-entries were the
    # weekly roll landing on the same Wednesday, reason=SCHEDULED_EXIT).
    #
    # This also became the ONLY re-entry path for per-leg breaches: the whole-trade
    # cut routes leg triggers through _leg_trigs -> _cands into the trade-level
    # spot_adj_overrides, so the per-leg block at :6616 — gated on the now
    # never-written spot_adj_leg_overrides — is dead code and re-enters nothing.
    #
    # Admit fixed entry, and skip only the trades the bridge already re-entered,
    # so bridge-served configs keep exactly the trades they have today and can
    # never double-enter.
    _sa_bridged_parents = set(_bt_bridge_by_new_tid.values())
    if spot_adj_overrides and _sa_cascade_active:
        if True:  # legacy indentation
            _sa_index = str(payload.get("symbol") or payload.get("index") or "NIFTY").upper()
            _sa_interval = _STRIKE_INTERVALS.get(_sa_index, 50.0)
            # Continue the shared id counter (re-entry → bridge → spot-adj) so
            # spot-adj mini-trades never collide with re-entry / bridge trade_ids.
            _sa_new_tid = _bt_new_tid

            for orig_tid in sorted(spot_adj_overrides.keys()):
                if orig_tid in _sa_bridged_parents:
                    continue  # bridge already re-entered this trade — don't double-enter
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
                # Which leg caused the breach that opens THIS mini-trade. Seeded
                # from the entry-side compare; re-pointed at each cascade hop.
                _sa_trig_leg: Optional[int] = spot_adj_trigger_leg.get(orig_tid)
                _sa_trig_leg_set: Optional[Set[int]] = spot_adj_breach_leg_set.get(orig_tid)
                _sa_prev_strike_by_leg: Dict[int, float] = {
                    int(_l.get("leg_id") or 1): float(_l.get("strike") or 0.0)
                    for _l in orig_legs_s
                }

                # Cap is a runaway-loop backstop only — far above any real
                # cycle's trading-day count. The loop really terminates on
                # `_sa_cur_entry < _sa_cur_exit`; each step strictly advances to
                # a later trigger. The old cap of 8 silently dropped the tail
                # re-entry segments of cycles with >8 spot-adj triggers (e.g.
                # Oct-2022 → flat gap 14-Oct→17-Oct + premature roll to Nov).
                while _sa_depth < 250 and _sa_cur_entry < _sa_cur_exit:
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
                    # Per-leg configs must cascade too. This continuation read ONLY
                    # the trade-level knob (_nifty_active / spot_adj_*), so a strategy
                    # whose adjustment lives entirely on the legs produced exactly ONE
                    # mini-trade and then hit `else: break` — the re-entry was never
                    # re-scanned, so a further breach inside it did nothing. Measured
                    # on the NIFTY weekly-CE(1%) + yearly-PE(1000pt) run: 72 re-entries
                    # of which only 2 ever adjusted again, leaving mid-window breaches
                    # unactioned (e.g. entry 2025-04-11 @22828.55 crossed 1% on 04-15
                    # at 23328.55 (+2.19%) yet exited SCHEDULED_EXIT on 04-16).
                    # Baseline is _sa_spot — the mini-trade's own entry spot — for
                    # every leg, matching the entry-side _leg_adj_baseline rule and
                    # the trade-level cascade above. Earliest breach wins, mirroring
                    # the entry-side _leg_trigs compare.
                    _sa_casc_cfg: Optional[Dict[str, Any]] = None
                    _sa_casc_leg: Optional[int] = None
                    _sa_casc_leg_trigs: List[Tuple[str, int]] = []
                    # Each leg's OWN baseline at this hop, so the reason can state the
                    # direction THAT leg actually moved. With independent anchors two
                    # legs can cross on the same date in OPPOSITE directions (one above
                    # its band, one below), which the winner's single tag would mislabel.
                    _sa_casc_bases: Dict[int, float] = {}
                    for _cl_lid, _cl_cfg in _per_leg_sa.items():
                        # A cycle-anchored (yearly) leg keeps measuring from ITS
                        # MARK — the spot at the expiry roll — not from this
                        # mini-trade's entry. Using _sa_spot here threw the mark
                        # away the moment any other leg cut the trade: measured on
                        # NIFTY monthly-CE(1%) + yearly-PE(1000pt), the mark was a
                        # correct 18,267.25 (target 19,267.25, crossed 03-07-2023)
                        # but the CE breached first on ~30-06, and every re-entry
                        # after that measured the yearly from 19,189.05 (target
                        # 20,189.05). With the CE firing 134 times, nearly the whole
                        # timeline is cascade re-entries, so the yearly almost never
                        # got to fire — it only won the rare scheduled trade where
                        # the CE happened not to breach first.
                        _cl_base = _sa_spot
                        if _leg_cycle_of.get((orig_tid, _cl_lid)) is not None:
                            _cl_base = (
                                _leg_mark_at_hop.get((orig_tid, _sa_cur_entry, _cl_lid))
                                or _leg_mark_at_trade.get((orig_tid, _cl_lid))
                                or _sa_spot
                            )
                        _sa_casc_bases[_cl_lid] = _cl_base
                        _cl_trig = _compute_spot_adjustment_trigger(
                            _sa_cur_entry,
                            _cl_base,
                            _sa_cur_exit,
                            _cl_cfg["direction"],
                            _cl_cfg["pct"],
                            _cl_cfg["units"],
                            trading_days,
                            spot_by_date,
                        )
                        if _cl_trig:
                            _sa_casc_leg_trigs.append((_cl_trig, _cl_lid))
                        if _cl_trig and (not _sa_casc or _cl_trig < _sa_casc):
                            _sa_casc = _cl_trig
                            _sa_casc_cfg = _cl_cfg
                            _sa_casc_leg = _cl_lid
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
                    # Same re-scan for MIDCPNIFTY on this re-entry window.
                    _mn_casc = None
                    _mc_casc_is_midcp = False
                    if _midcp_active:
                        _mn_entry_c = midcp_spot_by_date.get(_sa_cur_entry) or 0.0
                        if _mn_entry_c > 0:
                            _mn_casc = _compute_spot_adjustment_trigger(
                                _sa_cur_entry,
                                _mn_entry_c,
                                _sa_cur_exit,
                                midcp_adj_direction,
                                midcp_adj_pct,
                                midcp_adj_units,
                                trading_days,
                                midcp_spot_by_date,
                            )
                    # Earliest sub-trigger wins; track which source it came from.
                    if _sa_casc and _mc_casc:
                        if _mc_casc < _sa_casc:
                            _sa_casc = _mc_casc
                            _mc_casc_is_midcap = True
                    elif _mc_casc:
                        _sa_casc = _mc_casc
                        _mc_casc_is_midcap = True
                    if _mn_casc and (not _sa_casc or _mn_casc < _sa_casc):
                        _sa_casc = _mn_casc
                        _mc_casc_is_midcap = False
                        _mc_casc_is_midcp = True
                    # Confirm mode overrides the earliest-based sub-trigger for this
                    # re-entry window: both indices, SAME direction, within N days.
                    _sa_confirm_dir = None
                    if _confirm_mode:
                        _sa_mc_base = midcap_spot_by_date.get(_sa_cur_entry) or 0.0
                        _sa_casc, _sa_confirm_dir = _compute_confirm_trigger(
                            _sa_cur_entry, _sa_cur_exit, float(_sa_spot), _sa_mc_base,
                            spot_adj_direction, spot_adj_pct, spot_adj_units,
                            midcap_adj_direction, midcap_adj_pct, midcap_adj_units,
                            _confirm_days, trading_days, spot_by_date, midcap_spot_by_date,
                        )
                        _mc_casc_is_midcap = False
                        _mc_casc_is_midcp = False
                    _sa_this_exit = (
                        _sa_casc if (_sa_casc and _sa_casc < _sa_cur_exit)
                        else _sa_cur_exit
                    )
                    _sa_casc_leg_set: Optional[Set[int]] = None
                    if _sa_casc and _sa_casc_leg is not None:
                        _sa_casc_leg_set = {
                            int(_lid) for _dt, _lid in _sa_casc_leg_trigs
                            if _dt == _sa_casc
                        }

                    mini_specs: List[Dict[str, Any]] = []
                    # leg_id order so a Relative-to-Leg wing re-offsets from the
                    # short's NEW spot-adjusted strike (Iron Condor wing follows).
                    _sa_resolved: Dict[int, float] = {}
                    for _sa_leg in sorted(orig_legs_s, key=lambda _l: int(_l.get("leg_id") or 1)):
                        _sa_lid = int(_sa_leg.get("leg_id") or 1)
                        # This leg already ended at its own filter boundary —
                        # do not resurrect it into a spot-adj re-entry mini-trade,
                        # that would place it outside the window its own filter
                        # file allows. Skips only this leg for this hop; the
                        # other legs in the same mini-trade are unaffected.
                        # PRESENCE, not exit-equality: this is a window-violation
                        # guard, not a label. A leg truncated by its file but
                        # exited early on SL/Target must still never be revived
                        # into a mini-trade — those specs never pass back through
                        # apply_leg_filters, so the re-entry could hold PAST the
                        # leg's own window end.
                        if _leg_was_truncated(_sa_leg):
                            continue
                        _sa_lidx = int(_sa_leg.get("leg_id") or 1) - 1
                        _sa_leg_src = legs_src[_sa_lidx] if _sa_lidx < len(legs_src) else {}
                        _sa_prev_strike = (
                            _sa_prev_strike_by_leg.get(_sa_lid)
                            or float(_sa_leg.get("strike") or 0.0)
                        )
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
                        # Sanity-whitelist the leg's gap; anything else falls back to
                        # the index default. The list must track the gaps the UI can
                        # actually emit (STRIKE_INTERVAL_OPTIONS = 25/50/100/500/1000).
                        # It was written when 25/50/100 were the only choices and was
                        # never widened, so a leg on the coarse gaps silently re-struck
                        # at the index default on EVERY spot-adj re-entry: measured on a
                        # 2019-2026 NIFTY yearly run at gap 1000, 67 of 233 re-entries
                        # landed on the 50-grid (10850, 12250, 17250 ...) while all 86
                        # scheduled trades stayed on the 1000-grid. Scheduled trades were
                        # never affected — they read the leg value at :2239 with no
                        # whitelist, and Rust likewise has no such clamp.
                        if _sa_leg_interval not in (25.0, 50.0, 100.0, 500.0, 1000.0):
                            _sa_leg_interval = _sa_interval
                        # Each leg keeps its OWN contract. orig_expiry is leg 1's
                        # expiry (see the sorted(...)[0] read above) and was being
                        # stamped onto every leg of the re-entry, so a mixed
                        # weekly-CE + yearly-PE strategy had the yearly leg
                        # re-entered on the WEEKLY contract: measured on the
                        # 2019-2026 NIFTY run, PE re-entries carried expiry
                        # 2019-10-17 / 10-24 / 10-31 / 12-19 instead of 2019-12-26.
                        # Only ever bites when legs differ in expiry, which is why
                        # single-expiry strategies never showed it.
                        _sa_leg_expiry = (
                            _normalize_iso(_sa_leg.get("expiry") or "") or orig_expiry
                        )
                        _sa_strike_info: Dict[str, Any] = {}
                        # A YEARLY leg holds one strike per calendar MONTH; only a
                        # weekly/monthly leg re-strikes at every adjustment. This
                        # builder recomputed a fresh ATM for EVERY leg on every hop,
                        # so the yearly leg flip-flopped inside a single month —
                        # measured on the weekly-CE + yearly-CE run, Oct-2019:
                        # 11000 (scheduled) -> 12000 (re-entry) -> 11000 -> 12000,
                        # four changes in one month, while the scheduled rows
                        # correctly carried 11000. Mirrors the fixed-entry builder
                        # at :2276 — carry the epoch strike, then re-validate it for
                        # liquidity against THIS entry date rather than reuse blindly.
                        _sa_carry = None
                        _sa_leg_yearly = str(
                            (_sa_leg_src or {}).get("expiry")
                            or (_sa_leg_src or {}).get("expiry_type") or ""
                        ).upper() == "YEARLY"
                        # PINNED leg (MONTHLY under a WEEKLY cadence): its epoch is
                        # the CONTRACT, not the trade and not the calendar month —
                        # the June contract cycle runs 28-May..25-Jun, spanning two
                        # calendar months, so the yearly month-hold would re-strike
                        # mid-contract.
                        #
                        # FIXED-only, like `pinned_ids` in _apply_fixed_rollover_strike.
                        # This flag drives a strike CARRY, so applying it to a Fresh
                        # pinned leg would hold that leg's strike across re-entries
                        # inside a contract — the same mode-blind hold that made Fresh
                        # and Fixed produce identical sheets. A Fresh pinned leg
                        # re-strikes to its configured selection on every re-book,
                        # including a spot-adj re-entry. (The spot-adj BASELINE anchor
                        # for pinned legs stays mode-blind — it measures SPOT movement,
                        # which is independent of the strike; see `_leg_pinned_cyc`.)
                        _sa_leg_pinned = (
                            str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
                            and str((_sa_leg_src or {}).get("expiry") or "").upper()
                            in _MONTHLY_LEG_TYPES
                            and str((_sa_leg_src or {}).get("segment", "OPTIONS")).upper()
                            not in ("FUTURES", "FUTURE")
                            and str(
                                (_sa_leg_src or {}).get("rollover_strike_mode") or "fresh"
                            ).lower() == "fixed"
                        )
                        _sa_prev_entry = _normalize_iso(_sa_leg.get("entry_date") or "")
                        _sa_mode = str(
                            (_sa_leg_src or {}).get("rollover_strike_mode") or "fresh"
                        ).lower()
                        # Strike follows the BREACHING leg. A per-leg breach cuts
                        # the whole trade and re-enters every leg, but only the leg
                        # that actually breached re-strikes — the others hold what
                        # they had, because nothing happened to them.
                        #   · weekly breach → weekly re-strikes, yearly holds
                        #   · yearly breach → yearly re-strikes, weekly holds
                        # This was previously keyed on leg TYPE rather than on who
                        # breached, so a yearly breach moved the weekly leg and held
                        # the yearly one — exactly inverted. Measured on NIFTY
                        # weekly-CE + yearly-PE, the 07-11-2022 yearly breach:
                        # CE 18100 -> 18200 (should hold) and PE 18000 -> 18000
                        # (should move).
                        # A non-breaching leg still respects its own epoch, so a
                        # FRESH yearly leg re-strikes when the calendar MONTH turns.
                        # A FIXED leg HOLDS its locked strike across a cross-leg
                        # breach — only its OWN breach, a new yearly cycle, or a
                        # patch reset re-strikes it (user rule: "fixed until patch
                        # reset or its own spot adjustment"). This block previously
                        # broke the lock on ANY spot adj, so a weekly 1% breach
                        # dragged a fixed yearly PE off its patch anchor (11000 ->
                        # 12000 with spot only +190pts, no yearly 1000pt move).
                        # Trade-level breaches leave _sa_trig_leg None and keep the
                        # original behaviour: every leg re-strikes.
                        _sa_effective_trig_set = _sa_trig_leg_set
                        if _sa_effective_trig_set is None and _sa_trig_leg is not None:
                            _sa_effective_trig_set = {_sa_trig_leg}
                        # A pinned leg that breached HERE re-anchors its strike, and
                        # the new strike must carry to every later trade on the same
                        # contract. Record the date; sub-hops are invisible to the
                        # earlier _anchor_strike pass.
                        if (
                            _sa_leg_pinned
                            and _sa_effective_trig_set is not None
                            and (_sa_lidx + 1) in _sa_effective_trig_set
                        ):
                            _pinned_reanchor.setdefault(_sa_lidx + 1, set()).add(
                                _normalize_iso(_sa_cur_entry)
                            )
                        if (
                            _sa_effective_trig_set is not None
                            and (_sa_lidx + 1) not in _sa_effective_trig_set
                        ):
                            # Non-breaching leg — a breach in ANOTHER leg must not
                            # re-strike it.
                            #   · FIXED leg → holds its locked strike (until its own
                            #     breach / a new yearly cycle). _opens_new_epoch is
                            #     False for 'fixed' unless a new cycle, so this carries
                            #     within a contract and re-strikes only at the roll.
                            #   · FRESH yearly → holds within its calendar-MONTH epoch.
                            #   · FRESH weekly/monthly → still re-strikes to its
                            #     configured selection (holding it left the CE far off
                            #     ITM1 when spot ran between entry and the breach: trade
                            #     164, Jun-Dec roll, held 25100 into a re-entry at
                            #     25,637.80 where ITM1 is 25500).
                            if _sa_mode == "fixed":
                                if not _opens_new_epoch(
                                    _sa_mode, _sa_prev_entry, _sa_cur_entry, False, True,
                                ):
                                    _sa_carry = float(_sa_prev_strike or 0.0) or None
                            elif _sa_leg_pinned:
                                # Carry inside the SAME contract; a pin roll
                                # re-strikes to fresh ATM via the normal path.
                                _sa_prev_exp = _normalize_iso(_sa_leg.get("expiry") or "")
                                if (
                                    _sa_prev_exp
                                    and _normalize_iso(_sa_leg_expiry) == _sa_prev_exp
                                ):
                                    _sa_carry = float(_sa_prev_strike or 0.0) or None
                            elif (
                                _sa_leg_yearly
                                and not _opens_new_epoch(
                                    _sa_mode, _sa_prev_entry, _sa_cur_entry, False, True,
                                )
                            ):
                                _sa_carry = float(_sa_prev_strike or 0.0) or None
                            elif _PER_LEG_INDEPENDENT:
                                # FULL PER-LEG INDEPENDENCE: a FRESH cadence leg also
                                # holds its strike when the cut came from ANOTHER leg.
                                # Another leg's breach is only used to BREAK THE TRADE
                                # UP — nothing happened to this leg, so neither its
                                # strike nor its anchor may move. Keyed on this leg's
                                # own contract being unchanged, so an ordinary roll
                                # still re-strikes normally.
                                # Reverses the earlier "FRESH weekly/monthly still
                                # re-strikes" rule, whose stranded-strike concern
                                # (CE held at 25100 into a re-entry at 25,637.80 where
                                # ITM1 was 25500) is the accepted trade-off: a
                                # non-firing leg may sit off its configured selection
                                # until its own threshold fires.
                                _sa_prev_exp_ind = _normalize_iso(
                                    _sa_leg.get("expiry") or ""
                                )
                                if (
                                    _sa_prev_exp_ind
                                    and _normalize_iso(_sa_leg_expiry) == _sa_prev_exp_ind
                                ):
                                    _sa_carry = float(_sa_prev_strike or 0.0) or None
                        elif (
                            _sa_trig_leg is None
                            and _sa_leg_yearly
                            and _sa_prev_entry
                            and _sa_mode != "fixed"
                            and not _opens_new_epoch(
                                _sa_mode, _sa_prev_entry, _sa_cur_entry, False, True,
                            )
                        ):
                            _sa_carry = float(_sa_leg.get("strike") or 0.0) or None
                        if _sa_carry is not None:
                            _sa_is_ce = str(
                                _sa_leg.get("option_type") or "CE"
                            ).upper() in ("CE", "CALL", "C")
                            _sa_atm = round(_sa_spot / _sa_leg_interval) * _sa_leg_interval
                            _sa_strike = _validate_or_shift_strike_python(
                                _sa_carry, _sa_atm, _sa_leg_interval, _sa_is_ce,
                                _sa_cur_entry, _sa_leg_expiry, _sa_index,
                                str(_sa_leg.get("option_type") or "CE").upper(), 1,
                            ) or float(_sa_prev_strike or 0.0)
                            _sa_strike_info["requested_strike"] = float(_sa_carry)
                        else:
                            _sa_strike = _compute_strike_for_leg_python(
                                _sa_leg_src, _sa_spot, _sa_leg_interval,
                                entry_date=_sa_cur_entry, expiry=_sa_leg_expiry,
                                index=_sa_index,
                                out_info=_sa_strike_info, resolved_strikes=_sa_resolved,
                            ) or float(_sa_prev_strike or 0.0)
                        if not _sa_strike:
                            continue
                        _sa_resolved[_sa_lid] = float(_sa_strike)
                        mini_specs.append({
                            "trade_id":     _sa_new_tid,
                            "leg_id":       _sa_lid,
                            "index":        _sa_leg.get("index") or _sa_index,
                            "entry_date":   _sa_cur_entry,
                            "exit_date":    _sa_this_exit,
                            "expiry":       _sa_leg_expiry,
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
                            if _confirm_mode and _sa_confirm_dir:
                                _sa_reason = (
                                    f"SPOT_ADJ_{_sa_confirm_dir}+MIDCAP_SPOT_ADJ_{_sa_confirm_dir}"
                                )
                            elif _mc_casc_is_midcap:
                                _mc_e2 = midcap_spot_by_date.get(_sa_cur_entry) or 0.0
                                _mc_t2 = midcap_spot_by_date.get(_sa_casc) or 0.0
                                _sa_reason = (
                                    "MIDCAP_SPOT_ADJ_RISE" if _mc_t2 >= _mc_e2
                                    else "MIDCAP_SPOT_ADJ_FALL"
                                )
                            elif _mc_casc_is_midcp:
                                _mn_e2 = midcp_spot_by_date.get(_sa_cur_entry) or 0.0
                                _mn_t2 = midcp_spot_by_date.get(_sa_casc) or 0.0
                                _sa_reason = (
                                    "MIDCPNIFTY_SPOT_ADJ_RISE" if _mn_t2 >= _mn_e2
                                    else "MIDCPNIFTY_SPOT_ADJ_FALL"
                                )
                            else:
                                # Tag from the config that actually won. Falling back
                                # to the trade-level knob mislabels a per-leg cascade
                                # when the trade level is off (its direction defaults
                                # to "rise", so a fall-configured leg read as RISE).
                                _sa_reason = _spot_adj_reason_tag(
                                    (_sa_casc_cfg or {}).get("direction", spot_adj_direction),
                                    _sa_spot,
                                    spot_by_date.get(_sa_casc),
                                    (_sa_casc_cfg or {}).get("pct", spot_adj_pct),
                                    (_sa_casc_cfg or {}).get("units", spot_adj_units),
                                )
                                # Name the breaching leg(s) on cascade hops too.
                                # `_sa_casc_leg` is only the WINNER; `_sa_casc_leg_set`
                                # holds every leg that crossed on that same date. Naming
                                # just the winner under-reported co-triggers, so a hop
                                # that moved a leg's strike looked unexplained in the
                                # tradesheet (NIFTY 14-Jan-2020: reason said
                                # "(Leg 1 CE Weekly)" while the breach set was {1,2} and
                                # leg 2 re-anchored 12000 -> 12500). Winner first, then
                                # the rest in leg order, joined with " + " — the same
                                # shape the trade-level path already emits. A single-leg
                                # hop renders byte-identically to before.
                                if _sa_casc_leg is not None:
                                    _casc_lids = [_sa_casc_leg] + sorted(
                                        _l for _l in (_sa_casc_leg_set or set())
                                        if _l != _sa_casc_leg
                                    )
                                    # Tag computed PER LEG from that leg's own
                                    # direction, own baseline and own threshold —
                                    # mirroring the trade-level `_src_reason` path.
                                    # Reusing the winner's tag reported e.g.
                                    # "FALL (Leg 1) + FALL (Leg 3)" when Leg 1 had
                                    # actually risen off a lower anchor.
                                    _casc_trig_spot = spot_by_date.get(_sa_casc)
                                    _casc_parts = []
                                    for _l in _casc_lids:
                                        _lcfg_c = _per_leg_sa.get(_l) or {}
                                        _casc_parts.append("%s (%s)" % (
                                            _spot_adj_reason_tag(
                                                _lcfg_c.get("direction")
                                                or spot_adj_direction,
                                                _sa_casc_bases.get(_l) or _sa_spot,
                                                _casc_trig_spot,
                                                _lcfg_c.get("pct") or spot_adj_pct,
                                                _lcfg_c.get("units") or spot_adj_units,
                                            ),
                                            _sa_leg_label(_l),
                                        ))
                                    _sa_reason = " + ".join(_casc_parts)
                        else:
                            _sa_reason = "EXPIRY"
                        _sa_reentry_reasons[(_sa_new_tid, _sa_lid, _sa_cur_entry)] = _sa_reason

                    if mini_specs:
                        _sa_reentry_specs.extend(mini_specs)
                        _sa_reentry_by_new_tid[_sa_new_tid] = orig_tid
                        for _ms in mini_specs:
                            _sa_prev_strike_by_leg[int(_ms.get("leg_id") or 1)] = float(
                                _ms.get("strike") or 0.0
                            )
                        _sa_new_tid += 1

                    if _sa_casc and _sa_casc < _sa_cur_exit:
                        _sa_cur_entry = _sa_casc  # advance to cascade trigger for next mini-trade
                        _sa_trig_leg = _sa_casc_leg  # next hop is attributed to the leg that won
                        _sa_trig_leg_set = _sa_casc_leg_set
                    else:
                        break

    # ── Phase 4: per-leg spot-adj re-entry ────────────────────────────────────
    # A leg that adjusted out under Phase 3 must come back for the rest of its
    # own window, otherwise it sits flat — the exact defect the yearly gate fix
    # removed. Mirrors the whole-trade cascade above, with two differences:
    #   · the mini-trade carries ONLY the breaching leg; the trade's other legs
    #     are still running in the parent and must not be re-booked.
    #   · it cascades on that leg's OWN config, so a 300-point leg keeps
    #     measuring in points inside the residual window.
    # New trade_ids like every other re-entry path, appended after the overlap
    # walk (below) so being concurrent with the parent is not treated as an
    # overlap. Empty without per-leg config, so nothing existing is touched.
    _sa_leg_reentry_specs: List[Dict[str, Any]] = []
    _sa_leg_reentry_by_new_tid: Dict[int, int] = {}
    _sa_leg_reentry_reasons: Dict[Tuple[int, int, str], str] = {}
    # FIXED ENTRY IS INCLUDED HERE, unlike the trade-level block above.
    # That block excludes fixed because the bridge (Slice 7b) re-enters those
    # trades instead — but the bridge only ever reads the TRADE-level
    # spot_adj_overrides, so a leg with its own config got its exit clamped
    # (see the per-leg branch at the spot_adj_clamp site) and then never
    # re-entered. Measured on NIFTY 2019 with per-leg SA on the weekly leg:
    # entry mode "dte" -> 22 leg-1 trades, 0 gaps; entry mode "fixed" -> 21
    # trades, 4 breaches with NOTHING entering on the exit day (09-Oct, 16-Oct,
    # 23-Oct, 11-Dec 2019). The two maps are disjoint by construction — a leg
    # with its own config is recorded ONLY in spot_adj_leg_overrides and never
    # joins the trade-level earliest-wins compare — so the bridge and this
    # block can never both re-enter the same leg.
    if spot_adj_leg_overrides and _sa_cascade_active:
        _pl_index = str(payload.get("symbol") or payload.get("index") or "NIFTY").upper()
        _pl_interval_default = _STRIKE_INTERVALS.get(_pl_index, 50.0)
        # Continue past every id already handed out so mini-trades never collide.
        _pl_new_tid = max(
            [int(t) for t in by_trade.keys()]
            + [int(s["trade_id"]) for s in reentry_specs]
            + [int(s["trade_id"]) for s in _bt_bridge_specs]
            + [int(s["trade_id"]) for s in _sa_reentry_specs]
            + [0]
        ) + 1
        for (_pl_tid, _pl_lid) in sorted(spot_adj_leg_overrides.keys()):
            _pl_cfg = _per_leg_sa.get(_pl_lid)
            if not _pl_cfg:
                continue
            _pl_rows = by_trade.get(_pl_tid) or []
            _pl_row = next(
                (r for r in _pl_rows if int(r.get("leg_id") or 0) == _pl_lid), None
            )
            if _pl_row is None:
                continue
            _pl_expiry = _normalize_iso(_pl_row.get("expiry") or "")
            _pl_sched_exit = _normalize_iso(_pl_row.get("exit_date") or "")
            if not _pl_expiry or not _pl_sched_exit:
                continue
            if _pl_sched_exit > _pl_expiry:
                _pl_sched_exit = _pl_expiry
            _pl_cur_entry = spot_adj_leg_overrides[(_pl_tid, _pl_lid)]
            _pl_src = legs_src[_pl_lid - 1] if 0 <= _pl_lid - 1 < len(legs_src) else {}
            _pl_sel = (_pl_src or {}).get("strike_selection") or {}
            _pl_iv_raw = (
                (_pl_src or {}).get("strike_interval")
                or (_pl_src or {}).get("strike_gap")
                or (_pl_sel.get("strike_interval") if isinstance(_pl_sel, dict) else None)
            )
            try:
                _pl_iv = float(_pl_iv_raw) if _pl_iv_raw else _pl_interval_default
            except (TypeError, ValueError):
                _pl_iv = _pl_interval_default
            if _pl_iv not in (25.0, 50.0, 100.0, 500.0, 1000.0):
                _pl_iv = _pl_interval_default
            _pl_depth = 0
            while _pl_depth < 250 and _pl_cur_entry < _pl_sched_exit:
                _pl_depth += 1
                _pl_spot = float(spot_by_date.get(_pl_cur_entry) or 0.0)
                if not _pl_spot:
                    break
                # Re-entry re-bases on the trigger spot, matching the whole-trade
                # cascade and the _trade_adj_baseline re-base rule.
                _pl_next = _compute_spot_adjustment_trigger(
                    _pl_cur_entry, _pl_spot, _pl_sched_exit,
                    _pl_cfg["direction"], _pl_cfg["pct"], _pl_cfg["units"],
                    trading_days, spot_by_date,
                )
                _pl_this_exit = (
                    _pl_next if (_pl_next and _pl_next < _pl_sched_exit) else _pl_sched_exit
                )
                _pl_info: Dict[str, Any] = {}
                _pl_strike = _compute_strike_for_leg_python(
                    _pl_src, _pl_spot, _pl_iv,
                    entry_date=_pl_cur_entry, expiry=_pl_expiry, index=_pl_index,
                    out_info=_pl_info, resolved_strikes={},
                ) or float(_pl_row.get("strike") or 0.0)
                if not _pl_strike:
                    break
                _sa_leg_reentry_specs.append({
                    "trade_id":     _pl_new_tid,
                    "leg_id":       _pl_lid,
                    "index":        _pl_row.get("index") or _pl_index,
                    "entry_date":   _pl_cur_entry,
                    "exit_date":    _pl_this_exit,
                    "expiry":       _pl_expiry,
                    "strike":       float(_pl_strike),
                    "requested_strike": float(_pl_info.get("requested_strike") or _pl_strike),
                    "strike_interval": float(_pl_iv),
                    "option_type":  _pl_row.get("option_type") or "CE",
                    "position":     _pl_row.get("position") or "SELL",
                    "lots":         int(_pl_row.get("lots") or 1),
                    "lot_size":     int(_pl_row.get("lot_size") or lot_size),
                    "slippage_pct": float(_pl_row.get("slippage_pct") or 0.0),
                })
                _sa_leg_reentry_by_new_tid[_pl_new_tid] = _pl_tid
                if _pl_next and _pl_next < _pl_sched_exit:
                    _sa_leg_reentry_reasons[(_pl_new_tid, _pl_lid, _pl_cur_entry)] = (
                        _spot_adj_reason_tag(
                            _pl_cfg["direction"], _pl_spot, spot_by_date.get(_pl_next),
                            _pl_cfg["pct"], _pl_cfg["units"],
                        )
                    )
                _pl_new_tid += 1
                if not (_pl_next and _pl_next < _pl_sched_exit):
                    break
                _pl_cur_entry = _pl_next

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
    # Phase 4: per-leg spot-adj mini-trades, same survival rule. These are
    # deliberately CONCURRENT with their parent (the parent's other legs are
    # still open), which is why they are added here rather than walked through
    # the overlap filter above. Empty without per-leg config.
    adjusted_specs.extend(
        s for s in _sa_leg_reentry_specs
        if _sa_leg_reentry_by_new_tid.get(s["trade_id"]) in kept_trades
    )
    adjusted_reason_by_date.update(_sa_leg_reentry_reasons)
    # ── PINNED-LEG STRIKE EPOCH (final pass, after the cascade) ───────────────
    # A pinned leg holds ONE strike per contract, re-anchoring only at a contract
    # roll or its OWN spot-adj breach. `_apply_fixed_rollover_strike` sets those
    # epochs PRE-cascade, so it cannot see a breach that fired as a cascade
    # SUB-HOP — only the first trigger per trade reaches spot_adj_overrides.
    # Result: the leg re-anchored correctly on the hop, then every later base
    # trade reverted to the stale pre-cascade epoch. Measured on NIFTY Jan-2020
    # (weekly CE + monthly PE gap 500, own 1% rise): leg 2 re-anchored
    # 12000 -> 12500 on 14-Jan, then 15/22/24-Jan fell back to 12000 — the hedge
    # went ~343 points OTM and the leg's P&L on those three trades read
    # -19.00 instead of +203.00 pts.
    #
    # Runs on the FINAL spec list so it sees base trades AND cascade hops, and
    # before simulate_trades_batch so corrected strikes are actually priced.
    # Inert unless a leg is genuinely pinned.
    #
    # Was FIXED-only: this block HOLDS a strike across a contract, which reads as
    # Fixed semantics. It originally ran mode-blind, so on any spot-adj run a
    # Fresh pinned leg was force-held here even after the earlier passes were
    # corrected — this pass is reached only on the spot-adj path, which is why
    # that defect showed up with adjustment ON and not with it OFF.
    #
    # Under _PER_LEG_INDEPENDENT a pinned FRESH leg is included again, because the
    # original defect was "held even when it should have re-struck" and every
    # legitimate re-strike is now enumerated below:
    #   · its own breach / trade-level breach → `_ep_dates` (8524-8529)
    #   · its own contract roll               → `_ep_prev_exp`
    #   · a patch (filter segment) boundary   → `_ep_seg_ix`
    # Anything else is another leg's breach, which under the per-leg rule only
    # BREAKS THE TRADE UP and must move neither this leg's strike nor its anchor.
    #
    # This is the post-cascade half of a pair: `pinned_ids` in
    # _apply_fixed_rollover_strike is already mode-blind but runs PRE-cascade, so
    # while this pass stayed FIXED-only a pinned Fresh leg held its epoch until
    # the cascade re-split the trade list and then lost it. Measured on NIFTY
    # weekly cadence, CE weekly Fresh 1% + PE weekly Fresh 1.5% + PE MONTHLY
    # Fresh 500pt (2023-04-28..2024-03-28): all four trades on the 2023-05-25
    # contract should hold 18000, but a Leg-1 exit-day crossing on the 03-May
    # trade left 10-May on 18500 and 17-May back on 18000 — a strike oscillating
    # on one contract with Leg 3 named in no exit reason.
    _ep_pin_lids = {
        _i + 1
        for _i, _lg in enumerate(payload.get("legs") or [])
        if isinstance(_lg, dict)
        and str(payload.get("expiry_type") or "").upper() in _WEEKLY_CADENCE_TYPES
        and str(_lg.get("expiry") or "").upper() in _MONTHLY_LEG_TYPES
        and str(_lg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
        and (
            str(_lg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
            or _PER_LEG_INDEPENDENT
        )
    }

    # A PATCH (filter segment) boundary re-strikes, exactly like a contract roll.
    # The epoch below is keyed on the contract alone, so when a contract spans a
    # patch gap the strike walked straight across it: NIFTY weekly cadence +
    # MONTHLY CE Fixed, patches [01-Jul..31-Aug] and [15-Sep..31-Oct], the Sep
    # contract is held by both. Patch 2's first trade (15-Sep, spot 17,877.40,
    # own ATM 17900) was overwritten with patch 1's 24-Aug epoch of 17600 — the
    # "new patch, new strike" rule silently lost. Resolve each entry to its
    # segment index and treat a change as a re-anchor.
    def _ep_seg_ix(_iso: str) -> int:
        if not original_segments:
            return 0
        for _i, (_s0, _s1) in enumerate(original_segments):
            if _s0 <= _iso <= _s1:
                return _i
        return -1

    for _ep_lid in _ep_pin_lids:
        _ep_rows = sorted(
            (
                _s for _s in adjusted_specs
                if int(_s.get("leg_id") or 1) == _ep_lid and _s.get("strike")
            ),
            key=lambda _s: (
                _normalize_iso(_s.get("entry_date") or ""),
                int(_s.get("trade_id") or 0),
            ),
        )
        # Re-anchor dates for this leg = cascade sub-hops (_pinned_reanchor) PLUS
        # every date its own adjustment actually fired. Without the second half
        # this pass silently undid the `_anchor_strike` block's documented case
        # (2) — "trigger == scheduled-exit days have no bridge; the trade
        # entering that day re-anchors with its own natural ATM". That block set
        # the correct anchor (verified: anchor 2022-07-06 -> 16000) and this loop
        # then forced the trade back onto the contract epoch. Measured on NIFTY
        # weekly cadence + MONTHLY CE Fixed, own rise 1%: the 01-Jul trade exited
        # 06-Jul with SCHEDULED_EXIT+SPOT_ADJ_RISE and the 06-Jul re-entry held
        # 15800 at spot 15,989.80 (own ATM 16000) instead of re-striking. Same on
        # 20-Jul (16200 vs 16500) and 19-Oct (17300 vs 17500). An adjustment that
        # lands exactly on the scheduled exit is still an adjustment.
        _ep_dates = set(_pinned_reanchor.get(_ep_lid) or ())
        _ep_dates |= set(_leg_own_breach_dates.get(_ep_lid) or ())
        for _ov_tid, _ov_date in spot_adj_overrides.items():
            _ov_set = spot_adj_breach_leg_set.get(_ov_tid)  # None => trade-level
            if _ov_set is None or _ep_lid in _ov_set:
                _ep_dates.add(_normalize_iso(_ov_date))
        _ep_strike: Optional[float] = None
        _ep_prev_exp: Optional[str] = None
        _ep_prev_seg: Optional[int] = None
        for _s in _ep_rows:
            _ep_exp = _normalize_iso(_s.get("expiry") or "")
            _ep_ent = _normalize_iso(_s.get("entry_date") or "")
            _ep_seg = _ep_seg_ix(_ep_ent)
            if _ep_exp != _ep_prev_exp or _ep_seg != _ep_prev_seg or _ep_ent in _ep_dates:
                # Contract roll, or this leg's own breach → this trade's freshly
                # resolved strike DEFINES the epoch. Prefer the NATURAL strike
                # (resolved before _apply_fixed_rollover_strike forced the carried
                # epoch onto it), otherwise a re-anchor would just re-lock the old
                # value. Cascade mini-trades are absent from that map and already
                # carry a freshly resolved strike, so the fallback is correct.
                _ep_nat = _natural_spec_strikes.get(
                    (int(_s.get("trade_id") or 0), _ep_lid)
                )
                _ep_strike = float(_ep_nat or _s.get("strike") or 0.0) or None
                if _ep_nat and abs(float(_ep_nat) - float(_s.get("strike") or 0)) > 0.01:
                    _s["strike"] = float(_ep_nat)
                    _s["requested_strike"] = float(_ep_nat)
            elif _ep_strike and abs(_ep_strike - float(_s.get("strike") or 0.0)) > 0.01:
                _s["strike"] = _ep_strike
                _s["requested_strike"] = _ep_strike
            _ep_prev_exp = _ep_exp
            _ep_prev_seg = _ep_seg

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
            # P&L is POINTS x LOTS (lot_size excluded — display Qty only).
            per_leg_pnl_points = (entry_px - adjusted_exit) if position == "SELL" else (adjusted_exit - entry_px)
            _row_lots = float(row.get("lots") or 1)
            row["net_pnl"] = round(float(per_leg_pnl_points) * _row_lots, 4)

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
        _row_lots = float(row.get("lots") or 1)
        net_pnl = round(
            (entry_px - settlement if position == "SELL" else settlement - entry_px)
            * _row_lots,
            4,
        )
        row["exit_price"] = settlement
        row["raw_exit_price"] = settlement
        row["net_pnl"] = net_pnl

    # Final FILTER_END guarantee. The LAST trade of each filter patch (max exit
    # date among trades entering inside the window) must read FILTER_END,
    # combined with any co-occurring reason — no matter which path assigned its
    # reason. Anchoring on the last *exit* (not the original base trade's
    # entry+expiry) fixes two cases the old marker missed: spot-adjustment
    # SPLITTING the boundary trade (tag used to stick to the first split piece)
    # and the patch end landing exactly on a contract expiry (never 'clamped',
    # so no marker at all). Display-only: changes only exit_reason; never
    # overrides a genuine SL exit. Downstream patch / Live-DD resets follow the
    # corrected position.
    _apply_filter_end_last_per_patch(final_priced, original_segments, _clamp_reason)

    # Per-leg filter truncation. Runs AFTER the strategy-patch tagger so it wins
    # on the rows it owns, and joins any co-occurring reason with "+" to match
    # the combined-exit-reason convention used elsewhere in this module.
    if _leg_filter_end_keys:
        for _row in final_priced:
            if not _is_leg_filter_ended(_row):
                continue
            _cur = str(_row.get("exit_reason") or "").strip()
            if not _cur or _cur == "EXPIRY":
                _row["exit_reason"] = LEG_FILTER_END
            elif LEG_FILTER_END not in _cur:
                _row["exit_reason"] = _cur + "+" + LEG_FILTER_END

    # T-n scheduled-exit label. When the run exits N>0 trading days before the
    # contract expiry (exit_dte > 0), a trade that rides to its scheduled exit
    # did NOT reach the actual expiry date — so the neutral "EXPIRY" token reads
    # "SCHEDULED_EXIT" instead (alone, or within a combined reason such as
    # "SCHEDULED_EXIT+SPOT_ADJ_RISE"). T-0 runs (exit ON expiry) keep "EXPIRY".
    # Runs LAST, after every calc-relevant check (per-leg cascade, SLB
    # post-process, FILTER_END pass) has already keyed off the internal "EXPIRY"
    # token — so this is purely cosmetic: no exit date, price, or P&L changes.
    if int(payload.get("exit_dte") or 0) > 0:
        for row in final_priced:
            _er = str(row.get("exit_reason") or "")
            if "EXPIRY" not in _er.upper():
                continue
            row["exit_reason"] = "+".join(
                "SCHEDULED_EXIT" if p.upper() == "EXPIRY" else p
                for p in _er.split("+")
            )

    # Surface a FIXED yearly leg's OWN spot-adjustment in the Exit Reason. After the
    # strike fixes above, such a leg's strike moves ONLY on its own breach, a contract
    # roll, or a patch reset — so a mid-contract change that is neither a roll nor a
    # reset IS an own adjustment. It often fired as a cascade sub-hop (some other leg
    # cut the trade first), leaving the row labelled SCHEDULED_EXIT while the strike
    # still stepped. Credit the trade whose exit triggered the re-strike so the yearly
    # adjustment is visible as `SPOT_ADJ_RISE (Leg N ...)`. Strings only — no exit
    # date / price / P&L change. Corroborated against the mark-timeline breach dates.
    _yr_fixed_lids = {
        _i + 1 for _i, _lg in enumerate(legs_src)
        if isinstance(_lg, dict)
        and str(_lg.get("expiry") or _lg.get("expiry_type") or "").upper() == "YEARLY"
        and str(_lg.get("rollover_strike_mode") or "fresh").lower() == "fixed"
        and bool((_lg.get("spot_adjustment") or {}).get("enabled"))
        and str(_lg.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE")
    }
    if _yr_fixed_lids:
        _seg_starts_r = {s for s, _e in (_eff_segs_sa or [])}
        for _yl in _yr_fixed_lids:
            _own_dates_yl = _leg_own_breach_dates.get(_yl, set())
            _yrows = sorted(
                (r for r in final_priced if int(r.get("leg_id") or 0) == _yl),
                key=lambda r: (_normalize_iso(r.get("entry_date") or ""), int(r.get("trade_id") or 0)),
            )
            for _pi in range(len(_yrows) - 1):
                _a = _yrows[_pi]
                _b = _yrows[_pi + 1]
                _sa_stk = float(_a.get("strike") or 0)
                _sb_stk = float(_b.get("strike") or 0)
                if _sa_stk <= 0 or _sb_stk <= 0 or _sa_stk == _sb_stk:
                    continue
                if _normalize_iso(_a.get("expiry") or "") != _normalize_iso(_b.get("expiry") or ""):
                    continue  # contract roll
                if _normalize_iso(_b.get("entry_date") or "") in _seg_starts_r:
                    continue  # patch reset
                _a_exit = _normalize_iso(_a.get("exit_date") or "")
                # Corroborate with the independent mark-timeline breach schedule.
                if _own_dates_yl and _a_exit not in _own_dates_yl and \
                        _normalize_iso(_b.get("entry_date") or "") not in _own_dates_yl:
                    continue
                _yr_reason = "SPOT_ADJ_%s (%s)" % (
                    "RISE" if _sb_stk > _sa_stk else "FALL", _sa_leg_label(_yl)
                )
                _a_tid = int(_a.get("trade_id") or 0)
                for _rr in final_priced:
                    if int(_rr.get("trade_id") or 0) != _a_tid:
                        continue
                    if _normalize_iso(_rr.get("exit_date") or "") != _a_exit:
                        continue
                    _cur = str(_rr.get("exit_reason") or "")
                    if ("Leg %d" % _yl) in _cur:
                        continue  # already credited
                    if _cur in ("", "EXPIRY"):
                        _rr["exit_reason"] = _yr_reason
                    elif "SPOT_ADJ" in _cur:
                        _rr["exit_reason"] = _cur + " + " + _yr_reason
                    else:
                        _rr["exit_reason"] = _cur + "+" + _yr_reason

            # COINCIDENCE case (breach lands ON a SCHEDULED / FILTER exit day).
            # When the own-breach threshold is crossed on the trade's own scheduled
            # cadence-roll (or filter-end) date, the breach does NOT cut the trade
            # short (it was ending that day anyway) and — when the threshold is
            # sub-gap — does NOT change the strike, so the strike-change loop above
            # never credits it. The row then reads a bare SCHEDULED_EXIT/FILTER_END
            # even though the leg genuinely breached (e.g. a +203pt rise on 23-Apr,
            # the monthly roll). Surface the adjustment on the trade WHERE it
            # happened, corroborated by the independent breach schedule
            # (`_own_dates_yl`). Additive strings only — no date / strike / P&L
            # change; skips rows already carrying this leg's tag so it never
            # double-credits the strike-change loop above.
            if _own_dates_yl:
                for _ci in range(len(_yrows)):
                    _cr = _yrows[_ci]
                    _cx = _normalize_iso(_cr.get("exit_date") or "")
                    if _cx not in _own_dates_yl:
                        continue
                    _cur = str(_cr.get("exit_reason") or "")
                    if "SPOT_ADJ" in _cur or ("Leg %d" % _yl) in _cur:
                        continue  # already reflects an adjustment
                    if _cx in _seg_starts_r:
                        continue  # patch reset, not an own adjustment
                    _cxs = float(_cr.get("exit_spot") or 0)
                    _ces = float(_cr.get("entry_spot") or 0)
                    _ckind = "RISE" if _cxs >= _ces else "FALL"
                    _ctag = "SPOT_ADJ_%s (%s)" % (_ckind, _sa_leg_label(_yl))
                    _cr["exit_reason"] = (_cur + " + " + _ctag) if _cur else _ctag
                    # SHOW ONCE: the same breach's bridge re-enters on this exit date
                    # carrying a LONE SPOT_ADJ tag with the SAME strike (no strike
                    # change) and no own breach on its own exit — so without this the
                    # one breach shows on two consecutive rows. Reset that phantom
                    # re-entry to the plain scheduled exit so the breach is credited
                    # exactly once, on the row where spot actually crossed. Tightly
                    # gated (same entry date, same strike, lone SPOT_ADJ for THIS leg,
                    # own exit not itself a breach) → never touches a re-entry that
                    # genuinely breached or moved the strike; string-only.
                    _cstk = float(_cr.get("strike") or 0)
                    for _nr in _yrows[_ci + 1:]:
                        if _normalize_iso(_nr.get("entry_date") or "") != _cx:
                            break
                        _nx = _normalize_iso(_nr.get("exit_date") or "")
                        _nreason = str(_nr.get("exit_reason") or "")
                        if (float(_nr.get("strike") or 0) == _cstk
                                and _nx not in _own_dates_yl
                                and _nreason.startswith("SPOT_ADJ_")
                                and ("Leg %d" % _yl) in _nreason):
                            _nr["exit_reason"] = "SCHEDULED_EXIT"

    return final_priced
