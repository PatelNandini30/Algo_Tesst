"""
Build human-readable labels for a parameter combination.

Matches the research team's filename convention, e.g.

    CE_0.5%_OTM_Sell_PE_0.5%_ITM_Sell_NoAdjustment_Weekly_Expiry_T-1_To_T-1

Two labellers:

* `label_combo(payload_after_apply, combo)` → master-summary friendly dict of
  the six columns (Expiry, Shifting, Put ATM or ITM, Call ATM or ITM,
  Spot Adjustment) plus a single `combo_label` string built from them.

* `safe_filename(label)` → strip characters illegal in filenames.

The labeller is best-effort: any field it can't resolve falls back to `"-"`.
Callers should treat the master-summary columns as display-only and rely on
the structured combo dict for any logic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.optimizer.param_expander import get_by_path


def _strike_label(strike_selection: Optional[Dict[str, Any]], option_type: str = "CE") -> str:
    """Render a single leg's strike spec into '0.5%_OTM' / 'ATM' / '2.0%_ITM'.

    option_type is required for pct_of_atm because the direction field is stored
    as '+'/'-' (engine sign convention), not 'OTM'/'ITM'. The semantic meaning
    of '+'/'-' flips between calls and puts:

        CE '+' (above ATM) = OTM   |   CE '-' (below ATM) = ITM
        PE '-' (below ATM) = OTM   |   PE '+' (above ATM) = ITM
    """
    if not isinstance(strike_selection, dict):
        return "-"
    kind = (strike_selection.get("type") or "").lower()
    if kind in ("strike_type", "", None):
        st = (strike_selection.get("strike_type") or "ATM").upper()
        return st
    if kind == "pct_of_atm":
        try:
            val = float(strike_selection.get("value", 0))
        except (TypeError, ValueError):
            val = 0.0
        raw_dir = str(strike_selection.get("direction") or "").strip()
        is_call = option_type.upper().startswith("C")

        if raw_dir.upper() in ("OTM", "ITM", "ATM"):
            # Already a semantic label — use val as magnitude directly.
            if val == 0.0 or raw_dir.upper() == "ATM":
                return "ATM"
            return f"{abs(val):g}%_{raw_dir.upper()}"

        # Engine sign convention: "+" means add val% above ATM, "-" means below.
        # Default (empty direction) matches the strike-picker default in
        # engine_rust.py:_compute_strike_for_leg_python which does
        #     raw = entry_spot - shift if direction == "-" else entry_spot + shift
        # i.e. anything that isn't "-" behaves as "+" (add shift above ATM).
        # Previously this defaulted to -1, which mislabeled positive values as
        # ITM when the engine actually placed them OTM (above spot for CE).
        # When the optimizer sweeps value through negative territory (e.g. val=-1
        # with direction="+") the net offset is negative = below ATM = ITM for CE.
        dir_sign = -1 if raw_dir == "-" else +1
        net_offset = dir_sign * val
        abs_val = abs(net_offset)

        if abs_val == 0.0:
            return "ATM"

        # Above ATM (net_offset > 0): OTM for CE, ITM for PE.
        # Below ATM (net_offset < 0): ITM for CE, OTM for PE.
        if net_offset > 0:
            direction = "OTM" if is_call else "ITM"
        else:
            direction = "ITM" if is_call else "OTM"
        return f"{abs_val:g}%_{direction}"
    if kind == "atm_straddle_prem_pct":
        try:
            val = float(strike_selection.get("value", 0))
        except (TypeError, ValueError):
            val = 0.0
        return f"Straddle{val:g}%"
    if kind == "straddle_width":
        try:
            mult = float(strike_selection.get("straddle_multiplier", 1))
        except (TypeError, ValueError):
            mult = 1.0
        # Raw +/- sign, applied identically to every leg (no CE/PE meaning).
        direction = str(strike_selection.get("straddle_direction") or "+").strip()
        sign = "-" if direction == "-" else "+"
        return f"StraddleW{mult:g}_{sign}"
    if kind == "rel_leg":
        # Relative-to-Leg (Iron Condor wing): 'REL_L1_2G' = Leg 1 + 2 gaps.
        # Matches the backtest export filename label (ResultsPanel.jsx).
        try:
            ref = int(strike_selection.get("ref_leg") or 1)
            off = float(strike_selection.get("offset") or 0)
        except (TypeError, ValueError):
            ref, off = 1, 0.0
        return f"REL_L{ref}_{off:g}G"
    return kind.upper()


def _spot_adjustment_label(payload: Dict[str, Any]) -> str:
    if not payload.get("spot_adjustment_enabled"):
        return "NoAdjustment"
    direction = (payload.get("spot_adjustment_direction") or "").lower()
    try:
        pct = float(payload.get("spot_adjustment_value") or payload.get("spot_adjustment_pct") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    # Threshold can be a percent move or an absolute points move — label it as
    # whatever the payload actually ran with, so a 150-point sweep doesn't get
    # filed as "RiseBy150%".
    units = str(payload.get("spot_adjustment_units") or "percent").lower()
    pct_str = f"{pct:g}pts" if units == "points" else f"{pct:g}%"
    if direction in ("up", "rise", "rises"):
        return f"RiseBy{pct_str}"
    if direction in ("down", "fall", "falls"):
        return f"FallsBy{pct_str}"
    if direction in ("both", "either", "any"):
        return f"RisesOrFallsBy{pct_str}"
    return f"Adjust{pct_str}"


def _expiry_label(payload: Dict[str, Any]) -> str:
    legs = payload.get("legs") or []
    leg_expiries = {
        (leg.get("expiry") or "").lower()
        for leg in legs
        if isinstance(leg, dict)
    }
    # Prefer a per-leg NEXT_WEEKLY / NEXT_MONTHLY expiry so the ZIP filename
    # matches the backtest (which labels off the leg expiry, not the global
    # expiry_window — which stays "weekly"/"monthly" for next-* strategies).
    if any(e in ("next_weekly", "weekly_t1") for e in leg_expiries):
        return "Next_Weekly"
    if any(e in ("next_monthly", "monthly_t1") for e in leg_expiries):
        return "Next_Monthly"
    window = (payload.get("expiry_window") or "").lower()
    candidate = window or (next(iter(leg_expiries)) if leg_expiries else "")
    candidate = candidate.replace("_expiry", "")
    if not candidate:
        return "Weekly"
    return candidate[:1].upper() + candidate[1:]


def _shift_label(payload: Dict[str, Any]) -> str:
    """Render entry/exit DTE shifts as 'T-1_To_T-1' style."""
    try:
        entry = int(payload.get("entry_dte", 0) or 0)
    except (TypeError, ValueError):
        entry = 0
    try:
        exit_ = int(payload.get("exit_dte", 0) or 0)
    except (TypeError, ValueError):
        exit_ = 0

    def shift(n: int) -> str:
        if n == 0:
            return "T-0"
        sign = "-" if n > 0 else "+"
        return f"T{sign}{abs(n)}"

    return f"{shift(entry)}_To_{shift(exit_)}"


def _find_leg(payload: Dict[str, Any], option_type: str) -> Optional[Dict[str, Any]]:
    """Return the first leg with the given option_type (CE / PE), if any."""
    target = option_type.upper()
    for leg in payload.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        if (leg.get("option_type") or "").upper() == target:
            return leg
    return None


def _find_legs(payload: Dict[str, Any], option_type: str) -> List[Dict[str, Any]]:
    """Return ALL legs with the given option_type (CE / PE), in payload order.

    A spread whose two legs share an option type (e.g. PE Sell + PE Buy) has two
    PE legs; labelling only the first (via _find_leg) silently drops the second,
    so combos that differ only in the second leg's strike collapse to one label.
    This returns every matching leg so the label can describe them all.
    """
    target = option_type.upper()
    return [
        leg for leg in (payload.get("legs") or [])
        if isinstance(leg, dict) and (leg.get("option_type") or "").upper() == target
    ]


def _position_label(leg: Optional[Dict[str, Any]]) -> str:
    if not leg:
        return ""
    pos = (leg.get("position") or "").lower()
    if pos in ("buy", "long"):
        return "Buy"
    if pos in ("sell", "short"):
        return "Sell"
    return pos.capitalize() if pos else ""


def _sl_label(leg: Optional[Dict[str, Any]]) -> str:
    """Return SL suffix for a leg, e.g. 'SL_50%' or 'SL_50PTS_Buffer_10%'."""
    if not leg:
        return ""
    sl_buf = leg.get("slWithBuffer") or {}
    if sl_buf:
        val = sl_buf.get("value")
        buf_pct = sl_buf.get("buffer_pct")
        if val and buf_pct:
            mode = (sl_buf.get("mode") or "PERCENT").upper()
            suffix = "%" if "PERCENT" in mode else "PTS"
            val_str = f"{float(val):g}"
            buf_str = f"{float(buf_pct):g}"
            return f"SL_{val_str}{suffix}_Buffer_{buf_str}%"
    sl = leg.get("stopLoss") or {}
    if sl:
        val = sl.get("value")
        if val:
            mode = (sl.get("mode") or "PERCENT").upper()
            suffix = "%" if "PERCENT" in mode else "PTS"
            val_str = f"{float(val):g}"
            return f"SL_{val_str}{suffix}"
    return ""


def _find_futures_legs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all futures legs (segment == FUTURES), in payload order. Futures
    carry no option_type, so the CE/PE labellers skip them entirely."""
    return [
        leg for leg in (payload.get("legs") or [])
        if isinstance(leg, dict) and str(leg.get("segment") or "").upper() == "FUTURES"
    ]


