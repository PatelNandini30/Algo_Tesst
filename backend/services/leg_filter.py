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
    "last_trading_day_on_or_before",
    "first_trading_day_on_or_after",
    "split_windows",
    "resolve_leg_window",
    "apply_leg_filters",
    "apply_leg_filters_split",
    "LEG_FILTER_END",
    "CARRIED_SEG1_END_KEY",
]

LEG_FILTER_END = "LEG_FILTER_END"
# Spec key used to mark a carried (unfiltered) leg's segment-1 row whose exit
# lands on a filtered-leg range boundary.  The engine's _carried_seg_end_keys
# tagger reads this to emit LEG_FILTER_END, kept SEPARATE from
# _leg_filter_end_keys so _leg_was_truncated is not poisoned for an unfiltered leg.
CARRIED_SEG1_END_KEY = "_carried_seg1_end"


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


def last_trading_day_on_or_before(
    target: str, trading_days: Sequence[str]
) -> Optional[str]:
    """Latest trading day <= target, or None.

    THE single implementation: engine_rust._last_trading_day_on_or_before
    delegates here so a boundary can never be snapped two different ways.
    `trading_days` must be sorted ascending ISO strings.
    """
    if not trading_days or not target:
        return None
    idx = bisect.bisect_right(trading_days, target) - 1
    if idx < 0:
        return None
    return trading_days[idx]


def first_trading_day_on_or_after(
    target: str, trading_days: Sequence[str]
) -> Optional[str]:
    """Earliest trading day >= target, or None.

    Mirror of last_trading_day_on_or_before — same bisect approach, forward snap.
    `trading_days` must be sorted ascending ISO strings.
    """
    if not trading_days or not target:
        return None
    idx = bisect.bisect_left(trading_days, target)
    if idx >= len(trading_days):
        return None
    return trading_days[idx]


def split_windows(
    entry: str,
    exit: str,
    ranges: Sequence[Tuple[str, str]],
    trading_days: Sequence[str],
) -> List[Dict[str, Any]]:
    """Split [entry, exit] at filter-range boundaries that fall strictly inside.

    Each range start snaps forward (first trading day >= start); each range end
    snaps back (last trading day <= end).  Only snapped boundaries that are
    strictly between entry and exit produce a split.

    Returns a list of {"seg_start", "seg_end", "in_range"} dicts, consecutive,
    together covering exactly [entry, exit].  in_range is True iff the window's
    seg_start falls inside any snapped range [start, end] inclusive.
    """
    # Snap every range boundary and collect boundaries strictly inside (entry, exit).
    snapped: List[Tuple[str, str]] = []
    for rng_start, rng_end in ranges:
        s = first_trading_day_on_or_after(rng_start, trading_days)
        e = last_trading_day_on_or_before(rng_end, trading_days)
        if s is None or e is None or e < s:
            continue
        snapped.append((s, e))

    # Collect interior cut points (deduplicated, sorted).
    interior: List[str] = []
    seen: set = set()
    for s, e in snapped:
        for boundary in (s, e):
            if boundary not in seen and entry < boundary < exit:
                seen.add(boundary)
                interior.append(boundary)
    interior.sort()

    # Build consecutive windows over the cut points.
    cuts = [entry] + interior + [exit]

    def _in_range(date: str) -> bool:
        # Half-open: [s, e) — a window starting AT the range-end boundary is outside.
        for s, e in snapped:
            if s <= date < e:
                return True
        return False

    return [
        {"seg_start": cuts[i], "seg_end": cuts[i + 1], "in_range": _in_range(cuts[i])}
        for i in range(len(cuts) - 1)
    ]


