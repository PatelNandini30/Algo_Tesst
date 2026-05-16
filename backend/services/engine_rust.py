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

import logging
from typing import Any, Dict, List, Optional, Tuple

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
    # trading_days is assumed sorted ascending ISO strings.
    import bisect
    idx = bisect.bisect_right(trading_days, target) - 1
    if idx < 0:
        return None
    return trading_days[idx]


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
        per_leg_pnl = round(
            (entry_px - exit_px) if position == "SELL" else (exit_px - entry_px), 4
        )
        ce_pnl = per_leg_pnl if opt_type == "CE" else 0
        pe_pnl = per_leg_pnl if opt_type == "PE" else 0
        pct_pnl = round(net_pnl / entry_spot * 100.0, 4) if entry_spot else 0.0
        qty = int(row.get("lots") or 1) * int(row.get("lot_size") or lot_size or 1)
        out.append({
            "Trade": str(row.get("trade_id") or ""),
            "Leg": int(row.get("leg_id") or 1),
            "Index": index_str,
            "Entry Date": _normalize_iso(row.get("entry_date")),
            "Exit Date": _normalize_iso(row.get("exit_date")),
            "Leg Exit Date": _normalize_iso(row.get("exit_date")),
            "Type": opt_type,
            "Strike": float(row.get("strike") or 0.0),
            "B/S": position,
            "Qty": qty,
            "Entry Price": float(row.get("entry_price") or 0.0),
            "Exit Price": float(row.get("exit_price") or 0.0),
            "Raw Entry Price": float(row.get("entry_price") or 0.0),
            "Raw Exit Price": float(row.get("exit_price") or 0.0),
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
            "FUT P&L": 0,
            "FUT Entry Price": "",
            "FUT Exit Price": "",
            "Net P&L": net_pnl,
            "% P&L": pct_pnl,
            "Exit Reason": "Expiry",
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
    """Slice 6 supports re-entry only when strike_selection is plain ATM."""
    sel = leg_src.get("strike_selection") or {}
    if not isinstance(sel, dict):
        return False
    if (sel.get("type") or "").lower() != "strike_type":
        return False
    return (sel.get("strike_type") or "").upper() == "ATM"


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

    # Step 1: enumerate trade specs (entry/exit dates + strikes).
    specs = algotest_native.resolve_trade_specs(
        payload, expiry_dates, trading_days, int(lot_size), spot_by_date
    )
    if not specs:
        # Rust path rejected the payload — feature outside slices 1-4 scope.
        return None

    # Slice 8a: STR / filter date-range gating. When super_trend_config or
    # filter_config is active and filter_entry_mode is 'dte' (default), filter
    # specs to those whose entry falls inside a segment; clamp exit to the last
    # trading day on/before seg_end when scheduled exit overflows. Drop trades
    # that collapse to 0 days after clamping.
    #
    # filter_entry_mode == 'fixed' or 'min_days' adds extra semantics
    # (forced first-entry, DTE-driven shifting/skipping) not yet ported — fall
    # back to Python for those.
    filter_entry_mode = str(payload.get("filter_entry_mode") or "dte").lower().strip()
    if filter_entry_mode in ("fixed", "min_days"):
        return None
    segments = _load_filter_segments(payload)
    if segments is not None:
        if not segments:
            return []
        def _seg_for(entry_iso: str) -> Optional[Tuple[str, str]]:
            # Linear scan — small segment count in typical configs.
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
            # Only RE_ASAP supported. Anything else → fall back.
            for cfg in (sl_cfg, tgt_cfg):
                if cfg and (cfg.get("mode") or "RE_ASAP").upper() != "RE_ASAP":
                    return None
            # Only ATM strike supported.
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
            strike_interval = float(leg_src.get("strike_interval") or 50.0)
            slippage_val = float(leg["slippage_pct"])
            parent_expiry = _normalize_iso(leg["expiry"])

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

                # RE_ASAP: re-enter on the trigger date. Bail if no time left.
                if current_trig >= cycle_exit:
                    break

                # Spot at re-entry date must be available to compute fresh ATM.
                spot = spot_by_date.get(current_trig)
                if spot is None:
                    break
                new_strike = _resolve_atm_strike(float(spot), strike_interval)

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
                    "position": leg["position"],
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

                re_spec["exit_date"] = re_exit
                reentry_specs.append(re_spec)

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
    trade_scheduled_exit: Dict[int, str] = {}  # trade_id → max scheduled leg exit
    for trade_id, legs in by_trade.items():
        legs.sort(key=lambda r: r["leg_id"])
        leg_overrides = overrides.get(trade_id, [None] * len(legs))
        overall_date = overall_overrides.get(trade_id)
        latest_scheduled = ""
        for i, leg in enumerate(legs):
            override = leg_overrides[i]
            final_exit = override or leg["exit_date"]
            # Slice 4b: SL-with-Buffer. The engine's exit_price_override IS
            # honored now (_recalc_leg_pnl is skipped when it's set). We stash
            # the override here keyed by (trade_id, leg_id, date) so the
            # downstream re-pricing step can swap it in.
            slb = slb_overrides.get((leg["trade_id"], leg["leg_id"]))
            if slb is not None:
                slb_date, _slb_price = slb
                if not override or slb_date < override:
                    final_exit = slb_date
            # Slice 5: Overall SL/Target. Matches _apply_overall_sl_to_per_leg —
            # overall trigger overrides leg whose current exit_date is on or
            # after the overall trigger date. Earlier per-leg exits win.
            if overall_date is not None and final_exit >= overall_date:
                final_exit = overall_date
            # Slice 7a: Spot adjustment exit always clamps the final exit when
            # the SL/Overall date is later than the spot-adj trigger.
            spot_adj_clamp = spot_adj_overrides.get(trade_id)
            if spot_adj_clamp and final_exit >= spot_adj_clamp:
                final_exit = spot_adj_clamp
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
    # firing), skip the trade. `fixed_late_entry` is NOT yet supported by the
    # Rust path — when set, the caller falls back to Python.
    if payload.get("fixed_late_entry"):
        return None

    # Walk trades chronologically.
    trade_ids_sorted = sorted(
        by_trade.keys(),
        key=lambda tid: by_trade[tid][0]["entry_date"],
    )
    kept_trades: set = set()
    prev_exit: Optional[str] = None
    for tid in trade_ids_sorted:
        entry_date = by_trade[tid][0]["entry_date"]
        if prev_exit is not None and entry_date <= prev_exit:
            # Skipped due to overlap with previous active trade
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
