//! Rust port of `base.compute_analytics` (the per-combo/backtest summary engine).
//!
//! This reproduces the Python function OP-FOR-OP, including three quirks that govern
//! byte-parity (all verified against the live engine, all intentional per the "exact
//! copy" rule):
//!
//!   1. **DD-MM-YYYY lexicographic order.** `compute_analytics` receives Entry/Exit
//!      dates as `DD-MM-YYYY` strings and does `sort_values` / `.min()` / `.max()` on
//!      them — i.e. LEXICOGRAPHIC, day-of-month-first, NOT chronological
//!      (algotest_job.py:462-470). Order-dependent metrics (streaks, MDD dates, the
//!      final-NAV used for CAGR, n_years) all run in that scrambled order. We sort the
//!      SAME way (raw string compare) so the numbers match.
//!   2. **has_series_b reuse.** If the input already carries a `Cumulative` column whose
//!      first (post-sort) value is in [90,110], the equity chain is REUSED as-is;
//!      otherwise it is recomputed as 100·∏(1+pnl%/100). base.py:905-933.
//!   3. **Banker's rounding.** Python `round()` is round-half-to-even; Rust `f64::round`
//!      is half-away-from-zero. `py_round` below matches Python for the common cases.
//!
//! Exposed as `compute_analytics_summary(trades)` — takes the per-leg tradesheet rows
//! (same dicts `pd.DataFrame(trades)` would hold) and returns the summary dict. Parity
//! is checked field-by-field against `base.compute_analytics` in
//! backend/tests/test_analytics_rust_parity.py.

use std::collections::HashMap;

use chrono::NaiveDate;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

// ── helpers ──────────────────────────────────────────────────────────────────

/// Python-compatible round(x, ndigits): round-half-to-EVEN (banker's). Matches
/// CPython for the values that occur here; exact-halfway ties go to even.
pub(crate) fn py_round(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    // Match Python round(x, n) EXACTLY. Python uses David-Gay dtoa: decimal-correct,
    // round-half-to-EVEN. Neither (x*m).round() (half-away) nor an epsilon tie-break
    // reproduces it — the scaling itself perturbs the value. Rust's float FORMATTING
    // (Ryū) is also decimal-correct round-half-to-even, so `format!("{:.n}", x)` gives
    // the identical string Python's round produces, and parsing it back yields the same
    // f64. ndigits is always >= 0 here (2/4/6); a negative-digits fallback is kept for
    // completeness (unused).
    if ndigits >= 0 {
        return format!("{:.*}", ndigits as usize, x).parse::<f64>().unwrap_or(x);
    }
    let m = 10f64.powi(ndigits);
    (x * m).round() / m
}

fn get_f64(d: &PyDict, key: &str) -> Option<f64> {
    match d.get_item(key).ok().flatten() {
        None => None,
        Some(v) => {
            if v.is_none() {
                return None;
            }
            // numeric?
            if let Ok(f) = v.extract::<f64>() {
                if f.is_finite() {
                    return Some(f);
                }
                return None;
            }
            // string like "" or "123.4"
            if let Ok(s) = v.extract::<String>() {
                let t = s.trim();
                if t.is_empty() {
                    return None;
                }
                return t.parse::<f64>().ok();
            }
            None
        }
    }
}

fn get_str(d: &PyDict, key: &str) -> String {
    d.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_default()
}

fn has_key(d: &PyDict, key: &str) -> bool {
    d.get_item(key).ok().flatten().map(|v| !v.is_none()).unwrap_or(false)
}

/// Group key for `Trade` — the field may be int, float, or str. Use Python str()
/// so "1"/"2"/… group correctly regardless of dtype (the key value itself never
/// appears in the summary, only the grouping matters).
fn get_trade_key(d: &PyDict) -> String {
    for k in ["Trade", "trade"] {
        if let Some(v) = d.get_item(k).ok().flatten() {
            if !v.is_none() {
                if let Ok(s) = v.str() {
                    return s.to_string_lossy().into_owned();
                }
            }
        }
    }
    String::new()
}

/// pd.to_datetime(dayfirst=True) for the formats that occur: DD-MM-YYYY primarily,
/// with ISO fallback. Returns days since epoch for span math.
fn parse_dayfirst(s: &str) -> Option<NaiveDate> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"] {
        if let Ok(d) = NaiveDate::parse_from_str(t, fmt) {
            return Some(d);
        }
    }
    None
}

