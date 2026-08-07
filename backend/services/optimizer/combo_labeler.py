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
        if mult == 0.0:
            return "StraddleW0_ATM"
        # The engine's +/- sign is a raw offset applied identically to every
        # leg (no CE/PE meaning at the engine level) — but for the LABEL,
        # translate it into ITM/OTM per option_type, same convention as
        # pct_of_atm above, so two same-type legs (e.g. a Monthly PE + a
        # Weekly PE) read unambiguously instead of a bare "+"/"-":
        #   CE '+' (above ATM) = OTM   |   CE '-' (below ATM) = ITM
        #   PE '-' (below ATM) = OTM   |   PE '+' (above ATM) = ITM
        direction = str(strike_selection.get("straddle_direction") or "+").strip()
        is_call = option_type.upper().startswith("C")
        above_atm = direction != "-"
        if above_atm:
            moneyness = "OTM" if is_call else "ITM"
        else:
            moneyness = "ITM" if is_call else "OTM"
        return f"StraddleW{mult:g}_{moneyness}"
    if kind == "rel_leg":
        # Relative-to-Leg (Iron Condor wing): 'REL_L1_2G' = Leg 1 + 2 gaps.
        # Matches the backtest export filename label (ResultsPanel.jsx).
        try:
            ref = int(strike_selection.get("ref_leg") or 1)
            off = float(strike_selection.get("offset") or 0)
        except (TypeError, ValueError):
            ref, off = 1, 0.0
        return f"REL_L{ref}_{off:g}G"
    if kind.startswith("time_value"):
        # 'TV100' / 'TV100_GTE' / 'TV100_LTE'. Matches the backtest export
        # filename label (ResultsPanel.jsx) so an optim combo folder and a
        # standalone backtest of the same leg produce the same token.
        tv = strike_selection.get("time_value")
        if tv is None:
            tv = strike_selection.get("premium")
        try:
            tv = float(tv or 0)
        except (TypeError, ValueError):
            tv = 0.0
        suffix = {"time_value_gte": "_GTE", "time_value_lte": "_LTE"}.get(kind, "")
        side = str(strike_selection.get("moneyness") or "ATM").upper()
        try:
            cap = abs(float(strike_selection.get("tv_range_pct") or 0))
        except (TypeError, ValueError):
            cap = 0.0
        # Unit is ALWAYS spelled out so PTS vs PCT can never be confused,
        # and the range cap uses its own RNG token so the two "PCT"s in a
        # name are unambiguous: TV0.3PCT_ITM_RNG2PCT.
        unit = "PCT" if str(strike_selection.get("tv_units") or "points") == "percent" else "PTS"
        return f"TV{tv:g}{unit}{suffix}_{side}" + (f"_RNG{cap:g}PCT" if cap else "")
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
    # YEARLY holds the long-dated December contract while re-booking on a separate
    # weekly/monthly cadence. It has no weekly/monthly "expiry_window", so the
    # fall-through below mislabelled it "Monthly". Surface it (with the roll
    # cadence) instead.
    if str(payload.get("expiry_type") or "").upper() == "YEARLY":
        cadence = str(payload.get("rollover_cadence") or "").lower()
        if cadence in ("weekly", "monthly"):
            return "Yearly_" + cadence[:1].upper() + cadence[1:]
        return "Yearly"
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


