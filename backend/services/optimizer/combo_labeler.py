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
from typing import Any, Dict, Optional

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
        return f"StraddleW{mult:g}"
    return kind.upper()


def _spot_adjustment_label(payload: Dict[str, Any]) -> str:
    if not payload.get("spot_adjustment_enabled"):
        return "NoAdjustment"
    direction = (payload.get("spot_adjustment_direction") or "").lower()
    try:
        pct = float(payload.get("spot_adjustment_value") or payload.get("spot_adjustment_pct") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    pct_str = f"{pct:g}%"
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
    ce_leg = _find_leg(payload, "CE")
    pe_leg = _find_leg(payload, "PE")
    call_strike = _strike_label(ce_leg.get("strike_selection") if ce_leg else None, "CE")
    put_strike = _strike_label(pe_leg.get("strike_selection") if pe_leg else None, "PE")
    call_pos = _position_label(ce_leg)
    put_pos = _position_label(pe_leg)
    ce_sl = _sl_label(ce_leg)
    pe_sl = _sl_label(pe_leg)
    spot_adj = _spot_adjustment_label(payload)
    expiry = _expiry_label(payload)
    shift = _shift_label(payload)

    parts = []
    if ce_leg is not None:
        seg = f"CE_{call_strike}"
        if call_pos:
            seg += f"_{call_pos}"
        if ce_sl:
            seg += f"_{ce_sl}"
        parts.append(seg)
    if pe_leg is not None:
        seg = f"PE_{put_strike}"
        if put_pos:
            seg += f"_{put_pos}"
        if pe_sl:
            seg += f"_{pe_sl}"
        parts.append(seg)
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