def resolve_leg_window(
    leg: Dict[str, Any],
    entry_date: str,
    exit_date: str,
    trading_days: Sequence[str],
) -> Tuple[bool, str, bool]:
    """THE per-leg filter rule, shared by EVERY path that applies a leg mask.

    Options specs go through apply_leg_filters (a post-pass); futures rows are
    priced inside their own builders and call this directly via
    engine_rust._apply_leg_filter_mask. Both must behave identically on the same
    uploaded file, so both land here.

    TODO (FIX 3): Futures filtered legs receive subtract-only behaviour here
    (no mid-cycle split entry); there is no callback path for the futures inline
    builders.  The Task-6 optimizer/coverage gate MUST hard-fail a futures leg
    that carries filter_segments rather than silently diverge from options legs
    — emit a clear error at that gate rather than producing wrong numbers quietly.

    Returns (taken, exit_date, truncated):
      * taken=False    -> the leg is ABSENT from this trade.
      * exit_date      -> unchanged, or the truncated boundary SNAPPED BACK to
                          the last trading day on/before the window end. Uploaded
                          files routinely end on month/quarter ends that fall on a
                          weekend; an unsnapped exit has no price and books a
                          zero-P&L phantom row.
      * truncated=True -> exit came from the leg's own file; caller tags
                          LEG_FILTER_END *if the realised exit actually landed on
                          this boundary*.
    """
    mask = leg_segments(leg)
    if mask is None:
        return True, exit_date, False
    taken, leg_exit, truncated = leg_window(mask, entry_date, exit_date)
    if not taken:
        return False, exit_date, False
    if not truncated:
        return True, exit_date, False
    snapped = last_trading_day_on_or_before(leg_exit, trading_days) or leg_exit
    if snapped <= entry_date:
        # The snap swallowed the whole hold — drop the leg rather than emit a
        # zero/negative-length row (mirrors leg_window's degenerate-window rule).
        return False, exit_date, False
    return True, snapped, True


def apply_leg_filters(
    specs: List[Dict[str, Any]],
    legs: Sequence[Dict[str, Any]],
    trading_days: Sequence[str],
) -> List[Dict[str, Any]]:
    """Apply every leg's individual filter to a resolved spec list, IN ORDER.

    Runs as a post-pass rather than inside each spec builder because there are
    six builders (_build_fixed_entry_specs, _build_next_expiry_specs, the two
    futures ones and the two mixed ones) and they all converge on one spec list.
    Post-processing also leaves the STRIKE epochs alone: a masked-out leg still
    anchored its Fixed/pinned epoch when the schedule was built, which is what
    we want — a mask must not silently re-strike the legs that remain.

    Returns a NEW list; `specs` is not mutated.
    """
    masked_legs: Dict[int, Dict[str, Any]] = {}
    for i, leg in enumerate(legs or []):
        if leg_segments(leg):
            masked_legs[i + 1] = leg
    if not masked_legs:
        return specs  # nothing configured — identical object, zero cost

    kept: List[Dict[str, Any]] = []
    for s in specs:
        try:
            leg_id = int(s.get("leg_id") or 1)
        except (TypeError, ValueError):
            kept.append(s)
            continue
        leg = masked_legs.get(leg_id)
        if leg is None:
            kept.append(s)
            continue
        taken, leg_exit, truncated = resolve_leg_window(
            leg,
            str(s.get("entry_date") or ""),
            str(s.get("exit_date") or ""),
            trading_days,
        )
        if not taken:
            continue
        row = dict(s)
        if truncated:
            row["exit_date"] = leg_exit
            row["_leg_filter_end"] = True
        kept.append(row)

    # A trade every one of whose legs was masked out has already vanished: the
    # loop above simply never appended any of its rows.
    return kept


