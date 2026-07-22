"""
Multi-Index + Multi-Expiry feature (opt-in, isolated).

This is a NEW FEATURE, not a new engine. It lets one strategy hold legs on
different indices (e.g. NIFTY + MIDCPNIFTY) and/or different expiry cycles
(weekly + monthly) at once.

Approach (approved): do NOT re-implement any pricing/engine logic. Split the
legs into groups by (index, expiry) and run EACH group through the EXISTING
engine path (`services.algotest_job._try_rust_engine` ->
`engine_rust.run_rust_engine_pipeline`), so every existing feature (T-n,
fixed-entry, filter, spot-adjustment, re-entry, rollover, SL/target, MAE/MFE)
is byte-identical to a normal single-index run, per group. Then MERGE the
groups' trades into one combined tradesheet with combined analytics.

Nothing existing is modified. This module only orchestrates existing helpers
and is reached ONLY when `payload['multi_index_mode']` is set (the new builder
sets it); every existing strategy runs the existing path untouched.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Last (symbol, from, to) successfully bulk-loaded via _reload_bulk_if_needed,
# in THIS process. An optimizer sweep calls run_sync_weekly_cadence once per
# combo with the SAME cadence_index/date-range every time (only strategy
# params vary) — tracking this lets consecutive combos on the same worker
# reuse the already-loaded native cache instead of clearing and rebuilding it
# from scratch on every combo.
_last_bulk_load_key: Optional[Tuple[str, str, str]] = None

# Has bulk_load_options ever run in this process for the group-per-index path
# (run_multi_index_feature)? Set True on the first group's first combo.
_bulk_engine_activated = False


def _ensure_group_symbol_loaded(symbol: str, from_date: str, to_date: str) -> None:
    """Make `symbol` available to a group's engine run WITHOUT evicting any
    other symbol already resident — unlike bulk_load_options, whose Rust
    feather shortcut calls native.load_cache(), which REPLACES the whole
    cache. A 2-group sweep alternates symbols every combo, so replacing the
    cache per group meant re-loading the full native cache (millions of
    rows) up to twice per combo — the same runaway-RSS pattern that OOM'd
    the sync_weekly_roll path, just distributed across 2 symbols instead of
    1. The very first call in this process still goes through
    bulk_load_options once — purely to flip base.py's _rust_lookup_active
    flag (a process-global "is native mode on" switch, not symbol-specific,
    that load_ohlc_for_leg_range's MAE/MFE fast path checks; it falls back to
    a direct per-symbol Postgres query if never set, so this is a pure
    perf activation, not a correctness dependency). Every group after that —
    including every symbol on every later combo — uses the additive
    rf.ensure_symbol_merged instead, so each symbol loads ONCE per worker
    process for the life of the sweep.

    Audited safe: _try_rust_engine/run_rust_engine_pipeline (the entire
    per-group engine call) resolve every price through either an explicit
    symbol-parameterized DB call or the native Rust cache keyed by
    cache.symbol_ids (verified in lib.rs) — nothing in that call chain reads
    bulk_load_options' other bookkeeping (_bhav_by_date_symbol, _bulk_loaded)."""
    global _bulk_engine_activated
    from services import rust_fast_path as rf
    if not _bulk_engine_activated:
        from base import bulk_load_options
        from services.algotest_job import _build_fast_lookup_from_bulk
        bulk_load_options(symbol, from_date, to_date)
        _build_fast_lookup_from_bulk(symbol, from_date, to_date)
        _bulk_engine_activated = True
        return
    rf.ensure_symbol_merged(symbol)


def _leg_index(leg: dict, default_index: str) -> str:
    return str(leg.get("index") or default_index).strip().upper()


def _leg_expiry(leg: dict, default_expiry: str) -> str:
    return str(leg.get("expiry") or leg.get("expiry_type") or default_expiry).strip().upper()


def _leg_segment(leg: dict) -> str:
    """This leg's instrument family: "FUT" or "OPT". Futures and options are NOT on
    the same monthly calendar (MIDCPNIFTY 2023-09: option 29-Sep vs future 25-Sep),
    so a leg must roll on its OWN segment's expiries. Mirrors the is_fut test used
    throughout the overlay."""
    return "FUT" if str(leg.get("segment") or "").upper() in ("FUTURE", "FUTURES") else "OPT"


def _segment_like(segment: str) -> str:
    """SQL LIKE pattern for _data_expiries/_expiry_last_traded from a _leg_segment."""
    return "FUT%" if str(segment or "OPT").upper().startswith("FUT") else "OPT%"


def _group_expiry_type(sym: str, glegs: List[dict], payload: dict, default_index: str) -> str:
    """Strategy-level expiry cadence for an index group's engine sub-run.

    - The strategy (base) index keeps the payload's expiry_type verbatim, so the
      base group is byte-identical to a normal single-index run.
    - Any other index group's cadence = the SHORTEST leg expiry (weekly if any
      weekly leg AND the index actually supports weekly cadence), else monthly —
      mirroring a standalone backtest on that index.
    """
    strat_idx = str(payload.get("index") or default_index).strip().upper()
    if sym == strat_idx:
        return str(payload.get("expiry_type") or "MONTHLY").strip().upper()
    exps = [str(l.get("expiry") or l.get("expiry_type") or "").upper() for l in glegs]
    if any(e.startswith("WEEK") for e in exps):
        try:
            from services.index_metadata import get_index_config as _gic
            cfg = _gic(sym)
            if cfg and any(str(b).upper().startswith("WEEK") for b in (cfg.expiry_bases or ())):
                return "WEEKLY"
        except Exception:
            pass
    return "MONTHLY"


def _price_futures_group(
    payload: Dict[str, Any],
    symbol: str,
    legs: List[dict],
    effective_from: Optional[str],
    effective_to: Optional[str],
) -> List[dict]:
    """Price a FUTURES hedge as a real HELD monthly position (parallel), using the
    Rust-native futures cache (get_future_price). One trade per monthly cycle:
    enter just after the previous monthly expiry, exit at the cycle's expiry, on
    that cycle's contract — so the future has a genuine hold and real P&L (not the
    0-duration expiry-day snapshot the Python futures path produces here). Trades
    are clamped to filter segments (FILTER_END). Returns a list of record dicts."""
    import bisect
    import pandas as pd
    from base import get_trading_calendar, get_spot_price_from_db, get_expiry_dates
    from services.index_metadata import get_lot_size_for_index
    from services import rust_fast_path as rf
    from services.futures_cache_store import ensure_futures_loaded

    symbol = symbol.upper()
    if not ensure_futures_loaded(symbol):
        logger.warning("[MULTI_INDEX] futures cache unavailable for %s", symbol)
        return []

    exp_df = get_expiry_dates(symbol, "monthly", effective_from, effective_to)
    if exp_df is None or getattr(exp_df, "empty", True):
        return []
    ecol = "Current Expiry" if "Current Expiry" in exp_df.columns else exp_df.columns[0]
    expiries = sorted(pd.to_datetime(exp_df[ecol]).dt.strftime("%Y-%m-%d").unique().tolist())

    cal = get_trading_calendar(effective_from, effective_to)
    tdays = sorted(pd.to_datetime(cal["date"]).dt.strftime("%Y-%m-%d").tolist())
    if not tdays or not expiries:
        return []

    try:
        from services.engine_rust import _load_filter_segments
        segs = _load_filter_segments(payload) or []
    except Exception:
        segs = []
    if segs:
        norm_segs = [(pd.Timestamp(s).strftime("%Y-%m-%d"), pd.Timestamp(e).strftime("%Y-%m-%d")) for (s, e) in segs]
    else:
        norm_segs = [(tdays[0], tdays[-1])]

    def _first_td_ge(d):
        i = bisect.bisect_left(tdays, d)
        return tdays[i] if i < len(tdays) else None

    def _last_td_le(d):
        i = bisect.bisect_right(tdays, d) - 1
        return tdays[i] if i >= 0 else None

    records: List[dict] = []
    trade_no = 0
    for i, E in enumerate(expiries):
        prev_E = expiries[i - 1] if i > 0 else None
        if prev_E is None:
            cyc_start = tdays[0]
        else:
            j = bisect.bisect_right(tdays, prev_E)  # first trading day strictly after prev expiry
            cyc_start = tdays[j] if j < len(tdays) else None
        cyc_end = E
        if cyc_start is None or cyc_start >= cyc_end:
            continue

        for (s, e) in norm_segs:
            a = max(cyc_start, s)
            b = min(cyc_end, e)
            if a >= b:
                continue
            entry_day = _first_td_ge(a)
            exit_day = _last_td_le(b)
            if entry_day is None or exit_day is None or entry_day >= exit_day:
                continue

            entry_spot = get_spot_price_from_db(entry_day, symbol)
            exit_spot = get_spot_price_from_db(exit_day, symbol)
            reason = "FILTER_END" if (b == e and e < cyc_end) else "EXPIRY"

            leg_rows = []
            leg_no = 0
            for leg in legs:
                ep = rf.get_future_price(symbol, entry_day, E)
                xp = rf.get_future_price(symbol, exit_day, E)
                if ep is None or xp is None:
                    continue  # pre-2022 / missing contract → skip this cycle's leg
                ep = round(float(ep), 2)
                xp = round(float(xp), 2)
                pos = str(leg.get("position") or "SELL").upper()
                pos = "BUY" if pos.startswith("B") else "SELL"
                lots = int(leg.get("lots") or leg.get("lot") or 1)
                lot_size = int(get_lot_size_for_index(symbol, entry_day))
                # P&L = points x THIS leg's own lots (lot_size excluded), same
                # convention as _overlay_legs_onto_base's futures/options branches.
                # NOTE: `_price_futures_group` currently has no call sites in the
                # repo (audited 2026-07-21) — fixed here anyway so it isn't a
                # live landmine if it is ever wired up.
                pnl = round(((xp - ep) if pos == "BUY" else (ep - xp)) * lots, 2)
                es = float(entry_spot) if entry_spot else ep
                xs = float(exit_spot) if exit_spot else xp
                leg_no += 1
                leg_rows.append({
                    "Leg": leg_no, "Index": symbol, "Type": "FUT", "Strike": "",
                    "B/S": pos, "Qty": lots * lot_size,
                    "Entry Price": ep, "Exit Price": xp,
                    "Entry Spot": round(es, 2), "Exit Spot": round(xs, 2),
                    "Expiry": E, "CE P&L": 0.0, "PE P&L": 0.0, "FUT P&L": pnl,
                    "% P&L": round(pnl / es * 100.0, 4) if es else 0.0,
                    "Exit Reason": reason, "MAE": 0.0, "MFE": 0.0,
                    "_pos": pos,
                })
            if not leg_rows:
                continue
            trade_no += 1
            trade_total = round(sum(r["FUT P&L"] for r in leg_rows), 2)
            es0 = leg_rows[0]["Entry Spot"]
            xs0 = leg_rows[0]["Exit Spot"]
            spot_pl = round(xs0 - es0, 2)
            for k, r in enumerate(leg_rows):
                r.pop("_pos", None)
                r["Trade"] = trade_no
                r["Index"] = trade_no  # numeric trade index (matches options rows)
                r["Group Index"] = symbol
                r["Entry Date"] = entry_day
                r["Exit Date"] = exit_day
                # Parent (first) leg carries trade-total Net P&L + Spot P&L; others per-leg.
                r["Net P&L"] = trade_total if k == 0 else r["FUT P&L"]
                r["Spot P&L"] = spot_pl if k == 0 else 0.0
                records.append(r)

    return records


# Memoized per (symbol, instrument, from_iso, data_version). The DISTINCT-expiry
# query over the ~662k-row option_data table costs ~77ms and its result is CONSTANT
# across an optimizer sweep (only changes on a data import, which bumps data_version).
# Recomputing it per combo was ~45% of the multi-index sync per-combo cost. Keyed on
# the backtest_cache data_version so a real import correctly invalidates it; identical
# result on a hit, so ZERO effect on trades/pricing — pure memoization.
_DATA_EXPIRIES_CACHE: Dict[tuple, List[str]] = {}


def _roll_series_calendar(sym: str, exp_type: str, from_iso: str, to_iso: str) -> List[str]:
    """LEGACY roll series, read from the expiry_calendar via get_expiry_dates.

    Kept ONLY as the fallback when the data-driven `_roll_series` below cannot
    reach the DB. Its known limitation is that the calendar is incomplete at the
    range ends — it is missing MIDCPNIFTY's 2022-01-25 launch-month expiry, so a
    2022-start stateful walk begins a month out of step and leaves MIDCP
    PERPETUALLY holding the next month (~30 days early = the no-liquidity zone =
    frozen price = 0 P&L on every MIDCP leg).
    """
    import pandas as pd
    try:
        from base import get_expiry_dates
        et = "weekly" if str(exp_type or "").upper().startswith("WEEK") else "monthly"
        df = get_expiry_dates(sym, et, from_iso, to_iso)
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] roll series failed for %s/%s: %s", sym, exp_type, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    for col in ("Current Expiry", "expiry_date", "expiry", "date"):
        if col in df.columns:
            try:
                return sorted({pd.Timestamp(v).strftime("%Y-%m-%d")
                               for v in df[col].dropna().tolist()})
            except Exception:
                return []
    return []


def _is_tradeable_expiry(e: str, last_tr: Optional[str], sessions: List[str]) -> bool:
    """Did the contract expiring on `e` trade through to its own expiry?

    A REAL contract prints on the last session on or before its expiry (that is its
    settlement day). Two kinds of junk do not, and both corrupt the roll schedule:

      * STRAYS — dead long-dated listings. NIFTY 2026-03 carries a real 30-Mar
        (13,976 rows, traded to expiry) AND a stray 31-Mar (4,790 rows, listed
        2025-03-28, LAST TRADED 2025-12-26). MAX()-per-month picks the stray, whose
        absent data drops the entire March cycle.
      * RELABEL ORPHANS — when NSE revises an expiry mid-life the chain splits into
        two labels (see the `futures-nse-expiry-relabel-fix` note). MIDCPNIFTY moved
        Wed->Mon on 2023-08-17, killing every Wed label on 2023-08-16; NIFTY's
        June-2023 chain lived as 29-Jun (106,165 rows) then settled under a one-day
        28-Jun label. The dead label must not become a boundary — 29-Jun-2023 is a
        date the market never opened.

    Compared against the SESSIONS PRESENT IN THE DATA, not a holiday calendar, so
    genuinely holiday-shifted expiries survive: MIDCPNIFTY's 22-Jan-2024 monthly
    (Ayodhya closure) last traded Sat 20-Jan-2024, which IS the last session on or
    before it -> kept. Measured on NIFTY+MIDCPNIFTY 2022+: rejects 24 of 611
    expiries, every one verified junk.
    """
    if not last_tr:
        return False
    if not sessions:
        return True                      # cannot judge -> keep (never worse than before)
    if e > sessions[-1]:
        return True                      # still live at the data end -> nothing to judge
    import bisect
    i = bisect.bisect_right(sessions, e) - 1
    if i < 0:
        return True
    return last_tr >= sessions[i]


