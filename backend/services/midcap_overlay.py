"""
services/midcap_overlay.py
==========================
Computes the Midcap100 (cross-index) overlay leg(s) on top of a finished NIFTY
backtest. ADDITIVE post-process: it never runs or modifies the engine. It reads
the per-trade entry/exit dates produced by the (unchanged) NIFTY backtest and
prices a cash-index leg (Spot or Hypothetical-future) for each.

Decoded math (reproduces the sample workbook exactly):
    spot_pnl      = spot_exit - spot_entry        # negate for SELL
    spot_pnl_pct  = spot_pnl / spot_entry
    no_of_days    = (midcap_exit - entry).days    # calendar
    rollover_frac = (cost_pct_per_month/100) * no_of_days/30      # hypothetical
    SPOT:         leg_pnl = spot_pnl ;                          leg_pnl_pct = spot_pnl_pct
    HYPOTHETICAL: leg_pnl = spot_pnl - rollover_frac*spot_entry ; leg_pnl_pct = spot_pnl_pct - rollover_frac
    combined Net P&L   = nifty_pnl      + Σ midcap leg_pnl
    combined Net P&L % = nifty_pnl_pct  + Σ midcap leg_pnl_pct

Lookups go through the Rust INDEX_OHLC cache (primary); a bounded DB read is the
fallback / scan substrate. Nothing here loads the heavy options/spot MarketCache.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from database import get_engine
from services import rust_fast_path as _rf
from services import index_ohlc_store

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "NIFTYMIDCAP100"

_DATE_FMTS = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y")


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    # Trim a time component if present (e.g. "2019-02-28T00:00:00").
    s = s.replace("T", " ").split(" ")[0]
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _iso(d: date) -> str:
    return d.isoformat()


class MidcapCloseLookup:
    """Daily close lookup for one index symbol.

    Point lookups prefer the native Rust INDEX_OHLC cache (Rust-only path). A
    bounded DB read of the whole (tiny, static) symbol series is loaded lazily
    only when the Rust path is unavailable or when a range scan is needed
    (spot-adjustment). Memory footprint is sub-MB.
    """

    def __init__(self, symbol: str = DEFAULT_SYMBOL):
        self.symbol = symbol.upper()
        self._rust_ready = index_ohlc_store.ensure_index_ohlc_loaded(self.symbol)
        self._series: Optional[Dict[str, float]] = None
        self._sorted_dates: Optional[List[str]] = None

    def _load_series(self) -> Dict[str, float]:
        if self._series is None:
            self._load_ohlc()
        return self._series

    def _load_ohlc(self) -> None:
        """Load the full (tiny) OHLC series once: close dict + sorted dates +
        (high, low) dict for the MAE/MFE scan."""
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT trade_date::text, high_price, low_price, close_price "
                    "FROM index_ohlc WHERE symbol = :s ORDER BY trade_date"
                ),
                {"s": self.symbol},
            ).fetchall()
        self._series = {r[0]: float(r[3]) for r in rows if r[3] is not None}
        self._hl = {
            r[0]: (
                float(r[1]) if r[1] is not None else float(r[3]),
                float(r[2]) if r[2] is not None else float(r[3]),
            )
            for r in rows if r[3] is not None
        }
        self._sorted_dates = sorted(self._series.keys())

    def mae_mfe(self, entry_iso: str, exit_iso: str, entry_spot: float, position: str):
        """Worst/best excursion of the index over [entry, exit] (inclusive),
        expressed as % of entry_spot in the leg's P&L direction.
        Returns (mae_pct<=0, mfe_pct>=0) or (0.0, 0.0) if no data."""
        if self._series is None:
            self._load_ohlc()
        if entry_spot in (None, 0):
            return (0.0, 0.0)
        highs, lows = [], []
        for d in self._sorted_dates:
            if entry_iso <= d <= exit_iso:
                h, l = self._hl[d]
                highs.append(h)
                lows.append(l)
        if not highs:
            return (0.0, 0.0)
        max_high, min_low = max(highs), min(lows)
        if str(position).upper() == "SELL":  # bearish: adverse when price rises
            mae_pct = (entry_spot - max_high) / entry_spot * 100.0
            mfe_pct = (entry_spot - min_low) / entry_spot * 100.0
        else:  # BUY / bullish: adverse when price falls
            mae_pct = (min_low - entry_spot) / entry_spot * 100.0
            mfe_pct = (max_high - entry_spot) / entry_spot * 100.0
        return (round(mae_pct, 4), round(mfe_pct, 4))

    def close(self, iso_date: str) -> Optional[float]:
        if self._rust_ready:
            v = _rf.get_index_ohlc_close(self.symbol, iso_date)
            if v is not None:
                return v
            # Rust loaded but date genuinely absent → don't hit DB.
            if self._series is None:
                return None
        return self._load_series().get(iso_date)

    def closes_in_range(self, start_iso: str, end_iso: str) -> List[Tuple[str, float]]:
        """(date, close) for every available trading day in (start, end].
        Used for the spot-adjustment breach scan."""
        series = self._load_series()
        assert self._sorted_dates is not None
        return [
            (d, series[d])
            for d in self._sorted_dates
            if start_iso < d <= end_iso
        ]

    def ohlc_in_range(self, start_iso: str, end_iso: str) -> List[Tuple[str, float, float]]:
        """(date, high, low) for every available trading day in [start, end]
        (inclusive). Used for the leg MAE/MFE excursion scan."""
        if self._series is None:
            self._load_ohlc()
        assert self._sorted_dates is not None
        return [
            (d, self._hl[d][0], self._hl[d][1])
            for d in self._sorted_dates
            if start_iso <= d <= end_iso
        ]


def _scan_breach(
    lookup: MidcapCloseLookup,
    entry_iso: str,
    exit_iso: str,
    spot_entry: float,
    direction: str,
    value: float,
    units: str,
) -> Optional[str]:
    """First date in (entry, exit] where the index breaches the rise/fall
    threshold from spot_entry. Returns the ISO date, or None."""
    if spot_entry is None or value is None or value <= 0:
        return None
    direction = (direction or "rise").lower().strip()
    units = (units or "percent").lower().strip()
    watch_rise = direction in ("rise", "both")
    watch_fall = direction in ("fall", "both")
    if units == "points":
        rise_target = spot_entry + value
        fall_target = spot_entry - value
    else:  # percent
        rise_target = spot_entry * (1.0 + value / 100.0)
        fall_target = spot_entry * (1.0 - value / 100.0)

    for d, close in lookup.closes_in_range(entry_iso, exit_iso):
        if watch_rise and close >= rise_target:
            return d
        if watch_fall and close <= fall_target:
            return d
    return None


def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _leg_mae_mfe(lookup, entry, midcap_exit, entry_iso, exit_iso, spot_entry, position, cost):
    """Worst/best excursion of the leg, reproducing the reference workbook EXACTLY.

        f_entry = spot_entry * (1 + cost%/mo * total_days/30)   # Hypo close on the entry day
        for each trading day d in (entry, exit]:                # ENTRY-DAY BAR EXCLUDED, exit day included
            carry_d   = cost%/mo * (exit - d).days / 30
            hypo_high = high_d * (1 + carry_d)
            hypo_low  = low_d  * (1 + carry_d)
        max_hh = max(hypo_high) ; min_hl = min(hypo_low)
        BUY :  MFE = (max_hh/f_entry - 1)*100 ;  MAE = (min_hl/f_entry - 1)*100
        SELL:  MFE = (1 - min_hl/f_entry)*100 ;  MAE = (1 - max_hh/f_entry)*100

    Both reference point and denominator are f_entry (workbook DI8 = DI6/DM2 - 1).
    Cost is always applied as an added carry term for the Midcap path, regardless
    of position. Position only changes which direction is adverse/favorable.
    Spot mode (cost==0) collapses to raw OHLC with f_entry == spot_entry. Values
    are percent of f_entry; MAE may be positive if the path never went adverse.
    Returns (0,0) when the lookup can't provide OHLC (e.g. injected test fake)."""
    if not hasattr(lookup, "ohlc_in_range") or spot_entry in (None, 0):
        return (0.0, 0.0)
    # For Midcap MAE/MFE we always add carry to the synthetic path.
    carry_full = cost / 100.0 * (midcap_exit - entry).days / 30.0
    f_entry = spot_entry * (1.0 + carry_full)
    if not f_entry:
        return (0.0, 0.0)
    max_fh = None
    min_fl = None
    for d_iso, hi, lo in lookup.ohlc_in_range(entry_iso, exit_iso):
        if d_iso <= entry_iso:           # exclude the entry-day bar (workbook MAX/MIN start one row after entry)
            continue
        d = _parse_date(d_iso)
        cr = cost / 100.0 * ((midcap_exit - d).days) / 30.0 if d else 0.0
        fh = hi * (1.0 + cr)
        fl = lo * (1.0 + cr)
        max_fh = fh if max_fh is None else max(max_fh, fh)
        min_fl = fl if min_fl is None else min(min_fl, fl)
    if max_fh is None:
        return (0.0, 0.0)
    if str(position).upper() == "SELL":  # short: adverse when price rises, favorable when it falls
        mae = (1.0 - max_fh / f_entry) * 100.0
        mfe = (1.0 - min_fl / f_entry) * 100.0
    else:                                # long: adverse when price falls, favorable when it rises
        mae = (min_fl / f_entry - 1.0) * 100.0
        mfe = (max_fh / f_entry - 1.0) * 100.0
    return (round(mae, 4), round(mfe, 4))


