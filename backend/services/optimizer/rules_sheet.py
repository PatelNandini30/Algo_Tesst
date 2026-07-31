"""
build_rules_sheet — Python port of frontend/src/utils/backtestRulesSheet.js.

Produces the SAME typed rows (title / section / kv / spacer) that the backtest's
`buildRulesSheet` emits, so an optimizer per-combo tradesheet can carry the exact
same leg-wise "Rules" first sheet as a direct backtest — rendered by the shared
`excel_builder._write_rules_sheet`.

NOTE (maintenance): this mirrors the JS builder. The optimizer builds combo
tradesheets server-side (no JS runtime), so the logic is duplicated here. Keep the
two in sync when either changes — the row shape and labels must match exactly.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_EXPIRY_LABELS = {
    "WEEKLY": "Weekly", "MONTHLY": "Monthly",
    "NEXT_WEEKLY": "Next Weekly", "NEXT_MONTHLY": "Next Monthly",
    "YEARLY": "Yearly (December)",
}
_SA_DIR = {"rise": "Rise", "fall": "Fall", "both": "Rise or Fall"}


def _num(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "" if v is None else str(v)
    if n != n or n in (float("inf"), float("-inf")):
        return str(v)
    if n == int(n):
        return str(int(n))
    return ("%.4f" % n).rstrip("0").rstrip(".")


def _side(pos: Any) -> str:
    s = str(pos or "").lower()
    return "Sell" if s in ("sell", "short") else "Buy"


def _unit(mode: Any) -> str:
    m = str(mode or "").upper()
    return "%" if ("PERCENT" in m or "PCT" in m or m == "%") else " pts"


def _expiry(v: Any) -> str:
    return _EXPIRY_LABELS.get(str(v or "").upper()) or v or "—"


def _strike_label(ss: Optional[Dict[str, Any]], opt_type: Any = "") -> str:
    ss = ss or {}
    t = str(ss.get("type") or "").upper()
    if t == "PCT_OF_ATM":
        d = "-" if ss.get("direction") == "-" else "+"
        return f"% of ATM: {d}{_num(ss.get('value'))}%"
    if t == "ATM_STRADDLE_PREM_PCT":
        return f"ATM Straddle Premium: {_num(ss.get('value'))}%"
    if t == "STRADDLE_WIDTH":
        d = str(ss.get("straddle_direction") or "+")
        mult = ss.get("straddle_multiplier")
        return f"Straddle Width: {d}{_num(mult if mult is not None else 0.5)}x"
    if t == "REL_LEG":
        ref = ss.get("ref_leg")
        off = ss.get("offset")
        return (f"Relative to Leg {_num(ref if ref is not None else 1)}, "
                f"offset {_num(off if off is not None else 0)} gap(s)")
    if t in ("PREMIUM", "CLOSEST_PREMIUM"):
        if ss.get("premium") not in (None, ""):
            return f"Closest Premium: {_num(ss.get('premium'))}"
        if ss.get("lower") is not None or ss.get("upper") is not None:
            return f"Premium Range: {_num(ss.get('lower'))}–{_num(ss.get('upper'))}"
        return "Premium"
    return str(ss.get("strike_type") or "ATM")


def _kv(rows: List, label: str, value: Any) -> None:
    rows.append(["kv", label, "—" if value in (None, "") else str(value)])


def _leg_section(rows: List, leg: Dict[str, Any], n: int) -> None:
    seg = str(leg.get("segment") or "OPTIONS").upper()
    side = _side(leg.get("position"))
    head = f"FUT {side}" if seg == "FUTURES" else f"{str(leg.get('option_type') or '').upper()} {side}"
    rows.append(["section", f"Leg {n} — {head} ({'Futures' if seg == 'FUTURES' else 'Options'})"])

    _kv(rows, "Position", side)
    _kv(rows, "Lots", leg.get("lots") if leg.get("lots") is not None else 1)
    if leg.get("index"):
        _kv(rows, "Index", leg.get("index"))
    _kv(rows, "Expiry", _expiry(leg.get("expiry")))

    if seg == "FUTURES":
        _kv(rows, "Exit Mode", leg.get("fut_exit_mode"))
        if "N_DAY" in str(leg.get("fut_exit_mode") or "").upper() or leg.get("fut_n_days") is not None:
            _kv(rows, "Exit After N Days", leg.get("fut_n_days"))
        _kv(rows, "Apply Filter", "Yes" if leg.get("fut_with_filter") is not False else "No")
        _kv(rows, "Apply Overall SL", "Yes" if leg.get("fut_sl_override") is not False else "No")
        _kv(rows, "Apply Overall Target", "Yes" if leg.get("fut_target_override") is not False else "No")
        _kv(rows, "Apply Spot Adjustment", "Yes" if leg.get("fut_with_spot_adj") is not False else "No")
    else:
        _kv(rows, "Option Type", str(leg.get("option_type") or "").upper())
        _kv(rows, "Strike Selection", _strike_label(leg.get("strike_selection"), leg.get("option_type")))
        if leg.get("strike_interval"):
            _kv(rows, "Strike Gap", leg.get("strike_interval"))
        if leg.get("rollover_strike_mode"):
            _kv(rows, "Rollover Strike Mode", "Fixed" if leg.get("rollover_strike_mode") == "fixed" else "Fresh")

    # Per-leg spot adjustment (own threshold), else defers to strategy-level.
    sa = leg.get("spot_adjustment") or {}
    try:
        _sa_pct = float(sa.get("pct") or 0)
    except (TypeError, ValueError):
        _sa_pct = 0.0
    if sa.get("enabled") and _sa_pct > 0:
        d = {"fall": "Fall", "both": "Rise or Fall"}.get(sa.get("direction"), "Rise")
        u = " pts" if sa.get("units") == "points" else "%"
        _kv(rows, "Spot Adjustment", f"Yes ({d} {_num(sa.get('pct'))}{u})")
    else:
        _kv(rows, "Spot Adjustment", "Uses strategy-level setting")

    try:
        _slp = float(leg.get("slippage_pct") or 0)
    except (TypeError, ValueError):
        _slp = 0.0
    _kv(rows, "Slippage", f"Yes ({_num(_slp)}%)" if _slp > 0 else "No")

    if leg.get("stopLoss"):
        sl = leg["stopLoss"]
        _kv(rows, "Stop Loss", f"{_num(sl.get('value'))}{_unit(sl.get('mode'))}")
    if leg.get("slWithBuffer"):
        b = leg["slWithBuffer"]
        _kv(rows, "SL with Buffer",
            f"{_num(b.get('value'))}{_unit(b.get('mode'))} (buffer {_num(b.get('buffer_pct'))}%)")
    if leg.get("trailSL"):
        tr = leg["trailSL"]
        _kv(rows, "Trailing SL",
            f"trigger {_num(tr.get('trigger'))}, move {_num(tr.get('move'))}{_unit(tr.get('mode'))}")
    if leg.get("targetProfit"):
        tp = leg["targetProfit"]
        _kv(rows, "Target Profit", f"{_num(tp.get('value'))}{_unit(tp.get('mode'))}")
    if leg.get("reEntryOnSL"):
        re = leg["reEntryOnSL"]
        _kv(rows, "Re-entry on SL", f"{re.get('mode')} × {_num(re.get('count'))}")
    if leg.get("reEntryOnTarget"):
        re = leg["reEntryOnTarget"]
        _kv(rows, "Re-entry on Target", f"{re.get('mode')} × {_num(re.get('count'))}")
    if leg.get("simpleMomentum"):
        sm = leg["simpleMomentum"]
        _kv(rows, "Simple Momentum", f"{sm.get('mode')}: {_num(sm.get('value'))}")


def _midcap_leg_section(rows: List, leg: Dict[str, Any], n: int, payload: Dict[str, Any]) -> None:
    side = _side(leg.get("position"))
    sym = leg.get("symbol") or "NIFTYMIDCAP100"
    rows.append(["section", f"Leg {n} — {sym} {side} (Midcap Overlay)"])
    _kv(rows, "Position", side)
    _kv(rows, "Lots", leg.get("lots") if leg.get("lots") is not None else 1)
    _kv(rows, "Pricing Mode",
        "Hypothetical Future" if leg.get("midcap_mode") == "hypothetical" else (leg.get("midcap_mode") or "—"))
    if leg.get("cost_pct_per_month") is not None:
        _kv(rows, "Cost % / month", _num(leg.get("cost_pct_per_month")))
    mcsa = payload.get("midcap_spot_adjustment")
    if isinstance(mcsa, dict) and mcsa.get("enabled"):
        u = "%" if mcsa.get("units") == "percent" else " pts"
        d = _SA_DIR.get(mcsa.get("direction"), mcsa.get("direction") or "")
        _kv(rows, "Midcap Spot Adjustment", f"Yes ({d} {_num(mcsa.get('pct'))}{u})".replace("  ", " ").strip())
    try:
        _slp = float(leg.get("slippage_pct") or 0)
    except (TypeError, ValueError):
        _slp = 0.0
    _kv(rows, "Slippage", f"Yes ({_num(_slp)}%)" if _slp > 0 else "No")


def build_rules_sheet(payload: Optional[Dict[str, Any]], filter_name: Optional[str] = None) -> Optional[List]:
    """Return the typed rows for the leg-wise "Rules" first sheet (or None)."""
    if not payload:
        return None
    rows: List = [["title", "STRATEGY RULES"], ["section", "Strategy"]]
    _kv(rows, "Index", payload.get("index") or payload.get("underlying"))
    if payload.get("date_from") or payload.get("date_to"):
        _kv(rows, "Backtest Date Range", f"{payload.get('date_from') or ''} → {payload.get('date_to') or ''}")
    _kv(rows, "Expiry", _expiry(payload.get("expiry_type")))
    _kv(rows, "Entry / Exit DTE", f"T-{payload.get('entry_dte') or 0} to T-{payload.get('exit_dte') or 0}")
    if payload.get("square_off_mode"):
        _kv(rows, "Square-off Mode", payload.get("square_off_mode"))

    is_yearly = str(payload.get("expiry_type") or "").upper() == "YEARLY"
    if is_yearly:
        _kv(rows, "Roll Cadence", "Weekly" if payload.get("rollover_cadence") == "weekly" else "Monthly")
        try:
            _n = int(payload.get("yearly_exit_months_before") or 0)
        except (TypeError, ValueError):
            _n = 0
        _kv(rows, "Yearly Exit",
            "T-0 (hold to long-dated expiry)" if _n == 0
            else f"T-{_n} ({_n} month{'' if _n == 1 else 's'} before the long-dated expiry)")
        _mon = {"03": "March", "06": "June", "09": "September", "12": "December"}
        _rm = sorted(set(["12"] + [str(m) for m in (payload.get("yearly_roll_months") or ["12"])]))
        _kv(rows, "Roll Through", "December only" if len(_rm) == 1 else " + ".join(_mon.get(m, m) for m in _rm))

    if is_yearly and payload.get("rollover_toggle") and not payload.get("no_rollover"):
        _kv(rows, "Rollover",
            f"Yes (roll {'weekly' if payload.get('rollover_cadence') == 'weekly' else 'monthly'} within the December contract)")
    elif payload.get("rollover_toggle") and not payload.get("no_rollover"):
        _kv(rows, "Rollover", f"Yes (min {payload.get('rollover_min_days_to_expiry') or 0} days to expiry)")
    elif payload.get("no_rollover"):
        _kv(rows, "Rollover", f"No Rollover (min {payload.get('no_rollover_min_days') or 0} days)")
    else:
        _kv(rows, "Rollover", "None")

    if payload.get("spot_adjustment_enabled"):
        u = "%" if payload.get("spot_adjustment_units") == "percent" else " pts"
        d = _SA_DIR.get(payload.get("spot_adjustment_direction"), payload.get("spot_adjustment_direction") or "")
        _kv(rows, "Spot Adjustment", f"Yes ({d} {_num(payload.get('spot_adjustment_pct'))}{u})".replace("  ", " ").strip())
    else:
        _kv(rows, "Spot Adjustment", "No")

    _mnsa = payload.get("midcpnifty_spot_adjustment")
    _has_midcp = any(
        isinstance(l, dict) and l.get("segment") != "midcap100"
        and str(l.get("index") or payload.get("index") or "").upper() == "MIDCPNIFTY"
        for l in (payload.get("legs") or [])
    )
    if _has_midcp and isinstance(_mnsa, dict) and _mnsa.get("enabled"):
        u = "%" if _mnsa.get("units") == "percent" else " pts"
        d = _SA_DIR.get(_mnsa.get("direction"), _mnsa.get("direction") or "")
        _kv(rows, "MIDCPNIFTY Spot Adjustment", f"Yes ({d} {_num(_mnsa.get('pct'))}{u})".replace("  ", " ").strip())

    if payload.get("buffer_strike_enabled"):
        u = "%" if payload.get("buffer_strike_unit") == "percent" else " pts"
        _kv(rows, "Buffer Strike",
            f"Yes ({_num(payload.get('buffer_strike_value'))}{u}, apply to {payload.get('buffer_strike_apply_to') or 'both'})")
    try:
        _shift = float(payload.get("strike_shift_max_steps") or 0)
    except (TypeError, ValueError):
        _shift = 0.0
    if _shift > 0:
        _kv(rows, "Strike Shift Fallback", f"{payload.get('strike_shift_max_steps')} step(s)")
    if payload.get("overall_sl_type"):
        _kv(rows, "Overall Stop Loss", f"{payload.get('overall_sl_type')}: {_num(payload.get('overall_sl_value'))}")
    if payload.get("overall_target_type"):
        _kv(rows, "Overall Target", f"{payload.get('overall_target_type')}: {_num(payload.get('overall_target_value'))}")
    _kv(rows, "Cost / Charges", "Enabled" if payload.get("charges_enabled") else "Disabled")
    _kv(rows, "Filter", filter_name or "No Filter")

    rows.append(["spacer"])

    legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
    for i, leg in enumerate(legs):
        if isinstance(leg, dict):
            _leg_section(rows, leg, i + 1)
    mc_legs = payload.get("midcap_legs") if isinstance(payload.get("midcap_legs"), list) else []
    for i, leg in enumerate(mc_legs):
        if isinstance(leg, dict):
            _midcap_leg_section(rows, leg, len(legs) + i + 1, payload)

    return rows