def _roll_series(sym: str, exp_type: str, from_iso: str, to_iso: str,
                 segment: str = "OPT") -> List[str]:
    """Every expiry on THIS leg's OWN roll calendar: its index + expiry type +
    SEGMENT. WEEKLY -> that index's weekly expiries; MONTHLY -> its monthly, i.e.
    the last TRADEABLE expiry of each month (the same convention _pick_monthly uses).
    NOTE an index's monthly expiry is also one of its weeklies, so a weekly leg
    naturally rolls through it — no special case.

    Derived from the ACTUAL data (_data_expiries), NOT get_expiry_dates, because the
    expiry_calendar is incomplete at the range ends (see _roll_series_calendar).
    Both guards that forced the 2026-07-17 revert of the data-driven version are now
    in place and verified against Postgres:
      1. SEGMENT — futures and options have genuinely different monthlies
         (MIDCPNIFTY 2023-09: option 29-Sep vs future 25-Sep), so a FUT leg reads
         FUT% and an OPT leg reads OPT%. Hardcoding OPT% put the future on the
         option calendar and forced it onto October mid-week.
      2. STRAY/ORPHAN — see _is_tradeable_expiry.
    Memoized per data_version underneath, so this stays cheap in an optimizer sweep.
    """
    sessions = _data_sessions(sym, from_iso)

    def _guarded(like: str) -> List[str]:
        last_tr = _expiry_last_traded(sym, like, from_iso)
        return [e for e in _data_expiries(sym, like, from_iso)
                if e <= to_iso and _is_tradeable_expiry(e, last_tr.get(e), sessions)]

    exps = _guarded(_segment_like(segment))
    if not exps:
        logger.warning("[SYNC_CADENCE] no tradeable %s/%s/%s expiries in the data — "
                       "falling back to the expiry calendar", sym, exp_type, segment)
        return _roll_series_calendar(sym, exp_type, from_iso, to_iso)
    if str(exp_type or "").upper().startswith("WEEK"):
        return exps

    # The MONTHLY is ANCHORED TO THE FUTURES CALENDAR, not to MAX()-per-month.
    # "Last expiry of the calendar month" is WRONG whenever a following month's
    # weekly gets pulled back over a month boundary by a holiday: NIFTY 2025-04
    # carries a real monthly 24-Apr (10,672 rows, listed 31-Jan, and FUTIDX agrees)
    # plus a 30-Apr Wed (4,784 rows, listed only 21-Mar) that is really the 1-May
    # Thursday weekly moved off Maharashtra Day. MAX() picks 30-Apr and rolls the
    # whole strategy a week late. Futures carry no such weekly, so the futures
    # expiry DEFINES the month. Measured over 2022-01..2026-06: for NIFTY and
    # MIDCPNIFTY alike an option expiry coincides with the futures monthly in 56 of
    # 56 months, and MAX()-per-month disagrees in exactly the two trap months
    # (NIFTY 2025-04, MIDCPNIFTY 2023-09) — both of which this rule fixes.
    fut_by_month: Dict[str, str] = {}
    for e in _guarded("FUT%"):          # ascending -> last write per month wins
        fut_by_month[e[:7]] = e
    if not fut_by_month:                # no futures for this symbol -> old behaviour
        by_month: Dict[str, str] = {}
        for e in exps:
            by_month[e[:7]] = e
        return sorted(by_month.values())
    if str(segment or "OPT").upper().startswith("FUT"):
        return sorted(fut_by_month.values())

    opt_set = set(exps)
    opt_by_month: Dict[str, str] = {}
    for e in exps:
        opt_by_month[e[:7]] = e
    out: List[str] = []
    for ym in sorted(fut_by_month):
        fm = fut_by_month[ym]
        if fm in opt_set:               # the 56/56 case
            out.append(fm)
        elif ym in opt_by_month:        # no option on the futures expiry -> last of month
            logger.warning("[SYNC_CADENCE] %s %s: no option expiry on the futures "
                           "monthly %s; using %s", sym, ym, fm, opt_by_month[ym])
            out.append(opt_by_month[ym])
    return out


def _merged_roll_boundaries(legs: List[dict], default_index: str, default_expiry: str,
                            from_iso: str, to_iso: str) -> List[str]:
    """The SHARED roll schedule = the union of every leg's OWN expiry calendar.

    Whichever index expires FIRST becomes the next boundary, so at every boundary
    all legs square off and re-enter together — each on its own near contract.
    Handles both shapes with one rule:
      * mixed (NIFTY weekly + MIDCP monthly): NIFTY's weeklies + MIDCP's monthlies
      * both monthly (NIFTY + MIDCP): whichever monthly is earlier alternates
    """
    out: set = set()
    for leg in legs:
        sym = _leg_index(leg, default_index)
        et = _leg_expiry(leg, default_expiry)
        for e in _roll_series(sym, et, from_iso, to_iso, _leg_segment(leg)):
            if from_iso <= e <= to_iso:
                out.add(e)
    return sorted(out)


def _nth_trading_day_before(target: str, n: int, trading_days: List[str]) -> Optional[str]:
    """The session `n` trading days before `target` (n=0 -> target snapped back to a
    session). Used to turn a roll boundary into that cycle's T-n exit."""
    if not trading_days:
        return None
    import bisect
    i = bisect.bisect_right(trading_days, target) - 1   # last session <= target
    if i < 0:
        return None
    i -= max(0, int(n or 0))
    return trading_days[i] if i >= 0 else None


def _near_contract_on(sym: str, exp_type: str, on_iso: str, series: List[str],
                      floor_month: Optional[str] = None) -> Optional[str]:
    """This leg's OWN near contract that is still ALIVE on `on_iso` — the nearest
    expiry >= on_iso from its own calendar: a leg keeps its contract until its own
    expiry, then advances. This is the MIXED-frequency (Shape A) contract rule.

    `floor_month` ("YYYY-MM") enforces the SAME-MONTH rule: contracts expiring before
    that month are skipped, so a leg cannot sit in March while another leg has already
    rolled into April. See _sync_floor_month."""
    for e in series:  # ascending
        if floor_month and e[:7] < floor_month:
            continue
        if e >= on_iso:
            return e
    return None


def _holdable_contract(series: List[str], on_iso: str, exit_dte: int,
                       tdays: List[str], floor_month: Optional[str] = None) -> Optional[str]:
    """The first contract in `series` this leg would still be HOLDING at `on_iso` under
    the strategy's T-n rule: its roll date (n trading days before its own expiry) must
    not have passed yet. Mirrors `_holdable` in the overlay so the base cycle builder
    and the overlay agree on which contract a leg is on."""
    for e in series:  # ascending
        if floor_month and e[:7] < floor_month:
            continue
        if not tdays:
            if e >= on_iso:
                return e
            continue
        roll = _nth_trading_day_before(e, exit_dte, tdays)
        if roll is not None and roll >= on_iso:
            return e
    return None


def _sync_floor_month(tracks, track_series, on_iso: str, exit_dte: int,
                      tdays: List[str]) -> Optional[str]:
    """The expiry month EVERY leg must be in for a cycle exiting at `on_iso`.

    User rule (locked 2026-07-17): "once any leg rolls into a new expiry month, every
    other leg must be on a contract expiring in that month or later" — no cycle may
    hold March on one leg and April on another. So take each track's own contract for
    this cycle and return the LATEST month among them; whichever leg rolled furthest
    drags the rest forward.

    Concretely (NIFTY weekly CE + MIDCPNIFTY monthly FUT, T-1, cycle 21-Mar..27-Mar
    2024): MIDCP's March future expired 22-Mar so it is already on 29-Apr, while
    NIFTY's near weekly is 28-Mar. max month -> 2024-04, so the CE leg skips the
    28-Mar weekly and takes the first April weekly (04-Apr) instead.
    """
    months = []
    for t in tracks:
        c = _holdable_contract(track_series.get(t) or [], on_iso, exit_dte, tdays)
        if c:
            months.append(c[:7])
    return max(months) if months else None


def _leg_freq(leg: dict, default_expiry: str) -> str:
    """This leg's roll FREQUENCY (not its index): WEEKLY or MONTHLY. NEXT_WEEKLY and
    friends roll on the weekly calendar, so anything WEEK* buckets to WEEKLY."""
    return "WEEKLY" if _leg_expiry(leg, default_expiry).startswith("WEEK") else "MONTHLY"


def _sync_tracks(legs: List[dict], default_index: str, default_expiry: str) -> List[Tuple[str, str, str]]:
    """The distinct roll calendars in play, as (index, frequency, segment) triples, in
    leg order. Two legs on the same index+frequency+segment share one contract and one
    calendar, so they collapse to a single track.

    SEGMENT is part of the identity because futures and options do not share a monthly
    expiry (MIDCPNIFTY 2023-09: option 29-Sep vs future 25-Sep) — a NIFTY-FUT leg and a
    NIFTY-OPT leg genuinely hold two different contracts and both must be walked so that
    min(held) sees the earlier one. Callers that decide the SHAPE must still bucket on
    (index, frequency) only — see _build_sync_cycles."""
    out: List[Tuple[str, str, str]] = []
    for leg in legs:
        t = (_leg_index(leg, default_index), _leg_freq(leg, default_expiry), _leg_segment(leg))
        if t not in out:
            out.append(t)
    return out


def _next_contract_after(series: List[str], held: str) -> Optional[str]:
    """The contract one step past `held` on its own calendar (ascending series)."""
    for e in series:
        if e > held:
            return e
    return None


