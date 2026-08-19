//! Rust port of `services.optimizer.metrics.compute_optim_metrics` — the bundle of
//! "new" master-summary metrics computed from a per-leg tradesheet + the base summary.
//!
//! Reproduces all seven sub-functions op-for-op (parity-verified vs the Python in
//! tools/optim_metrics_parity.py):
//!   per_leg_pnl · roi_vs_spot · actual_live_dd · car_mdd_live ·
//!   outlier_stripped_live_dd (_trade_outlier_analysis) · leg_pct_no_outliers ·
//!   cagr_midcap_for_period.
//!
//! Quirks matched: prev-peak-shift Live DD (avg over ALL rows incl. 0-filled non-parent
//! rows); chronological (dayfirst) parent sort in the outlier block; the LDD rebuild's
//! two-stage banker's rounding; per-row %-of-entry-spot sums. Python round() = banker's
//! via analytics::py_round.

use chrono::NaiveDate;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::analytics::py_round;

// ── row extraction ───────────────────────────────────────────────────────────

fn cell_f64(d: &PyDict, key: &str) -> Option<f64> {
    match d.get_item(key).ok().flatten() {
        Some(v) if !v.is_none() => {
            if let Ok(f) = v.extract::<f64>() {
                return if f.is_finite() { Some(f) } else { None };
            }
            if let Ok(s) = v.extract::<String>() {
                let t = s.trim();
                if t.is_empty() { return None; }
                return t.parse::<f64>().ok();
            }
            None
        }
        _ => None,
    }
}

fn cell_str(d: &PyDict, key: &str) -> Option<String> {
    d.get_item(key).ok().flatten().and_then(|v| {
        if v.is_none() { None } else { v.str().ok().map(|s| s.to_string_lossy().into_owned()) }
    })
}

/// numeric with pandas fillna(0): missing/blank/non-numeric → 0.0
fn num0(d: &PyDict, key: &str) -> f64 {
    cell_f64(d, key).unwrap_or(0.0)
}

fn col_present(rows: &[&PyDict], key: &str) -> bool {
    rows.first().map(|d| d.contains(key).unwrap_or(false)).unwrap_or(false)
}

fn first_col<'a>(rows: &[&PyDict], names: &[&'a str]) -> Option<&'a str> {
    names.iter().copied().find(|n| col_present(rows, n))
}

fn parse_dayfirst(s: &str) -> Option<NaiveDate> {
    let t = s.trim();
    if t.is_empty() { return None; }
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"] {
        if let Ok(d) = NaiveDate::parse_from_str(t, fmt) { return Some(d); }
    }
    None
}

/// first non-blank Entry Spot, last non-blank Exit Spot (metrics._safe_first_last_spot)
fn safe_first_last_spot(rows: &[&PyDict]) -> (f64, f64) {
    if !col_present(rows, "Entry Spot") || !col_present(rows, "Exit Spot") {
        return (0.0, 0.0);
    }
    let first = rows.iter().find_map(|d| cell_f64(d, "Entry Spot"));
    let last = rows.iter().rev().find_map(|d| cell_f64(d, "Exit Spot"));
    match (first, last) {
        (Some(a), Some(b)) => (a, b),
        _ => (0.0, 0.0),
    }
}