def _futures_segment(leg: Optional[Dict[str, Any]]) -> str:
    """Render a futures leg for the combo label / filename, e.g. 'FUT_Buy' or
    'FUT_Sell_SL_50%'. Futures have no strike, so only position (+ any SL) is
    shown — mirrors the CE_/PE_ option segments. Empty when leg is missing."""
    if not isinstance(leg, dict):
        return ""
    pos = _position_label(leg)
    sl = _sl_label(leg)
    seg = "FUT"
    if pos:
        seg += f"_{pos}"
    if sl:
        seg += f"_{sl}"
    return seg


def _midcap_label(payload: Dict[str, Any]) -> str:
    """Midcap cross-index overlay segment for the combo label, matching the
    backtest filename: e.g. "BUY_MIDCAP100_Hypothetical_Future" / "..._Spot".
    Empty when no Midcap leg is present (non-Midcap combos unchanged)."""
    legs = payload.get("midcap_legs") or []
    segs = []
    for l in legs:
        if not isinstance(l, dict):
            continue
        pos = str(l.get("position") or "BUY").upper()
        mode = str(l.get("midcap_mode") or l.get("mode") or "hypothetical").lower()
        sym = str(l.get("symbol") or "NIFTYMIDCAP100").upper().replace("NIFTY", "") or "MIDCAP100"
        mode_lbl = "Hypothetical_Future" if mode == "hypothetical" else "Spot"
        segs.append(f"{pos}_{sym}_{mode_lbl}")
    return "_".join(segs)


