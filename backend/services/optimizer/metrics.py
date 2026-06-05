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
  positive_outlier_1 / _2 / _3
  negative_outlier_1 / _2 / _3
  outlier_dd_1 / outlier_dd_2 / outlier_dd_3  (and their Avg variants)
  ce_pe_pnl_pct_without_top_1_outliers / _2 / _3
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

    All `_pct` variants sum per-row (P&L / Entry Spot * 100), matching the
    research-team convention used for spot_change_pct in compute_analytics.
    """
    ce_col = next((c for c in ("Call P&L", "CE P&L", "call_pnl") if c in trades.columns), None)
    pe_col = next((c for c in ("Put P&L", "PE P&L", "put_pnl") if c in trades.columns), None)
    spot_col = next((c for c in ("Spot P&L", "spot_pnl") if c in trades.columns), None)

    def _s(col):
        if col is None:
            return pd.Series(0.0, index=trades.index, dtype=float)
        return pd.to_numeric(trades[col], errors="coerce").fillna(0)

    ce_s = _s(ce_col)
    pe_s = _s(pe_col)
    spot_s = _s(spot_col)

    ce_total = float(ce_s.sum())
    pe_total = float(pe_s.sum())
    spot_total = float(spot_s.sum())

    if "Entry Spot" in trades.columns:
        es = pd.to_numeric(trades["Entry Spot"].replace("", np.nan), errors="coerce").replace(0, np.nan)
        ce_pnl_pct = round(float((ce_s / es).fillna(0).sum() * 100), 4)
        pe_pnl_pct = round(float((pe_s / es).fillna(0).sum() * 100), 4)
        long_spot_pnl_pct = round(float((spot_s / es).fillna(0).sum() * 100), 4)
    else:
        ce_pnl_pct = 0.0
        pe_pnl_pct = 0.0
        long_spot_pnl_pct = 0.0

    return {
        "ce_pnl_total": round(ce_total, 2),
        "ce_pnl_pct": ce_pnl_pct,
        "pe_pnl_total": round(pe_total, 2),
        "pe_pnl_pct": pe_pnl_pct,
        "long_spot_pnl": round(spot_total, 2),
        "long_spot_pnl_pct": long_spot_pnl_pct,
    }


def roi_vs_spot(summary: Dict[str, Any]) -> float:
    """Net P&L % / |Spot %|. Returns 0 if spot_change_pct is zero."""
    try:
        spot_change_pct = float(summary.get("spot_change_pct", 0) or 0)
    except (TypeError, ValueError):
        spot_change_pct = 0.0
    try:
        total_pnl_pct = float(summary.get("total_pnl_pct", 0) or 0)
    except (TypeError, ValueError):
        total_pnl_pct = 0.0
    if spot_change_pct == 0:
        return 0.0
    return round(total_pnl_pct / abs(spot_change_pct), 4)


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
    return round((cagr / 100.0) / abs(live_dd_max), 4)


def _trade_outlier_analysis(trades: pd.DataFrame) -> Dict[str, float]:
    """
    Match the tradesheet Summary outlier block.

    Outlier N is cumulative: `+ve Outlier 2` is the sum of the top two
    positive P&L% trades, and `-ve Outlier 2` is the sum of the bottom two
    P&L% trades. Stripped Live DD removes both sides for the given N, then
    rebuilds the remaining trade path before measuring Live DD.
    """
    out: Dict[str, float] = {}
    for n in (1, 2, 3):
        out[f"positive_outlier_{n}"] = 0.0
        out[f"negative_outlier_{n}"] = 0.0
        out[f"outlier_dd_{n}"] = 0.0
        out[f"outlier_dd_{n}_avg"] = 0.0
        out[f"ce_pe_pnl_pct_without_top_{n}_outliers"] = 0.0

    if trades is None or trades.empty:
        return out

    # Extract one parent row per trade, preserving chronological order.
    df_all = trades.copy()
    if "Entry Date" in df_all.columns:
        df_all["__entry_sort__"] = pd.to_datetime(
            df_all["Entry Date"], errors="coerce", dayfirst=True
        )
        df_all = df_all.sort_values("__entry_sort__", na_position="last")
    seen: set = set()
    parent_idx = []
    for idx, row in df_all.iterrows():
        tid = str(row.get("Trade", row.get("trade", idx)))
        if tid not in seen:
            seen.add(tid)
            parent_idx.append(idx)
    df = df_all.loc[parent_idx].copy()
    df = df.drop(columns=["__entry_sort__"], errors="ignore")

    if df.empty:
        return out

    for _rcol in ("% P&L", "Net P&L %"):
        if _rcol in df.columns:
            pct_series = pd.to_numeric(df[_rcol], errors="coerce")
            break
    else:
        return out

    df = df.copy()
    df["__pct__"] = pct_series.fillna(0).values
    if "Final MAE" in df.columns:
        mae_series = pd.to_numeric(df["Final MAE"], errors="coerce")
    else:
        mae_series = pd.Series(np.nan, index=df.index)

    if "Lowest NAV During Trade" in df.columns and "Peak" in df.columns:
        low = pd.to_numeric(df["Lowest NAV During Trade"], errors="coerce")
        peak = pd.to_numeric(df["Peak"], errors="coerce")
        live = low - peak
    elif "Actual Live DD" in df.columns:
        live = pd.to_numeric(df["Actual Live DD"], errors="coerce")
    elif "%DD" in df.columns:
        live = pd.to_numeric(df["%DD"], errors="coerce")
    else:
        live = pd.Series(np.nan, index=df.index)
    df["__live_dd__"] = live.values

    pairs = [
        {
            "pct": float(row["__pct__"]),
            "mae": None if pd.isna(mae_series.iloc[i]) else float(mae_series.iloc[i]),
            "ldd": None if pd.isna(row["__live_dd__"]) else float(row["__live_dd__"]),
            "idx": i,
        }
        for i, (_, row) in enumerate(df.iterrows())
    ]
    n_trades = len(pairs)
    if n_trades == 0:
        return out

    by_pct_desc = sorted(pairs, key=lambda p: p["pct"], reverse=True)
    total_pct_sum = sum(p["pct"] for p in pairs)

    def _sum_top(count: int) -> float:
        return sum(p["pct"] for p in by_pct_desc[:count])

    def _sum_bottom(count: int) -> float:
        start = max(0, n_trades - count)
        return sum(p["pct"] for p in by_pct_desc[start:])

    def _ldd_exc_stats(exc_top: int, exc_bot: int) -> Dict[str, float]:
        exc_idx = {
            *[p["idx"] for p in by_pct_desc[:exc_top]],
            *[p["idx"] for p in by_pct_desc[max(0, n_trades - exc_bot):]],
        }
        filtered = [p for p in pairs if p["idx"] not in exc_idx]
        if not filtered:
            return {"max": 0.0, "avg": 0.0}
        cumulative = 100.0
        peak = 100.0
        prev_cum = 100.0
        first_done = False
        rebuilt_ldds = []
        for p in filtered:
            pct = p["pct"]
            cumulative *= (1.0 + pct / 100.0)
            peak = max(peak, cumulative)
            mae = p["mae"]
            if mae is not None and peak != 0:
                if not first_done:
                    lowest_nav = round(cumulative * 100) / 100
                else:
                    lowest_nav = round(prev_cum * (1.0 + mae / 100.0) * 100) / 100
                actual_ldd = round((lowest_nav / peak - 1) * 10000) / 100
                rebuilt_ldds.append(actual_ldd)
                first_done = True
            else:
                first_done = True
            prev_cum = cumulative
        if not rebuilt_ldds:
            return {"max": 0.0, "avg": 0.0}
        return {
            "max": round(float(min(rebuilt_ldds)), 4),
            "avg": round(float(sum(rebuilt_ldds) / len(rebuilt_ldds)), 4),
        }

    for n in (1, 2, 3):
        pos = _sum_top(n) if n_trades > 0 else 0.0
        neg = _sum_bottom(n) if n_trades > 0 else 0.0
        stats = _ldd_exc_stats(n, n)
        out[f"positive_outlier_{n}"] = round(float(pos), 4)
        out[f"negative_outlier_{n}"] = round(float(neg), 4)
        out[f"outlier_dd_{n}"] = stats["max"]
        out[f"outlier_dd_{n}_avg"] = stats["avg"]
        out[f"ce_pe_pnl_pct_without_top_{n}_outliers"] = round(
            float(total_pct_sum - pos - neg), 4
        )
    return out


def outlier_stripped_live_dd(trades: pd.DataFrame) -> Dict[str, float]:
    """Compute tradesheet-style outlier analysis fields for top 1/2/3."""
    return _trade_outlier_analysis(trades)


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
            results[i].update(outlier_stripped_live_dd(df))
        return list(results)
    except Exception:
        # Fall back to the per-sheet Python pipeline.
        return [compute_optim_metrics(df, s) for (df, s) in tradesheets_and_summaries]
