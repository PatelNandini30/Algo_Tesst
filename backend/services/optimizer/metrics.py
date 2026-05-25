"""
Extra metrics for the optimization master-summary that are NOT produced by
`base.compute_analytics`.

Inputs are the per-trade DataFrame produced by the engine + the summary dict
already returned by `compute_analytics`. We do not re-compute anything that's
already in `summary`.

New metrics produced (matching `Summary_of_Weekly_Non-QTR_CE.xlsx` columns):

  ce_pnl_total / ce_pnl_pct
  pe_pnl_total / pe_pnl_pct
  long_spot_pnl / long_spot_pnl_pct
  roi_vs_spot
  actual_live_dd_max / actual_live_dd_avg
  car_mdd_live
  outlier_dd_1 / outlier_dd_2 / outlier_dd_3  (and their Avg variants)
  ce_pnl_pct_no_outlier_1 / _2 / _3
  pe_pnl_pct_no_outlier_1 / _2 / _3

All functions take an already-analytics-enriched trades DataFrame (so it has
Cumulative, Peak, DD, %DD columns) and return either a float or a dict.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def _safe_first_last_spot(trades: pd.DataFrame) -> tuple[float, float]:
    """Return (first Entry Spot, last Exit Spot). Zero if missing."""
    if "Entry Spot" not in trades.columns or "Exit Spot" not in trades.columns:
        return 0.0, 0.0
    entry = pd.to_numeric(trades["Entry Spot"].replace("", np.nan), errors="coerce")
    exit_ = pd.to_numeric(trades["Exit Spot"].replace("", np.nan), errors="coerce")
    entry = entry.dropna()
    exit_ = exit_.dropna()
    if entry.empty or exit_.empty:
        return 0.0, 0.0
    return float(entry.iloc[0]), float(exit_.iloc[-1])


def _sum_col(trades: pd.DataFrame, *names: str) -> float:
    """Return the sum of the first column in `names` that exists."""
    for n in names:
        if n in trades.columns:
            return float(pd.to_numeric(trades[n], errors="coerce").fillna(0).sum())
    return 0.0


def per_leg_pnl(trades: pd.DataFrame) -> Dict[str, float]:
    """
    CE P&L = sum(Call P&L).  PE P&L = sum(Put P&L).
    Long Spot P&L = sum(Spot P&L) — already a reference column built by engine.

    All `_pct` variants are expressed as percentage of initial spot,
    consistent with how `total_pnl_pct = total_pnl / initial_spot * 100` is
    computed elsewhere.
    """
    # Rust path uses "CE P&L"/"PE P&L"; Python engine uses "Call P&L"/"Put P&L".
    ce_total = _sum_col(trades, "Call P&L", "CE P&L", "call_pnl")
    pe_total = _sum_col(trades, "Put P&L", "PE P&L", "put_pnl")
    spot_total = _sum_col(trades, "Spot P&L", "spot_pnl")

    initial_spot, _ = _safe_first_last_spot(trades)
    to_pct = (lambda v: round(v / initial_spot * 100, 4)) if initial_spot > 0 else (lambda v: 0.0)

    return {
        "ce_pnl_total": round(ce_total, 2),
        "ce_pnl_pct": to_pct(ce_total),
        "pe_pnl_total": round(pe_total, 2),
        "pe_pnl_pct": to_pct(pe_total),
        "long_spot_pnl": round(spot_total, 2),
        "long_spot_pnl_pct": to_pct(spot_total),
    }


def roi_vs_spot(summary: Dict[str, Any]) -> float:
    """`total_pnl_pct / spot_change_pct`. Falls back to total_pnl_pct itself if spot_change is zero."""
    try:
        spot_change = float(summary.get("spot_change", 0) or 0)
    except (TypeError, ValueError):
        spot_change = 0.0
    try:
        total_pnl = float(summary.get("total_pnl", 0) or 0)
    except (TypeError, ValueError):
        total_pnl = 0.0
    if spot_change == 0:
        return 0.0
    # spot_change is in absolute points (matches compute_analytics).
    # Use total_pnl in the same unit. Ratio of points.
    return round(total_pnl / spot_change, 4)


def actual_live_dd(trades: pd.DataFrame) -> Dict[str, float]:
    """
    "Actual Live DD" = the deepest intra-trade drawdown, using the
    `Lowest NAV During Trade` column when present, else falling back to
    booked `DD`/`%DD`.

    Two values are returned:
        actual_live_dd_max  — minimum (most negative) Live DD across trades
        actual_live_dd_avg  — mean of the per-trade Live DD column

    The convention matches the research team's spreadsheet:
        Lowest NAV During Trade = min(running NAV during the trade window)
        Actual Live DD          = Lowest NAV During Trade - Peak (≤ 0)
    """
    if "Lowest NAV During Trade" in trades.columns and "Peak" in trades.columns:
        low = pd.to_numeric(trades["Lowest NAV During Trade"], errors="coerce")
        peak = pd.to_numeric(trades["Peak"], errors="coerce")
        live = (low - peak).fillna(0)
    elif "Actual Live DD" in trades.columns:
        live = pd.to_numeric(trades["Actual Live DD"], errors="coerce").fillna(0)
    elif "%DD" in trades.columns:
        live = pd.to_numeric(trades["%DD"], errors="coerce").fillna(0)
    else:
        return {"actual_live_dd_max": 0.0, "actual_live_dd_avg": 0.0}

    if live.empty:
        return {"actual_live_dd_max": 0.0, "actual_live_dd_avg": 0.0}

    return {
        "actual_live_dd_max": round(float(live.min()), 4),
        "actual_live_dd_avg": round(float(live.mean()), 4),
    }


def car_mdd_live(summary: Dict[str, Any], live_dd_max: float) -> float:
    """`cagr_options / |Actual Live DD|`. 0 if live DD is 0."""
    if not live_dd_max:
        return 0.0
    try:
        cagr = float(summary.get("cagr_options", 0) or 0)
    except (TypeError, ValueError):
        cagr = 0.0
    return round(cagr / abs(live_dd_max), 4)


def _recompute_live_dd_after_dropping(trades: pd.DataFrame, drop_n: int) -> Dict[str, float]:
    """
    Drop the top-`drop_n` outlier trades (by absolute Net P&L %), rebuild the
    live NAV series, and return the new Actual Live DD.

    Uses Lowest NAV During Trade (MAE-based) when available — same methodology
    as actual_live_dd() so the outlier-stripped values are comparable.  Falls
    back to the booked cumulative approach when MAE data is absent.

    Outlier definition: trades with the largest |% P&L| (or |Net P&L|).
    """
    if trades is None or trades.empty or drop_n <= 0:
        return {"max": 0.0, "avg": 0.0}

    # Extract one parent row per trade (first row per Trade ID, sorted by Entry Date).
    df_all = trades.copy()
    if "Entry Date" in df_all.columns:
        df_all = df_all.sort_values("Entry Date", na_position="last")
    seen: set = set()
    parent_idx = []
    for idx, row in df_all.iterrows():
        tid = str(row.get("Trade", ""))
        if tid not in seen:
            seen.add(tid)
            parent_idx.append(idx)
    df = df_all.loc[parent_idx].copy()

    if df.empty:
        return {"max": 0.0, "avg": 0.0}

    # Rank column for outlier identification.
    for _rcol in ("% P&L", "Net P&L %", "Net P&L"):
        if _rcol in df.columns:
            rank_series = pd.to_numeric(df[_rcol], errors="coerce").fillna(0)
            break
    else:
        return {"max": 0.0, "avg": 0.0}

    df = df.copy()
    df["__rank__"] = rank_series.abs().values
    df = df.sort_values("__rank__", ascending=False).iloc[drop_n:].copy()
    df = df.drop(columns="__rank__")
    if df.empty:
        return {"max": 0.0, "avg": 0.0}

    # Re-sort chronologically for correct NAV accumulation.
    if "Entry Date" in df.columns:
        df = df.sort_values("Entry Date", na_position="last").reset_index(drop=True)

    # Determine P&L% column.
    pct_col = "% P&L" if "% P&L" in df.columns else ("Net P&L %" if "Net P&L %" in df.columns else None)

    has_lnav = "Lowest NAV During Trade" in df.columns
    has_cum = "Cumulative" in df.columns

    cum_idx = 100.0
    peak = 100.0
    live_dd_list = []

    for _, r in df.iterrows():
        pct = float(pd.to_numeric(r.get(pct_col, 0) if pct_col else 0, errors="coerce") or 0)

        if has_lnav and has_cum:
            # Derive the Final MAE ratio from the stored Lowest NAV During Trade.
            # Lowest NAV was stored as prev_cum * (1 + final_mae/100), so
            # final_mae/100 = lnav / prev_cum - 1, independent of absolute NAV level.
            lnav_orig = r.get("Lowest NAV During Trade")
            cum_orig = r.get("Cumulative")
            if (
                lnav_orig is not None and not pd.isna(lnav_orig)
                and cum_orig is not None and not pd.isna(cum_orig)
            ):
                denom = 1.0 + pct / 100.0
                orig_prev_cum = float(cum_orig) / denom if denom != 0 else 100.0
                if orig_prev_cum > 0:
                    final_mae_ratio = float(lnav_orig) / orig_prev_cum - 1.0
                    lowest_nav = cum_idx * (1.0 + final_mae_ratio)
                else:
                    lowest_nav = cum_idx * (1.0 + pct / 100.0)
            else:
                lowest_nav = cum_idx * (1.0 + pct / 100.0)
        else:
            lowest_nav = cum_idx * (1.0 + pct / 100.0)

        cum_idx = cum_idx * (1.0 + pct / 100.0)
        peak = max(peak, cum_idx)
        live_dd_list.append(lowest_nav - peak)

    if not live_dd_list:
        return {"max": 0.0, "avg": 0.0}
    arr = np.array(live_dd_list, dtype=float)
    return {"max": round(float(arr.min()), 4), "avg": round(float(arr.mean()), 4)}


def outlier_stripped_live_dd(trades: pd.DataFrame) -> Dict[str, float]:
    """Compute Live DD after dropping the top 1, 2, and 3 outliers."""
    out: Dict[str, float] = {}
    for n in (1, 2, 3):
        r = _recompute_live_dd_after_dropping(trades, n)
        out[f"outlier_dd_{n}"] = r["max"]
        out[f"outlier_dd_{n}_avg"] = r["avg"]
    return out


def _ce_pe_pct_no_outliers(trades: pd.DataFrame, leg_col: str) -> Dict[str, float]:
    """
    Reproduce the "CE P&L % Without Top {1,2,3} Outliers" / PE equivalent.

    Outlier here is per the team's spreadsheet convention: drop the trades
    with the largest **positive** P&L on that leg (those that flatter the
    average). Numbers in the sample are computed as % of initial spot, the
    same denominator as `ce_pnl_pct`.
    """
    out: Dict[str, float] = {}
    if leg_col not in trades.columns:
        return out
    initial_spot, _ = _safe_first_last_spot(trades)
    if initial_spot <= 0:
        return out
    s = pd.to_numeric(trades[leg_col], errors="coerce").fillna(0).copy()
    s = s.sort_values(ascending=False)  # largest first
    for n in (1, 2, 3):
        if len(s) <= n:
            out[f"{n}"] = 0.0
            continue
        kept = s.iloc[n:]
        out[f"{n}"] = round(float(kept.sum()) / initial_spot * 100, 4)
    return out


def leg_pct_no_outliers(trades: pd.DataFrame) -> Dict[str, float]:
    """Per-leg P&L % with top 1/2/3 outliers stripped."""
    result: Dict[str, float] = {}
    ce_col = "Call P&L" if "Call P&L" in trades.columns else "CE P&L"
    ce = _ce_pe_pct_no_outliers(trades, ce_col)
    for n, v in ce.items():
        result[f"ce_pnl_pct_no_outlier_{n}"] = v
    pe_col = "Put P&L" if "Put P&L" in trades.columns else "PE P&L"
    pe = _ce_pe_pct_no_outliers(trades, pe_col)
    for n, v in pe.items():
        result[f"pe_pnl_pct_no_outlier_{n}"] = v
    return result


def cagr_midcap_for_period(
    trades: pd.DataFrame,
    midcap_first_spot: Optional[float] = None,
    midcap_last_spot: Optional[float] = None,
) -> float:
    """
    CAGR of MIDCPNIFTY over the trade period. Caller must provide the
    MIDCPNIFTY spot at the first Entry Date and the last Exit Date; if the
    strategy already runs on MIDCPNIFTY we reuse `cagr_spot` from the
    summary instead (caller's responsibility — this function only handles
    the cross-benchmark case).
    """
    if (
        midcap_first_spot is None
        or midcap_last_spot is None
        or midcap_first_spot <= 0
        or midcap_last_spot <= 0
    ):
        return 0.0
    if "Entry Date" not in trades.columns or "Exit Date" not in trades.columns:
        return 0.0
    start = pd.to_datetime(trades["Entry Date"].min(), errors="coerce", dayfirst=True)
    end = pd.to_datetime(trades["Exit Date"].max(), errors="coerce", dayfirst=True)
    if pd.isna(start) or pd.isna(end):
        return 0.0
    n_years = max((end - start).days / 365.0, 0.01)
    return round(100.0 * ((midcap_last_spot / midcap_first_spot) ** (1.0 / n_years) - 1), 2)


def compute_optim_metrics(
    trades: pd.DataFrame,
    summary: Dict[str, Any],
    midcap_first_spot: Optional[float] = None,
    midcap_last_spot: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Bundle every NEW metric the master-summary needs into one flat dict.

    `summary` is the dict returned by `base.compute_analytics`. We add new
    keys and never overwrite existing ones.
    """
    out: Dict[str, Any] = {}
    out.update(per_leg_pnl(trades))
    out["roi_vs_spot"] = roi_vs_spot(summary)
    live = actual_live_dd(trades)
    out.update(live)
    out["car_mdd_live"] = car_mdd_live(summary, live["actual_live_dd_max"])
    out.update(outlier_stripped_live_dd(trades))
    out.update(leg_pct_no_outliers(trades))
    out["cagr_midcap"] = cagr_midcap_for_period(
        trades, midcap_first_spot, midcap_last_spot
    )
    return out


