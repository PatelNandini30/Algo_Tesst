//! Rust port of `runner._compute_mae_mfe_batch` (per-leg MAE/MFE window scan).
//!
//! Reads day High/Low/Settled straight from the shared Rust cache (the same data the
//! Python path scans out of the feather — the Python code was written to MIRROR the
//! Rust `get_ohlc_range`, so the cache IS the source of truth). Reproduces the four
//! edge cases exactly (all verified against the Python):
//!
//!   1. Window = (next trading day AFTER entry) .. exit  (entry-day bar excluded;
//!      same-day fallback to entry).
//!   2. Expiry ±1 candidate matching, tried in order [expiry, +1d, -1d]; first day-row
//!      that exists wins.
//!   3. Settled-price substitution applied INDEPENDENTLY per High and Low: use the raw
//!      value if >0, else the day's settled price if >0, else that side contributes
//!      nothing that day.
//!   4. SL-family adverse cap (STOP_LOSS / SL_WITH_BUFFER[_GAP] / STOP_LOSS_BUFFER[_GAP])
//!      with exit_price>0: SELL caps max_high at exit_price, BUY floors min_low.
//!
//!   SELL: mae=(entry-high)/spot, mfe=(entry-low)/spot ; BUY mirrored. ×100, round4.
//!
//! `compute_mae_mfe_batch(trades, index, trading_days)` returns a list aligned with the
//! input rows, each `[mae, mfe]`. Ineligible rows return their EXISTING MAE/MFE
//! unchanged (exactly like the Python, which seeds mae_vals from the df and only
//! overwrites eligible rows). Parity: tools/mae_parity.py.

use chrono::{Duration, NaiveDate};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::analytics::py_round;
use crate::{lookup_option_high, lookup_option_low, lookup_option_settled};

fn get_f64(d: &PyDict, key: &str) -> Option<f64> {
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

fn get_str(d: &PyDict, key: &str) -> Option<String> {
    d.get_item(key).ok().flatten().and_then(|v| {
        if v.is_none() { None } else { v.str().ok().map(|s| s.to_string_lossy().into_owned()) }
    })
}

/// _to_iso: DD-MM-YYYY (Python engine output) → YYYY-MM-DD; else parse common forms.
fn to_iso(v: &str) -> Option<String> {
    let t = v.trim();
    let b = t.as_bytes();
    if t.len() == 10 && b[2] == b'-' && b[5] == b'-' {
        if let Ok(d) = NaiveDate::parse_from_str(t, "%d-%m-%Y") {
            return Some(d.format("%Y-%m-%d").to_string());
        }
    }
    for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"] {
        if let Ok(d) = NaiveDate::parse_from_str(t, fmt) {
            return Some(d.format("%Y-%m-%d").to_string());
        }
    }
    None
}

/// [expiry, expiry+1day, expiry-1day] as ISO strings (mirrors _expiry_cands_str).
fn expiry_cands(iso: &str) -> Vec<String> {
    match NaiveDate::parse_from_str(iso, "%Y-%m-%d") {
        Ok(d) => vec![
            iso.to_string(),
            (d + Duration::days(1)).format("%Y-%m-%d").to_string(),
            (d - Duration::days(1)).format("%Y-%m-%d").to_string(),
        ],
        Err(_) => vec![iso.to_string()],
    }
}

fn is_sl_cap_reason(r: &str) -> bool {
    matches!(r,
        "STOP_LOSS" | "SL_WITH_BUFFER" | "SL_WITH_BUFFER_GAP"
        | "STOP_LOSS_BUFFER" | "STOP_LOSS_BUFFER_GAP")
}

