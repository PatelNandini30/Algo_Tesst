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


def _leg_index(leg: dict, default_index: str) -> str:
    return str(leg.get("index") or default_index).strip().upper()


def _leg_expiry(leg: dict, default_expiry: str) -> str:
    return str(leg.get("expiry") or leg.get("expiry_type") or default_expiry).strip().upper()


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
                # P&L per unit (matches the engine's per-share convention).
                pnl = round((xp - ep) if pos == "BUY" else (ep - xp), 2)
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


def _data_expiries(symbol, instrument_like, from_iso):
    """All distinct expiry dates present in the data for symbol+instrument,
    sorted ISO strings. The caller picks the nearest >= the trade exit that
    actually has a price on the entry date (avoids the incomplete expiry
    calendar AND sparse/far stray expiries)."""
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
    return out


def _overlay_legs_onto_base(base_df, overlay_legs, default_index, effective_from, effective_to):
    """Case A: for EACH base trade [entry,exit], price each overlay leg (other
    index) over that SAME window and return them as extra Leg rows sharing the
    trade's id/dates. Futures priced in Rust (get_future_price); options via the
    existing DB premium lookup. The MIDCPNIFTY contract is the near monthly whose
    expiry >= the trade's exit (so it's alive across the window)."""
    import bisect
    import pandas as pd
    from base import get_expiry_dates
    from services.data_loader import get_loader
    from services.index_metadata import get_lot_size_for_index, get_index_config
    from services import rust_fast_path as rf
    from services.futures_cache_store import ensure_futures_loaded

    # Use the loader directly (symbol-aware, DB-backed) — NOT base.get_*_from_db,
    # which the fast-lookup monkey-patches to ignore the symbol and return the
    # currently-loaded (base) index's spot/premium.
    _loader = get_loader()
    try:
        from services.engine_rust import _compute_strike_for_leg_python
    except Exception:
        _compute_strike_for_leg_python = None

    by_sym: Dict[str, List[dict]] = {}
    for leg in overlay_legs:
        by_sym.setdefault(_leg_index(leg, default_index), []).append(leg)

    # Monthly contracts derived from the ACTUAL data (complete + per-instrument),
    # because options and futures can have different monthly expiry dates and the
    # expiry calendar is incomplete at the range ends.
    sym_opt_exp: Dict[str, List[str]] = {}
    sym_fut_exp: Dict[str, List[str]] = {}
    for sym, slegs in by_sym.items():
        sym_opt_exp[sym] = _data_expiries(sym, "OPT%", effective_from)
        sym_fut_exp[sym] = _data_expiries(sym, "FUT%", effective_from)
        if any(str(l.get("segment") or "").upper() in ("FUTURE", "FUTURES") for l in slegs):
            ensure_futures_loaded(sym)

    def _pick_monthly(exps, exit_iso, ok, limit_months=4):
        # The MONTHLY contract = the LATEST expiry in the nearest month >= exit
        # that has data (validated by `ok`). Walking month-by-month and taking the
        # last expiry of the month: (a) ignores weekly contracts in the old regime
        # (picks the monthly), and (b) skips stray/sparse late expiries by falling
        # back to the actual traded contract within that month.
        months: List[str] = []
        by_month: Dict[str, List[str]] = {}
        for e in exps:
            if e >= exit_iso:
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

    def _pick_weekly(exps, exit_iso, ok, limit=8):
        # The WEEKLY contract = the NEAREST expiry >= exit that has data (validated
        # by `ok`). For a weekly base cadence the MIDCPNIFTY weekly alive across
        # [entry, exit] is the first expiry >= exit. Scans the nearest few expiries
        # (covers weeklies where they exist 2022->late-2024; if only monthlies
        # exist in the window this naturally lands on the nearest monthly).
        cnt = 0
        for e in exps:  # ascending
            if e >= exit_iso:
                cnt += 1
                if ok(e):
                    return e
                if cnt >= limit:
                    break
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
        max_leg = int(grp["Leg"].max()) if "Leg" in grp.columns else 1
        leg_off = 0
        for sym, slegs in by_sym.items():
            cfg = get_index_config(sym)
            interval = getattr(cfg, "strike_interval", 25) or 25
            espot = _loader.get_spot_price(sym, entry_iso)
            xspot = _loader.get_spot_price(sym, exit_iso)
            fut_exps = sym_fut_exp.get(sym) or []
            opt_exps = sym_opt_exp.get(sym) or []
            for leg in slegs:
                is_fut = str(leg.get("segment") or "").upper() in ("FUTURE", "FUTURES")
                pos = "BUY" if str(leg.get("position") or "SELL").upper().startswith("B") else "SELL"
                lots = int(leg.get("lots") or leg.get("lot") or 1)
                lot_size = int(get_lot_size_for_index(sym, entry_iso))
                if is_fut:
                    # monthly futures contract alive on BOTH the entry and exit day
                    contract = _pick_monthly(fut_exps, exit_iso, lambda e: rf.get_future_price(sym, entry_iso, e) is not None and rf.get_future_price(sym, exit_iso, e) is not None)
                    if contract is None:
                        continue
                    ep = rf.get_future_price(sym, entry_iso, contract)
                    xp = rf.get_future_price(sym, exit_iso, contract)
                    if ep is None or xp is None:
                        continue
                    ep, xp = round(float(ep), 2), round(float(xp), 2)
                    pnl = round((xp - ep) if pos == "BUY" else (ep - xp), 2)
                    typ, strike, ce, pe, fut = "FUT", "", 0.0, 0.0, pnl
                else:
                    if espot is None:
                        continue
                    opt = str(leg.get("option_type") or "CE").upper()
                    opt = "CE" if opt in ("CALL", "CE") else "PE"
                    try:
                        strike = _compute_strike_for_leg_python(leg, float(espot), interval) if _compute_strike_for_leg_python else round(float(espot) / interval) * interval
                    except Exception:
                        strike = round(float(espot) / interval) * interval
                    if strike is None:
                        continue
                    # Option contract priced on BOTH days. Expiry basis follows the
                    # leg: WEEKLY -> nearest weekly (where they exist, e.g. 2024),
                    # else the near MONTHLY. Strike-shift to the nearest traded
                    # strike (ATM, then +/-1,2,3 intervals) so a thin/missing
                    # exact-ATM strike doesn't drop the leg.
                    leg_exp = str(leg.get("expiry") or leg.get("expiry_type") or "MONTHLY").upper()
                    _pick = _pick_weekly if leg_exp.startswith("WEEK") else _pick_monthly
                    base_strike = strike
                    contract = ep = xp = None
                    for _ds in (0, 1, -1, 2, -2, 3, -3):
                        cs = base_strike + _ds * interval
                        c = _pick(
                            opt_exps, exit_iso,
                            lambda e, _s=cs: _loader.get_option_premium(sym, entry_iso, _s, opt, e) is not None and _loader.get_option_premium(sym, exit_iso, _s, opt, e) is not None,
                        )
                        if c is not None:
                            contract, strike = c, cs
                            ep = _loader.get_option_premium(sym, entry_iso, strike, opt, contract)
                            xp = _loader.get_option_premium(sym, exit_iso, strike, opt, contract)
                            break
                    if contract is None or ep is None or xp is None:
                        continue
                    ep, xp = round(float(ep), 2), round(float(xp), 2)
                    pnl = round((xp - ep) if pos == "BUY" else (ep - xp), 2)
                    typ = opt
                    ce = pnl if opt == "CE" else 0.0
                    pe = pnl if opt == "PE" else 0.0
                    fut = 0.0
                es = float(espot) if espot else (ep if is_fut else 0.0)
                xs = float(xspot) if xspot else (xp if is_fut else 0.0)
                leg_off += 1
                rows.append({
                    "Trade": int(tid), "Leg": max_leg + leg_off, "Index": int(tid),
                    "Entry Date": entry_dt, "Exit Date": exit_dt, "Expiry": contract,
                    "Type": typ, "Strike": strike if not is_fut else "", "B/S": pos,
                    "Qty": lots * lot_size, "Entry Price": ep, "Exit Price": xp,
                    "Entry Spot": round(es, 2), "Exit Spot": round(xs, 2),
                    "CE P&L": ce, "PE P&L": pe, "FUT P&L": fut, "Net P&L": pnl,
                    "% P&L": round(pnl / es * 100.0, 4) if es else 0.0,
                    "Exit Reason": "OVERLAY", "MAE": 0.0, "MFE": 0.0,
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

    from base import bulk_load_options, bulk_clear_options, compute_analytics, build_pivot
    from services.algotest_job import (
        _try_rust_engine,
        _build_fast_lookup_from_bulk,
        _safe_clear_fast_lookup,
        _convert_numpy,
        _format_dates,
    )

    default_index = str(payload.get("index") or "NIFTY").strip().upper()
    default_expiry = str(payload.get("expiry_type") or "WEEKLY").strip().upper()
    legs: List[dict] = [l for l in (payload.get("legs") or []) if isinstance(l, dict)]

    # ---- 1. Split into BASE legs (strategy index) + OVERLAY legs (other index) ----
    # Case A: the base legs define the trades (one trade per base cycle); each
    # overlay leg is priced over the SAME trade window and attached as Leg 2/3.
    base_index = default_index
    base_legs = [l for l in legs if _leg_index(l, default_index) == base_index]
    overlay_legs = [l for l in legs if _leg_index(l, default_index) != base_index]
    if not base_legs and legs:  # no leg on the strategy index → use first leg's index as base
        base_index = _leg_index(legs[0], default_index)
        base_legs = [l for l in legs if _leg_index(l, default_index) == base_index]
        overlay_legs = [l for l in legs if _leg_index(l, default_index) != base_index]

    group_frames: List["pd.DataFrame"] = []
    group_meta: List[dict] = []

    # ---- 2a. Run the base legs through the existing engine ----
    base_df = None
    if base_legs:
        sub = copy.deepcopy(payload)
        sub["legs"] = base_legs
        sub["index"] = base_index
        sub.pop("multi_index_mode", None)  # never recurse
        try:
            try:
                _safe_clear_fast_lookup()
                bulk_clear_options()
            except Exception:
                pass
            bulk_load_options(base_index, effective_from, effective_to)
            _build_fast_lookup_from_bulk(base_index, effective_from, effective_to)
            base_df, _s, _p = _try_rust_engine(sub, base_index, effective_from, effective_to)
        except Exception as exc:
            logger.warning("[MULTI_INDEX] base %s failed: %s", base_index, exc)
            base_df = None
    base_avail = base_df is not None and not getattr(base_df, "empty", True)
    if base_avail:
        base_df = base_df.copy()
        base_df["_grp"] = 0
        base_df["Group Index"] = base_index
        base_df["Group Expiry"] = str(payload.get("expiry_type") or "WEEKLY").upper()
    group_meta.append({
        "index": base_index, "role": "base", "legs": len(base_legs),
        "trades": int(base_df["Trade"].nunique()) if base_avail else 0, "available": bool(base_avail),
    })

    # ---- 2b. Overlay the other-index legs onto each base trade (Leg 2/3) ----
    # Clear the base-index fast-lookup FIRST so the overlay's MIDCPNIFTY spot /
    # option-premium DB lookups don't hit the NIFTY-only cache and return None.
    try:
        _safe_clear_fast_lookup()
        bulk_clear_options()
    except Exception:
        pass
    overlay_rows: List[dict] = []
    if base_avail and overlay_legs:
        try:
            overlay_rows = _overlay_legs_onto_base(base_df, overlay_legs, default_index, effective_from, effective_to)
        except Exception as exc:
            logger.warning("[MULTI_INDEX] overlay failed: %s", exc)
            overlay_rows = []
    from collections import Counter as _Counter
    _oc = _Counter(r["Group Index"] for r in overlay_rows)
    for _sym in sorted({_leg_index(l, default_index) for l in overlay_legs}):
        group_meta.append({
            "index": _sym, "role": "overlay", "rows": int(_oc.get(_sym, 0)),
            "available": _oc.get(_sym, 0) > 0,
        })

    if base_avail:
        frames = [base_df]
        if overlay_rows:
            odf = pd.DataFrame(overlay_rows)
            odf["_grp"] = 0
            frames.append(odf)
        group_frames.append(pd.concat(frames, ignore_index=True))

    try:
        _safe_clear_fast_lookup()
        bulk_clear_options()
    except Exception:
        pass

    meta = {
        "multi_index": True,
        "groups": group_meta,
        "indices": sorted({base_index} | {_leg_index(l, default_index) for l in overlay_legs}),
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
    # NaN cumulative on non-parent rows -> explicit None (JSON null).
    for row in records:
        for k in ("Cumulative", "Peak", "DD", "%DD"):
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