def _trades_to_rust_payload(trades: pd.DataFrame, summary: Dict[str, Any]) -> Dict[str, Any]:
    """Project a tradesheet DataFrame into the compact float-arrays the
    Rust batch metric function expects."""
    def _col(name: str) -> list:
        if name not in trades.columns:
            return []
        return (
            pd.to_numeric(trades[name].replace("", np.nan), errors="coerce")
            .fillna(0)
            .astype(float)
            .tolist()
        )

    return {
        "net_pnl": _col("Net P&L"),
        "net_pnl_pct": _col("Net P&L %") or _col("% P&L"),
        "call_pnl": _col("Call P&L"),
        "put_pnl": _col("Put P&L"),
        "spot_pnl": _col("Spot P&L"),
        "lowest_nav": _col("Lowest NAV During Trade"),
        "peak": _col("Peak"),
        "entry_spot": _col("Entry Spot"),
        "exit_spot": _col("Exit Spot"),
        "cagr_options": float(summary.get("cagr_options", 0) or 0),
        "spot_change": float(summary.get("spot_change", 0) or 0),
    }


def compute_optim_metrics_batch_rust(
    tradesheets_and_summaries: list,
) -> list:
    """
    Phase 2 fast-path: compute optim metrics for many tradesheets in one
    PyO3 round-trip, parallelised across CPU cores by rayon.

    Returns one dict per input. Falls back to per-tradesheet Python loop
    if the Rust extension is unavailable.
    """
    payloads = [
        _trades_to_rust_payload(df, summary) for (df, summary) in tradesheets_and_summaries
    ]
    try:
        import algotest_native  # type: ignore

        results = algotest_native.batch_compute_metrics(payloads)
        # Rust path covers 22 of the optim metrics; per-period cagr_midcap is
        # still Python because it needs date math.
        for i, (df, summary) in enumerate(tradesheets_and_summaries):
            results[i]["cagr_midcap"] = cagr_midcap_for_period(df, None, None)
        return list(results)
    except Exception:
        # Fall back to the per-sheet Python pipeline.
        return [compute_optim_metrics(df, s) for (df, s) in tradesheets_and_summaries]