// ── per-trade aggregate ──────────────────────────────────────────────────────

#[derive(Clone)]
struct TradeAgg {
    trade_key: String,
    net_pnl: f64,        // SUM of per-leg Net P&L
    entry_date: String,  // first
    exit_date: String,   // first
    entry_spot: Option<f64>,
    exit_spot: Option<f64>,
    spot_pnl: Option<f64>,
    cumulative: Option<f64>, // present only if input carried it (has_series_b)
    peak: Option<f64>,
    dd: Option<f64>,
    pct_dd: Option<f64>,
    // filled during the equity pass:
    eq_cum: f64,
    eq_peak: f64,
    eq_dd: f64,
    eq_pct_dd: f64,
    net_pnl_pct: f64,
}

fn empty_summary(py: Python<'_>) -> PyResult<PyObject> {
    let d = PyDict::new(py);
    for k in ["total_pnl", "count", "win_pct", "loss_pct", "avg_win", "avg_loss",
              "max_win_streak", "max_loss_streak", "cagr_options", "max_dd_pct",
              "max_dd_pts", "car_mdd", "reward_to_risk", "expectancy"] {
        d.set_item(k, 0)?;
    }
    Ok(d.into())
}

#[pyfunction]
pub fn compute_analytics_summary(trades: &PyList) -> PyResult<PyObject> {
    let py = trades.py();
    if trades.is_empty() {
        return empty_summary(py);
    }

    // Collect per-leg rows.
    struct Leg {
        trade: String,
        net_pnl: f64,
        entry_date: String,
        exit_date: String,
        entry_spot: Option<f64>,
        exit_spot: Option<f64>,
        spot_pnl: Option<f64>,
        cumulative: Option<f64>,
        peak: Option<f64>,
        dd: Option<f64>,
        pct_dd: Option<f64>,
        has_cum_col: bool,
    }
    let mut legs: Vec<Leg> = Vec::with_capacity(trades.len());
    let mut any_spot_pnl_col = false;
    let mut any_cum_col = false;
    for obj in trades.iter() {
        let d = obj.downcast::<PyDict>()?;
        if has_key(d, "Spot P&L") {
            any_spot_pnl_col = true;
        }
        let has_cum = has_key(d, "Cumulative");
        if has_cum {
            any_cum_col = true;
        }
        legs.push(Leg {
            trade: get_trade_key(d),
            net_pnl: get_f64(d, "Net P&L").or_else(|| get_f64(d, "net_pnl")).unwrap_or(0.0),
            entry_date: get_str(d, "Entry Date"),
            exit_date: get_str(d, "Exit Date"),
            entry_spot: get_f64(d, "Entry Spot"),
            exit_spot: get_f64(d, "Exit Spot"),
            spot_pnl: get_f64(d, "Spot P&L"),
            cumulative: get_f64(d, "Cumulative"),
            peak: get_f64(d, "Peak"),
            dd: get_f64(d, "DD"),
            pct_dd: get_f64(d, "%DD"),
            has_cum_col: has_cum,
        });
    }

    // line 860: sort per-leg by Entry Date STRING (lexicographic, stable).
    legs.sort_by(|a, b| a.entry_date.cmp(&b.entry_date));

    // groupby Trade (sort=False → first-seen order over the sorted legs), agg:
    // Net P&L=sum, others=first.
    let mut order: Vec<String> = Vec::new();
    let mut map: HashMap<String, TradeAgg> = HashMap::new();
    for lg in &legs {
        match map.get_mut(&lg.trade) {
            Some(a) => {
                a.net_pnl += lg.net_pnl;
            }
            None => {
                order.push(lg.trade.clone());
                map.insert(lg.trade.clone(), TradeAgg {
                    trade_key: lg.trade.clone(),
                    net_pnl: lg.net_pnl,
                    entry_date: lg.entry_date.clone(),
                    exit_date: lg.exit_date.clone(),
                    entry_spot: lg.entry_spot,
                    exit_spot: lg.exit_spot,
                    spot_pnl: lg.spot_pnl,
                    cumulative: if lg.has_cum_col { lg.cumulative } else { None },
                    peak: lg.peak,
                    dd: lg.dd,
                    pct_dd: lg.pct_dd,
                    eq_cum: 0.0, eq_peak: 0.0, eq_dd: 0.0, eq_pct_dd: 0.0, net_pnl_pct: 0.0,
                });
            }
        }
    }
    let mut adf: Vec<TradeAgg> = order.iter().map(|k| map.get(k).unwrap().clone()).collect();

    // line 891: sort per-trade by Entry Date STRING again (stable).
    adf.sort_by(|a, b| a.entry_date.cmp(&b.entry_date));

    let n = adf.len();

    // net_pnl_pct = trade_pnl / entry_spot(nonzero→nan) * 100 (line 897)
    for t in adf.iter_mut() {
        let es = t.entry_spot.filter(|&v| v != 0.0);
        t.net_pnl_pct = match es {
            Some(v) => t.net_pnl / v * 100.0,
            None => f64::NAN,
        };
    }

    // has_series_b: base.py:905 checks whether the AGGREGATED df still has a
    // Cumulative column. But compute_analytics's groupby agg_dict (base.py:871) does
    // NOT include Cumulative, so pandas DROPS it during aggregation — meaning
    // _adf never has 'Cumulative' and this branch is effectively always False (the
    // equity chain is always RECOMPUTED in the DD-MM-YYYY scramble order). We mirror
    // that exactly. (any_cum_col retained only to silence unused-field lints.)
    let _ = any_cum_col;
    let has_series_b = false;

    if has_series_b {
        for t in adf.iter_mut() {
            t.eq_cum = t.cumulative.unwrap_or(100.0);
            t.eq_peak = t.peak.unwrap_or(100.0);
            t.eq_dd = t.dd.unwrap_or(0.0);
            t.eq_pct_dd = t.pct_dd.unwrap_or(0.0);
        }
    } else {
        let mut cum = 100.0f64;
        let mut peak = 100.0f64;
        for t in adf.iter_mut() {
            let pnl_pct = if t.net_pnl_pct.is_finite() { t.net_pnl_pct } else { 0.0 };
            cum *= 1.0 + pnl_pct / 100.0;
            if cum > peak { peak = cum; }
            let dd = cum - peak;
            let pct_dd = if peak != 0.0 { dd / peak * 100.0 } else { 0.0 };
            t.eq_cum = py_round(cum, 6);
            t.eq_peak = py_round(peak, 6);
            t.eq_dd = py_round(dd, 6);
            t.eq_pct_dd = py_round(pct_dd, 6);
        }
    }

    // ── summary metrics (base.py:944-1112) ──
    let sum_pnl: f64 = adf.iter().map(|t| t.net_pnl).sum();
    let total_pnl = py_round(sum_pnl, 2);
    let count = n;
    let wins: Vec<&TradeAgg> = adf.iter().filter(|t| t.net_pnl > 0.0).collect();
    let losses: Vec<&TradeAgg> = adf.iter().filter(|t| t.net_pnl < 0.0).collect();
    let win_count = wins.len();
    let loss_count = losses.len();

    let win_pct = if count > 0 { py_round(win_count as f64 / count as f64 * 100.0, 2) } else { 0.0 };
    let loss_pct = if count > 0 { py_round(loss_count as f64 / count as f64 * 100.0, 2) } else { 0.0 };
    let mean = |v: &Vec<&TradeAgg>| -> f64 { v.iter().map(|t| t.net_pnl).sum::<f64>() / v.len() as f64 };
    let avg_win = if win_count > 0 { py_round(mean(&wins), 2) } else { 0.0 };
    let avg_loss = if loss_count > 0 { py_round(mean(&losses), 2) } else { 0.0 };
    let max_win = if win_count > 0 { py_round(wins.iter().map(|t| t.net_pnl).fold(f64::NEG_INFINITY, f64::max), 2) } else { 0.0 };
    let max_loss = if loss_count > 0 { py_round(losses.iter().map(|t| t.net_pnl).fold(f64::INFINITY, f64::min), 2) } else { 0.0 };
    let avg_profit_per_trade = if count > 0 { py_round(total_pnl / count as f64, 2) } else { 0.0 };
    let reward_to_risk = if avg_loss != 0.0 { py_round(avg_win.abs() / avg_loss.abs(), 2) } else { 0.0 };

    let gross_profit = if win_count > 0 { py_round(wins.iter().map(|t| t.net_pnl).sum::<f64>(), 2) } else { 0.0 };
    let gross_loss = py_round(if loss_count > 0 { losses.iter().map(|t| t.net_pnl).sum::<f64>().abs() } else { 0.0 }, 2);
    let profit_factor = if gross_loss == 0.0 && gross_profit > 0.0 {
        999.99
    } else if gross_loss == 0.0 && gross_profit == 0.0 {
        0.0
    } else {
        py_round(gross_profit / gross_loss, 2)
    };

    // streaks — iterate in adf (scramble) order (base.py:985)
    let (mut max_win_streak, mut max_loss_streak, mut cur_win, mut cur_loss) = (0i64, 0i64, 0i64, 0i64);
    for t in &adf {
        if t.net_pnl > 0.0 {
            cur_win += 1; cur_loss = 0;
            if cur_win > max_win_streak { max_win_streak = cur_win; }
        } else if t.net_pnl < 0.0 {
            cur_loss += 1; cur_win = 0;
            if cur_loss > max_loss_streak { max_loss_streak = cur_loss; }
        } else {
            cur_win = 0; cur_loss = 0;
        }
    }

    // n_years: pd.to_datetime(min entry str, dayfirst) .. max exit str (base.py:997)
    let min_entry = adf.iter().map(|t| t.entry_date.as_str()).min().unwrap_or("");
    let max_exit = adf.iter().map(|t| t.exit_date.as_str()).max().unwrap_or("");
    let n_years = match (parse_dayfirst(min_entry), parse_dayfirst(max_exit)) {
        (Some(a), Some(b)) => ((b - a).num_days() as f64 / 365.0).max(0.01),
        _ => 0.01,
    };

    // final_nav = last (scramble order) Cumulative (base.py:1001)
    let final_nav = if n > 0 { adf[n - 1].eq_cum } else { 0.0 };
    let cagr = if final_nav > 0.0 && n_years > 0.0 {
        let raw = 100.0 * ((final_nav / 100.0).powf(1.0 / n_years) - 1.0);
        py_round(raw.clamp(-99999.0, 99999.0), 2)
    } else {
        py_round(-100.0, 2)
    };

    let max_dd_pct = adf.iter().map(|t| t.eq_pct_dd).fold(f64::INFINITY, f64::min);
    let max_dd_pts = py_round(adf.iter().map(|t| t.eq_dd).fold(f64::INFINITY, f64::min), 2);

    // MDD duration/dates (base.py:1011-1030), scramble order
    let (mut mdd_duration, mut mdd_start, mut mdd_end, mut mdd_trade_no): (i64, Option<String>, Option<String>, Option<i64>) = (0, None, None, None);
    if max_dd_pts < 0.0 {
        // trough_idx = idxmin(DD) — first occurrence of the min in adf order
        let mut trough_idx = 0usize;
        let mut trough_val = f64::INFINITY;
        for (i, t) in adf.iter().enumerate() {
            if t.eq_dd < trough_val {
                trough_val = t.eq_dd;
                trough_idx = i;
            }
        }
        mdd_trade_no = Some(trough_idx as i64 + 1);
        let peak_val = adf[trough_idx].eq_peak;
        // pre_trough = adf[..=trough_idx]; peak_candidates = Cumulative >= peak_val; take last
        let mut peak_date_str: Option<&str> = None;
        for t in adf[..=trough_idx].iter() {
            if t.eq_cum >= peak_val {
                peak_date_str = Some(t.exit_date.as_str());
            }
        }
        let peak_date_str = peak_date_str.unwrap_or(adf[0].exit_date.as_str());
        let trough_date_str = adf[trough_idx].exit_date.as_str();
        if let (Some(pd_), Some(td_)) = (parse_dayfirst(peak_date_str), parse_dayfirst(trough_date_str)) {
            mdd_duration = (td_ - pd_).num_days();
            mdd_start = Some(pd_.format("%Y-%m-%d").to_string());
            mdd_end = Some(td_.format("%Y-%m-%d").to_string());
        }
    }

    let car_mdd = if max_dd_pct != 0.0 { py_round((cagr / 100.0 / max_dd_pct.abs()).min(99999.0), 4) } else { 0.0 };
    let recovery_factor = if max_dd_pts != 0.0 { py_round((total_pnl / max_dd_pts.abs()).min(99999.0), 2) } else { 0.0 };

    // spot change (base.py:1035-1049)
    let (spot_change, spot_change_pct) = if any_spot_pnl_col {
        let sc: f64 = adf.iter().filter_map(|t| t.spot_pnl).sum();
        let scp: f64 = adf.iter().filter_map(|t| {
            match (t.spot_pnl, t.entry_spot.filter(|&v| v != 0.0)) {
                (Some(sp), Some(es)) => Some(sp / es * 100.0),
                _ => None,
            }
        }).sum();
        (py_round(sc, 2), py_round(scp, 4))
    } else {
        (0.0, 0.0)
    };

    // expectancy % (base.py:1051-1067) — full precision, no round
    let win_pcts: Vec<f64> = adf.iter().filter(|t| t.net_pnl > 0.0).map(|t| t.net_pnl_pct).filter(|v| v.is_finite()).collect();
    let loss_pcts: Vec<f64> = adf.iter().filter(|t| t.net_pnl < 0.0).map(|t| t.net_pnl_pct).filter(|v| v.is_finite()).collect();
    let avg_win_pct = if !win_pcts.is_empty() { win_pcts.iter().sum::<f64>() / win_pcts.len() as f64 } else { 0.0 };
    let avg_loss_pct = if !loss_pcts.is_empty() { loss_pcts.iter().sum::<f64>() / loss_pcts.len() as f64 } else { 0.0 };
    let w_decimal = win_pct / 100.0;
    let expectancy = if avg_loss_pct != 0.0 {
        (avg_win_pct / avg_loss_pct.abs()) * w_decimal - (1.0 - w_decimal)
    } else {
        0.0
    };

    // cagr_spot (base.py:1069-1079)
    let cagr_spot = {
        let is0 = adf[0].entry_spot.unwrap_or(0.0);
        let fs = adf[n - 1].exit_spot.unwrap_or(0.0);
        if n_years > 0.0 && is0 > 0.0 && fs > 0.0 {
            py_round(100.0 * ((fs / is0).powf(1.0 / n_years) - 1.0), 2)
        } else {
            0.0
        }
    };

    let sum_npp: f64 = adf.iter().map(|t| if t.net_pnl_pct.is_finite() { t.net_pnl_pct } else { 0.0 }).sum();
    let total_pnl_pct = py_round(sum_npp, 4);
    let avg_ppt_pct = if count > 0 { py_round(sum_npp / count as f64, 4) } else { 0.0 };

    // ── build summary dict ──
    let d = PyDict::new(py);
    d.set_item("total_pnl", total_pnl)?;
    d.set_item("total_pnl_pct", total_pnl_pct)?;
    d.set_item("avg_profit_per_trade_pct", avg_ppt_pct)?;
    d.set_item("count", count)?;
    d.set_item("win_pct", win_pct)?;
    d.set_item("loss_pct", loss_pct)?;
    d.set_item("avg_win", avg_win)?;
    d.set_item("avg_loss", avg_loss)?;
    d.set_item("max_win", max_win)?;
    d.set_item("max_loss", max_loss)?;
    d.set_item("avg_profit_per_trade", avg_profit_per_trade)?;
    d.set_item("expectancy", expectancy)?;
    d.set_item("avg_win_pct", py_round(avg_win_pct, 4))?;
    d.set_item("avg_loss_pct", py_round(avg_loss_pct, 4))?;
    d.set_item("reward_to_risk", reward_to_risk)?;
    d.set_item("profit_factor", profit_factor)?;
    d.set_item("cagr_options", cagr)?;
    d.set_item("max_dd_pct", max_dd_pct)?;
    d.set_item("max_dd_pts", max_dd_pts)?;
    d.set_item("mdd_duration_days", mdd_duration)?;
    match mdd_start { Some(s) => d.set_item("mdd_start_date", s)?, None => d.set_item("mdd_start_date", py.None())? }
    match mdd_end { Some(s) => d.set_item("mdd_end_date", s)?, None => d.set_item("mdd_end_date", py.None())? }
    match mdd_trade_no { Some(x) => d.set_item("mdd_trade_number", x)?, None => d.set_item("mdd_trade_number", py.None())? }
    d.set_item("car_mdd", car_mdd)?;
    d.set_item("recovery_factor", recovery_factor)?;
    d.set_item("max_win_streak", max_win_streak)?;
    d.set_item("max_loss_streak", max_loss_streak)?;
    d.set_item("spot_change", spot_change)?;
    d.set_item("spot_change_pct", spot_change_pct)?;
    d.set_item("cagr_spot", cagr_spot)?;
    Ok(d.into())
}