def _stateful_advance_cycles(tracks: List[Tuple[str, str, str]], base_track: Tuple[str, str, str],
                             from_iso: str, to_iso: str, exit_dte: int,
                             tdays: List[str]) -> Tuple[List[Dict[str, str]], List[str]]:
    """SAME-FREQUENCY (Shape B) schedule — e.g. NIFTY monthly + MIDCPNIFTY monthly.

    Every track holds a contract; the boundary is min(HELD expiries), and at each
    boundary EVERY track advances to the next contract on its own calendar — even
    the one that still had days left. That "advance both" is why this must be
    STATEFUL and cannot come from a union of the raw calendars: the union invents
    boundaries nobody is holding by then (29-Jan/29-Feb/28-Mar in 2024), because
    once both legs advance to February at 25-Jan there is no live 29-Jan contract.

        hold NIFTY 25-Jan, MIDCP 29-Jan -> min=25-Jan -> both advance
        hold NIFTY 29-Feb, MIDCP 26-Feb -> min=26-Feb -> both advance
        hold NIFTY 28-Mar, MIDCP 22-Mar -> min=22-Mar -> both advance
        => boundaries 25-Jan, 26-Feb, 22-Mar, ...  (the earliest ALTERNATES index)

    The returned contract is the BASE track's held contract — what the base leg is
    actually holding through that cycle, which may outlive the cycle's own exit.

    Returns (cycles, boundaries). The BOUNDARIES are the raw merged expiries and
    become the engine's cadence list (they drive entry/exit); the cycles pin the
    base leg's contract per window. Both are needed — see _build_sync_cycles.
    """
    import pandas as pd

    # Widen past to_iso: the last cycles need contracts that expire after the
    # backtest ends, or the walk starves early and silently truncates the run.
    wide_to = (pd.Timestamp(to_iso) + pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    series: Dict[Tuple[str, str, str], List[str]] = {}
    for t in tracks:
        s = _roll_series(t[0], t[1], from_iso, wide_to, t[2])
        if not s:
            logger.warning("[SYNC_CADENCE] no roll series for %s/%s/%s", t[0], t[1], t[2])
            return [], []
        series[t] = s

    held: Dict[Tuple[str, str, str], str] = {}
    for t in tracks:
        c = _near_contract_on(t[0], t[1], from_iso, series[t])
        if not c:
            return [], []
        held[t] = c

    # ALIGN THE START. The tracks must begin in the SAME month, or advance-both
    # preserves the skew for the whole run. from_iso can land between two indices'
    # expiries: starting 2023-06-29, NIFTY's June monthly (29-Jun) is still alive so
    # NIFTY starts on JUNE, while MIDCPNIFTY's June future (27-Jun) has already
    # expired so it starts on JULY. min() then follows NIFTY's calendar forever
    # (boundaries 27-Jul/31-Aug instead of the true earliest 26-Jul/28-Aug) and the
    # same-month floor drags NIFTY up to MIDCP's month — so every trade ends up
    # holding the NEXT month's contract. Snap every lagging track forward to the
    # latest starting month so the walk begins in step. Same family as the missing
    # 2022-01-25 launch expiry that put MIDCP a month out of step.
    _start_month = max(c[:7] for c in held.values())
    for t in tracks:
        if held[t][:7] < _start_month:
            nxt = _near_contract_on(t[0], t[1], held[t], series[t], _start_month)
            if not nxt:
                return [], []
            logger.info("[SYNC_CADENCE] start-align %s/%s/%s: %s -> %s (month %s)",
                        t[0], t[1], t[2], held[t], nxt, _start_month)
            held[t] = nxt

    cycles: List[Dict[str, str]] = []
    bounds: List[str] = []
    prev_end: Optional[str] = None
    for _ in range(10000):                       # walk guard; real runs are << this
        b = min(held.values())
        if b > to_iso:
            break
        end = _nth_trading_day_before(b, exit_dte, tdays)
        start = prev_end or from_iso
        if end and start < end:
            # The base leg's OWN held contract — no same-month bump (removed
            # 2026-07-18, see _build_sync_cycles): pushing a leg into another leg's
            # month made it abandon its own live contract. The walk already advances
            # every track together, so same-frequency legs stay in step naturally.
            cycles.append({"contract": held[base_track], "start": start, "end": end})
            bounds.append(b)
            prev_end = end
        for t in tracks:                          # advance-both: EVERY track steps on
            nxt = _next_contract_after(series[t], held[t])
            if not nxt:
                return cycles, bounds             # calendar exhausted — stop clean
            held[t] = nxt
    return cycles, bounds


def _build_sync_cycles(all_legs: List[dict], cadence: str, cadence_index: str,
                       default_index: str, default_expiry: str,
                       from_iso: str, to_iso: str,
                       payload: dict,
                       cadence_segment: str = "OPT") -> Tuple[List[Dict[str, str]], List[str]]:
    """Explicit {contract,start,end} cycles for the base run, built so that whichever
    index expires FIRST ends the cycle for EVERY leg — all legs square off and
    re-enter together, each on its own index's contract.

    That shared goal needs TWO boundary rules, because the shapes disagree on what
    happens to a leg whose contract has NOT expired at the boundary:

      * MIXED frequency (NIFTY weekly + MIDCP monthly) -> NEAR-CONTRACT / UNION.
        Boundaries = union of the raw calendars. NIFTY rolls weekly; MIDCP keeps its
        month until its OWN expiry, re-booked each cycle. Must NOT advance-both here:
        NIFTY hits a boundary every week, so stepping MIDCP each time would burn
        May->Jun->Jul in three weeks.
      * SAME frequency (NIFTY monthly + MIDCP monthly) -> STATEFUL ADVANCE-BOTH.
        Boundary = min(held expiries); at each one BOTH legs move to the next month
        even if the later still had days left. See _stateful_advance_cycles for why a
        union is wrong here (phantom boundaries nobody holds).

    Cycle N's start == cycle N-1's end, so the roll (exit old, re-enter new the same
    day) falls out of the engine's existing same-day chain — same contract as
    _build_yearly_cycles, which is what consumes this.

    Returns (cycles, boundaries). BOTH are needed, because in Rust these are two
    different jobs (simulate.rs:477-481): the cadence list `expiry_dates` drives
    ENTRY/EXIT, while `yearly_cycles` only pins the CONTRACT for whichever cycle
    owns an entry. So the merged boundaries must be handed over as the cadence — if
    we only passed cycles, the base leg would keep exiting on its OWN expiries
    (NIFTY 29-Feb) instead of the merged boundary (26-Feb), which is exactly the
    stale-price bug this feature exists to fix.

    Returns ([], []) on any doubt, and the caller then keeps the old single-index
    cadence, so this can only ever improve on the previous behaviour.
    """
    import pandas as pd
    try:
        from base import get_trading_calendar
        cal = get_trading_calendar(from_iso, to_iso)
        tdays = sorted(pd.to_datetime(cal["date"]).dt.strftime("%Y-%m-%d").tolist())
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] trading calendar failed: %s", exc)
        return [], []
    if not tdays:
        return [], []

    try:
        exit_dte = max(0, int(payload.get("exit_dte") or 0))
    except (TypeError, ValueError):
        exit_dte = 0

    # ---- Shape test: do ALL legs roll on the same frequency? ----
    tracks = _sync_tracks(all_legs, default_index, default_expiry)
    base_freq = "WEEKLY" if str(cadence).upper().startswith("WEEK") else "MONTHLY"
    base_track = (cadence_index, base_freq, cadence_segment)
    if base_track not in tracks:                 # base must be one of the tracks we walk
        logger.warning("[SYNC_CADENCE] base track %s absent from legs %s", base_track, tracks)
        return [], []
    # The shape is a question about roll FREQUENCY, so it buckets on (index, frequency)
    # and deliberately ignores the segment that _sync_tracks now splits on. Counting the
    # raw triples instead would flip a single-index OPT+FUT strategy from the union path
    # onto stateful advance-both purely because the tracks got finer-grained.
    idx_freq = {(t[0], t[1]) for t in tracks}
    if len({t[1] for t in tracks}) == 1 and len(idx_freq) > 1:
        logger.info("[SYNC_CADENCE] same-frequency shape -> stateful advance-both over %s", tracks)
        return _stateful_advance_cycles(tracks, base_track, from_iso, to_iso, exit_dte, tdays)

    # ---- Mixed frequency: near-contract / union of the raw calendars ----
    raw_bounds = _merged_roll_boundaries(all_legs, default_index, default_expiry, from_iso, to_iso)
    # Widen past to_iso for the same reason _stateful_advance_cycles does: the last
    # cycles need contracts that expire after the backtest ends.
    wide_to = (pd.Timestamp(to_iso) + pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    base_series = _roll_series(cadence_index, cadence, from_iso, wide_to, cadence_segment)
    if not raw_bounds or not base_series:
        return [], []
    track_series = {t: _roll_series(t[0], t[1], from_iso, wide_to, t[2]) for t in tracks}

    cycles: List[Dict[str, str]] = []
    bounds: List[str] = []
    prev_end: Optional[str] = None

    # ── Stateful walk (replaces the raw union of calendars) ──────────────────────
    # The earliest T-n roll across the legs ends the cycle and BOTH legs exit. What
    # they re-enter depends on WHICH leg triggered:
    #   * MONTHLY leg triggered -> every leg advances. Otherwise the weekly leg would
    #     re-enter a contract that dies inside the new cycle, forcing an immediate
    #     second exit: the 25-Jul-2023 case, where MIDCPNIFTY's future rolled on 25-Jul
    #     but the CE stayed on the 27-Jul weekly, producing a 1-day trade and a wash
    #     round-trip that entered and exited the SAME 28-Aug future a day apart.
    #   * WEEKLY leg triggered -> only it advances; the monthly holds its live contract
    #     to its own T-n. (Anything else would roll the future weekly — the tradesheet
    #     shows one future held across four weekly cycles with the prices chained.)
    # The union approach could not express this: it emitted a cycle per raw expiry, so
    # a monthly roll landing mid-week always chopped that week in two.
    #
    # Only the BOUNDARIES change here. Each cycle's contract is still chosen by
    # _holdable_contract at the cycle END, so merging 25-Jul..26-Jul into 25-Jul..02-Aug
    # makes the 27-Jul weekly unholdable and the CE lands on 03-Aug by itself.
    _is_monthly = lambda t: not str(t[1] or "").upper().startswith("WEEK")
    _pos = {t: 0 for t in tracks}
    for t in tracks:                      # seat each track on its first live contract
        s = track_series.get(t) or []
        while _pos[t] < len(s):
            r = _nth_trading_day_before(s[_pos[t]], exit_dte, tdays)
            if r is not None and r > from_iso:
                break
            _pos[t] += 1

    _walk_bounds: List[str] = []
    for _ in range(4000):                 # bounded; real runs need a few hundred
        rolls = {}
        for t in tracks:
            s = track_series.get(t) or []
            if _pos[t] >= len(s):
                rolls = {}
                break
            r = _nth_trading_day_before(s[_pos[t]], exit_dte, tdays)
            if r is None:
                rolls = {}
                break
            rolls[t] = r
        if not rolls:
            break
        _end = min(rolls.values())
        if _end > to_iso:
            break
        _trig = [t for t in tracks if rolls[t] == _end]
        # the cadence boundary is the expiry of whichever contract just rolled
        _walk_bounds.append((track_series[_trig[0]])[_pos[_trig[0]]])
        _monthly_fired = any(_is_monthly(t) for t in _trig)
        # PAIR-TRADE ALIGNMENT. The legs hedge each other, so leaving one on a
        # September future while the other moves to an October weekly breaks the
        # offset the strategy depends on. When the WEEKLY triggers and a monthly
        # leg's contract is nearly dead anyway, retire it early so both legs move
        # together (25-Sep-2024: the MIDCPNIFTY 30-Sep future had 2 days left while
        # the CE went to the 03-Oct weekly).
        # Bounded at 3 days ON PURPOSE — an unconditional same-month rule was tried
        # and reverted on 2026-07-18 because it pushed legs off contracts that were
        # still healthy (see the note in the union path below). Measured over
        # 2023-06..2026-06 there are 7 different-month boundaries: six have 1-3 days
        # left (align, cost is trivial) and one has 35 (2025-04-23 — aligning there
        # would make the weekly leg hold a month-dated option, changing what the
        # strategy trades). The data has no case between 3 and 35, so the threshold
        # separates two genuinely different situations rather than tuning a knob.
        # LIQUIDITY GUARD on the early roll. Retiring the monthly a day or two early
        # is only safe if the contract we move INTO is actually traded — otherwise we
        # swap a live, liquid future for one with no market. Measured over
        # 2023-06..2026-06 the rule fires 17 times; the incoming contract's volume as
        # a share of the outgoing one's runs 2%, 5%, 12% | 20%, 24%, 24%, 31%, 31%,
        # 38%, 50%, 56%, 60%, 67%, 80%, 84%, 86%, 99%. Three are dead — worst is
        # 20-Sep-2023, where the 30-Oct future had 212 lots against September's 4,059
        # and did not clear 1,600 until September expired. Unlike the 3-day cut (a
        # clean 3-vs-35 split) this is a continuum, so 15% is a judgement call placed
        # in the widest low-end gap (11.7% -> 20.2%) rather than an obvious boundary.
        # RELATIVE, not absolute: MIDCPNIFTY near-month volume went ~4k (2023) to ~21k
        # (2024), so a fixed lot count would drift with the liquidity regime.
        # PAIR-TRADE ALIGNMENT, same-month test. The legs hedge each other, so they
        # should sit on the same month wherever the calendar allows it. When the WEEKLY
        # triggers, retire the monthly early ONLY IF its next contract lands in the same
        # month the weekly is moving to — that is the only case where moving early
        # actually buys alignment:
        #   25-Sep-2024  CE -> 03-Oct (Oct), FUT -> 28-Oct (Oct)  SAME  -> roll early
        #   20-Sep-2023  CE -> 28-Sep (Sep), FUT -> 30-Oct (Oct)  DIFF  -> hold, let the
        #                FUT's own 22-Sep roll end the cycle on the liquid Sept contract
        #   23-Oct-2024  CE -> 31-Oct (Oct), FUT -> 25-Nov (Nov)  DIFF  -> hold, cycle
        #                ends 25-Oct with both legs still on October
        # This REPLACES an earlier days-remaining threshold (N<=3): the day count was a
        # proxy for "is the roll cheap", but the thing that matters is whether it makes
        # the pair match. The same-month test excludes the cases the threshold got wrong
        # without needing to tune a number.
        # NOTE it cannot fix every mismatch, and must not try: once a month's future has
        # expired (Oct-2024 died 28-Oct, Apr-2025 died 24-Apr) its weeklies run on with
        # no same-month future left to hold. Forcing alignment there means the weekly
        # abandoning the month's MOST liquid contract (30-Apr-2025 traded 5.86M lots vs
        # 362k on 08-May) — that is the floor reverted on 2026-07-18; see the note in the
        # union path below. Those tails stay cross-month by design.
        #
        # There is deliberately NO liquidity condition on the early roll. An earlier
        # version required the incoming contract to trade >=15% of the outgoing one's
        # volume; across 2023-04..2026-06 that fired exactly once (24-May-2023, entering
        # 27-Jun-2023 at 0 lots vs 8) and its only effect was to break the pair on that
        # date. MIDCPNIFTY futures are thin enough in 2023 that the FRONT month traded 8
        # lots, so a volume ratio there compares noise with noise while the strategy is
        # already holding those contracts. Pairing is structural; liquidity is a separate
        # concern and must not silently override it.
        _weekly_fired = any(not _is_monthly(t) for t in _trig)
        _wk_month = None
        for _wt in _trig:
            if not _is_monthly(_wt):
                _ws = track_series.get(_wt) or []
                if _pos[_wt] + 1 < len(_ws):
                    _wk_month = _ws[_pos[_wt] + 1][:7]
                break
        for t in tracks:
            _adv = t in _trig or (_monthly_fired and not _is_monthly(t))
            if (not _adv) and _weekly_fired and _is_monthly(t) and _wk_month:
                _ser = track_series.get(t) or []
                _nxt = _ser[_pos[t] + 1] if _pos[t] + 1 < len(_ser) else None
                if _nxt is not None and _nxt[:7] == _wk_month:
                    _adv = True
                    logger.info("[SYNC_CADENCE] early roll at %s: %s -> %s to pair with "
                                "the weekly's %s contract",
                                _end, _ser[_pos[t]], _nxt, _wk_month)
            if _adv:
                _pos[t] += 1
    if _walk_bounds:
        raw_bounds = _walk_bounds

    for b in raw_bounds:
        end = _nth_trading_day_before(b, exit_dte, tdays)
        if not end:
            continue
        start = prev_end or from_iso
        if start >= end:
            continue
        # The BASE leg's contract for this cycle, under the SAME T-n rule the overlay
        # uses (_holdable): "nearest expiry >= exit" was T-n BLIND, so on a cycle whose
        # exit lands on a weekly expiry the base sat on that contract and died on it —
        # exit date == expiry date, which T-n says must never happen.
        #
        # NO same-month floor here (removed 2026-07-18). Forcing a leg into another
        # leg's month made it ABANDON ITS OWN LIVE CONTRACT, in both directions:
        #   * FUT: cycle 24-Dec..27-Dec 2024 exists because MIDCP's Dec future expires
        #     30-Dec (T-1 = 27-Dec). The CE sat on 02-Jan (Jan), so the floor was Jan
        #     and the FUT was pushed off its own live 30-Dec onto 30-Jan — 31 days
        #     early, on the very contract whose expiry created the cycle.
        #   * CE: after MIDCP's 25-Nov-2024 expiry the FUT is necessarily on 30-Dec, so
        #     the floor was Dec and the weekly CE skipped the live 28-Nov (12,468 rows)
        #     for 05-Dec. Same on 30-Apr-2025.
        # The floor is also REDUNDANT: a leg whose own month is genuinely dead is
        # skipped by the T-n test anyway and lands on the later month by itself. So
        # each leg simply takes its own near HOLDABLE contract; legs may therefore
        # show different months for the few days when one index's monthly has expired
        # and the other's has not — that is the real market state.
        contract = _holdable_contract(base_series, end, exit_dte, tdays)
        if not contract:
            continue
        cycles.append({"contract": contract, "start": start, "end": end})
        bounds.append(b)
        prev_end = end
    return cycles, bounds


def _data_expiries(symbol, instrument_like, from_iso):
    """All distinct expiry dates present in the data for symbol+instrument,
    sorted ISO strings. The caller picks the nearest >= the trade exit that
    actually has a price on the entry date (avoids the incomplete expiry
    calendar AND sparse/far stray expiries). Memoized per data_version."""
    try:
        from services.backtest_cache import get_data_version
        _dv = get_data_version() or "0"
    except Exception:
        _dv = "0"
    _ckey = (symbol.upper(), instrument_like, from_iso, _dv)
    _hit = _DATA_EXPIRIES_CACHE.get(_ckey)
    if _hit is not None:
        return _hit
    from database import get_engine
    from sqlalchemy import text
    try:
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT expiry_date FROM option_data "
                    "WHERE symbol = :s AND instrument LIKE :inst AND expiry_date >= :f "
                    "ORDER BY expiry_date"
                ),
                {"s": symbol.upper(), "inst": instrument_like, "f": from_iso},
            ).fetchall()
    except Exception:
        return []
    import pandas as pd
    out = []
    for (e,) in rows:
        if e is not None:
            out.append(pd.Timestamp(e).strftime("%Y-%m-%d"))
    _DATA_EXPIRIES_CACHE[_ckey] = out
    return out


_EXPIRY_LAST_TRADED_CACHE: Dict[tuple, Dict[str, str]] = {}
_DATA_SESSIONS_CACHE: Dict[tuple, List[str]] = {}


def _data_version() -> str:
    try:
        from services.backtest_cache import get_data_version
        return get_data_version() or "0"
    except Exception:
        return "0"


