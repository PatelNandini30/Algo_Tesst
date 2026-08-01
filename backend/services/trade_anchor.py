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

import pandas as pd

__all__ = [
    "anchor_row",
    "exit_anchor_row",
    "anchor_sorted",
    "apply_exit_anchor_exclusion",
    "trade_net_pnl",
    "trade_entry_spot",
    "trade_pct_pnl",
    "is_reentry_row",
    "spot_first_non_empty",
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


def anchor_sorted(trades_df: "pd.DataFrame") -> "pd.DataFrame":
    """Re-order the per-leg rows WITHIN each trade so the trade's ANCHOR leg
    leads, making every downstream `.agg("first")` order-invariant.

    Ordering key: (Trade, is_re-entry ASC, Entry Date DESC, Leg ASC) — main legs
    before re-entry sub-rows, then the LATEST leg entry, ties broken by the
    lowest Leg number. This is the pandas expression of
    anchor_row() in this module; see this module's header for why "latest entry"
    is the right anchor (a CARRIED yearly leg keeps an older entry date than the
    weekly leg that re-enters each cycle).

    Rows are NOT mutated and the caller's frame is left untouched — the returned
    frame is a re-sorted copy used only to feed the groupby. Blank/NaT entry
    dates sort last, so a row with no entry date can never become the anchor.
    """
    if trades_df is None or "Trade" not in getattr(trades_df, "columns", ()):
        return trades_df
    if "Entry Date" not in trades_df.columns:
        return trades_df

    out = trades_df.copy()
    # Re-entry / lazy-leg sub-rows must never define the parent trade's window.
    _re = pd.Series(0, index=out.index, dtype="int8")
    for _c in ("ReEntryIndex", "ReEntryTrigger", "ReEntryMode"):
        if _c in out.columns:
            _col = out[_c].astype(str).str.strip()
            _re = _re | (_col.notna() & ~_col.isin(("", "0", "nan", "None", "NaT"))).astype("int8")
    out["_ta_re"] = _re
    out["_ta_leg"] = (
        pd.to_numeric(out["Leg"], errors="coerce").fillna(0)
        if "Leg" in out.columns else 0
    )
    out = out.sort_values(
        ["Trade", "_ta_re", "Entry Date", "_ta_leg"],
        ascending=[True, True, False, True],
        kind="stable",
        na_position="last",
    )
    return out.drop(columns=["_ta_re", "_ta_leg"])


def apply_exit_anchor_exclusion(aggregated: "pd.DataFrame", sorted_df: "pd.DataFrame") -> "pd.DataFrame":
    """Overwrite `aggregated`'s Exit Date / Exit Reason columns so a leg
    truncated by its own per-leg filter file (LEG_FILTER_END) can't hijack
    the trade's reported exit.

    SHARED by every site that aggregates a trade-level Exit Date/Exit Reason
    from per-leg rows via groupby("Trade").agg({"Exit Date": "first", ...}) --
    currently services/algotest_job.py's `_try_rust_engine` (backtest path)
    and services/optimizer/runner.py's per-combo tradesheet builder (optimizer
    path). The project's hard rule is that the optimizer's per-combo
    tradesheet must equal a direct backtest exactly, so both callers must
    apply this identical correction rather than duplicating the logic.

    `aggregated` is the frame produced by the caller's own existing
    `groupby("Trade").agg({..., "Exit Date": "first", "Exit Reason": "first",
    ...})` call -- untouched by this function except for those two columns, so
    every other column (Entry Date, Entry Spot, Exit Spot, Spot P&L, CE/PE/FUT
    P&L) keeps the caller's exact pre-existing aggregation. `sorted_df` is the
    per-leg row frame that fed that groupby (already in whatever order the
    caller's own anchor rule expects -- e.g. `_anchor_sorted(...)` in
    algotest_job.py).

    For Exit Date/Exit Reason specifically: drop any row whose Exit Reason
    contains "LEG_FILTER_END", then re-apply the SAME rule the caller already
    used ("first" on `sorted_df`'s row order) to the rows that remain. If a
    trade has no remaining rows (every leg truncated), fall back to that
    trade's full row set so the value is never null.

    When no row anywhere is tagged LEG_FILTER_END -- true for every run
    before this feature and every run that doesn't use it -- the exclusion
    removes nothing, so this reproduces the caller's original "first" value
    exactly: unmasked runs are byte-identical by construction.

    Deliberately NOT `exit_anchor_row` (latest EXIT date): that would change
    Exit Date for strategies where legs already exit on different dates today
    (e.g. a carried YEARLY leg vs. a weekly leg), which is the exact
    regression this design avoids. See `exit_anchor_row`'s own docstring for
    that helper's narrower contract.
    """
    if "Exit Reason" not in sorted_df.columns or "Trade" not in sorted_df.columns:
        return aggregated
    truncated = sorted_df["Exit Reason"].astype(str).str.contains("LEG_FILTER_END", na=False)
    if not truncated.any():
        return aggregated
    candidates = sorted_df[~truncated]
    exit_pick = candidates.groupby("Trade", as_index=False).agg({
        "Exit Date": "first",
        "Exit Reason": "first",
    })
    covered_trades = set(exit_pick["Trade"])
    fallback_trades = set(sorted_df["Trade"].unique()) - covered_trades
    if fallback_trades:
        fallback = sorted_df[sorted_df["Trade"].isin(fallback_trades)].groupby(
            "Trade", as_index=False
        ).agg({"Exit Date": "first", "Exit Reason": "first"})
        exit_pick = pd.concat([exit_pick, fallback], ignore_index=True)
    exit_pick = exit_pick.set_index("Trade")
    # Every Trade in `aggregated` came from `sorted_df`, so every one of them
    # MUST be covered by exit_pick (the fallback above guarantees it). A miss
    # means the two frames disagree on the Trade key -- which is precisely how
    # a trade-id renumbering bug hides. A silent .fillna() here would revert to
    # the un-fixed value and ship wrong Exit Dates with no signal, so fail loud.
    # Membership, not NaN: a genuinely blank Exit Date is not a key mismatch.
    missing = sorted(set(aggregated["Trade"]) - set(exit_pick.index))
    if missing:
        raise RuntimeError(
            "apply_exit_anchor_exclusion: %d trade(s) in the aggregated frame "
            "have no matching row in sorted_df -- the Trade keys diverge, so "
            "the LEG_FILTER_END exit correction cannot be applied: %s"
            % (len(missing), missing[:10])
        )
    aggregated["Exit Date"] = aggregated["Trade"].map(exit_pick["Exit Date"])
    aggregated["Exit Reason"] = aggregated["Trade"].map(exit_pick["Exit Reason"])
    return aggregated


def spot_first_non_empty(series: Iterable[Any]) -> Any:
    """Aggregate a trade-level column that rides ONE leg row (e.g. Spot P&L).

    Positional "first" is wrong here: `anchor_sorted` orders by LATEST entry
    date, so the first row of a trade is not necessarily the row carrying the
    value. A carried-YEARLY leg holds an older entry date than the weekly leg
    that re-enters each cycle, so the weekly leg sorts first and its blank
    would win. Returns "" when no row carries a value.
    """
    for v in series:
        if v != "" and v is not None and not (isinstance(v, float) and v != v):
            return v
    return ""


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
