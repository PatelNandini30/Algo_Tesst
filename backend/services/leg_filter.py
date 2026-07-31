"""
services/leg_filter.py

Per-leg individual filter files.

The STRATEGY filter decides which trades exist. An individual per-leg file is a
purely SUBTRACTIVE mask on top of that: it can drop a leg from a trade, or end
that leg's hold early, and nothing else. It can never create a trade, widen a
window, or move a leg onto a date the strategy filter excludes.

See docs/superpowers/specs/2026-07-31-per-leg-filter-design.md.
"""

from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "seg_iso",
    "normalize_segments",
    "leg_segments",
    "leg_window",
    "apply_leg_filters",
    "LEG_FILTER_END",
]

LEG_FILTER_END = "LEG_FILTER_END"


def seg_iso(v: Any) -> str:
    """Normalize a segment boundary to ISO YYYY-MM-DD.

    Moved verbatim out of engine_rust._load_filter_segments so the strategy
    filter and the per-leg filter parse dates through ONE implementation.

    A datetime / date / Timestamp is ALREADY unambiguous — format it directly
    and NEVER reparse. str(datetime) is "2019-05-10 00:00:00", whose " 00:00:00"
    defeats the year-first strptime formats below, after which dayfirst=True
    FLIPS every date with day<=12 & month<=12 (10-May -> 05-Oct), inverting
    segments.
    """
    import pandas as pd

    if not isinstance(v, str) and hasattr(v, "strftime"):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    text = str(v).strip()
    for _fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y%m%d"):
        try:
            return pd.to_datetime(text, format=_fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return pd.to_datetime(text, dayfirst=True).strftime("%Y-%m-%d")


def normalize_segments(raw: Any) -> List[Tuple[str, str]]:
    """[{'start':…,'end':…}, …] or [(s, e), …] -> sorted, DISJOINT [(iso, iso), …].

    Malformed rows are skipped rather than raising: a filter file is user input
    and one bad line must not abort a backtest. Inverted ranges (end < start)
    are dropped for the same reason get_filter_segments drops them (base.py).

    Overlapping and adjacent ranges are merged so leg_window's rightmost-window
    bisect is correct by construction (it only ever looks at ONE segment).
    "Adjacent" here means next.start <= current.end on these ISO date strings
    directly — there is no trading-day calendar in this module, so a same-day
    boundary counts as overlap and merges.
    """
    segs: List[Tuple[str, str]] = []
    for s in raw or []:
        try:
            if isinstance(s, dict):
                start, end = s["start"], s["end"]
            else:
                start, end = s[0], s[1]
            a, b = seg_iso(start), seg_iso(end)
        except Exception:
            continue
        if b >= a:
            segs.append((a, b))
    segs.sort()

    merged: List[Tuple[str, str]] = []
    for a, b in segs:
        if merged and a <= merged[-1][1]:
            prev_a, prev_b = merged[-1]
            merged[-1] = (prev_a, max(prev_b, b))
        else:
            merged.append((a, b))
    return merged


def leg_segments(leg: Dict[str, Any]) -> Optional[List[Tuple[str, str]]]:
    """The leg's own mask, or None when it has no individual filter.

    An EMPTY list means "uploaded then cleared" and must behave exactly like no
    file at all — returning [] here would mask the leg out of every trade.
    """
    if not isinstance(leg, dict):
        return None
    segs = normalize_segments(leg.get("filter_segments"))
    return segs or None


def leg_window(
    mask: Sequence[Tuple[str, str]],
    entry_date: str,
    trade_exit: str,
) -> Tuple[bool, str, bool]:
    """Decide this leg's fate for one trade.

    Returns (taken, leg_exit, truncated):
      * taken=False    -> the leg is ABSENT from this trade (entry outside every
                          window, or the window leaves it no holding period).
      * leg_exit       -> min(window end, trade exit) — earliest wins.
      * truncated=True -> leg_exit came from the window, not the trade: the row
                          is tagged LEG_FILTER_END instead of its natural reason.
    """
    entry = seg_iso(entry_date)
    exit_ = seg_iso(trade_exit)

    # Rightmost window whose start <= entry; it contains entry iff entry <= its end.
    starts = [s for s, _ in mask]
    idx = bisect.bisect_right(starts, entry) - 1
    if idx < 0:
        return (False, exit_, False)
    seg_start, seg_end = mask[idx]
    if entry > seg_end:
        return (False, exit_, False)

    if seg_end < exit_:
        if seg_end <= entry:
            # Zero or negative holding period — emit nothing rather than a
            # degenerate row that would divide by a zero-length window downstream.
            return (False, exit_, False)
        return (True, seg_end, True)
    return (True, exit_, False)