def _expiry_last_traded(symbol, instrument_like, from_iso) -> Dict[str, str]:
    """{expiry_date: LAST date that contract actually traded}, for symbol+instrument.

    Feeds the stray/orphan guard in `_roll_series`: a REAL contract trades through
    to the last session on or before its own expiry, while a stray or a relabel
    orphan goes dead weeks earlier. Memoized per data_version, same as
    `_data_expiries`."""
    _ckey = (str(symbol).upper(), instrument_like, str(from_iso), _data_version())
    _hit = _EXPIRY_LAST_TRADED_CACHE.get(_ckey)
    if _hit is not None:
        return _hit
    from database import get_engine
    from sqlalchemy import text
    import pandas as pd
    try:
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT expiry_date, MAX(date) FROM option_data "
                    "WHERE symbol = :s AND instrument LIKE :inst AND expiry_date >= :f "
                    "GROUP BY expiry_date"
                ),
                {"s": str(symbol).upper(), "inst": instrument_like, "f": from_iso},
            ).fetchall()
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] expiry last-traded query failed for %s/%s: %s",
                       symbol, instrument_like, exc)
        return {}
    out: Dict[str, str] = {}
    for e, d in rows:
        if e is not None and d is not None:
            out[pd.Timestamp(e).strftime("%Y-%m-%d")] = pd.Timestamp(d).strftime("%Y-%m-%d")
    _EXPIRY_LAST_TRADED_CACHE[_ckey] = out
    return out


_FUT_ILLIQUID_CACHE: Dict[tuple, set] = {}


def _fut_illiquid_days(symbol, from_iso) -> set:
    """Set of (date_iso, expiry_iso) futures bars that DID NOT TRADE — contracts
    (volume) is 0 or NULL. Their `close` is a stale carry-forward, not a real mark
    (MIDCPNIFTY 27-Jun future on 24-May-2023: contracts=0, close=6745 while
    settled=7643.85). The overlay uses this to BLANK such a leg instead of pricing
    off the stale close, so the bad data is visible rather than fabricating P&L.
    Memoized per data_version, same as _data_expiries."""
    _ckey = (str(symbol).upper(), str(from_iso), _data_version())
    _hit = _FUT_ILLIQUID_CACHE.get(_ckey)
    if _hit is not None:
        return _hit
    from database import get_engine
    from sqlalchemy import text
    import pandas as pd
    out: set = set()
    try:
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT date, expiry_date FROM option_data "
                     "WHERE symbol = :s AND instrument LIKE 'FUT%' AND date >= :f "
                     "AND (contracts IS NULL OR contracts = 0)"),
                {"s": str(symbol).upper(), "f": from_iso},
            ).fetchall()
        for d, e in rows:
            if d is not None and e is not None:
                out.add((pd.Timestamp(d).strftime("%Y-%m-%d"),
                         pd.Timestamp(e).strftime("%Y-%m-%d")))
    except Exception as exc:
        logger.warning("[MULTI_INDEX] futures illiquidity query failed for %s: %s", symbol, exc)
        return set()
    _FUT_ILLIQUID_CACHE[_ckey] = out
    return out


def _data_sessions(symbol, from_iso) -> List[str]:
    """Every session this symbol actually traded, ascending ISO.

    Used INSTEAD of the trading calendar for the stray guard so the guard is
    self-consistent with the `_expiry_last_traded` dates it compares against, and
    so it keeps working past the calendar's range end. NSE special sessions
    (Sat 20-Jan-2024) are present here because they are present in the data —
    which is exactly why MIDCPNIFTY's holiday-shifted 22-Jan-2024 expiry survives
    the guard. Memoized per data_version."""
    _ckey = (str(symbol).upper(), str(from_iso), _data_version())
    _hit = _DATA_SESSIONS_CACHE.get(_ckey)
    if _hit is not None:
        return _hit
    from database import get_engine
    from sqlalchemy import text
    import pandas as pd
    try:
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT date FROM option_data "
                     "WHERE symbol = :s AND date >= :f ORDER BY date"),
                {"s": str(symbol).upper(), "f": from_iso},
            ).fetchall()
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] session query failed for %s: %s", symbol, exc)
        return []
    out = [pd.Timestamp(d).strftime("%Y-%m-%d") for (d,) in rows if d is not None]
    _DATA_SESSIONS_CACHE[_ckey] = out
    return out


def _reload_bulk_if_needed(symbol: str, from_date: str, to_date: str) -> None:
    """Bulk-load `symbol` for [from_date, to_date] into the native cache and
    fast-lookup dicts, UNLESS this exact (symbol, range, data_version) is
    already the active load in this process — then it's a no-op.

    An optimizer sweep calls this once per combo with the SAME cadence index
    and date range every time (only strategy params vary between combos), so
    without this guard every combo was clearing and rebuilding the whole
    native cache (millions of rows) from scratch — that per-combo teardown is
    what let the pool worker's RSS climb until Celery's --max-memory-per-child
    recycled it mid-sweep. data_version is included so a real data change
    (e.g. a fresh import) still forces a genuine reload, same as the identical
    memoization pattern in `_data_expiries` above."""
    global _last_bulk_load_key
    from base import bulk_load_options, bulk_clear_options
    from services.algotest_job import _build_fast_lookup_from_bulk, _safe_clear_fast_lookup
    try:
        from services.backtest_cache import get_data_version
        _dv = get_data_version() or "0"
    except Exception:
        _dv = "0"
    key = (str(symbol).upper(), str(from_date), str(to_date), _dv)
    if _last_bulk_load_key == key:
        return
    try:
        _safe_clear_fast_lookup()
        bulk_clear_options()
    except Exception:
        pass
    bulk_load_options(symbol, from_date, to_date)
    _build_fast_lookup_from_bulk(symbol, from_date, to_date)
    _last_bulk_load_key = key