def compute_midcap_legs(
    rows: List[Dict[str, Any]],
    *,
    midcap_legs: List[Dict[str, Any]],
    midcap_spot_adjustment: Optional[Dict[str, Any]] = None,
    symbol: str = DEFAULT_SYMBOL,
    lookup: Optional["MidcapCloseLookup"] = None,
) -> Dict[str, Any]:
    """Enrich each projected trade row with Midcap leg + combined fields.

    `rows`: [{trade_id, reentry_index?, entry_date, exit_date, nifty_pnl, nifty_pnl_pct}]
    Returns {results: [...per row...], summary: {...}, available: bool}.
    Per-row quantities are normalized to 1 unit (points / % of entry spot),
    matching the sample workbook; `lots` is carried but not scaled (open item).
    `lookup` may be injected for testing; otherwise built from the symbol.
    """
    symbol = (symbol or DEFAULT_SYMBOL).upper()
    if lookup is None:
        lookup = MidcapCloseLookup(symbol)
    sa = midcap_spot_adjustment or {}
    sa_enabled = bool(sa.get("enabled"))

    results: List[Dict[str, Any]] = []
    any_priced = False
    sum_leg_pnl = 0.0
    sum_leg_pnl_pct = 0.0
    sum_combined_pnl = 0.0
    sum_combined_pnl_pct = 0.0

    for row in rows:
        entry = _parse_date(row.get("entry_date"))
        exit_ = _parse_date(row.get("exit_date"))
        nifty_pnl = _f(row.get("nifty_pnl")) or 0.0
        nifty_pnl_pct = _f(row.get("nifty_pnl_pct")) or 0.0  # already a percent number

        out: Dict[str, Any] = {
            "trade_id": row.get("trade_id"),
            "reentry_index": row.get("reentry_index"),
        }

        if entry is None or exit_ is None:
            out["available"] = False
            results.append(out)
            continue

        entry_iso = _iso(entry)
        spot_entry = lookup.close(entry_iso)

        midcap_exit = exit_
        if sa_enabled and spot_entry is not None:
            trig = _scan_breach(
                lookup, entry_iso, _iso(exit_), spot_entry,
                sa.get("direction", "rise"), _f(sa.get("pct")) or 0.0,
                sa.get("units", "percent"),
            )
            if trig:
                trig_d = _parse_date(trig)
                if trig_d and trig_d < midcap_exit:
                    midcap_exit = trig_d

        midcap_exit_iso = _iso(midcap_exit)
        spot_exit = lookup.close(midcap_exit_iso)

        if spot_entry is None or spot_exit is None or spot_entry == 0:
            out["available"] = False
            out["Midcap Entry Spot"] = spot_entry
            out["Midcap Exit Spot"] = spot_exit
            results.append(out)
            continue

        no_of_days = (midcap_exit - entry).days
        raw_spot_pnl = spot_exit - spot_entry

        leg_pnl_total = 0.0
        leg_pnl_pct_total = 0.0
        rollover_pct_repr = 0.0
        total_mae = total_mfe = 0.0
        for leg in midcap_legs:
            position = str(leg.get("position", "buy")).upper()
            mode = str(leg.get("midcap_mode") or leg.get("mode") or "spot").lower()
            cost = (_f(leg.get("cost_pct_per_month")) or 0.0) if mode == "hypothetical" else 0.0
            sp = -raw_spot_pnl if position == "SELL" else raw_spot_pnl
            sp_pct = sp / spot_entry  # fraction
            roll = cost / 100.0 * no_of_days / 30.0
            # Cost%/month carry sign by position: BUY subtracts the cost (-), SELL
            # adds it (+) for leg P&L. MAE/MFE always uses added carry.
            csign = 1.0 if position == "SELL" else -1.0
            if mode == "hypothetical":
                pnl = sp + csign * roll * spot_entry
                pnl_pct = sp_pct + csign * roll
                rollover_pct_repr = csign * roll * 100.0
            else:
                pnl, pnl_pct = sp, sp_pct
            leg_pnl_total += pnl
            leg_pnl_pct_total += pnl_pct
            # MAE/MFE on the leg's price path: carry-adjusted (Hypo) OHLC for
            # hypothetical mode, raw OHLC for spot mode.
            mae_pct, mfe_pct = _leg_mae_mfe(
                lookup, entry, midcap_exit, entry_iso, midcap_exit_iso, spot_entry, position, cost
            )
            total_mae += mae_pct
            total_mfe += mfe_pct

        spot_pnl_signed = raw_spot_pnl  # display column is long spot move
        combined_net = nifty_pnl + leg_pnl_total
        combined_net_pct = nifty_pnl_pct + leg_pnl_pct_total * 100.0

        out.update({
            "available": True,
            "Midcap Entry Spot": round(spot_entry, 4),
            "Midcap Exit Spot": round(spot_exit, 4),
            "Midcap Spot P&L": round(spot_pnl_signed, 4),
            "Midcap Spot P&L %": round(spot_pnl_signed / spot_entry * 100.0, 4),
            "Midcap No Of Days": no_of_days,
            "Midcap Rollover Cost %": round(rollover_pct_repr, 6),
            "Midcap Exit Date": midcap_exit.strftime("%d-%m-%Y"),
            "Midcap Leg P&L": round(leg_pnl_total, 4),
            "Midcap Leg P&L %": round(leg_pnl_pct_total * 100.0, 4),
            "Combined Net P&L": round(combined_net, 4),
            "Combined Net P&L %": round(combined_net_pct, 4),
            # Per-trade Midcap leg excursion (sum across midcap legs), percent of
            # f_entry. Net MAE 1/2/Final are formed downstream by pairing these
            # with the NIFTY legs' MAE/MFE (Net MAE 1 = Midcap MFE + NIFTY MAE,
            # Net MAE 2 = Midcap MAE + NIFTY MFE, Final = min).
            "Midcap MAE": round(total_mae, 4),
            "Midcap MFE": round(total_mfe, 4),
        })
        results.append(out)

        any_priced = True
        sum_leg_pnl += leg_pnl_total
        sum_leg_pnl_pct += leg_pnl_pct_total * 100.0
        sum_combined_pnl += combined_net
        sum_combined_pnl_pct += combined_net_pct

    summary = {
        "midcap_leg_pnl_sum": round(sum_leg_pnl, 4),
        "midcap_leg_pnl_pct_sum": round(sum_leg_pnl_pct, 4),
        "combined_pnl_sum": round(sum_combined_pnl, 4),
        "combined_pnl_pct_sum": round(sum_combined_pnl_pct, 4),
        "priced_rows": sum(1 for r in results if r.get("available")),
        "symbol": symbol,
    }
    return {"results": results, "summary": summary, "available": any_priced}
