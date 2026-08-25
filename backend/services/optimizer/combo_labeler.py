"""
Build human-readable labels for a parameter combination.

Matches the research team's filename convention, e.g.

    NF_CE_0.5%_OTM_Wkly_Sell_NF_PE_0.5%_ITM_Wkly_Sell_NoAdjustment_T-1_To_T-1

Each leg is tagged with its own index abbreviation (NF=NIFTY, BNF=BANKNIFTY,
FNF=FINNIFTY, MCN=MIDCPNIFTY) instead of an ordinal leg number — a leg is
self-identifying by index + option type + strike + expiry + position, so two
legs never need a "L1"/"L2" tag to tell them apart. Expiry is per-leg (inline,
right after that leg's strike), not one combo-level token at the end, since a
same-index mixed-expiry strategy (e.g. a Weekly PE + a Yearly PE) has no single
expiry that would be correct for every leg.

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


_INDEX_ABBREV = {
    "NIFTY": "NF",
    "BANKNIFTY": "BNF",
    "FINNIFTY": "FNF",
    "MIDCPNIFTY": "MCN",
}

_MIDCAP_SYM_ABBREV = {"MIDCAP100": "MC100"}

_EXPIRY_LABEL_ABBREV = {
    "weekly": "Wkly",
    "monthly": "Mnly",
    "yearly": "Yrly",
    "yearly_weekly": "YrlyWkly",
    "yearly_monthly": "YrlyMnly",
    "next_weekly": "NxtWkly",
    "next_monthly": "NxtMnly",
}

_LEG_EXPIRY_RAW = {
    "weekly": "weekly", "weekly_expiry": "weekly",
    "monthly": "monthly", "monthly_expiry": "monthly",
    "yearly": "yearly",
    "next_weekly": "next_weekly", "weekly_t1": "next_weekly",
    "next_monthly": "next_monthly", "monthly_t1": "next_monthly",
}


def _leg_index_abbrev(leg: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> str:
    """This leg's own index (falls back to the strategy default), abbreviated
    for filenames — NF/BNF/FNF/MCN. An unrecognized index falls back to its
    raw upper-cased symbol so a new index can never crash the labeller."""
    sym = str((leg or {}).get("index") or payload.get("index") or "NIFTY").strip().upper()
    return _INDEX_ABBREV.get(sym, sym)


def _abbrev_expiry_word(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_")
    return _EXPIRY_LABEL_ABBREV.get(key, raw)


# FILENAME_FORMAT.md only defines W/NW/M/NM — YEARLY is an EOD-only cadence its
# source repo doesn't have, so 'Y' is a local extension. Separate from
# `_EXPIRY_LABEL_ABBREV` above, which still feeds the master-summary display
# columns (Wkly/Mnly/...) — this map is filename-only.
_EXPIRY_FILENAME_CODE = {
    "weekly": "W", "monthly": "M", "yearly": "Y",
    "next_weekly": "NW", "next_monthly": "NM",
}


def _expiry_filename_code(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_")
    return _EXPIRY_FILENAME_CODE.get(key, raw.upper() if raw else "W")


def _leg_expiry_abbrev(leg: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> str:
    """This leg's own expiry cadence, as a short filename code (W/NW/M/NM/Y).
    Falls back to the combo-level expiry (`_expiry_label`, defined below) when
    the leg carries no override of its own — the common case, where every leg
    shares one expiry."""
    raw = str((leg or {}).get("expiry") or (leg or {}).get("expiry_type") or "").strip().lower()
    mapped = _LEG_EXPIRY_RAW.get(raw)
    if mapped:
        return _expiry_filename_code(mapped)
    return _expiry_filename_code(_expiry_label(payload))


def _fmt_num(v: Any) -> str:
    """Integers stay whole ('40'); else trimmed to <=2 decimals ('0.3')."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    if n == int(n):
        return str(int(n))
    return f"{round(n, 2):g}"


_PCT_BASIS = re.compile(r"pct|percent", re.IGNORECASE)


def _unit_of(basis: Any) -> str:
    """unit(basis): a basis containing 'pct'/'percent' -> 'pct', else 'pts'."""
    return "pct" if _PCT_BASIS.search(str(basis or "")) else "pts"