def _overlay_legs_onto_base(base_df, overlay_legs, default_index, effective_from, effective_to,
                            exit_dte: int = 0,
                            sched_end_by_entry: Optional[List[Tuple[str, str]]] = None,
                            yearly_roll_months: Optional[List[str]] = None,
                            yearly_cycles: Optional[List[Dict[str, str]]] = None):
    """Case A: for EACH base trade [entry,exit], price each overlay leg (other
    index) over that SAME window and return them as extra Leg rows sharing the
    trade's id/dates. Futures priced in Rust (get_future_price); options via the
    existing DB premium lookup. The overlay contract is the near one that the engine
    would still be HOLDING at the trade's exit under this strategy's T-n rule —
    hence `exit_dte`, which must match the base run's (see `_holdable`)."""
    import bisect
    import pandas as pd
    from base import get_expiry_dates
    from services.index_metadata import get_lot_size_for_index, get_index_config
    from services import rust_fast_path as rf
    from services.futures_cache_store import ensure_futures_loaded

    # Pricing here is Rust-only: ensure_symbol_merged() below makes each overlay
    # index resident in the native cache, which is symbol-keyed. (The old DB loader
    # existed because base.get_*_from_db is monkey-patched to ignore the symbol and
    # return the currently-loaded base index's spot/premium — Rust's symbol_ids
    # lookup has no such flaw.)
    try:
        from services.engine_rust import _compute_strike_for_leg_python
    except Exception:
        _compute_strike_for_leg_python = None

    by_sym: Dict[str, List[dict]] = {}
    # Roll months a YEARLY overlay leg may pin to (NSE lists long-dated contracts
    # only in Mar/Jun/Sep/Dec). Defaults to December, matching the engine's own
    # default, and is unused unless some leg is actually YEARLY.
    _yr_roll_months = {
        str(m).zfill(2) for m in (yearly_roll_months or ["12"])
    } or {"12"}
    # Engine-resolved {contract,start,end} windows for a YEARLY overlay leg. When
    # present _pick_yearly reads the contract straight off them, so the roll date
    # matches the base engine's yearly T-n exactly instead of being re-derived.
    _yr_cycles = list(yearly_cycles or [])
    for leg in overlay_legs:
        by_sym.setdefault(_leg_index(leg, default_index), []).append(leg)

    # Monthly contracts derived from the ACTUAL data (complete + per-instrument),
    # because options and futures can have different monthly expiry dates and the
    # expiry calendar is incomplete at the range ends.
    sym_opt_exp: Dict[str, List[str]] = {}
    sym_fut_exp: Dict[str, List[str]] = {}
    sym_fut_illiq: Dict[str, set] = {}
    for sym, slegs in by_sym.items():
        sym_opt_exp[sym] = _data_expiries(sym, "OPT%", effective_from)
        sym_fut_exp[sym] = _data_expiries(sym, "FUT%", effective_from)
        sym_fut_illiq[sym] = _fut_illiquid_days(sym, effective_from)
        if any(str(l.get("segment") or "").upper() in ("FUTURE", "FUTURES") for l in slegs):
            ensure_futures_loaded(sym)
        # Put this overlay index's OPTION/SPOT data into the Rust cache ALONGSIDE the
        # base index. load_cache only ever holds the base symbol (it replaces), which
        # is precisely why the pricing below had to reach for Postgres; merge_cache is
        # additive, so those lookups now hit Rust instead. Costs ~600 MB for MIDCPNIFTY
        # (measured), and only for multi-index runs.
        rf.ensure_symbol_merged(sym)

    # Trading-day calendar for the leg MAE/MFE scans. The FUTURES scan takes the sorted
    # ISO list (_sorted_td); the OPTIONS scan takes the calendar DataFrame (_cal) — keep
    # both, and keep _cal defined even on failure so the option branch can't NameError.
    _cal = None
    try:
        from base import get_trading_calendar as _gtc
        _cal = _gtc(effective_from, effective_to)
        _sorted_td = sorted(pd.to_datetime(_cal["date"]).dt.strftime("%Y-%m-%d").tolist()) if _cal is not None else []
    except Exception:
        _cal = None
        _sorted_td = []

    def _holdable(e, exit_iso, floor_month=None):
        # SAME-MONTH rule: never sit in an earlier expiry month than a leg that has
        # already rolled on. The base leg's cycle contract carries the floor (the base
        # cycle builder set it to the latest month across all legs), so an overlay leg
        # whose own near contract is a month behind is dragged forward to match.
        if floor_month and e[:7] < floor_month:
            return False
        # Would the engine still be HOLDING the contract expiring on `e` at this
        # cycle's exit? A leg exits T-n trading days before its own expiry, so `e`
        # only qualifies while its T-n roll date is still >= the exit.
        #
        # Without this the overlay was T-n BLIND — it asked only "is `e` alive at the
        # exit?" while the base engine honours T-n, so the legs drifted onto different
        # contracts. Proof (T7_T7, trade 29, 17-Jun->20-Jun 2025): the NIFTY CE base
        # rolled to 31-Jul at T-7 ahead of its 26-Jun expiry while the MIDCP FUT
        # overlay kept 26-Jun — a MONTH apart. Weekly has the identical defect with a
        # smaller step, so BOTH pickers gate on this.
        #
        # At exit_dte=0 this reduces to the previous `e >= exit_iso` test (T-0 snaps
        # `e` back to its own session), so the default path is unchanged.
        if not _sorted_td:
            return e >= exit_iso              # no calendar -> previous behaviour
        roll = _nth_trading_day_before(e, exit_dte, _sorted_td)
        return roll is not None and roll >= exit_iso

    def _pick_monthly(exps, exit_iso, ok, limit_months=4, floor_month=None):
        # The MONTHLY contract = the LATEST expiry in the nearest month still HOLDABLE
        # at the exit that has data (validated by `ok`). Walking month-by-month and
        # taking the last expiry of the month: (a) ignores weekly contracts in the old
        # regime (picks the monthly), and (b) skips stray/sparse late expiries by
        # falling back to the actual traded contract within that month.
        months: List[str] = []
        by_month: Dict[str, List[str]] = {}
        for e in exps:
            if _holdable(e, exit_iso, floor_month):
                ym = e[:7]
                if ym not in by_month:
                    by_month[ym] = []
                    months.append(ym)
                by_month[ym].append(e)
        for ym in months[:limit_months]:
            for e in sorted(by_month[ym], reverse=True):  # latest-in-month first
                if ok(e):
                    return e
        return None

    def _pick_weekly(exps, exit_iso, ok, limit=8, floor_month=None):
        # The WEEKLY contract = the NEAREST still-HOLDABLE expiry that has data
        # (validated by `ok`). For a weekly base cadence the MIDCPNIFTY weekly alive
        # across [entry, exit] is the first such expiry. Scans the nearest few
        # (covers weeklies where they exist 2022->late-2024; if only monthlies
        # exist in the window this naturally lands on the nearest monthly).
        cnt = 0
        for e in exps:  # ascending
            if _holdable(e, exit_iso, floor_month):
                cnt += 1
                if ok(e):
                    return e
                if cnt >= limit:
                    break
        return None

    def _pick_yearly(exps, exit_iso, ok, limit=None, floor_month=None):
        # Preferred path: use the engine's OWN resolved cycles, so the roll fires on
        # exactly the same date the base yearly engine would. _holdable only knows
        # the trade's exit DTE (1 trading day), NOT yearly_exit_months_before — so
        # deriving the roll here made T-1 behave as T-0: the contract was held to
        # its December expiry instead of rolling in November (observed 26-Dec-2024
        # held until the 26-Dec-2024 entry, then straight to 30-Dec-2025).
        if _yr_cycles:
            for _c in _yr_cycles:
                if str(_c.get("start")) <= exit_iso < str(_c.get("end")):
                    _ct = str(_c.get("contract") or "")
                    return _ct if (_ct and ok(_ct)) else None
            return None
        # The YEARLY contract = the nearest LONG-DATED contract (a roll-month
        # expiry, December by default) still HOLDABLE at the exit. Unlike
        # _pick_monthly this deliberately SKIPS the near contract: a yearly leg
        # pins one December and re-books against it every cadence, so the picker
        # must look past every intervening monthly/weekly.
        #
        # Needed because a genuinely-yearly leg reaching this path previously fell
        # through to _pick_monthly (`_pick = _pick_weekly if WEEK else _pick_monthly`)
        # and silently took the near monthly — the leg then rolled monthly while the
        # tradesheet still called it Yearly.
        # Month-membership alone is NOT enough: NSE also lists ordinary weekly and
        # monthly contracts inside March/December, and taking the first ascending
        # match picked those (observed 2025-03-06 and 2025-12-02 — short-dated
        # expiries that merely fall in a roll month). The long-dated contract is
        # the LAST expiry of its roll month, so group by month and take the latest
        # in each — the same "latest-in-month first" rule _pick_monthly uses.
        _by_ym: Dict[str, List[str]] = {}
        _yms: List[str] = []
        for e in exps:  # ascending
            if e[5:7] not in _yr_roll_months:
                continue
            if not _holdable(e, exit_iso, floor_month):
                continue
            ym = e[:7]
            if ym not in _by_ym:
                _by_ym[ym] = []
                _yms.append(ym)
            _by_ym[ym].append(e)
        for ym in _yms:  # nearest roll month first
            for e in sorted(_by_ym[ym], reverse=True):  # latest-in-month first
                if ok(e):
                    return e
        return None

    base = base_df.copy()
    for c in ("Entry Date", "Exit Date"):
        if c in base.columns:
            base[c] = pd.to_datetime(base[c], errors="coerce")

    rows: List[dict] = []
    for tid, grp in base.groupby("Trade"):
        entry_dt = grp["Entry Date"].min()
        exit_dt = grp["Exit Date"].max()
        if pd.isna(entry_dt) or pd.isna(exit_dt):
            continue
        entry_iso = entry_dt.strftime("%Y-%m-%d")
        exit_iso = exit_dt.strftime("%Y-%m-%d")
        # LOOKAHEAD FIX. Pick the contract against the cycle's SCHEDULED end, not
        # the actual exit. When spot-adj (or an SL) truncates a trade mid-cycle the
        # actual exit moves EARLIER, which can leave a contract that was about to
        # roll still "holdable" — so the overlay leg sat in a different month from
        # the base leg (25-Oct-2023: CE 02-Nov vs FUT 30-Oct; 24-Jul-2024: CE 01-Aug
        # vs FUT 29-Jul). It is also lookahead: at ENTRY you cannot know the trade
        # will be cut short, so you could only have bought the contract that covers
        # the scheduled window. Pricing still uses the ACTUAL entry/exit dates —
        # only holdability moves.
        # Resolve by CONTAINMENT, not by exact cycle-start match: a same-day
        # spot-adj re-entry starts mid-cycle, and it needs the same scheduled end
        # as the trade it continues.
        # HALF-OPEN [start, end): consecutive cycles SHARE a boundary date (one
        # cycle's end is the next one's start), so a closed test matches the
        # PREVIOUS cycle on that date and hands back an end that is the entry
        # itself — which defeats the whole fix. The trade that opens on a
        # boundary belongs to the cycle STARTING there.
        sel_iso = exit_iso
        for _cs, _ce in (sched_end_by_entry or ()):
            if _cs <= entry_iso < _ce:
                sel_iso = _ce
                break
            if _cs > entry_iso:
                break                   # sorted; past this entry
        if sel_iso < exit_iso:
            sel_iso = exit_iso          # never select on a window shorter than real
        max_leg = int(grp["Leg"].max()) if "Leg" in grp.columns else 1
        leg_off = 0
        # No same-month floor (removed 2026-07-18 — see _build_sync_cycles). Each
        # overlay leg takes its OWN near HOLDABLE contract; it is never dragged into
        # the base leg's month, because doing so pushed legs off live contracts (the
        # MIDCP 30-Dec-2024 future and the NIFTY 28-Nov-2024 / 30-Apr-2025 weeklies).
        _floor_month = None
        _sw_ctx_cache: Dict[tuple, Any] = {}   # memo for _atm_straddle_prices
        for sym, slegs in by_sym.items():
            cfg = get_index_config(sym)
            # This index's NATIVE listing step (NIFTY 50 / BANKNIFTY 100 /
            # MIDCPNIFTY 25). Used only as the fallback when a leg has no
            # configured strike gap — see `interval` inside the leg loop.
            _idx_interval = getattr(cfg, "strike_interval", 25) or 25
            # Rust-only (no Postgres fallback). ensure_symbol_merged() above put this
            # index's feather in the native cache, so Rust is authoritative here and a
            # miss means a genuine data gap, not "not loaded yet". Failing loudly is
            # mandatory: espot/xspot of None degrades to 0.0 downstream, which would
            # silently corrupt strike selection and % P&L rather than error.
            espot = rf.get_spot_price(entry_iso, sym)
            xspot = rf.get_spot_price(exit_iso, sym)
            if espot is None or xspot is None:
                _miss = entry_iso if espot is None else exit_iso
                raise RuntimeError(
                    f"[MULTI_INDEX] no {sym} spot in the Rust cache for {_miss}. "
                    f"The {sym} feather is missing that date — rebuild it "
                    f"(backend/rebuild_feather.py); refusing to price off a stale "
                    f"or zero spot."
                )
            fut_exps = sym_fut_exp.get(sym) or []
            opt_exps = sym_opt_exp.get(sym) or []

            # Rust-only (no Postgres fallback), same as the spot lookup above.
            # Unlike spot, None must NOT raise here: the strike/expiry search below
            # probes candidate contracts with `_premium(...) is not None` to find one
            # that exists, so None is a MEANINGFUL answer ("no such contract"), not a
            # failure to hide. Routing these probes through Postgres one at a time is
            # what produced the flood of [PERF] get_option_premium log lines.
            def _premium(_d, _s, _o, _e, _sym=sym):
                return rf.get_option_price(_d, _sym, _s, _o, _e)

            for leg in slegs:
                is_fut = str(leg.get("segment") or "").upper() in ("FUTURE", "FUTURES")
                pos = "BUY" if str(leg.get("position") or "SELL").upper().startswith("B") else "SELL"
                lots = int(leg.get("lots") or leg.get("lot") or 1)
                lot_size = int(get_lot_size_for_index(sym, entry_iso))
                # THIS leg's strike gap. The overlay used to take the index's native
                # step unconditionally, so a leg configured with (say) Strike Gap 100
                # still resolved MIDCPNIFTY strikes on its native 25 grid — the gap
                # reached the base leg (which runs through the engine) but never the
                # overlay leg. Prefer the leg's own setting, fall back to the index
                # default only when it is unset/invalid.
                try:
                    interval = int(leg.get("strike_interval") or 0) or _idx_interval
                except (TypeError, ValueError):
                    interval = _idx_interval
                if interval <= 0:
                    interval = _idx_interval
                _mae = _mfe = 0.0
                _sw_ctx: Dict[str, Any] = {}
                # PER-LEG SLIPPAGE. The base engine applies each leg's own
                # slippage_pct (_leg_slippage_pct / _apply_per_leg_slippage in
                # engine_rust); the overlay path never did, so a MIDCPNIFTY leg
                # traded at raw close regardless of what the user set. Same sign
                # convention as the base: a SELL gets LESS on entry and pays MORE
                # on exit; a BUY the reverse.
                try:
                    _leg_slip = max(0.0, float(leg.get("slippage_pct") or 0.0))
                except (TypeError, ValueError):
                    _leg_slip = 0.0
                def _slip(_e, _x, _p, _s=_leg_slip):
                    if _s <= 0:
                        return _e, _x
                    _ef = (1.0 - _s / 100.0) if _p == "SELL" else (1.0 + _s / 100.0)
                    _xf = (1.0 + _s / 100.0) if _p == "SELL" else (1.0 - _s / 100.0)
                    return (round(max(_e * _ef, 0.0), 2), round(max(_x * _xf, 0.0), 2))
                _blank_reason = None      # set when a leg is priced off a non-trading bar
                _shift_reason = ""        # set when the strike walk moved the strike
                if is_fut:
                    # monthly futures contract alive on BOTH the entry and exit day
                    contract = _pick_monthly(fut_exps, sel_iso, lambda e: rf.get_future_price(sym, entry_iso, e) is not None and rf.get_future_price(sym, exit_iso, e) is not None, floor_month=_floor_month)
                    if contract is None:
                        continue
                    ep = rf.get_future_price(sym, entry_iso, contract)
                    xp = rf.get_future_price(sym, exit_iso, contract)
                    if ep is None or xp is None:
                        continue
                    ep_raw, xp_raw = round(float(ep), 2), round(float(xp), 2)
                    ep, xp = _slip(ep_raw, xp_raw, pos)
                    # P&L = points x THIS leg's own lots (lot_size excluded — see
                    # the MAE/MFE scaling note below at the row-write site, and
                    # services/algotest_job.py for the same convention). Was left
                    # at 1x while MAE/MFE on this same row were scaled by `lots`,
                    # so a row carried 2x MAE against 1x P&L.
                    pnl = round(((xp - ep) if pos == "BUY" else (ep - xp)) * lots, 2)
                    typ, strike, ce, pe, fut = "FUT", "", 0.0, 0.0, pnl
                    # DATA GUARD: if the chosen contract DID NOT TRADE on entry and/or
                    # exit (contracts=0 -> `close` is a stale carry-forward, e.g.
                    # MIDCPNIFTY 27-Jun future on 24-May-2023: close 6745 vs settle
                    # 7643.85), do NOT fabricate a price/P&L. Leave the leg blank and
                    # record the reason so the data gap is visible in the tradesheet
                    # instead of a manufactured gain. Contract selection is unchanged —
                    # the contract IS the right one, its price on that day just isn't real.
                    _illiq = sym_fut_illiq.get(sym) or set()
                    _bad = []
                    if (entry_iso, contract) in _illiq:
                        _bad.append(f"entry {entry_iso}")
                    if (exit_iso, contract) in _illiq:
                        _bad.append(f"exit {exit_iso}")
                    if _bad:
                        _blank_reason = (f"NO_VOLUME {sym} {contract} fut did not trade on "
                                         + " & ".join(_bad) + " (stale close, price blanked)")
                    # Futures-leg MAE/MFE (FUTIDX high/low over the hold, normalized by
                    # f_entry) via the SAME routine the single-index futures path uses.
                    # Without this the overlay future reported MAE/MFE = 0.
                    try:
                        from services.engine_rust import _fut_leg_mae_mfe as _fmm
                        _m, _f = _fmm(sym, entry_iso, exit_iso, contract, ep, pos,
                                      (float(espot) if espot else ep), _sorted_td, "OVERLAY", xp)
                        _mae = round(float(_m), 4) if _m is not None else 0.0
                        _mfe = round(float(_f), 4) if _f is not None else 0.0
                    except Exception as _me:
                        logger.debug("[MULTI_INDEX] futures MAE/MFE skipped: %s", _me)
                else:
                    if espot is None:
                        continue
                    opt = str(leg.get("option_type") or "CE").upper()
                    opt = "CE" if opt in ("CALL", "CE") else "PE"
                    # Expiry basis follows the leg: WEEKLY -> nearest weekly (where
                    # they exist, e.g. 2024), else the near MONTHLY.
                    leg_exp = str(leg.get("expiry") or leg.get("expiry_type") or "MONTHLY").upper()
                    _pick = (
                        _pick_yearly if leg_exp.startswith("YEAR")
                        else _pick_weekly if leg_exp.startswith("WEEK")
                        else _pick_monthly
                    )

                    # PREMIUM-BASED STRIKE MODES on an overlay (non-base) leg.
                    # _compute_strike_for_leg_python resolves straddle_width /
                    # closest_premium / premium_gte|lte|range / atm_straddle_prem_pct only
                    # when it is ALSO given entry_date + expiry + index (it must read the
                    # real option chain to price them). This call site passed none of the
                    # three, so every premium mode returned None and the very next
                    # `if strike is None: continue` dropped the leg — silently, every
                    # cycle. A MIDCPNIFTY "Straddle Width" leg therefore produced zero
                    # rows while the NIFTY base leg (which runs through engine_rust) was
                    # fine. Only ATM/ITM/OTM/pct_of_atm ever worked here.
                    #
                    # Chicken-and-egg: the premium lookup needs an expiry, but the expiry
                    # is normally chosen AFTER the strike. Resolve it in two phases —
                    # probe an ATM strike purely to establish which contract is live and
                    # has data, then compute the real strike against that contract.
                    _sel = leg.get("strike_selection") or {}
                    _sel_type = str((_sel or {}).get("type") or "strike_type").lower().strip()
                    _needs_chain = _sel_type in (
                        "straddle_width", "atm_straddle_prem_pct",
                        "closest_premium", "premium_gte", "premium_lte", "premium_range",
                    )
                    _probe_expiry = None
                    if _needs_chain:
                        _atm_probe = round(float(espot) / interval) * interval
                        # Widen the probe outward: a thin exact-ATM strike must not stop
                        # us identifying the contract the leg would trade.
                        for _pd in (0, 1, -1, 2, -2, 3, -3):
                            _ps = _atm_probe + _pd * interval
                            _probe_expiry = _pick(
                                opt_exps, sel_iso,
                                lambda e, _s=_ps: _premium(entry_iso, _s, opt, e) is not None,
                                floor_month=_floor_month,
                            )
                            if _probe_expiry is not None:
                                break
                    try:
                        if _compute_strike_for_leg_python is None:
                            strike = round(float(espot) / interval) * interval
                        elif _needs_chain and _probe_expiry is not None:
                            strike = _compute_strike_for_leg_python(
                                leg, float(espot), interval,
                                entry_date=entry_iso, expiry=_probe_expiry, index=sym,
                            )
                        else:
                            strike = _compute_strike_for_leg_python(leg, float(espot), interval)
                    except Exception as _sx:
                        logger.debug("[MULTI_INDEX] %s strike resolve failed (%s): %s",
                                     sym, _sel_type, _sx)
                        strike = None
                    if strike is None:
                        # Do NOT fall back to plain ATM here: a premium mode that could
                        # not be resolved is a DIFFERENT strike from ATM, and silently
                        # substituting one would misreport the strategy. Skip loudly.
                        if _needs_chain:
                            logger.warning(
                                "[MULTI_INDEX] %s leg dropped on %s: strike mode '%s' "
                                "could not be resolved (probe expiry=%s)",
                                sym, entry_iso, _sel_type, _probe_expiry,
                            )
                        continue
                    # Strike-shift to the nearest traded strike (requested, then
                    # +/-1,2,3 intervals) so a thin/missing strike doesn't drop the leg.
                    base_strike = strike
                    contract = ep = xp = None
                    for _ds in (0, 1, -1, 2, -2, 3, -3):
                        cs = base_strike + _ds * interval
                        c = _pick(
                            opt_exps, sel_iso,
                            lambda e, _s=cs: _premium(entry_iso, _s, opt, e) is not None and _premium(exit_iso, _s, opt, e) is not None,
                            floor_month=_floor_month,
                        )
                        if c is not None:
                            contract, strike = c, cs
                            ep = _premium(entry_iso, strike, opt, contract)
                            xp = _premium(exit_iso, strike, opt, contract)
                            break
                    if contract is None or ep is None or xp is None:
                        continue
                    # Strike Shift Reason. The walk above moves the strike off the
                    # requested one when that contract can't be priced on both days;
                    # until now the overlay recorded NOTHING when it did, so a shifted
                    # midcap strike was invisible in the sheet. Mirrors the base
                    # engine's format exactly (engine_rust.py ~3260) — from→to, the
                    # real cause, step count on the per-index walk step, and direction
                    # — so both legs read identically in the same column.
                    if float(strike) != float(base_strike):
                        try:
                            from services.engine_rust import _liquidity_walk_step as _lws
                            _walk, _ = _lws(sym, float(interval))
                        except Exception:
                            _walk = float(interval)
                        _walk = float(_walk or interval) or float(interval)
                        _steps = max(1, int(round(abs(float(strike) - float(base_strike)) / _walk)))
                        _cause = "zero turnover"          # historical default (safe)
                        try:
                            import algotest_native  # type: ignore
                            _stfn = getattr(algotest_native, "get_option_status", None)
                            if _stfn is not None:
                                _st = _stfn(entry_iso, sym, float(base_strike), opt, contract)
                                if _st == "missing":
                                    _cause = "strike not listed"
                                elif _st == "zero_contracts":
                                    _cause = "zero turnover"
                        except Exception:
                            pass
                        _atm = (round(float(espot) / interval) * interval) if espot else float(strike)
                        _dir = ("toward ATM"
                                if abs(float(strike) - _atm) <= abs(float(base_strike) - _atm)
                                else "outward")
                        _f = lambda x: int(x) if float(x).is_integer() else round(float(x), 2)
                        _shift_reason = (
                            f"{_f(base_strike)}→{_f(strike)} "
                            f"({_cause}, {_steps} step{'s' if _steps != 1 else ''} {_dir})"
                        )
                    ep_raw, xp_raw = round(float(ep), 2), round(float(xp), 2)
                    ep, xp = _slip(ep_raw, xp_raw, pos)
                    # P&L = points x THIS leg's own lots — same convention/reason
                    # as the futures branch above.
                    pnl = round(((xp - ep) if pos == "BUY" else (ep - xp)) * lots, 2)
                    typ = opt
                    # Straddle-width context for THIS index. The base leg gets these
                    # from engine_rust; an overlay leg on another index was left blank,
                    # so a MIDCPNIFTY straddle-width leg showed no ATM strike/CE/PE at
                    # all. Same helper the base path uses, so the liquidity check and
                    # gap-widening fallback are identical — it re-reads the very prices
                    # the strike selection used, never a stale zero-turnover close.
                    # Written on the leg's OWN row (not leg 1) because this is that
                    # index's ATM, not the trade's.
                    if _needs_chain and _sel_type == "straddle_width":
                        try:
                            import algotest_native as _an
                            from services.engine_rust import _atm_straddle_prices as _asp
                            _a = _asp(_an, _sw_ctx_cache, entry_iso, sym,
                                      float(espot), float(interval), contract)
                            if _a is not None:
                                _sw_ctx = {"ATM Strike": _a[0], "ATM Call Price": _a[1],
                                           "ATM Put Price": _a[2], "ATM Call+Put Price": _a[3]}
                                if _a[4]:
                                    _sw_ctx["ATM Straddle Price Source"] = _a[4]
                        except Exception as _ax:
                            logger.debug("[MULTI_INDEX] %s ATM straddle context skipped: %s", sym, _ax)
                    ce = pnl if opt == "CE" else 0.0
                    pe = pnl if opt == "PE" else 0.0
                    fut = 0.0
                    # Option-leg MAE/MFE. Only the FUTURES branch above was wired
                    # (2026-07-15), so overlay OPTION legs reported MAE=MFE=0 — the
                    # "midcap has no MAE/MFE" report. Uses the SAME routine the
                    # single-index option path uses, against THIS leg's own index,
                    # and the strike/contract the overlay just resolved (the caller's
                    # leg dict carries neither). `_resolved_expiry` is the key
                    # _get_leg_expiry reads first.
                    try:
                        from engines.generic_algotest_engine import _calculate_leg_mae_mfe as _omm
                        _mleg = dict(leg)
                        _mleg.update({"strike": strike, "option_type": opt,
                                      "_resolved_expiry": contract})
                        _m, _f = _omm(sym, entry_iso, exit_iso, _mleg, ep, pos,
                                      (float(espot) if espot else ep), _cal,
                                      None, "OVERLAY", xp)
                        _mae = round(float(_m), 4) if _m is not None else 0.0
                        _mfe = round(float(_f), 4) if _f is not None else 0.0
                    except Exception as _me:
                        logger.debug("[MULTI_INDEX] option MAE/MFE skipped: %s", _me)
                es = float(espot) if espot else (ep if is_fut else 0.0)
                xs = float(xspot) if xspot else (xp if is_fut else 0.0)
                leg_off += 1
                # When a leg was priced off a non-trading bar, blank every price/P&L/
                # MAE/MFE field (None -> shows blank AND drops out of the P&L sum, so no
                # fabricated number reaches the parent trade) and surface the reason.
                if _blank_reason:
                    _row = {
                        "Trade": int(tid),
                        "Leg": int(leg.get("_orig_leg_no") or (max_leg + leg_off)),
                        "Index": int(tid),
                        "Entry Date": entry_dt, "Exit Date": exit_dt, "Expiry": contract,
                        "Type": typ, "Strike": strike if not is_fut else "", "B/S": pos,
                        "lots": lots,
                        "Qty": lots * lot_size, "Entry Price": None, "Exit Price": None,
                        "Entry Spot": round(es, 2), "Exit Spot": round(xs, 2),
                        "CE P&L": None, "PE P&L": None, "FUT P&L": None, "Net P&L": None,
                        "% P&L": None, "Exit Reason": _blank_reason, "MAE": None, "MFE": None,
                        "Strike Shift Reason": _shift_reason,
                        "Group Index": sym,
                        "Group Expiry": ("MONTHLY" if is_fut else str(leg.get("expiry") or leg.get("expiry_type") or "MONTHLY").upper()),
                    }
                    rows.append(_row)
                    logger.warning("[MULTI_INDEX] %s", _blank_reason)
                    continue
                rows.append({
                    # Use the leg's CONFIGURED position so the sheet keeps the user's
                    # leg order (falls back to the old append-after-base numbering
                    # when the caller didn't stamp _orig_leg_no).
                    "Trade": int(tid),
                    "Leg": int(leg.get("_orig_leg_no") or (max_leg + leg_off)),
                    "Index": int(tid),
                    "Entry Date": entry_dt, "Exit Date": exit_dt, "Expiry": contract,
                    "Type": typ, "Strike": strike if not is_fut else "", "B/S": pos,
                    # Explicit lots so downstream consumers (e.g. the charges
                    # recalc fallback in routers/backtest.py) never have to
                    # derive it from Qty/lot_size or misread "Index" — on THIS
                    # row "Index" is the numeric trade id (see `int(tid)` above
                    # and run_multi_index_feature's `combined["Index"] =
                    # combined["Trade"]`), not a symbol, so a lot_size lookup
                    # keyed off it would silently be bogus.
                    "lots": lots,
                    "Qty": lots * lot_size, "Entry Price": ep, "Exit Price": xp,
                    "Raw Entry Price": ep_raw, "Raw Exit Price": xp_raw,
                    "Entry Spot": round(es, 2), "Exit Spot": round(xs, 2),
                    # This index's OWN spot move over the hold. Was blank on overlay
                    # rows while the base leg showed it, so a MIDCPNIFTY leg gave no
                    # indication of what its underlying did. "Spot P&L %" is NOT
                    # stored — excel_builder derives it as Spot P&L / Entry Spot on
                    # each row, which for this row is MIDCPNIFTY's own spot.
                    "Spot P&L": round(xs - es, 2) if (es and xs) else 0.0,
                    "CE P&L": ce, "PE P&L": pe, "FUT P&L": fut, "Net P&L": pnl,
                    "% P&L": round(pnl / es * 100.0, 4) if es else 0.0,
                    **_sw_ctx,
                    # MAE/MFE must be commensurate with % P&L, which is lots-scaled
                    # (points x lots) — summary_metrics.rs:336 compounds NAV by
                    # % P&L while :362 applies MAE to that same NAV, so leaving
                    # MAE unscaled understates Live DD / Max DD by ~1/lots. Both
                    # _fmm (futures branch above) and _omm (option branch above)
                    # return a plain unscaled ratio into these same `_mae`/`_mfe`
                    # locals, so scale once here by THIS leg's own `lots` (the
                    # local set at the top of this leg's iteration) — lot_size
                    # excluded. Same convention as services/algotest_job.py.
                    "Exit Reason": "OVERLAY", "MAE": round(_mae * lots, 4), "MFE": round(_mfe * lots, 4),
                    "Strike Shift Reason": _shift_reason,
                    "Group Index": sym,
                    "Group Expiry": ("MONTHLY" if is_fut else str(leg.get("expiry") or leg.get("expiry_type") or "MONTHLY").upper()),
                })
    return rows