#[pyfunction]
#[pyo3(signature = (trades, summary, midcap_first_spot=None, midcap_last_spot=None))]
pub fn compute_optim_metrics(
    trades: &PyList,
    summary: &PyDict,
    midcap_first_spot: Option<f64>,
    midcap_last_spot: Option<f64>,
) -> PyResult<PyObject> {
    let py = trades.py();
    let rows: Vec<&PyDict> = trades.iter().map(|o| o.downcast::<PyDict>()).collect::<Result<_, _>>()?;
    let out = PyDict::new(py);

    // ── per_leg_pnl ──
    let ce_col = first_col(&rows, &["Call P&L", "CE P&L", "call_pnl"]);
    let pe_col = first_col(&rows, &["Put P&L", "PE P&L", "put_pnl"]);
    let spot_col = first_col(&rows, &["Spot P&L", "spot_pnl"]);
    let sum_col = |c: Option<&str>| -> f64 { c.map(|c| rows.iter().map(|d| num0(d, c)).sum()).unwrap_or(0.0) };
    let ce_total = sum_col(ce_col);
    let pe_total = sum_col(pe_col);
    let spot_total = sum_col(spot_col);
    let (mut ce_pct, mut pe_pct, mut spot_pct) = (0.0, 0.0, 0.0);
    if col_present(&rows, "Entry Spot") {
        // per row: col / entry_spot (0→nan→0), sum, ×100
        let pct_of = |c: Option<&str>| -> f64 {
            match c {
                None => 0.0,
                Some(c) => py_round(rows.iter().map(|d| {
                    let es = cell_f64(d, "Entry Spot").filter(|&v| v != 0.0);
                    match es { Some(es) => num0(d, c) / es, None => 0.0 }
                }).sum::<f64>() * 100.0, 4),
            }
        };
        ce_pct = pct_of(ce_col);
        pe_pct = pct_of(pe_col);
        spot_pct = pct_of(spot_col);
    }
    out.set_item("ce_pnl_total", py_round(ce_total, 2))?;
    out.set_item("ce_pnl_pct", ce_pct)?;
    out.set_item("pe_pnl_total", py_round(pe_total, 2))?;
    out.set_item("pe_pnl_pct", pe_pct)?;
    out.set_item("long_spot_pnl", py_round(spot_total, 2))?;
    out.set_item("long_spot_pnl_pct", spot_pct)?;

    // ── roi_vs_spot(summary) ──
    let s_f64 = |k: &str| -> f64 {
        summary.get_item(k).ok().flatten()
            .and_then(|v| if v.is_none() { None } else { v.extract::<f64>().ok() })
            .unwrap_or(0.0)
    };
    let spot_change_pct = s_f64("spot_change_pct");
    let total_pnl_pct = s_f64("total_pnl_pct");
    let roi = if spot_change_pct == 0.0 { 0.0 } else { py_round(total_pnl_pct / spot_change_pct.abs(), 4) };
    out.set_item("roi_vs_spot", roi)?;

    // ── actual_live_dd ──
    let (live_max, live_avg) = actual_live_dd(&rows);
    out.set_item("actual_live_dd_max", live_max)?;
    out.set_item("actual_live_dd_avg", live_avg)?;

    // ── car_mdd_live ──
    let cagr = s_f64("cagr_options");
    let car_mdd_live = if live_max == 0.0 { 0.0 } else { py_round(cagr / live_max.abs(), 4) };
    out.set_item("car_mdd_live", car_mdd_live)?;

    // ── outlier_stripped_live_dd (_trade_outlier_analysis) ──
    trade_outlier_analysis(&rows, out)?;

    // ── leg_pct_no_outliers ──
    let ce_leg = if col_present(&rows, "Call P&L") { "Call P&L" } else { "CE P&L" };
    let pe_leg = if col_present(&rows, "Put P&L") { "Put P&L" } else { "PE P&L" };
    for (n, v) in ce_pe_pct_no_outliers(&rows, ce_leg) {
        out.set_item(format!("ce_pnl_pct_no_outlier_{}", n), v)?;
    }
    for (n, v) in ce_pe_pct_no_outliers(&rows, pe_leg) {
        out.set_item(format!("pe_pnl_pct_no_outlier_{}", n), v)?;
    }

    // ── cagr_midcap_for_period ──
    out.set_item("cagr_midcap", cagr_midcap(&rows, midcap_first_spot, midcap_last_spot))?;

    Ok(out.into())
}