#[pyfunction]
pub fn compute_mae_mfe_batch(
    trades: &PyList,
    index: String,
    trading_days: Vec<String>,
) -> PyResult<PyObject> {
    let py = trades.py();
    let out = PyList::empty(py);
    if trades.is_empty() || trading_days.is_empty() {
        // mirror Python's early return: leave rows as-is
        for obj in trades.iter() {
            let d = obj.downcast::<PyDict>()?;
            let mae = get_f64(d, "MAE").unwrap_or(0.0);
            let mfe = get_f64(d, "MFE").unwrap_or(0.0);
            out.append(PyList::new(py, [mae, mfe]))?;
        }
        return Ok(out.into());
    }

    // td_sorted = sorted(set(trading_days))
    let mut td: Vec<String> = trading_days;
    td.sort();
    td.dedup();

    for obj in trades.iter() {
        let d = obj.downcast::<PyDict>()?;
        let existing_mae = get_f64(d, "MAE").unwrap_or(0.0);
        let existing_mfe = get_f64(d, "MFE").unwrap_or(0.0);

        // eligibility (Python: opt_type in CE/PE; strike>0 & entry_spot>0; dates present)
        let opt_type = get_str(d, "Type").unwrap_or_default().to_uppercase();
        let keep = || -> PyResult<()> {
            out.append(PyList::new(py, [existing_mae, existing_mfe]))?;
            Ok(())
        };
        if opt_type != "CE" && opt_type != "PE" { keep()?; continue; }
        let strike = get_f64(d, "Strike").unwrap_or(0.0);
        let entry_price = get_f64(d, "Entry Price").unwrap_or(0.0);
        let entry_spot = get_f64(d, "Entry Spot").unwrap_or(0.0);
        let position = get_str(d, "B/S").unwrap_or_else(|| "SELL".to_string()).to_uppercase();
        if strike <= 0.0 || entry_spot <= 0.0 { keep()?; continue; }
        let (expiry_raw, entry_dt, exit_dt) = (get_str(d, "Expiry"), get_str(d, "Entry Date"), get_str(d, "Exit Date"));
        let (expiry_raw, entry_dt, exit_dt) = match (expiry_raw, entry_dt, exit_dt) {
            (Some(e), Some(en), Some(ex)) if !e.is_empty() => (e, en, ex),
            _ => { keep()?; continue; }
        };
        let (entry_str, exit_str, expiry_str) = match (to_iso(&entry_dt), to_iso(&exit_dt), to_iso(&expiry_raw)) {
            (Some(a), Some(b), Some(c)) => (a, b, c),
            _ => { keep()?; continue; }
        };

        // window: win_start = first td strictly AFTER entry (bisect_right); fallback entry
        let idx_r = td.partition_point(|v| v.as_str() <= entry_str.as_str());
        let mut win_start = if idx_r < td.len() { td[idx_r].clone() } else { entry_str.clone() };
        if win_start.as_str() > exit_str.as_str() { win_start = entry_str.clone(); }
        if win_start.as_str() > exit_str.as_str() { keep()?; continue; }

        let exit_reason = get_str(d, "Exit Reason").unwrap_or_default().trim().to_uppercase();
        let exit_price = get_f64(d, "Exit Price");

        // window_days = td[bisect_left(win_start) .. bisect_right(win_end=exit)]
        let lo = td.partition_point(|v| v.as_str() < win_start.as_str());
        let hi = td.partition_point(|v| v.as_str() <= exit_str.as_str());
        let cands = expiry_cands(&expiry_str);

        let mut highs: Vec<f64> = Vec::new();
        let mut lows: Vec<f64> = Vec::new();
        for day in &td[lo..hi] {
            for cand in &cands {
                // "row exists" = EITHER High or Low is present for this
                // (day, strike, opt, cand-expiry) — NOT High alone. High and
                // Low are independently-optional columns (older feathers may
                // be missing just one; lib.rs's own lookup_option_high/_low
                // docstrings say "None if absent" for EACH separately, backed
                // by separate cache maps options_high/options_low). Gating
                // row-existence on High alone meant a day with a null High
                // but a valid Low was skipped ENTIRELY — the valid Low was
                // silently never captured, and if enough days were affected
                // this way `lows` could end up empty while `highs` was not,
                // tripping the empty-guard below and zeroing BOTH MAE and MFE
                // for the whole trade. The Python reference this mirrors
                // (runner.py's _compute_mae_mfe_batch) evaluates High and Low
                // independently for exactly this reason.
                let high_opt = lookup_option_high(day, &index, strike, &opt_type, cand);
                let low_opt = lookup_option_low(day, &index, strike, &opt_type, cand);
                if high_opt.is_none() && low_opt.is_none() {
                    continue; // genuinely no row for this candidate expiry
                }
                let settled = lookup_option_settled(day, &index, strike, &opt_type, cand)
                    .filter(|&s| s > 0.0);
                match high_opt {
                    Some(high) if high > 0.0 => highs.push(high),
                    _ => if let Some(s) = settled { highs.push(s); }
                }
                match low_opt {
                    Some(low) if low > 0.0 => lows.push(low),
                    _ => if let Some(s) = settled { lows.push(s); }
                }
                break; // first existing candidate wins
            }
        }

        if highs.is_empty() || lows.is_empty() { keep()?; continue; }

        let mut max_high = highs.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let mut min_low = lows.iter().cloned().fold(f64::INFINITY, f64::min);

        // SL-family adverse cap
        if is_sl_cap_reason(&exit_reason) {
            if let Some(ep) = exit_price {
                if ep > 0.0 {
                    if position == "SELL" { max_high = max_high.min(ep); }
                    else { min_low = min_low.max(ep); }
                }
            }
        }

        let (mae, mfe) = if position == "SELL" {
            ((entry_price - max_high) / entry_spot, (entry_price - min_low) / entry_spot)
        } else {
            ((min_low - entry_price) / entry_spot, (max_high - entry_price) / entry_spot)
        };
        out.append(PyList::new(py, [py_round(mae * 100.0, 4), py_round(mfe * 100.0, 4)]))?;
    }

    Ok(out.into())
}
