"""
services/trade_anchor.py

Canonical trade-level values derived from a trade's per-leg rows.

WHY THIS EXISTS
---------------
Leg ORDER in the UI must never change a statistic. Several call sites used to
read trade-level fields off "whichever row came first":

  * services/algotest_job.py      groupby("Trade").agg({"Entry Spot": "first", ...})
  * services/algotest_job.py      cumcount() == 0  -> the NAV/%P&L denominator
  * services/optimizer/excel_builder.py::_project_rows_for_midcap  -> legs[0]
  * frontend .../StrategyBuilder.jsx::projectTradesForOverlay      -> sum over rows

"First" is the user's configured leg position, so Entry Spot -- and therefore
% P&L, the base-100 NAV, Max DD, CAGR and the whole Midcap overlay window --
moved when the same legs were reordered in the builder.

THE ANCHOR RULE
---------------
The anchor row is chosen by DATA, not by position:

    anchor = the leg row with the LATEST Entry Date,
             ties broken by the LOWEST Leg number.

* Legs that enter together (the overwhelming majority of strategies) all share
  one Entry Date, so the anchor reduces to Leg 1 and every such run is
  BYTE-IDENTICAL to the previous behaviour.
* When a long-dated leg is CARRIED -- a YEARLY leg holding its December
  contract while a weekly leg re-enters every cycle -- that leg's Entry Date is
  an older anchor that does not describe THIS trade's window. The latest entry
  does. Under the old rule the answer flipped depending on whether the user put
  the weekly or the yearly leg first.
* The tie-break on the LOWEST Leg number is only reached when two rows share an
  Entry Date, in which case they also share Entry Spot, so it cannot itself
  introduce an order dependency -- it exists purely to make the pick total.

Net P&L is NOT read off the anchor. `simulate.rs:1794-1806` writes the trade
TOTAL onto the lowest-leg_id row and leaves per-leg values on the others, so
summing the column double-counts. The trade total is recomputed here from the
per-leg CE/PE/FUT columns, matching the convention already documented at
services/algotest_job.py:446-450.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "anchor_row",
    "exit_anchor_row",
    "trade_net_pnl",
    "trade_entry_spot",
    "trade_pct_pnl",
    "is_reentry_row",
]

# Columns holding genuinely PER-LEG P&L (engine_rust.py:3740-3745 recomputes
# each from that row's own entry/exit prices x that row's own lots).
_PER_LEG_PNL_COLS = ("CE P&L", "PE P&L", "FUT P&L")

_DATE_KEYS = ("Entry Date", "entry_date")
_LEG_KEYS = ("Leg", "leg")


def _num(value: Any) -> Optional[float]:
    """Lenient float(): returns None for blanks/None/unparseable, never raises."""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN -> None


def _first_present(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _date_sort_key(value: Any) -> str:
    """Sortable string for a date that may be a datetime, an ISO string or
    dd-mm-yyyy. Unparseable/blank sorts FIRST so a row with no entry date can
    never win the `latest entry` contest and hijack the anchor."""
    if value is None or value == "":
        return ""
    # datetime / pandas Timestamp / date
    for attr in ("strftime",):
        if hasattr(value, attr):
            try:
                return value.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                return ""
    text = str(value).strip()
    if not text:
        return ""
    sep = "/" if "/" in text else "-"
    parts = text.split(" ")[0].split(sep)
    if len(parts) == 3:
        if len(parts[0]) == 4:                      # yyyy-mm-dd
            y, m, d = parts[0], parts[1], parts[2]
        else:                                       # dd-mm-yyyy
            d, m, y = parts[0], parts[1], parts[2]
        try:
            return "%04d-%02d-%02d" % (int(y), int(m), int(d))
        except (TypeError, ValueError):
            return ""
    return ""


def is_reentry_row(row: Dict[str, Any]) -> bool:
    """A re-entry / lazy-leg sub-row rather than one of the trade's main legs.
    Mirrors the predicate already used by excel_builder._project_rows_for_midcap
    and ResultsPanel.groupedTrades."""
    for key in ("ReEntryIndex", "ReEntryTrigger", "ReEntryMode"):
        if row.get(key):
            return True
    # Lazy-leg rows carry a fractional Leg ("2.1") or a dotted Index.
    if "." in str(row.get("Leg") or ""):
        return True
    if "." in str(row.get("Index") or ""):
        return True
    return False


def anchor_row(
    rows: Sequence[Dict[str, Any]],
    *,
    include_reentries: bool = False,
) -> Optional[Dict[str, Any]]:
    """The row whose Entry Date defines this trade's window.

    LATEST Entry Date wins; ties broken by the LOWEST Leg number. Returns None
    for an empty input. See the module docstring for the rationale.

    `include_reentries=False` (default) restricts the choice to the trade's main
    leg rows; a re-entry's own entry date must not redefine the parent trade's
    window. If every row is a re-entry row the full set is used rather than
    returning None, so a trade can never lose its anchor entirely.
    """
    if not rows:
        return None
    candidates: List[Dict[str, Any]] = list(rows)
    if not include_reentries:
        mains = [r for r in candidates if not is_reentry_row(r)]
        if mains:
            candidates = mains

    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[str, float]] = None
    for row in candidates:
        date_key = _date_sort_key(_first_present(row, _DATE_KEYS))
        leg_no = _num(_first_present(row, _LEG_KEYS))
        # Maximise (date, -leg) => latest date, then lowest leg number.
        key = (date_key, -(leg_no if leg_no is not None else 0.0))
        if best_key is None or key > best_key:
            best, best_key = row, key
    return best


def exit_anchor_row(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The row that fixes this trade's Exit Date / Exit Reason.

    Among the rows GIVEN, the one with the LATEST Exit Date wins; ties broken
    by the LOWEST Leg number (same tie-break shape as anchor_row(), but on
    exit rather than entry). Returns None for an empty input.

    SPLIT OF RESPONSIBILITY -- read before reusing this: this function does
    NOT know about LEG_FILTER_END. A leg truncated by its own per-leg filter
    file exits before the trade actually ends, so a row like that must not be
    allowed to define the trade's exit. It is the CALLER's job to drop any row
    whose Exit Reason contains "LEG_FILTER_END" before calling this helper --
    and, if every row of a trade was truncated (so the filtered set is empty),
    to fall back to passing the full, unfiltered row set so the trade never
    ends up with no Exit Date at all. See the exclusion + fallback in
    services/algotest_job.py's per-trade aggregation for the reference caller.
    """
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[str, float]] = None
    for row in rows or []:
        date_key = _date_sort_key(row.get("Exit Date"))
        leg_no = _num(row.get("Leg"))
        # Maximise (date, -leg) => latest exit date, then lowest leg number.
        key = (date_key, -(leg_no if leg_no is not None else 0.0))
        if best_key is None or key > best_key:
            best, best_key = row, key
    return best


def trade_net_pnl(rows: Iterable[Dict[str, Any]]) -> float:
    """Trade total = SUM of the per-leg CE/PE/FUT P&L columns.

    NOT the `Net P&L` column: simulate.rs puts the trade total on the lowest
    leg_id row and per-leg values on the rest, so summing that column
    double-counts (see services/algotest_job.py:446-450).
    """
    total = 0.0
    for row in rows:
        for col in _PER_LEG_PNL_COLS:
            val = _num(row.get(col))
            if val is not None:
                total += val
    return total


def trade_entry_spot(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """The % P&L / NAV denominator: the anchor row's Entry Spot."""
    row = anchor_row(rows)
    if row is None:
        return None
    spot = _num(row.get("Entry Spot"))
    return spot if spot else None


def trade_pct_pnl(rows: Sequence[Dict[str, Any]], ndigits: int = 4) -> float:
    """Trade return % = trade total P&L / the anchor row's Entry Spot x 100."""
    spot = trade_entry_spot(rows)
    if not spot:
        return 0.0
    return round(trade_net_pnl(rows) / spot * 100.0, ndigits)