fn actual_live_dd(rows: &[&PyDict]) -> (f64, f64) {
    // live per ALL rows (0 on non-parent rows), then min & mean over all.
    let n = rows.len();
    if n == 0 { return (0.0, 0.0); }
    let mut live: Vec<f64>;
    if col_present(rows, "Lowest NAV During Trade") && col_present(rows, "Peak") {
        // parent rows = those with a non-nan Lowest NAV; prev_peak = shift(1) of parent
        // peaks, first seeded 100.
        let mut prev_peak = 100.0f64;
        live = vec![0.0; n];
        for (i, d) in rows.iter().enumerate() {
            if let Some(low) = cell_f64(d, "Lowest NAV During Trade") {
                let peak = cell_f64(d, "Peak").unwrap_or(f64::NAN);
                live[i] = low - prev_peak;
                if peak.is_finite() { prev_peak = peak; }
            }
        }
    } else if col_present(rows, "Actual Live DD") {
        live = rows.iter().map(|d| num0(d, "Actual Live DD")).collect();
    } else if col_present(rows, "%DD") {
        live = rows.iter().map(|d| num0(d, "%DD")).collect();
    } else {
        return (0.0, 0.0);
    }
    if live.is_empty() { return (0.0, 0.0); }
    let min = live.iter().cloned().fold(f64::INFINITY, f64::min);
    let avg = live.iter().sum::<f64>() / live.len() as f64;
    (py_round(min, 4), py_round(avg, 4))
}

struct Pair { pct: f64, mae: Option<f64>, idx: usize }

fn trade_outlier_analysis(rows: &[&PyDict], out: &PyDict) -> PyResult<()> {
    // defaults
    for n in 1..=3 {
        out.set_item(format!("positive_outlier_{}", n), 0.0)?;
        out.set_item(format!("negative_outlier_{}", n), 0.0)?;
        out.set_item(format!("outlier_dd_{}", n), 0.0)?;
        out.set_item(format!("outlier_dd_{}_avg", n), 0.0)?;
        out.set_item(format!("ce_pe_pnl_pct_without_top_{}_outliers", n), 0.0)?;
    }
    if rows.is_empty() { return Ok(()); }

    // parent extraction: sort by Entry Date (dayfirst, na last, STABLE), first-seen Trade
    let mut order: Vec<usize> = (0..rows.len()).collect();
    if col_present(rows, "Entry Date") {
        order.sort_by(|&a, &b| {
            let da = cell_str(rows[a], "Entry Date").and_then(|s| parse_dayfirst(&s));
            let db = cell_str(rows[b], "Entry Date").and_then(|s| parse_dayfirst(&s));
            match (da, db) {
                (Some(x), Some(y)) => x.cmp(&y),
                (Some(_), None) => std::cmp::Ordering::Less,   // na last
                (None, Some(_)) => std::cmp::Ordering::Greater,
                (None, None) => std::cmp::Ordering::Equal,
            }
        });
    }
    let pct_col = match first_col(rows, &["% P&L", "Net P&L %"]) { Some(c) => c, None => return Ok(()) };
    let has_lowpeak = col_present(rows, "Lowest NAV During Trade") && col_present(rows, "Peak");

    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut pairs: Vec<Pair> = Vec::new();
    for &i in &order {
        let d = rows[i];
        let tid = cell_str(d, "Trade").or_else(|| cell_str(d, "trade")).unwrap_or_else(|| i.to_string());
        if !seen.insert(tid) { continue; }
        let pct = cell_f64(d, pct_col).unwrap_or(0.0);
        let mae = if col_present(rows, "Final MAE") { cell_f64(d, "Final MAE") } else { None };
        // ldd retained in pairs by Python but unused in the rebuild (mae drives it); skip.
        let _ldd = if has_lowpeak {
            match (cell_f64(d, "Lowest NAV During Trade"), cell_f64(d, "Peak")) {
                (Some(l), Some(p)) => Some(l - p), _ => None,
            }
        } else { None };
        pairs.push(Pair { pct, mae, idx: pairs.len() });
    }
    let n_trades = pairs.len();
    if n_trades == 0 { return Ok(()); }

    let mut by_desc: Vec<usize> = (0..n_trades).collect();
    // sort pairs by pct DESC, stable (Python sorted() is stable)
    by_desc.sort_by(|&a, &b| pairs[b].pct.partial_cmp(&pairs[a].pct).unwrap_or(std::cmp::Ordering::Equal));
    let total_pct_sum: f64 = pairs.iter().map(|p| p.pct).sum();

    let sum_top = |count: usize| -> f64 { by_desc.iter().take(count).map(|&i| pairs[i].pct).sum() };
    let sum_bottom = |count: usize| -> f64 {
        let start = n_trades.saturating_sub(count);
        by_desc[start..].iter().map(|&i| pairs[i].pct).sum()
    };

    let ldd_exc_stats = |exc_top: usize, exc_bot: usize| -> (f64, f64) {
        let mut exc: std::collections::HashSet<usize> = std::collections::HashSet::new();
        for &i in by_desc.iter().take(exc_top) { exc.insert(pairs[i].idx); }
        let start = n_trades.saturating_sub(exc_bot);
        for &i in &by_desc[start..] { exc.insert(pairs[i].idx); }
        let filtered: Vec<&Pair> = pairs.iter().filter(|p| !exc.contains(&p.idx)).collect();
        if filtered.is_empty() { return (0.0, 0.0); }
        let (mut cumulative, mut peak, mut prev_cum) = (100.0f64, 100.0f64, 100.0f64);
        let mut rebuilt: Vec<f64> = Vec::new();
        for p in &filtered {
            let prev_peak = peak;
            cumulative *= 1.0 + p.pct / 100.0;
            if cumulative > peak { peak = cumulative; }
            if let Some(mae) = p.mae {
                if prev_peak != 0.0 {
                    let lowest_nav = py_round(prev_cum * (1.0 + mae / 100.0), 2);
                    let actual_ldd = py_round((lowest_nav / prev_peak - 1.0) * 100.0, 2);
                    rebuilt.push(actual_ldd);
                }
            }
            prev_cum = cumulative;
        }
        if rebuilt.is_empty() { return (0.0, 0.0); }
        let min = rebuilt.iter().cloned().fold(f64::INFINITY, f64::min);
        let avg = rebuilt.iter().sum::<f64>() / rebuilt.len() as f64;
        (py_round(min, 4), py_round(avg, 4))
    };

    for n in 1..=3usize {
        let pos = sum_top(n);
        let neg = sum_bottom(n);
        let (mx, av) = ldd_exc_stats(n, n);
        out.set_item(format!("positive_outlier_{}", n), py_round(pos, 4))?;
        out.set_item(format!("negative_outlier_{}", n), py_round(neg, 4))?;
        out.set_item(format!("outlier_dd_{}", n), mx)?;
        out.set_item(format!("outlier_dd_{}_avg", n), av)?;
        out.set_item(format!("ce_pe_pnl_pct_without_top_{}_outliers", n), py_round(total_pct_sum - pos - neg, 4))?;
    }
    Ok(())
}