def run_multi_index_feature(
    payload: Dict[str, Any],
    effective_from: Optional[str],
    effective_to: Optional[str],
) -> Dict[str, Any]:
    """Run a multi-index / multi-expiry strategy and return the standard
    result_payload shape ({status, trades, summary, pivot, meta, cached})."""
    t0 = time.perf_counter()
    import pandas as pd
    import numpy as np

    from base import compute_analytics, build_pivot
    from services.algotest_job import _try_rust_engine, _convert_numpy, _format_dates

    default_index = str(payload.get("index") or "NIFTY").strip().upper()
    default_expiry = str(payload.get("expiry_type") or "WEEKLY").strip().upper()
    legs: List[dict] = [l for l in (payload.get("legs") or []) if isinstance(l, dict)]

    # ---- 1. Group legs by INDEX. Each index-group runs the FULL engine ----
    # Option A: every index's legs go through the existing engine path exactly
    # like a standalone single-index backtest for that index — so rollover,
    # legwise SL/target, filter, spot-adjustment, re-entry, MAE/MFE all apply to
    # every group (including MIDCPNIFTY), not just the strategy index. Each group
    # produces its OWN trades (its own expiry cadence); the groups are then merged
    # into one combined tradesheet with a single compounded equity curve.
    from collections import OrderedDict as _OrderedDict
    groups: "OrderedDict[str, List[dict]]" = _OrderedDict()
    # Strategy index first (so it stays group 0 / base), then any other indices.
    groups[default_index] = []
    for l in legs:
        groups.setdefault(_leg_index(l, default_index), [])
    for l in legs:
        groups[_leg_index(l, default_index)].append(l)
    groups = _OrderedDict((k, v) for k, v in groups.items() if v)  # drop empty
    base_index = next(iter(groups), default_index)

    group_frames: List["pd.DataFrame"] = []
    group_meta: List[dict] = []

    # ---- 2. Run each index-group through the existing engine ----
    for gid, (sym, glegs) in enumerate(groups.items()):
        grp_expiry = _group_expiry_type(sym, glegs, payload, default_index)
        sub = copy.deepcopy(payload)
        sub["legs"] = glegs
        sub["index"] = sym
        sub["expiry_type"] = grp_expiry
        sub["expiry_window"] = "weekly_expiry" if grp_expiry == "WEEKLY" else "monthly_expiry"
        sub.pop("multi_index_mode", None)  # never recurse
        gdf = None
        try:
            _ensure_group_symbol_loaded(sym, effective_from, effective_to)
            gdf, _s, _p = _try_rust_engine(sub, sym, effective_from, effective_to)
        except Exception as exc:
            logger.warning("[MULTI_INDEX] group %s failed: %s", sym, exc)
            gdf = None
        avail = gdf is not None and not getattr(gdf, "empty", True)
        if avail:
            gdf = gdf.copy()
            gdf["_grp"] = gid
            gdf["Group Index"] = sym
            gdf["Group Expiry"] = grp_expiry
            group_frames.append(gdf)
        group_meta.append({
            "index": sym, "role": ("base" if gid == 0 else "leg"), "legs": len(glegs),
            "expiry": grp_expiry,
            "trades": int(gdf["Trade"].nunique()) if avail else 0, "available": bool(avail),
        })

    meta = {
        "multi_index": True,
        "groups": group_meta,
        "indices": sorted(groups.keys()),
        "index": default_index,
        "from_date": payload.get("from_date"),
        "to_date": payload.get("to_date"),
        "slippage_pct": payload.get("slippage_pct", 0),
    }
    try:
        from services.multi_index_tradesheet import build_export_filename
        meta["export_filename"] = build_export_filename(payload)
    except Exception:
        meta["export_filename"] = "multi_index_backtest"
    try:
        from services.engine_rust import _load_filter_segments as _lfs
        _segs = _lfs(payload) or []
        meta["filter_segments"] = [{"start": s, "end": e} for (s, e) in _segs]
    except Exception:
        meta["filter_segments"] = payload.get("filter_segments") or []

    if not group_frames:
        logger.info("[MULTI_INDEX] no base trades (%.2fs)", time.perf_counter() - t0)
        return {
            "status": "success",
            "trades": [],
            "summary": {},
            "pivot": {"headers": [], "rows": []},
            "meta": _convert_numpy(meta),
            "cached": False,
        }

    # ---- 3. Merge groups into one tradesheet ----
    combined = pd.concat(group_frames, ignore_index=True)
    for c in ("Entry Date", "Exit Date"):
        if c in combined.columns:
            combined[c] = pd.to_datetime(combined[c], errors="coerce")
    if "Leg" not in combined.columns:
        combined["Leg"] = 1

    # Globally-unique, chronological trade numbering. A unique trade never spans
    # two groups (uid includes the group index), so each group's per-leg
    # Net P&L parent-row convention is preserved.
    combined["_uid"] = combined["_grp"].astype(str) + ":" + combined["Trade"].astype(str)
    first_entry = combined.groupby("_uid")["Entry Date"].min()
    uid_order = first_entry.sort_values(kind="stable").index.tolist()
    uid_to_new = {uid: i + 1 for i, uid in enumerate(uid_order)}
    combined["Trade"] = combined["_uid"].map(uid_to_new).astype(int)
    combined["Index"] = combined["Trade"]

    for col in ("CE P&L", "PE P&L", "FUT P&L", "Spot P&L", "Entry Spot", "Exit Spot"):
        if col not in combined.columns:
            combined[col] = 0.0

    agg_spec = {
        "Entry Date": "first",
        "Exit Date": "first",
        "Entry Spot": "first",
        "Exit Spot": "first",
        "Spot P&L": "first",
        "CE P&L": "sum",
        "PE P&L": "sum",
        "FUT P&L": "sum",
    }
    if "Exit Reason" in combined.columns:
        agg_spec["Exit Reason"] = "first"
    agg = combined.groupby("Trade", as_index=False).agg(agg_spec)
    agg["Net P&L"] = agg["CE P&L"] + agg["PE P&L"] + agg["FUT P&L"]
    _es = agg["Entry Spot"].replace(0, np.nan)
    agg["% P&L"] = (agg["Net P&L"] / _es * 100.0).round(2).fillna(0)
    agg = agg.sort_values("Entry Date").reset_index(drop=True)

    # Combined base-100 compound equity (same convention as engine_rust path).
    cum = peak = 100.0
    cs, ps, ds, pds = [], [], [], []
    for _, r in agg.iterrows():
        es = float(r["Entry Spot"]) if r["Entry Spot"] else 0.0
        npl = float(r["Net P&L"]) if r["Net P&L"] else 0.0
        pct = (npl / es * 100.0) if es != 0 else 0.0
        cum = cum * (1.0 + pct / 100.0)
        peak = max(cum, peak)
        dd = cum - peak
        pct_dd = (dd / peak) if peak != 0 else 0.0
        cs.append(cum); ps.append(peak); ds.append(dd); pds.append(pct_dd)
    agg["Cumulative"], agg["Peak"], agg["DD"], agg["%DD"] = cs, ps, ds, pds

    # Summary via the existing compute_analytics (dd-mm-yyyy strings like engine).
    dfa = agg.copy()
    for c in ("Entry Date", "Exit Date"):
        dfa[c] = pd.to_datetime(dfa[c]).dt.strftime("%d-%m-%Y")
    try:
        dfa, summary = compute_analytics(dfa)
        for col in ("Cumulative", "Peak", "DD", "%DD"):
            if col in dfa.columns:
                agg[col] = dfa[col].values
    except Exception as exc:
        logger.warning("[MULTI_INDEX] compute_analytics failed: %s", exc)
        summary = {}

    # cagr_spot must be measured on the OPTIONS leg's index. On THIS path the rows are
    # not legs of one trade — each index contributes its OWN trades, concatenated (e.g.
    # 37 NIFTY CE trades + 36 MIDCPNIFTY FUT trades) — so compute_analytics's
    # first-Entry-Spot/last-Exit-Spot lands on NIFTY's entry and MIDCPNIFTY's exit and
    # divides two unrelated price scales: measured -8.45% where NIFTY actually did
    # +8.21%. (The sync path has the same recovery below; this one needs its own
    # because there is no shared-trade structure to key off.)
    try:
        _sp = combined
        if "Type" in _sp.columns:
            _opt_rows = _sp[_sp["Type"].astype(str).str.upper().isin(("CE", "PE"))]
            if not _opt_rows.empty:
                _sp = _opt_rows
        _sp = _sp.sort_values(["Entry Date", "Trade"], kind="stable")
        _es = pd.to_numeric(_sp["Entry Spot"], errors="coerce").replace(0, np.nan).dropna()
        _xs = pd.to_numeric(_sp["Exit Spot"], errors="coerce").replace(0, np.nan).dropna()
        if len(_es) and len(_xs):
            _i, _f = float(_es.iloc[0]), float(_xs.iloc[-1])
            _days = (pd.to_datetime(_sp["Exit Date"]).max()
                     - pd.to_datetime(_sp["Entry Date"]).min()).days
            _ny = max(_days / 365.0, 0.01)
            if _i > 0 and _f > 0:
                summary["cagr_spot"] = round(100 * ((_f / _i) ** (1.0 / _ny) - 1), 2)
    except Exception as exc:
        logger.warning("[MULTI_INDEX] cagr_spot recovery failed: %s", exc)

    try:
        pivot = build_pivot(agg, "Exit Date")
    except Exception:
        pivot = {"headers": [], "rows": []}

    # Propagate trade-level Cumulative/Peak/DD/%DD/Spot P&L onto the parent
    # (first-leg) row only; other leg rows stay None (engine convention).
    t2c = {
        int(r["Trade"]): {
            "Cumulative": r.get("Cumulative"),
            "Peak": r.get("Peak"),
            "DD": r.get("DD"),
            "%DD": r.get("%DD"),
            "Spot P&L": r.get("Spot P&L"),
        }
        for _, r in agg.iterrows()
    }
    combined = combined.sort_values(["Entry Date", "Trade", "Leg"], kind="stable").reset_index(drop=True)
    seen_tid = set()
    cum_c, pk_c, dd_c, pdd_c, spl_c = [], [], [], [], []
    for _, row in combined.iterrows():
        tid = int(row["Trade"])
        if tid in seen_tid:
            cum_c.append(None); pk_c.append(None); dd_c.append(None); pdd_c.append(None); spl_c.append(None)
            continue
        seen_tid.add(tid)
        v = t2c.get(tid, {})
        cum_c.append(v.get("Cumulative")); pk_c.append(v.get("Peak")); dd_c.append(v.get("DD"))
        pdd_c.append(v.get("%DD")); spl_c.append(v.get("Spot P&L"))
    combined["Cumulative"], combined["Peak"], combined["DD"], combined["%DD"] = cum_c, pk_c, dd_c, pdd_c
    combined["Spot P&L"] = spl_c

    combined = combined.drop(columns=[c for c in ("_grp", "_uid") if c in combined.columns])

    # ---- 4. Result payload (same shape as execute_algotest_job) ----
    records = combined.to_dict("records")
    # NaN -> explicit None (JSON null) so blanked legs and non-parent cumulative
    # cells render as empty, not the string "nan". Covers the Cumulative family AND
    # the price/P&L/MAE cells blanked by the no-volume data guard (real values are
    # always finite, so only genuine blanks are affected).
    _nan_cols = ("Cumulative", "Peak", "DD", "%DD", "Entry Price", "Exit Price",
                 "Raw Entry Price", "Raw Exit Price", "CE P&L", "PE P&L", "FUT P&L",
                 "Net P&L", "% P&L", "MAE", "MFE")
    for row in records:
        for k in _nan_cols:
            v = row.get(k)
            if v is not None:
                try:
                    f = float(v)
                    if f != f:  # NaN
                        row[k] = None
                except (TypeError, ValueError):
                    row[k] = None
    records = _convert_numpy(_format_dates(records))

    try:
        from services.multi_index_tradesheet import per_index_summary
        meta["per_index_summary"] = per_index_summary(records)
    except Exception:
        meta["per_index_summary"] = []

    logger.info(
        "[MULTI_INDEX] base+overlay, %d trade-rows (%.2fs)",
        len(records), time.perf_counter() - t0,
    )
    return {
        "status": "success",
        "trades": records,
        "summary": _convert_numpy(summary),
        "pivot": _convert_numpy(pivot),
        "meta": _convert_numpy(meta),
        "cached": False,
    }