_REENTRY_MODE = {
    "RE_ASAP": "asap", "RE_ASAP_REV": "asaprev",
    "RE_MOMENTUM": "momentum", "RE_MOMENTUM_REV": "momentumrev",
    "LAZY_LEG": "lazy",
}


def _reentry_mode(mode: Any) -> str:
    return _REENTRY_MODE.get(str(mode or "").upper(), "asap")


def _side_code(leg: Optional[Dict[str, Any]]) -> str:
    pos = str((leg or {}).get("position") or "").lower()
    if pos in ("buy", "long"):
        return "B"
    if pos in ("sell", "short"):
        return "S"
    return pos[:1].upper() or "S"


def _strike_label(strike_selection: Optional[Dict[str, Any]], option_type: str = "CE") -> str:
    """Render a leg's strike spec per FILENAME_FORMAT.md's token table
    (initials + value): 'pctoA2_OTM' / 'ATM' / 'SW0.5' / 'CP50' / etc.

    Two things the spec doesn't cover, because this app has them and the
    doc's source repo doesn't: 'rel_leg' (gap-offset Iron Condor wing) and
    the EOD fixed-IV Delta strike mode.

    option_type is required for pct_of_atm because the direction field is
    stored as '+'/'-' (engine sign convention), not 'OTM'/'ITM'. The semantic
    meaning of '+'/'-' flips between calls and puts:

        CE '+' (above ATM) = OTM   |   CE '-' (below ATM) = ITM
        PE '-' (below ATM) = OTM   |   PE '+' (above ATM) = ITM
    """
    if not isinstance(strike_selection, dict):
        # `_join_strikes` passes None for a master-summary column when no leg of
        # this option type exists at all — distinct from a leg that HAS a
        # strike_selection but no explicit strike_type (which defaults to ATM
        # in the branch below). Filename building never hits this branch: every
        # leg in ce_legs/pe_legs always carries a real strike_selection dict.
        return "-"
    kind = (strike_selection.get("type") or "").lower()
    if kind in ("strike_type", "", None):
        return str(strike_selection.get("strike_type") or "ATM").upper().replace(" ", "")
    if kind == "closest_premium":
        return f"CP{_fmt_num(strike_selection.get('premium'))}"
    if kind == "premium_gte":
        return f"Pgte{_fmt_num(strike_selection.get('premium'))}"
    if kind == "premium_lte":
        return f"Plte{_fmt_num(strike_selection.get('premium'))}"
    if kind == "premium_range":
        return f"PR{_fmt_num(strike_selection.get('lower'))}_{_fmt_num(strike_selection.get('upper'))}"
    if kind == "synthetic_future":
        return "SF"
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
            return f"pctoA{_fmt_num(abs(val))}_{raw_dir.upper()}"

        # Engine sign convention: "+" means add val% above ATM, "-" means below.
        # Default (empty direction) matches the strike-picker default in
        # engine_rust.py:_compute_strike_for_leg_python which does
        #     raw = entry_spot - shift if direction == "-" else entry_spot + shift
        # i.e. anything that isn't "-" behaves as "+" (add shift above ATM).
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
        return f"pctoA{_fmt_num(abs_val)}_{direction}"
    if kind == "atm_straddle_prem_pct":
        try:
            val = float(strike_selection.get("value", 0))
        except (TypeError, ValueError):
            val = 0.0
        return f"ASP{_fmt_num(val)}pct"
    if kind == "straddle_width":
        try:
            mult = float(strike_selection.get("straddle_multiplier", 0.5))
        except (TypeError, ValueError):
            mult = 0.5
        # Engine's +/- is a raw offset applied identically to both legs; for the
        # filename translate it to ITM/OTM per option type (CE + = OTM, CE - =
        # ITM; PE reversed) so a +/- direction sweep doesn't collapse to one
        # token. Matches ResultsPanel.jsx's strikeCriteriaToken.
        is_call = option_type.upper().startswith("C")
        above_atm = str(strike_selection.get("straddle_direction") or "+").strip() != "-"
        if mult == 0.0:
            moneyness = "ATM"
        elif above_atm:
            moneyness = "OTM" if is_call else "ITM"
        else:
            moneyness = "ITM" if is_call else "OTM"
        return f"SW{_fmt_num(mult)}_{moneyness}"
    if kind == "rel_leg":
        # Gap-offset Iron Condor wing has no FILENAME_FORMAT.md equivalent —
        # extend RtL with a _G{gaps} suffix (mirrors _TV/_D there). Matches
        # the backtest export filename label (ResultsPanel.jsx).
        try:
            ref = int(strike_selection.get("ref_leg") or 1)
            off = float(strike_selection.get("offset") or 0)
        except (TypeError, ValueError):
            ref, off = 1, 0.0
        return f"RtL{ref}_G{_fmt_num(off)}"
    if kind == "rel_leg_premium":
        # Premium target derived from leg #ref_leg's entry fill — the spec's
        # default "All"/premium RtL basis, so no suffix. Matches the backtest
        # export filename label (ResultsPanel.jsx).
        try:
            ref = int(strike_selection.get("ref_leg") or 1)
        except (TypeError, ValueError):
            ref = 1
        return f"RtL{ref}"
    if kind == "delta":
        try:
            delta = float(strike_selection.get("delta") or 0.3)
        except (TypeError, ValueError):
            delta = 0.3
        return f"D{_fmt_num(delta * 100.0)}"
    if kind.startswith("time_value"):
        tv = strike_selection.get("time_value")
        if tv is None:
            tv = strike_selection.get("premium")
        try:
            tv = float(tv or 0)
        except (TypeError, ValueError):
            tv = 0.0
        base = {"time_value_gte": "TVgte", "time_value_lte": "TVlte"}.get(kind, "TV")
        side = str(strike_selection.get("moneyness") or "ATM").upper()
        try:
            cap = abs(float(strike_selection.get("tv_range_pct") or 0))
        except (TypeError, ValueError):
            cap = 0.0
        unit = _unit_of(strike_selection.get("tv_units"))
        token = f"{base}{_fmt_num(tv)}{unit}"
        if side != "ATM":
            token += f"_{side}"
        if cap:
            token += f"_rng{_fmt_num(cap)}pct"
        return token
    return kind.upper()