def apply_leg_filters_split(
    specs: List[Dict[str, Any]],
    legs: Sequence[Dict[str, Any]],
    trading_days: Sequence[str],
    *,
    spot_by_date: Optional[Dict[str, float]] = None,
    resolve_strike: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Mid-cycle split variant of apply_leg_filters (Option C).

    Behaviour when NO leg carries a filter: returns `specs` unchanged (same
    list object) — byte-identical to before. This is the single most important
    property.

    When at least one leg carries a filter:

    1. Group specs by trade_id (preserving arrival order).
    2. For each trade, collect interior cut-points from every filtered leg's
       range boundaries that fall strictly inside the trade's (entry, exit)
       window, via split_windows.
    3. No interior cuts → apply the original drop/truncate logic (same as
       apply_leg_filters).
    4. Interior cuts → split the trade's [entry, exit] window into sub-windows
       at those boundaries. Each sub-window becomes its own trade block:
         - Unfiltered legs: duplicated across every sub-window with the same
           strike/expiry/contract. Adjacent rows with identical (strike, expiry)
           are automatically treated as carries by _apply_carry_slippage_guard
           — no extra marking needed.  P&L is only partitioned; the boundary
           mark cancels and the total is unchanged.
         - Filtered legs: if `in_range` but the original spec's entry was outside
           the range (so resolve_leg_window would drop it), emit a FRESH spec
           with entry_date=seg_start, strike resolved at seg_start via the
           `resolve_strike` callback.  If the callback returns None (illiquid /
           not listed), that filtered-leg segment is dropped and the carried leg
           is kept — mirror the builders' unresolvable-strike behaviour.
           Requires `spot_by_date` and `resolve_strike` (Task 3).  When absent,
           the old Task-2 drop/truncate behaviour is preserved.
    5. Renumber trade_id sequentially (1-based) across the whole output list.

    See docs/superpowers/specs/2026-08-01-per-leg-filter-split-design.md.

    LOAD-BEARING INVARIANT — pass ordering:
    The cost-free boundary relies on the two split rows of a carried leg sharing
    an IDENTICAL (strike, expiry) key.  This is only guaranteed when this
    function runs AFTER _apply_fixed_rollover_strike (which resolves the epoch
    strike that both sub-windows must share) and AFTER the initial spec list is
    assembled with strikes already set.  If a later task reorders these passes so
    that a carried leg's strike could DIFFER between sub-windows, the carry guard
    (_apply_carry_slippage_guard) would treat the boundary as a real open/close
    and charge slippage, breaking P&L conservation.  Any change to the call
    ordering in engine_rust.py that touches `apply_leg_filters` must verify this
    invariant is preserved.  See also the comment at the call site in engine_rust.py.
    """
    masked_legs: Dict[int, Dict[str, Any]] = {}
    for i, leg in enumerate(legs or []):
        if leg_segments(leg):
            masked_legs[i + 1] = leg
    if not masked_legs:
        return specs  # ← same list object; no-filter path is byte-identical

    # Group by trade_id, preserving insertion order.
    from collections import defaultdict as _dd
    trade_groups: Dict[int, List[Dict[str, Any]]] = _dd(list)
    for s in specs:
        try:
            tid = int(s.get("trade_id") or 0)
        except (TypeError, ValueError):
            tid = 0
        trade_groups[tid].append(s)

    out: List[Dict[str, Any]] = []
    new_tid = 1  # sequential counter for renumbered trade_ids

    for tid, group in trade_groups.items():
        # Trade window: entry is the min entry_date in the group; exit is
        # the max exit_date (legs can have different exit_dates when one is
        # already truncated — use the unfiltered legs' exit where possible).
        entry = min(str(s.get("entry_date") or "") for s in group)
        exit_ = max(str(s.get("exit_date") or "") for s in group)

        # Collect interior cut-points from every filtered leg in this trade.
        interior_cuts: set = set()
        for leg_id, leg in masked_legs.items():
            ranges = leg_segments(leg)
            if not ranges:
                continue
            # split_windows returns windows with boundaries; extract the
            # interior cuts (the seg_start values that are > entry).
            for w in split_windows(entry, exit_, ranges, trading_days):
                if entry < w["seg_start"] < exit_:
                    interior_cuts.add(w["seg_start"])

        if not interior_cuts:
            # No split needed: apply the original drop/truncate logic.
            for s in group:
                try:
                    leg_id = int(s.get("leg_id") or 1)
                except (TypeError, ValueError):
                    leg_id = -1
                leg = masked_legs.get(leg_id)
                if leg is None:
                    row = dict(s)
                    row["trade_id"] = new_tid
                    out.append(row)
                    continue
                taken, leg_exit, truncated = resolve_leg_window(
                    leg,
                    str(s.get("entry_date") or ""),
                    str(s.get("exit_date") or ""),
                    trading_days,
                )
                if not taken:
                    continue
                row = dict(s)
                row["trade_id"] = new_tid
                if truncated:
                    row["exit_date"] = leg_exit
                    row["_leg_filter_end"] = True
                out.append(row)
            # Advance trade counter only if at least one leg survived.
            if out and out[-1].get("trade_id") == new_tid:
                new_tid += 1
        else:
            # Split: build sub-windows and emit one trade block per sub-window.
            cuts = sorted(interior_cuts)
            windows = [entry] + cuts + [exit_]
            # Build sub-windows as (seg_start, seg_end) pairs.
            sub_windows = [(windows[i], windows[i + 1]) for i in range(len(windows) - 1)]

            for seg_start, seg_end in sub_windows:
                seg_had_any = False
                for s in group:
                    try:
                        leg_id = int(s.get("leg_id") or 1)
                    except (TypeError, ValueError):
                        leg_id = -1
                    leg = masked_legs.get(leg_id)
                    if leg is None:
                        # Unfiltered leg: carried across the sub-window with
                        # the same strike/expiry — _apply_carry_slippage_guard
                        # detects the carry automatically via identical keys.
                        row = dict(s)
                        row["trade_id"] = new_tid
                        row["entry_date"] = seg_start
                        row["exit_date"] = seg_end
                        # Preserve _seg_clamped only on the LAST sub-window
                        # (the boundary that was clamped to a segment/filter end
                        # is the final exit, not the mid-cycle splits).
                        if seg_end != exit_ and row.get("_seg_clamped"):
                            row["_seg_clamped"] = False
                        # A carried sub-window ending at a filter boundary (not
                        # the trade's natural exit) gets tagged LEG_FILTER_END by
                        # the engine's _carried_seg_end_keys tagger — separate
                        # from _leg_filter_end_keys so _leg_was_truncated is NOT
                        # poisoned for this unfiltered leg.
                        if seg_end != exit_:
                            row["_carried_seg1_end"] = True
                        else:
                            row.pop("_carried_seg1_end", None)
                        out.append(row)
                        seg_had_any = True
                    else:
                        # Filtered leg: check if this sub-window is in-range.
                        # Use seg_start as entry (same as original Task-2 logic).
                        taken, leg_exit, truncated = resolve_leg_window(
                            leg,
                            seg_start,
                            seg_end,
                            trading_days,
                        )
                        if not taken:
                            continue

                        # The sub-window is in-range.  Now decide whether this
                        # is a continuation of the original range entry (Case A)
                        # or a FRESH mid-cycle entry triggered by a range-start
                        # boundary landing at seg_start (Case B).
                        #
                        #   Case A: original spec entry was already inside the
                        #   range (seg_start > orig_entry but both in-range) →
                        #   keep the original strike; just update the dates.
                        #
                        #   Case B: original spec entry was BEFORE the range
                        #   start, and seg_start == range-start boundary →
                        #   resolve a fresh strike at the boundary-date spot.
                        #   Falls back to original strike (Task-2 behaviour)
                        #   when callbacks are absent.
                        orig_entry = str(s.get("entry_date") or "")
                        _orig_in_range, _, _ = resolve_leg_window(
                            leg, orig_entry, seg_end, trading_days,
                        )
                        # ponytail: fresh emission requires a resolver; production
                        # always supplies one via _mid_cycle_strike_resolver.
                        # Without it, DROP the filtered leg for this sub-window
                        # rather than emit a wrong-strike row (FIX 2).
                        is_fresh_entry = (
                            not _orig_in_range
                            and seg_start > orig_entry
                            and resolve_strike is not None
                            and spot_by_date is not None
                        )
                        _needs_fresh = (
                            not _orig_in_range
                            and seg_start > orig_entry
                        )
                        if _needs_fresh and not is_fresh_entry:
                            # No callbacks: drop rather than emit wrong-strike row.
                            continue
                        if is_fresh_entry:
                            spot = spot_by_date.get(seg_start)
                            if not spot:
                                # Missing spot at boundary — drop filtered leg,
                                # keep carried leg (guard against unpriced entry).
                                continue
                            fresh_strike = resolve_strike(leg, s, spot, seg_start)
                            if fresh_strike is None:
                                # Illiquid / not listed — drop just this
                                # filtered-leg segment; the carried leg stays.
                                continue
                            row = dict(s)
                            row["trade_id"] = new_tid
                            row["entry_date"] = seg_start
                            row["exit_date"] = leg_exit if truncated else seg_end
                            row["strike"] = float(fresh_strike)
                            row["requested_strike"] = float(fresh_strike)
                            if truncated:
                                row["_leg_filter_end"] = True
                            else:
                                row.pop("_leg_filter_end", None)
                            # Mirror carried-leg rule: _seg_clamped belongs only
                            # on the FINAL sub-window (the true exit boundary).
                            if seg_end != exit_ and row.get("_seg_clamped"):
                                row["_seg_clamped"] = False
                            out.append(row)
                            seg_had_any = True
                        else:
                            # Case A (or no callbacks): keep original strike.
                            row = dict(s)
                            row["trade_id"] = new_tid
                            row["entry_date"] = seg_start
                            row["exit_date"] = leg_exit if truncated else seg_end
                            if truncated:
                                row["_leg_filter_end"] = True
                            # Mirror carried-leg rule: _seg_clamped belongs only
                            # on the FINAL sub-window (the true exit boundary).
                            if seg_end != exit_ and row.get("_seg_clamped"):
                                row["_seg_clamped"] = False
                            out.append(row)
                            seg_had_any = True
                if seg_had_any:
                    new_tid += 1

    return out