def run_sync_weekly_cadence(
    payload: Dict[str, Any],
    effective_from: Optional[str],
    effective_to: Optional[str],
) -> Dict[str, Any]:
    """Unified-cadence multi-index path (opt-in via `sync_weekly_roll`).

    The SHORTEST-expiry leg sets ONE cadence (weekly if any weekly leg, else
    monthly). On each cadence T-1 EVERY leg squares off and re-enters together —
    one Trade# per cycle, all legs sharing the trade's entry/exit dates. Weekly
    legs re-pick a fresh strike each cycle; monthly legs are carried and re-booked
    each cycle on their OWN monthly contract, rolling to the next month on their
    OWN monthly expiry. Reuses the existing engine for the cadence (base) legs and
    `_overlay_legs_onto_base` for the rest — NO pricing/engine logic re-implemented.

    Gated: reached ONLY when payload['sync_weekly_roll'] is set. Every other path
    (single-index, and the group-per-index multi-index path) is untouched.
    """
    t0 = time.perf_counter()
    import pandas as pd
    import numpy as np
    from base import compute_analytics, build_pivot
    from services.algotest_job import _try_rust_engine, _convert_numpy, _format_dates

    default_index = str(payload.get("index") or "NIFTY").strip().upper()
    default_expiry = str(payload.get("expiry_type") or "MONTHLY").strip().upper()
    legs: List[dict] = [l for l in (payload.get("legs") or []) if isinstance(l, dict)]

    # Preserve the user's CONFIGURED leg order. The base (cadence-index) legs run
    # through the engine and get numbered 1..N among THEMSELVES, and overlay legs
    # were appended after them (max_leg + offset) — so a futures-first strategy
    # came back with the option as Leg 1 and the future as Leg 2, regardless of how
    # it was built. Stamp each leg with its position in payload['legs'] and use that
    # as the Leg number below, so leg order — and therefore the parent row that
    # carries Net P&L / % P&L / Cumulative / Peak / DD — matches what the user
    # configured, exactly like the single-index path already does.
    for _i, _l in enumerate(legs):
        _l["_orig_leg_no"] = _i + 1

    def _is_weekly(l):
        return _leg_expiry(l, default_expiry).startswith("WEEK")

    # Cadence = shortest expiry. cadence_index = index of the first shortest leg.
    weekly_legs = [l for l in legs if _is_weekly(l)]
    if weekly_legs:
        cadence = "WEEKLY"
        cadence_index = _leg_index(weekly_legs[0], default_index)
        base_legs = [l for l in legs if _is_weekly(l) and _leg_index(l, default_index) == cadence_index]
    else:
        cadence = "MONTHLY"
        cadence_index = default_index
        base_legs = [l for l in legs if _leg_index(l, default_index) == cadence_index]
    # A GENUINELY yearly base leg cannot stay in the base run. The sync mechanism
    # below rewrites every base leg to expiry="YEARLY" purely as a marker so Rust
    # hands it the merged CYCLE contract (simulate.rs:1223) — which makes a real
    # yearly leg indistinguishable from a synced monthly one and rolls it monthly
    # (observed: the PE leg's expiry tracking the CE leg's 26-Dec-2024 -> 30-Jan-2025
    # -> 24-Apr-2025 instead of holding December). yearly_cycles carries ONE contract
    # per cycle, so the base run has no room for a second one.
    # Route it through the overlay path instead: overlay legs are priced over the
    # SAME base cadence windows and share the base Trade#, so the leg stays in sync
    # while _pick_yearly gives it its own December contract.
    _base_yearly = [
        l for l in base_legs
        if str(l.get("expiry") or l.get("expiry_type") or "").upper().startswith("YEAR")
        and str(l.get("segment") or "OPTIONS").upper() not in ("FUTURE", "FUTURES")
    ]
    if _base_yearly and len(_base_yearly) < len(base_legs):
        _yr_ids = {id(l) for l in _base_yearly}
        base_legs = [l for l in base_legs if id(l) not in _yr_ids]
        logger.info("[SYNC_CADENCE] %d yearly base leg(s) routed to the overlay path "
                    "so they hold their own long-dated contract", len(_base_yearly))
    else:
        # Every base leg is yearly (or none is): leave the split alone. With none,
        # this is the original behaviour untouched; with all, there is no non-yearly
        # base leg to drive the cadence and the existing path already handles it.
        _base_yearly = []
    # Resolve the December cycles through the ENGINE's own resolver so a yearly
    # overlay leg rolls on exactly the date the base yearly engine would (honouring
    # yearly_exit_months_before). Best-effort: on failure _pick_yearly falls back to
    # its own roll-month scan, which still picks December but rolls at T-0.
    _yr_leg_cycles: List[Dict[str, str]] = []
    if _base_yearly:
        try:
            import pandas as _pd
            from base import get_trading_calendar as _gtc2
            from services.engine_rust import resolve_expiry_inputs as _rei
            _days2 = (
                _pd.to_datetime(_gtc2(effective_from, effective_to)["date"])
                .sort_values().dt.strftime("%Y-%m-%d").tolist()
            )
            _, _cyc2 = _rei(
                cadence_index,
                {
                    "expiry_type": "YEARLY",
                    "rollover_cadence": ("weekly" if cadence == "WEEKLY" else "monthly"),
                    "yearly_exit_months_before": payload.get("yearly_exit_months_before") or 0,
                    "yearly_roll_months": payload.get("yearly_roll_months"),
                },
                effective_from, effective_to, _days2,
            )
            _yr_leg_cycles = list(_cyc2 or [])
            logger.info("[SYNC_CADENCE] yearly overlay cycles: %s",
                        [(c["contract"], c["start"], c["end"]) for c in _yr_leg_cycles][:4])
        except Exception as _yexc:
            logger.warning("[SYNC_CADENCE] yearly cycle resolve failed (%s) — "
                           "overlay falls back to its own roll-month scan", _yexc)
            _yr_leg_cycles = []

    base_ids = {id(l) for l in base_legs}
    overlay_legs = [l for l in legs if id(l) not in base_ids]

    meta = {
        "multi_index": True, "sync_weekly_roll": True,
        "cadence": cadence, "cadence_index": cadence_index,
        "indices": sorted({_leg_index(l, default_index) for l in legs}),
        "index": default_index,
        "from_date": payload.get("from_date"), "to_date": payload.get("to_date"),
        "slippage_pct": payload.get("slippage_pct", 0),
    }
    try:
        from services.multi_index_tradesheet import build_export_filename
        meta["export_filename"] = build_export_filename(payload)
    except Exception:
        meta["export_filename"] = "multi_index_backtest"
    try:
        from services.engine_rust import _load_filter_segments as _lfs
        _segs = _lfs(payload) or []
        meta["filter_segments"] = [{"start": s, "end": e} for (s, e) in _segs]
    except Exception:
        meta["filter_segments"] = payload.get("filter_segments") or []

    def _empty():
        return {"status": "success", "trades": [], "summary": {},
                "pivot": {"headers": [], "rows": []}, "meta": _convert_numpy(meta), "cached": False}

    if not base_legs:
        logger.info("[SYNC_CADENCE] no cadence (base) legs on %s", cadence_index)
        return _empty()

    # ---- 1. Base run: the cadence legs through the EXISTING engine (cadence windows) ----
    sub = copy.deepcopy(payload)
    sub["legs"] = base_legs
    sub["index"] = cadence_index
    sub["expiry_type"] = cadence
    sub["expiry_window"] = "weekly_expiry" if cadence == "WEEKLY" else "monthly_expiry"
    sub.pop("multi_index_mode", None)
    sub.pop("sync_weekly_roll", None)

    # MERGED roll boundaries: whichever index expires FIRST ends the cycle for EVERY
    # leg. Handed to the engine as explicit {contract,start,end} cycles so the base
    # leg keeps all engine features (SL/target/spot-adj) while trading its OWN index's
    # near contract; the overlay legs then ride these same windows (they derive them
    # from base_df). Falls back to the single-index cadence if the schedule can't be
    # built, so behaviour is never worse than before.
    # The base engine run holds ONE contract per cycle, so the cycle contract must come
    # from the base legs' OWN segment calendar — a futures-first base rolls on FUT%.
    _cadence_segment = _leg_segment(base_legs[0])
    _cycles, _bounds = _build_sync_cycles(legs, cadence, cadence_index, default_index,
                                          default_expiry, effective_from, effective_to, payload,
                                          _cadence_segment)
    if _cycles and _bounds:
        sub["expiry_type"] = "YEARLY"          # the explicit-cycle mechanism
        # Mark the BASE legs as pinned so Rust hands them the cycle contract.
        # simulate.rs:1223 does `if pinned && !leg.is_yearly { _orig_expiry }` —
        # a non-YEARLY leg takes the CADENCE ELEMENT instead of the pinned
        # contract. That mechanism was built for a December pin where the cadence
        # element is always the same index's own expiry. Under the MERGED sync
        # cadence it is whichever leg expires FIRST — so when the futures leg ends
        # the cycle (MIDCPNIFTY 24-Nov-2023 / 22-Dec-2023) the NIFTY option leg was
        # handed a date no NIFTY option expires on, could not resolve, and the
        # WHOLE TRADE vanished with no warning. Measured: 2 of 5 cycles dropped in
        # Sep-2023..Jan-2024; a cycle survived iff its contract happened to be in
        # sync_cadence_expiries. is_yearly is read ONLY at that one call site
        # (simulate.rs:1223), and only when yearly_cycles is present — i.e. only on
        # this sync path — so genuine YEARLY and plain weekly/monthly runs are
        # untouched. This states what the block above already intends: "the base
        # engine run holds ONE contract per cycle".
        sub["legs"] = [dict(_l, expiry="YEARLY") for _l in base_legs]
        sub["yearly_cycles"] = _cycles
        # The merged boundaries ARE the cadence: in Rust the cadence list drives
        # entry/exit while yearly_cycles only pins the contract (simulate.rs:477).
        # Without this the base leg would exit on its OWN expiries, not the merged
        # boundary, and the whole earliest-expiry-wins rule would be inert.
        sub["sync_cadence_expiries"] = _bounds
        sub["sync_cadence_expiry_type"] = "weekly" if cadence == "WEEKLY" else "monthly"
        sub["rollover_min_days_to_expiry"] = 0  # YEARLY + min-days is rejected
        logger.info("[SYNC_CADENCE] merged roll boundaries -> %d cycles (%s..%s), cadence=%s",
                    len(_cycles), _cycles[0]["start"], _cycles[-1]["end"], _bounds[:3])
    base_df = None
    try:
        _reload_bulk_if_needed(cadence_index, effective_from, effective_to)
        r = _try_rust_engine(sub, cadence_index, effective_from, effective_to)
        base_df = r[0] if isinstance(r, tuple) else r
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] base run failed: %s", exc)
        base_df = None
    if base_df is None or getattr(base_df, "empty", True):
        logger.info("[SYNC_CADENCE] no base trades")
        return _empty()
    base_df = base_df.copy()
    base_df["Group Index"] = cadence_index

    # The base engine numbered its legs 1..N over base_legs ONLY. Map those back to
    # each leg's configured position in payload['legs'] so they interleave correctly
    # with the overlay legs (which use _orig_leg_no directly).
    _base_leg_map = {
        i + 1: int(l.get("_orig_leg_no") or (i + 1)) for i, l in enumerate(base_legs)
    }
    if "Leg" in base_df.columns:
        base_df["Leg"] = (
            pd.to_numeric(base_df["Leg"], errors="coerce")
            .fillna(1).astype(int)
            .map(lambda k: _base_leg_map.get(k, k))
        )

    # ---- 2. Overlay the remaining legs onto the base cadence windows (shared Trade#) ----
    ov_rows: List[dict] = []
    if overlay_legs:
        try:
            # Same T-n the base engine ran with, so the overlay rolls in step with it.
            try:
                _ov_exit_dte = max(0, int(payload.get("exit_dte") or 0))
            except (TypeError, ValueError):
                _ov_exit_dte = 0
            # Scheduled cycle ends keyed by entry date, so a truncated trade still
            # selects the contract its FULL window needed (see the lookahead note in
            # _overlay_legs_onto_base).
            _sched_end = sorted((c["start"], c["end"]) for c in (_cycles or []))
            ov_rows = _overlay_legs_onto_base(base_df, overlay_legs, cadence_index,
                                              effective_from, effective_to, _ov_exit_dte,
                                              sched_end_by_entry=_sched_end,
                                              yearly_roll_months=payload.get("yearly_roll_months"),
                                              yearly_cycles=_yr_leg_cycles)
        except Exception as exc:
            logger.warning("[SYNC_CADENCE] overlay failed: %s", exc)
            ov_rows = []

    # ---- 3. Combine: base + overlay share the base Trade#s => unified cadence ----
    for col in ("CE P&L", "PE P&L", "FUT P&L", "Spot P&L", "Entry Spot", "Exit Spot"):
        if col not in base_df.columns:
            base_df[col] = 0.0
    combined = pd.concat([base_df, pd.DataFrame(ov_rows)], ignore_index=True) if ov_rows else base_df.copy()
    for c in ("Entry Date", "Exit Date"):
        if c in combined.columns:
            combined[c] = pd.to_datetime(combined[c], errors="coerce")
    if "Leg" not in combined.columns:
        combined["Leg"] = 1
    for col in ("CE P&L", "PE P&L", "FUT P&L", "Spot P&L", "Entry Spot", "Exit Spot"):
        if col not in combined.columns:
            combined[col] = 0.0
    # The engine emits Trade/Leg as STRINGS ('1','2'); _overlay_legs_onto_base emits
    # INTS (1,2). A mixed-type groupby("Trade") would split a cadence cycle's legs
    # into separate groups (str '1' != int 1) and wreck the combined P&L/NAV — so
    # normalize both to int before aggregating and sorting.
    combined["Trade"] = pd.to_numeric(combined["Trade"], errors="coerce").fillna(0).astype(int)
    combined["Leg"] = pd.to_numeric(combined["Leg"], errors="coerce").fillna(1).astype(int)
    combined["Index"] = combined["Trade"]

    # ---- 4. Aggregate per cadence Trade + combined base-100 compound equity ----
    # Each leg row already carries its OWN "% P&L" = leg P&L / that leg's own-index
    # Entry Spot (CE ÷ NIFTY spot, FUT ÷ MIDCPNIFTY spot). The combined trade return
    # is the SUM of those per-leg %s (option %P&L + future %P&L), NOT Net points ÷ one
    # index's spot. Each per-leg CE/PE/FUT P&L fed into this sum (from base_df,
    # the Rust engine, and from ov_rows, _overlay_legs_onto_base) is ALREADY
    # lots-scaled per its own leg (points x that leg's lots) — Net P&L here is
    # just their sum and must NOT be multiplied by lots again at this level.
    agg_spec = {"Entry Date": "first", "Exit Date": "first", "Entry Spot": "first",
                "Exit Spot": "first", "Spot P&L": "first",
                "CE P&L": "sum", "PE P&L": "sum", "FUT P&L": "sum", "% P&L": "sum"}
    if "Exit Reason" in combined.columns:
        agg_spec["Exit Reason"] = "first"
    agg = combined.groupby("Trade", as_index=False).agg(agg_spec)
    agg["Net P&L"] = agg["CE P&L"] + agg["PE P&L"] + agg["FUT P&L"]
    agg["% P&L"] = agg["% P&L"].round(4).fillna(0)  # Σ per-leg % (option% + fut%)
    agg = agg.sort_values("Entry Date").reset_index(drop=True)

    cum = peak = 100.0
    cs, ps, ds, pds = [], [], [], []
    for _, r in agg.iterrows():
        pct = float(r["% P&L"]) if r["% P&L"] else 0.0  # Σ per-leg %
        cum = cum * (1.0 + pct / 100.0)
        peak = max(cum, peak)
        dd = cum - peak
        pct_dd = (dd / peak) if peak != 0 else 0.0
        cs.append(cum); ps.append(peak); ds.append(dd); pds.append(pct_dd)
    agg["Cumulative"], agg["Peak"], agg["DD"], agg["%DD"] = cs, ps, ds, pds

    # Summary via compute_analytics on a MINIMAL frame (only the columns it reads).
    # Our own compound loop above (Net points ÷ Entry Spot, in agg order) is the
    # authoritative NAV — copying compute_analytics's per-row columns back onto agg
    # can mis-align rows, so we keep our loop's Cumulative/Peak/DD/%DD.
    try:
        # Feed the summed-% return as Net÷Spot (Net=% , Spot=100) so compute_analytics's
        # NAV / max-DD / CAGR are built on the SAME Σ-per-leg-% return as our loop above.
        _mini = pd.DataFrame({
            "Trade": agg["Trade"].values,
            "Entry Date": pd.to_datetime(agg["Entry Date"]).dt.strftime("%d-%m-%Y").values,
            "Exit Date": pd.to_datetime(agg["Exit Date"]).dt.strftime("%d-%m-%Y").values,
            "Entry Spot": [100.0] * len(agg),
            "Net P&L": agg["% P&L"].values,
            "% P&L": agg["% P&L"].values,
        })
        _out, summary = compute_analytics(_mini)
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] compute_analytics failed: %s", exc)
        summary = {}

    # `_mini` pushed the per-leg "% P&L" through the Net P&L column so the NAV compounds
    # the Σ-per-leg-% return (intended, above). Side effect: compute_analytics's POINT
    # metrics were then sums of PERCENTS — total_pnl 3.77 where the real Net P&L is
    # 407.65, and likewise max_win / max_loss / avg_profit_per_trade, all ~100x off and
    # rendered under point labels. Restate those four from the real points in `agg`;
    # the %-based NAV, CAGR and drawdown are deliberately left untouched.
    try:
        _net_pts = pd.to_numeric(agg["Net P&L"], errors="coerce").dropna()
        if len(_net_pts):
            summary["total_pnl"] = round(float(_net_pts.sum()), 2)
            summary["max_win"] = round(float(_net_pts.max()), 2)
            summary["max_loss"] = round(float(_net_pts.min()), 2)
            summary["avg_profit_per_trade"] = round(float(_net_pts.mean()), 2)
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] point-metric restatement failed: %s", exc)

    # `_mini` carries a SYNTHETIC Entry Spot of 100.0 (so the NAV is built on the
    # Σ-per-leg-% return, above) and no Exit Spot at all, so compute_analytics can only
    # return cagr_spot = 0.0 — the spot benchmark is silently lost on this path. Recover
    # it from the REAL spots on the OPTIONS leg (user rule: spot comes from the options
    # leg, never the futures leg — on a multi-index run they are different indices with
    # different price scales). base.py's exact convention: first trade's Entry Spot, last
    # trade's Exit Spot, (last exit - first entry).days / 365, floored at 0.01.
    # The backtest is the source of truth, so this value is what the optim per-combo
    # sheet and the Rust master summary both pin to downstream.
    try:
        _sp = combined
        if "Type" in _sp.columns:
            _opt_rows = _sp[_sp["Type"].astype(str).str.upper().isin(("CE", "PE"))]
            if not _opt_rows.empty:
                _sp = _opt_rows
        _sp = _sp.sort_values(["Entry Date", "Trade", "Leg"], kind="stable")
        _es = pd.to_numeric(_sp["Entry Spot"], errors="coerce").replace(0, np.nan).dropna()
        _xs = pd.to_numeric(_sp["Exit Spot"], errors="coerce").replace(0, np.nan).dropna()
        if len(_es) and len(_xs):
            _i, _f = float(_es.iloc[0]), float(_xs.iloc[-1])
            _days = (pd.to_datetime(agg["Exit Date"]).max()
                     - pd.to_datetime(agg["Entry Date"]).min()).days
            _ny = max(_days / 365.0, 0.01)
            if _i > 0 and _f > 0:
                summary["cagr_spot"] = round(100 * ((_f / _i) ** (1.0 / _ny) - 1), 2)
    except Exception as exc:
        logger.warning("[SYNC_CADENCE] cagr_spot recovery failed: %s", exc)
    try:
        pivot = build_pivot(agg, "Exit Date")
    except Exception:
        pivot = {"headers": [], "rows": []}

    # Propagate trade-level values onto the parent (first-leg) row. Net P&L / % P&L
    # become the COMBINED trade total (both legs, lot-weighted) — not just the call
    # leg — so the sheet's Net P&L column reflects the whole book, consistent with
    # the NAV. Non-parent leg rows keep their own per-leg Net P&L (points).
    t2c = {int(r["Trade"]): {k: r.get(k) for k in ("Cumulative", "Peak", "DD", "%DD", "Spot P&L", "Net P&L", "% P&L")}
           for _, r in agg.iterrows()}
    combined = combined.sort_values(["Entry Date", "Trade", "Leg"], kind="stable").reset_index(drop=True)
    seen = set()
    cc, pc, dc, pdc, sc, npc, ppc = [], [], [], [], [], [], []
    for _, row in combined.iterrows():
        tid = int(row["Trade"])
        if tid in seen:
            cc.append(None); pc.append(None); dc.append(None); pdc.append(None)
            # Spot P&L is normally a TRADE-level fact carried on leg 1 only, so
            # leg 2+ is blanked. But a multi-index overlay leg trades a DIFFERENT
            # underlying — its own spot move is its own fact, not a duplicate of
            # leg 1's. Keep it for overlay rows; base-engine leg 2+ rows (same
            # index, Spot P&L 0.0) stay blank exactly as before.
            _ov = str(row.get("Exit Reason") or "").upper() == "OVERLAY"
            _rs = row.get("Spot P&L")
            sc.append(_rs if (_ov and _rs is not None and pd.notna(_rs)) else None)
            npc.append(row.get("Net P&L")); ppc.append(row.get("% P&L"))
            continue
        seen.add(tid)
        v = t2c.get(tid, {})
        cc.append(v.get("Cumulative")); pc.append(v.get("Peak")); dc.append(v.get("DD"))
        pdc.append(v.get("%DD")); sc.append(v.get("Spot P&L"))
        npc.append(v.get("Net P&L")); ppc.append(v.get("% P&L"))
    combined["Cumulative"], combined["Peak"], combined["DD"], combined["%DD"] = cc, pc, dc, pdc
    combined["Spot P&L"] = sc
    combined["Net P&L"] = npc
    combined["% P&L"] = ppc

    records = combined.to_dict("records")
    # NaN -> None (JSON null) so it renders as an empty cell, not the string "nan".
    # Covers non-parent cumulative cells AND every price/P&L/MAE field blanked by the
    # no-volume data guard in _overlay_legs_onto_base (real values are finite).
    _nan_cols = ("Cumulative", "Peak", "DD", "%DD", "Entry Price", "Exit Price",
                 "Raw Entry Price", "Raw Exit Price", "CE P&L", "PE P&L", "FUT P&L",
                 "Net P&L", "% P&L", "MAE", "MFE")
    for row in records:
        for k in _nan_cols:
            v = row.get(k)
            if v is not None:
                try:
                    if float(v) != float(v):  # NaN
                        row[k] = None
                except (TypeError, ValueError):
                    row[k] = None
    records = _convert_numpy(_format_dates(records))
    try:
        from services.multi_index_tradesheet import per_index_summary
        meta["per_index_summary"] = per_index_summary(records)
    except Exception:
        meta["per_index_summary"] = []

    meta["trades"] = int(agg["Trade"].nunique())
    logger.info(
        "[SYNC_CADENCE] %s cadence on %s, %d cycles, %d legs (%.2fs)",
        cadence, cadence_index, len(agg), len(legs), time.perf_counter() - t0,
    )
    return {
        "status": "success",
        "trades": records,
        "summary": _convert_numpy(summary),
        "pivot": _convert_numpy(pivot),
        "meta": _convert_numpy(meta),
        "cached": False,
    }