def _midcpnifty_spot_adjustment_label(payload: Dict[str, Any]) -> str:
    """MIDCPNIFTY cross-index spot adjustment label, e.g. 'MidcpniftyRiseBy1%'.

    Empty unless the strategy ACTUALLY HOLDS a MIDCPNIFTY leg and the adjustment
    is enabled — so a plain NIFTY combo's label/filename is byte-identical to
    before. Mirrors _midcap_spot_adjustment_label above.
    """
    mn = payload.get("midcpnifty_spot_adjustment") or {}
    if not mn.get("enabled"):
        return ""
    _legs = payload.get("legs") or []
    _has = any(
        isinstance(l, dict)
        and str(l.get("segment") or "").lower() != "midcap100"
        and str(l.get("index") or payload.get("index") or "").upper() == "MIDCPNIFTY"
        for l in _legs
    )
    if not _has:
        return ""
    direction = str(mn.get("direction") or "").lower()
    try:
        pct = float(mn.get("pct") or mn.get("value") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    units = str(mn.get("units") or "percent").lower()
    pct_str = f"{pct:g}pts" if units == "points" else f"{pct:g}%"
    if direction in ("up", "rise", "rises"):
        return f"MidcpniftyRiseBy{pct_str}"
    if direction in ("down", "fall", "falls"):
        return f"MidcpniftyFallsBy{pct_str}"
    if direction == "both":
        return f"MidcpniftyMoveBy{pct_str}"
    return ""


def _per_leg_spot_adjustment_label(payload: Dict[str, Any]) -> str:
    """Per-leg ("own") spot adjustment label, e.g. 'L1RiseBy1%_L2RiseBy1000pts'.

    Empty unless at least one leg carries its OWN enabled spot_adjustment dict, so
    a strategy that uses only the strategy-level knob (or none) gets a byte-identical
    label/filename to before. Distinguishes combos that sweep a leg's own threshold
    / direction / units. Mirrors _spot_adjustment_label's Rise/Falls/Move wording.
    """
    segs: List[str] = []
    for _i, leg in enumerate(payload.get("legs") or [], start=1):
        if not isinstance(leg, dict):
            continue
        sa = leg.get("spot_adjustment")
        if not isinstance(sa, dict) or not sa.get("enabled"):
            continue
        try:
            pct = float(sa.get("pct") or sa.get("value") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        units = str(sa.get("units") or "percent").lower()
        pct_str = f"{pct:g}pts" if units == "points" else f"{pct:g}%"
        direction = str(sa.get("direction") or "").lower()
        if direction in ("up", "rise", "rises"):
            word = f"RiseBy{pct_str}"
        elif direction in ("down", "fall", "falls"):
            word = f"FallsBy{pct_str}"
        elif direction in ("both", "either", "any"):
            word = f"MoveBy{pct_str}"
        else:
            word = f"AdjustBy{pct_str}"
        segs.append(f"L{_i}{word}")
    return "_".join(segs)


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
    # (index, leg) pairs — the 1-based index is the leg's position in the
    # OVERALL strategy (matching the "Leg" column everywhere else), needed to
    # tag same-type legs unambiguously below (L1/L2), not just filter by type.
    _all_legs = list(enumerate(payload.get("legs") or [], start=1))
    ce_legs = [(i, leg) for i, leg in _all_legs if isinstance(leg, dict) and (leg.get("option_type") or "").upper() == "CE"]
    pe_legs = [(i, leg) for i, leg in _all_legs if isinstance(leg, dict) and (leg.get("option_type") or "").upper() == "PE"]

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
    call_strikes: List[tuple] = []
    for _i, _leg in ce_legs:
        _seg, _st = _leg_segment(_leg, "CE")
        # Same L{n} disambiguation as the summary columns below, applied to the
        # combo filename too — only when there's more than one CE (or PE) leg,
        # so a single-CE/single-PE strategy's filename is byte-identical to
        # before ("easy search": a Monthly PE + Weekly PE combo's ZIP/tradesheet
        # filename now reads ..._L1_StraddleW2_OTM_Sell_..._L2_StraddleW0.5_OTM_Sell...
        # instead of two look-alike "PE_StraddleW..._Sell" segments back to back).
        if len(ce_legs) > 1:
            _seg = f"L{_i}_{_seg}"
        parts.append(_seg)
        call_strikes.append((_i, _st))
    put_strikes: List[tuple] = []
    for _i, _leg in pe_legs:
        _seg, _st = _leg_segment(_leg, "PE")
        if len(pe_legs) > 1:
            _seg = f"L{_i}_{_seg}"
        parts.append(_seg)
        put_strikes.append((_i, _st))

    # Futures legs (no strike) — appended after the option legs, e.g. 'FUT_Sell'.
    # Previously dropped from the combo label / per-combo filename entirely.
    for _leg in _find_futures_legs(payload):
        _fseg = _futures_segment(_leg)
        if _fseg:
            parts.append(_fseg)

    # Master-summary strike columns: single leg → unchanged (byte-identical to
    # before). Multiple same-type legs → tagged with the leg's own number and
    # joined with '/' (e.g. "L1_StraddleW2_OTM/L2_StraddleW0.5_OTM") so a
    # Monthly PE + Weekly PE combo no longer collapses into an ambiguous
    # "StraddleW2_-+StraddleW0.5_-" that doesn't say which leg is which.
    def _join_strikes(pairs: List[tuple], otype: str) -> str:
        if not pairs:
            return _strike_label(None, otype)
        if len(pairs) == 1:
            return pairs[0][1]
        return "/".join(f"L{i}_{st}" for i, st in pairs)

    call_strike = _join_strikes(call_strikes, "CE")
    put_strike = _join_strikes(put_strikes, "PE")
    # Midcap cross-index overlay leg(s) — appended after the option legs, like
    # the backtest filename (only present when a Midcap leg ran).
    midcap_seg = _midcap_label(payload)
    if midcap_seg:
        parts.append(midcap_seg)
    midcap_adj_seg = _midcap_spot_adjustment_label(payload)
    if midcap_adj_seg:
        parts.append(midcap_adj_seg)
    midcp_adj_seg = _midcpnifty_spot_adjustment_label(payload)
    if midcp_adj_seg:
        parts.append(midcp_adj_seg)
    per_leg_adj_seg = _per_leg_spot_adjustment_label(payload)
    if per_leg_adj_seg:
        parts.append(per_leg_adj_seg)
    # Strategy-level token in the FILENAME: only when it's a REAL strategy-level
    # adjustment, OR when no other adjustment token exists (so a genuinely-unadjusted
    # combo still reads "NoAdjustment"). Without this, a combo that sweeps a leg's OWN
    # spot_adjustment (or the midcap/midcpnifty overlay) also carried a redundant
    # "NoAdjustment" — filing an adjusted combo under the "No Adjustment" folder.
    # Mirrors the spot_adjustment_col logic below so filename == column.
    if payload.get("spot_adjustment_enabled") or not (
        per_leg_adj_seg or midcap_adj_seg or midcp_adj_seg
    ):
        parts.append(spot_adj)
    parts.append(f"{expiry}_Expiry")
    parts.append(shift)
    combo_label = "_".join(parts)

    # The master-summary "Spot Adjustment" column must reflect ANY active
    # adjustment, not just the strategy-level knob. A run that sweeps a leg's OWN
    # spot_adjustment (legs[N].spot_adjustment.*) or the midcap/midcpnifty overlay
    # otherwise shows "NoAdjustment" on every row even though the combos differ.
    # Combine the active labels in the same order as combo_label.
    _adj = []
    if payload.get("spot_adjustment_enabled"):
        _adj.append(spot_adj)          # strategy-level RiseBy.. / FallsBy.. etc.
    if midcap_adj_seg:
        _adj.append(midcap_adj_seg)
    if midcp_adj_seg:
        _adj.append(midcp_adj_seg)
    if per_leg_adj_seg:
        _adj.append(per_leg_adj_seg)   # e.g. L2RiseBy1000pts
    spot_adjustment_col = "_".join(_adj) if _adj else "NoAdjustment"

    return {
        "expiry": expiry,
        "shifting": shift,
        "put_strike_label": put_strike,
        "call_strike_label": call_strike,
        "spot_adjustment": spot_adjustment_col,
        "combo_label": combo_label,
    }


_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._%+\-]")


def safe_filename(label: str) -> str:
    """Strip characters illegal on Windows/macOS/Linux filesystems."""
    return _FILENAME_BAD.sub("_", label)