/// _ce_pe_pct_no_outliers → Vec<(n, value)>; empty if col missing or initial_spot<=0.
fn ce_pe_pct_no_outliers(rows: &[&PyDict], leg_col: &str) -> Vec<(u8, f64)> {
    if !col_present(rows, leg_col) { return vec![]; }
    let (initial_spot, _) = safe_first_last_spot(rows);
    if initial_spot <= 0.0 { return vec![]; }
    let mut s: Vec<f64> = rows.iter().map(|d| num0(d, leg_col)).collect();
    // sort DESC (largest first). Python sort_values(ascending=False) stable.
    s.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    let mut res = vec![];
    for n in 1..=3usize {
        if s.len() <= n {
            res.push((n as u8, 0.0));
        } else {
            let kept: f64 = s[n..].iter().sum();
            res.push((n as u8, py_round(kept / initial_spot * 100.0, 4)));
        }
    }
    res
}

fn cagr_midcap(rows: &[&PyDict], first: Option<f64>, last: Option<f64>) -> f64 {
    let (first, last) = match (first, last) {
        (Some(f), Some(l)) if f > 0.0 && l > 0.0 => (f, l),
        _ => return 0.0,
    };
    if !col_present(rows, "Entry Date") || !col_present(rows, "Exit Date") { return 0.0; }
    let start = rows.iter().filter_map(|d| cell_str(d, "Entry Date").and_then(|s| parse_dayfirst(&s))).min();
    let end = rows.iter().filter_map(|d| cell_str(d, "Exit Date").and_then(|s| parse_dayfirst(&s))).max();
    match (start, end) {
        (Some(s), Some(e)) => {
            let n_years = ((e - s).num_days() as f64 / 365.0).max(0.01);
            py_round(100.0 * ((last / first).powf(1.0 / n_years) - 1.0), 2)
        }
        _ => 0.0,
    }
}