def _midcap_spot_adjustment_label(payload: Dict[str, Any]) -> str:
    """Midcap cross-index spot adjustment label, e.g. 'MidcapRiseBy1%'. Empty
    when the Midcap spot adjustment is disabled (non-Midcap combos unchanged)."""
    mc = payload.get("midcap_spot_adjustment") or {}
    if not mc.get("enabled"):
        return ""
    direction = str(mc.get("direction") or "").lower()
    try:
        pct = float(mc.get("pct") or mc.get("value") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    units = str(mc.get("units") or "percent").lower()
    pct_str = f"{pct:g}pts" if units == "points" else f"{pct:g}%"
    if direction in ("up", "rise", "rises"):
        return f"MidcapRiseBy{pct_str}"
    if direction in ("down", "fall", "falls"):
        return f"MidcapFallsBy{pct_str}"
    if direction in ("both", "either", "any"):
        return f"MidcapRisesOrFallsBy{pct_str}"
    return f"MidcapAdjust{pct_str}"


def label_combo(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Inspect a (combo-applied) payload and return the master-summary columns
    + a single underscore-joined `combo_label`.

    Returned keys:
        expiry              e.g. "Weekly"
        shifting            e.g. "T-1_To_T-1"
        put_strike_label    e.g. "0.5%_ITM"
        call_strike_label   e.g. "3.0%_OTM"
        spot_adjustment     e.g. "NoAdjustment"
        combo_label         e.g. "CE_3.0%_OTM_Sell_PE_0.5%_ITM_Sell_NoAdjustment_Weekly_Expiry_T-1_To_T-1"
    """
    # Describe EVERY leg, not just the first CE + first PE. For the common
    # single-CE / single-PE strategy this produces byte-identical output to the
    # previous first-leg-only logic (one CE segment then one PE segment); it only
    # differs when a strategy has multiple legs of the same option type (e.g. a
    # PE Sell + PE Buy spread), where both legs are now described so combos that
    # differ only in the second leg no longer collapse to an identical label.
    ce_legs = _find_legs(payload, "CE")
    pe_legs = _find_legs(payload, "PE")

    def _leg_segment(leg: Dict[str, Any], otype: str):
        strike = _strike_label(leg.get("strike_selection"), otype)
        pos = _position_label(leg)
        sl = _sl_label(leg)
        seg = f"{otype}_{strike}"
        if pos:
            seg += f"_{pos}"
        if sl:
            seg += f"_{sl}"
        return seg, strike

    spot_adj = _spot_adjustment_label(payload)
    expiry = _expiry_label(payload)
    shift = _shift_label(payload)

    parts = []
    call_strikes: List[str] = []
    for _leg in ce_legs:
        _seg, _st = _leg_segment(_leg, "CE")
        parts.append(_seg)
        call_strikes.append(_st)
    put_strikes: List[str] = []
    for _leg in pe_legs:
        _seg, _st = _leg_segment(_leg, "PE")
        parts.append(_seg)
        put_strikes.append(_st)

    # Futures legs (no strike) — appended after the option legs, e.g. 'FUT_Sell'.
    # Previously dropped from the combo label / per-combo filename entirely.
    for _leg in _find_futures_legs(payload):
        _fseg = _futures_segment(_leg)
        if _fseg:
            parts.append(_fseg)

    # Master-summary strike columns: single leg → unchanged; multiple same-type
    # legs → joined with '+' so each distinct multi-leg combo gets a distinct
    # label (previously only the first leg per type was recorded).
    call_strike = "+".join(call_strikes) if call_strikes else _strike_label(None, "CE")
    put_strike = "+".join(put_strikes) if put_strikes else _strike_label(None, "PE")
    # Midcap cross-index overlay leg(s) — appended after the option legs, like
    # the backtest filename (only present when a Midcap leg ran).
    midcap_seg = _midcap_label(payload)
    if midcap_seg:
        parts.append(midcap_seg)
    midcap_adj_seg = _midcap_spot_adjustment_label(payload)
    if midcap_adj_seg:
        parts.append(midcap_adj_seg)
    parts.append(spot_adj)
    parts.append(f"{expiry}_Expiry")
    parts.append(shift)
    combo_label = "_".join(parts)

    return {
        "expiry": expiry,
        "shifting": shift,
        "put_strike_label": put_strike,
        "call_strike_label": call_strike,
        "spot_adjustment": spot_adj,
        "combo_label": combo_label,
    }


_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._%+\-]")


def safe_filename(label: str) -> str:
    """Strip characters illegal on Windows/macOS/Linux filesystems."""
    return _FILENAME_BAD.sub("_", label)