def _on_toggle_tokens(leg: Dict[str, Any]) -> List[str]:
    """TP / SL / TS / SLB / RoS / RoT, in FILENAME_FORMAT.md's order, only
    when active. Breakeven / Wait&Trade / Scale&Trade have no equivalent in
    this app (confirmed absent from the leg schema — intraday-only features),
    so they're skipped entirely; Volume Filter and Roll Forward are never
    named, per spec.
    """
    def _val(d: Dict[str, Any], key: str) -> float:
        try:
            return float(d.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    out: List[str] = []
    tp = leg.get("targetProfit") or {}
    if isinstance(tp, dict) and _val(tp, "value") > 0:
        out.append(f"TP{_fmt_num(tp.get('value'))}{_unit_of(tp.get('mode'))}")
    sl = leg.get("stopLoss") or {}
    if isinstance(sl, dict) and _val(sl, "value") > 0:
        out.append(f"SL{_fmt_num(sl.get('value'))}{_unit_of(sl.get('mode'))}")
    trail = leg.get("trailSL") or {}
    if isinstance(trail, dict) and trail:
        out.append(f"TS{_fmt_num(trail.get('trigger'))}_{_fmt_num(trail.get('move'))}{_unit_of(trail.get('mode'))}")
    slb = leg.get("slWithBuffer") or {}
    if isinstance(slb, dict) and _val(slb, "value") > 0:
        out.append(f"SLB{_fmt_num(slb.get('value'))}{_unit_of(slb.get('mode'))}_{_fmt_num(slb.get('buffer_pct'))}pct")
    ros = leg.get("reEntryOnSL") or {}
    if isinstance(ros, dict) and ros:
        out.append(f"RoS{_fmt_num(ros.get('count'))}{_reentry_mode(ros.get('mode'))}")
    rot = leg.get("reEntryOnTarget") or {}
    if isinstance(rot, dict) and rot:
        out.append(f"RoT{_fmt_num(rot.get('count'))}{_reentry_mode(rot.get('mode'))}")
    return out


def _adjustment_token(leg: Dict[str, Any]) -> Optional[str]:
    """Per-leg adjustment sub-trigger token. This app only has "Spot
    Adjustment" as an own-leg sub-trigger — no Spot-vs-Strike / Adjustment-
    Relative-to-Leg controls exist here (confirmed absent from the leg
    schema) — so leg['spot_adjustment']['enabled'] doubles as both the
    spec's adjOn parent and the sub-trigger.
    """
    sa = leg.get("spot_adjustment")
    if not isinstance(sa, dict) or not sa.get("enabled"):
        return None
    direction = str(sa.get("direction") or "").lower()
    if direction not in ("rise", "fall", "both"):
        direction = "rise"
    pct = sa.get("pct")
    if pct is None:
        pct = sa.get("value")
    return f"adj_{direction}_{_fmt_num(pct)}{_unit_of(sa.get('units'))}"


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
    # Per-leg expiry PREFERRED over payload.expiry_window generally, not just
    # for the next_* cases above. legs[*].expiry IS a swept axis
    # (param_expander.py), but expiry_window is a base-payload field that
    # does not change per-combo — so a sweep across Weekly/Monthly on the
    # leg had every combo's "Expiry" column/WOW-MOM axis read the SAME
    # base-payload value regardless of which cadence that combo actually
    # traded, collapsing genuinely distinct combos onto one grid cell with
    # no way to tell them apart. The leg's own value is what the trades
    # actually ran under; window is now only a fallback when no leg has one.
    _leg_candidates = {e for e in leg_expiries if e}
    window = (payload.get("expiry_window") or "").lower()
    if len(_leg_candidates) == 1:
        candidate = next(iter(_leg_candidates))
    else:
        candidate = window or (next(iter(_leg_candidates)) if _leg_candidates else "")
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


def _find_futures_legs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all futures legs (segment == FUTURES), in payload order. Futures
    carry no option_type, so the CE/PE labellers skip them entirely."""
    return [
        leg for leg in (payload.get("legs") or [])
        if isinstance(leg, dict) and str(leg.get("segment") or "").upper() == "FUTURES"
    ]


def _futures_segment(leg: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> str:
    """Render a futures leg for the combo label / filename: '{idx}_M_{B|S}_FUT'
    per FILENAME_FORMAT.md ('Futures / non-Options legs collapse to
    {expiry}_{B|S}_FUT'). Expiry is hardcoded to 'M' — futures only ever trade
    a monthly contract, never weekly/yearly. idx is only shown when this leg's
    index differs from the strategy default (multi-index feature the spec's
    source repo doesn't have — see _leg_segment below for the same rule).
    Lots is likewise not in the spec but kept so a lots-only sweep still gets
    a distinct filename. Empty when leg is missing."""
    if not isinstance(leg, dict):
        return ""
    strat_idx = str(payload.get("index") or "NIFTY").strip().upper()
    leg_idx = str(leg.get("index") or strat_idx).strip().upper()
    parts = []
    if leg_idx != strat_idx:
        parts.append(_leg_index_abbrev(leg, payload))
    parts += ["M", _side_code(leg), "FUT"]
    try:
        _lots = int(round(float(leg.get("lots") or 1)))
    except (TypeError, ValueError):
        _lots = 1
    if _lots != 1:
        parts.append(f"{_lots}lt")
    return "_".join(parts)


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
        sym = _MIDCAP_SYM_ABBREV.get(sym, sym)
        # "Hypothetical_Future" pricing mode always trades a monthly contract
        # (same reasoning as a real futures leg — never weekly/yearly), so it
        # carries "Mnly" too. "Spot" mode has no expiry concept at all.
        mode_lbl = "Hypothetical_Future_Mnly" if mode == "hypothetical" else "Spot"
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
    adj = _per_leg_spot_adjustment_map(payload)
    return "_".join(f"L{i}{word}" for i, word in sorted(adj.items()))


def _per_leg_spot_adjustment_map(payload: Dict[str, Any]) -> Dict[int, str]:
    """{leg_number: 'RiseBy1%'} for every leg carrying its OWN enabled adjustment.

    Split out of `_per_leg_spot_adjustment_label` so the filename (which writes
    each leg's adjustment beside that leg) and the master-summary column (which
    collects them into one cell) derive from ONE rule. Wording is deliberately
    identical to `_spot_adjustment_label`.
    """
    out: Dict[int, str] = {}
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
        out[_i] = word
    return out


def _capital_sizing_label(payload: Dict[str, Any]) -> str:
    """Filename token for capital-weighted sizing, or '' when unused.

    e.g. 'CAP700000000_L1-70_L2-30_V1'. Includes each sized leg's allocation so
    two different allocation configs file to different folders. Empty when the
    Sizing feature is off ⇒ existing combo labels stay byte-identical.
    """
    cap = payload.get("capital_sizing")
    if not isinstance(cap, dict) or not cap.get("enabled"):
        return ""
    try:
        total = float(cap.get("total_capital") or 0.0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    parts = [f"CAP{_fmt_num(total)}"]
    for _i, _leg in enumerate((payload.get("legs") or []), start=1):
        if not isinstance(_leg, dict):
            continue
        _a = _leg.get("capital_alloc_pct")
        if _a in (None, ""):
            continue
        try:
            parts.append(f"L{_i}-{_fmt_num(float(_a))}")
        except (TypeError, ValueError):
            continue
    parts.append("V2" if str(cap.get("version") or "v1").lower() == "v2" else "V1")
    return "_".join(parts)


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
        combo_label         e.g. "NF_CE_3.0%_OTM_Wkly_Sell_NF_PE_0.5%_ITM_Wkly_Sell_NoAdjustment_T-1_To_T-1"
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
        """Leg block per FILENAME_FORMAT.md:
        '{expiry}_{opt}_{B|S}_{strikeCriteria}[_{onToggle}...][_{adjustment}...]'.

        idx prefix and lots suffix aren't in the spec (its source repo is
        single-index / doesn't need lots for dedup) — added only when this
        leg's index differs from the strategy default / lots != 1, so a
        normal single-index 1-lot strategy stays byte-identical to the spec's
        worked examples. The adjustment token is now INLINE per leg (matching
        the spec's own per-leg placement) rather than gated on whether any
        OTHER leg in the strategy also uses per-leg adjustment.
        """
        strike = _strike_label(leg.get("strike_selection"), otype)
        leg_expiry = _leg_expiry_abbrev(leg, payload)
        side = _side_code(leg)
        strat_idx = str(payload.get("index") or "NIFTY").strip().upper()
        leg_idx = str(leg.get("index") or strat_idx).strip().upper()
        seg_parts: List[str] = []
        if leg_idx != strat_idx:
            seg_parts.append(_leg_index_abbrev(leg, payload))
        seg_parts += [leg_expiry, otype, side, strike]
        try:
            _lots = int(round(float(leg.get("lots") or 1)))
        except (TypeError, ValueError):
            _lots = 1
        if _lots != 1:
            seg_parts.append(f"{_lots}lt")
        seg_parts += _on_toggle_tokens(leg)
        adj_tok = _adjustment_token(leg)
        if adj_tok:
            seg_parts.append(adj_tok)
        return "_".join(seg_parts), strike

    spot_adj = _spot_adjustment_label(payload)
    expiry = _expiry_label(payload)
    shift = _shift_label(payload)

    # Leading {SYMBOL} token — the full instrument name (not abbreviated),
    # matching ResultsPanel.jsx's buildExcelFileName so an optim combo folder
    # and a standalone backtest filename read identically.
    parts = [str(payload.get("index") or "NIFTY").strip().upper()]
    call_strikes: List[tuple] = []
    for _i, _leg in ce_legs:
        _seg, _st = _leg_segment(_leg, "CE")
        parts.append(_seg)
        call_strikes.append((_i, _st))
    put_strikes: List[tuple] = []
    for _i, _leg in pe_legs:
        _seg, _st = _leg_segment(_leg, "PE")
        parts.append(_seg)
        put_strikes.append((_i, _st))

    # Futures legs (no strike) — appended after the option legs, e.g. 'NF_Fut_Sell'.
    for _leg in _find_futures_legs(payload):
        _fseg = _futures_segment(_leg, payload)
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
    # NOT appended here any more — each leg carries its own adjustment inline
    # (see _with_adj). Still computed for the master-summary column below, which
    # wants them collected in one cell.
    per_leg_adj_seg = _per_leg_spot_adjustment_label(payload)
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
    # Expiry is no longer a single combo-level token here — each leg already
    # carries its OWN expiry inline (see _leg_segment), which is required for
    # same-index mixed-expiry strategies (e.g. a Weekly PE + Yearly PE) where a
    # single global expiry token would be wrong for at least one leg. The
    # combo-level `expiry` value is still returned below for the master-summary
    # column, which wants one column value regardless of per-leg mixing.
    # Capital-weighted sizing token (empty ⇒ no change to existing labels).
    _cap_tok = _capital_sizing_label(payload)
    if _cap_tok:
        parts.append(_cap_tok)
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

    # ── LEG-WISE master-summary columns (additive) ───────────────────────────
    # The three columns above pack every leg into one cell: two same-type legs
    # collapse to "L1_ATM/L3_OTM2", every adjustment concatenates into
    # "L1RiseBy1%_L2MoveBy1%_L3FallsBy1000pts", and a futures leg disappears
    # entirely. None of that is sortable or filterable per leg, and it does not
    # say which index a leg belongs to. So emit ONE BLOCK PER LEG as well, using
    # the same abbreviations the combo_label already uses (NF/BNF/FNF/MCN,
    # Wkly/Mnly/Yrly) so the two namings can't drift.
    #
    # Header carries the leg's identity — safe because option_type / position /
    # index / expiry are NEVER swept (only strike_selection.* and
    # spot_adjustment.*), so it is constant for every combo of a sweep.
    # Only legs that EXIST get a block: a 2-leg sweep emits no L3 columns.
    _leg_adj_map = _per_leg_spot_adjustment_map(payload)
    _has_global_adj = bool(payload.get("spot_adjustment_enabled"))
    leg_cols: List[Dict[str, str]] = []
    for _i, _leg in enumerate(payload.get("legs") or [], 1):
        if not isinstance(_leg, dict):
            continue
        _seg_raw = str(_leg.get("segment") or "OPTIONS").upper()
        _is_fut = _seg_raw in ("FUTURES", "FUTURE", "FUT")
        _ot = "FUT" if _is_fut else str(_leg.get("option_type") or "").upper()
        _idx = _leg_index_abbrev(_leg, payload)
        _exp = _EXPIRY_LABEL_ABBREV.get(
            str(_leg.get("expiry") or payload.get("expiry_type") or "").strip().lower(),
            str(_leg.get("expiry") or "").title() or "-")
        _side = str(_leg.get("position") or "").title()
        _hdr = " ".join(x for x in (f"L{_i}", _idx, _ot, _exp, _side) if x)
        # Header WITHOUT the expiry token — used by summary_workbook when a leg's
        # expiry is itself the swept axis, so a single static column header does
        # not falsely assert one cadence (e.g. "L1 NF CE Wkly Sell") over rows
        # that actually swept Monthly/Next-Weekly/Next-Monthly for that leg.
        _hdr_stable = " ".join(x for x in (f"L{_i}", _idx, _ot, _side) if x)
        # Futures have no strike; an em dash reads better than an empty cell.
        _strike = "-" if _is_fut else _strike_label(_leg.get("strike_selection"),
                                                    _ot if _ot in ("CE", "PE") else "CE")
        _own = _leg_adj_map.get(_i)
        # "(strategy)" distinguishes "inherits the payload-level knob" from "none",
        # which the packed column cannot express.
        _adj_val = _own if _own else ("(strategy)" if _has_global_adj else "-")
        leg_cols.append({"hdr": _hdr, "hdr_stable": _hdr_stable, "strike": _strike, "adj": _adj_val})

    # Strategy-wide adjustments live in their own column, NOT in a leg block —
    # they apply to every leg, so numbering them would be a lie.
    _overall = []
    if _has_global_adj:
        _overall.append(spot_adj)
    if midcp_adj_seg:
        _overall.append(midcp_adj_seg)
    overall_adjustment = "_".join(_overall) if _overall else "NoAdjustment"

    return {
        "expiry": expiry,
        "shifting": shift,
        "put_strike_label": put_strike,
        "call_strike_label": call_strike,
        "spot_adjustment": spot_adjustment_col,
        "combo_label": combo_label,
        # additive leg-wise view — see block above
        "leg_cols": leg_cols,
        "overall_adjustment": overall_adjustment,
        # Midcap is an OVERLAY (payload["midcap_legs"]), not an L1/L2/L3 leg, so it
        # gets its own pair of columns rather than a numbered block.
        "midcap_leg": midcap_seg or "",
        "midcap_adj": midcap_adj_seg or "",
    }


_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._%+\-]")


def safe_filename(label: str) -> str:
    """Strip characters illegal on Windows/macOS/Linux filesystems."""
    return _FILENAME_BAD.sub("_", label)
