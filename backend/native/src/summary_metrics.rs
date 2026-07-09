//! Rust port of `excel_builder.compute_xlsx_summary_metrics` (_cxsm) — the CHRONOLOGICAL
//! master/optim summary engine — for the NON-MIDCAP case (overall + patchwise).
//!
//! This is the LIVE-matching summary (execute_algotest_job / the optim master), unlike
//! base.compute_analytics which uses a DD-MM-YYYY scramble and diverges on some
//! strategies. It reproduces `_aggregate_trades` (per-trade net/pct/FinalMAE, the
//! canonical sort (parse(entry), int(Trade), int(Leg)), the patch-aware equity chain and
//! Live-DD chain) and the `_cxsm` summary assembly (per-leg sums, CAGR, max-DD from the
//! source %DD, outliers, outlier-stripped Live DD), all with Python banker's rounding.
//!
//! Parity: tools/summary_metrics_parity.py vs compute_xlsx_summary_metrics. Midcap is
//! out of scope here (needs the overlay engine) — the caller passes no midcap legs.

use std::collections::HashMap;

use chrono::NaiveDate;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::analytics::py_round;

// ── cell helpers ─────────────────────────────────────────────────────────────

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

fn cell_str(d: &PyDict, key: &str) -> String {
    d.get_item(key).ok().flatten().and_then(|v| {
        if v.is_none() { None } else { v.str().ok().map(|s| s.to_string_lossy().into_owned()) }
    }).unwrap_or_default()
}

fn cell_truthy(d: &PyDict, key: &str) -> bool {
    match d.get_item(key).ok().flatten() {
        Some(v) if !v.is_none() => {
            if let Ok(b) = v.extract::<bool>() { return b; }
            if let Ok(s) = v.extract::<String>() {
                let t = s.trim().to_lowercase();
                return !(t.is_empty() || t == "0" || t == "false" || t == "none");
            }
            if let Ok(f) = v.extract::<f64>() { return f != 0.0; }
            true
        }
        _ => false,
    }
}

/// _parse_date: year-first then day-first formats (matches excel_builder._parse_date).
fn parse_date(s: &str) -> Option<NaiveDate> {
    let t = s.trim();
    if t.is_empty() { return None; }
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"] {
        if let Ok(d) = NaiveDate::parse_from_str(t, fmt) { return Some(d); }
    }
    None
}

/// _date_ms: UTC ms at 00:00 (calendar.timegm). Also used for the span difference — the
/// tz offset cancels in max_exit-min_entry, so UTC matches Python's local-tz _parse_date_ms.
fn date_ms(s: &str) -> Option<i64> {
    parse_date(s).map(|d| d.and_hms_opt(0, 0, 0).unwrap().and_utc().timestamp() * 1000)
}

fn is_bullish(typ: &str, bs: &str) -> bool {
    (matches!(typ, "CE" | "CALL") && bs == "BUY")
        || (matches!(typ, "PE" | "PUT") && bs == "SELL")
        || (typ == "FUT" && bs == "BUY")
}
fn is_bearish(typ: &str, bs: &str) -> bool {
    (matches!(typ, "CE" | "CALL") && bs == "SELL")
        || (matches!(typ, "PE" | "PUT") && bs == "BUY")
        || (typ == "FUT" && bs == "SELL")
}

struct Leg {
    trade: String,
    trade_i: i64,
    leg_i: i64,
    typ: String,
    bs: String,
    net_pnl: Option<f64>,
    ce_pnl: Option<f64>,
    pe_pnl: Option<f64>,
    fut_pnl: Option<f64>,
    entry_spot: Option<f64>,
    exit_spot: Option<f64>,
    spot_pnl: Option<f64>,
    mae: Option<f64>,
    mfe: Option<f64>,
    entry_date: String,
    exit_date: String,
    exit_reason: String,
    is_dir: bool, // Type in CE/CALL/PE/PUT/FUT
    is_main: bool, // not reentry & not lazy
}

/// _calc_trade_mae → (nm1, nm2, final) or None.
fn calc_trade_mae(legs: &[&Leg], net_pnl_pct: f64) -> Option<(f64, f64, f64)> {
    let dir: Vec<&&Leg> = legs.iter().filter(|l| l.is_dir).collect();
    if dir.is_empty() { return None; }
    let sum_field = |sel: &dyn Fn(&Leg) -> Option<f64>, pred: &dyn Fn(&Leg) -> bool| -> Option<f64> {
        let mut total = 0.0;
        for l in dir.iter().filter(|l| pred(l)) {
            match sel(l) { Some(v) => total += v, None => return None }
        }
        Some(total)
    };
    let bull = |l: &Leg| is_bullish(&l.typ, &l.bs);
    let bear = |l: &Leg| is_bearish(&l.typ, &l.bs);
    let mae = |l: &Leg| l.mae;
    let mfe = |l: &Leg| l.mfe;
    let bull_mae = sum_field(&mae, &bull)?;
    let bull_mfe = sum_field(&mfe, &bull)?;
    let bear_mae = sum_field(&mae, &bear)?;
    let bear_mfe = sum_field(&mfe, &bear)?;
    let nm1 = bull_mae + bear_mfe;
    let nm2 = bull_mfe + bear_mae;
    let final_v = if dir.len() > 1 { nm1.min(nm2).min(net_pnl_pct) } else { nm1.min(nm2) };
    Some((py_round(nm1, 4), py_round(nm2, 4), py_round(final_v, 4)))
}

struct TradeAgg {
    net: f64,
    pct: f64,
    final_mae: Option<f64>,
    exit_reason: String,
    cumulative: f64,
    peak: f64,
    pct_dd: f64,
    actual_ldd: Option<f64>,
    main_entry: String,
    main_exit: String,
    main_entry_spot: Option<f64>,
    main_exit_spot: Option<f64>,
    main_spot_pnl: Option<f64>,
}

fn empty(py: Python<'_>) -> PyResult<PyObject> {
    Ok(PyDict::new(py).into())
}

#[pyfunction]
#[pyo3(signature = (trades, summary, patchwise=false, filter_segments=None))]
pub fn compute_summary_metrics(
    trades: &PyList,
    summary: &PyDict,
    patchwise: bool,
    filter_segments: Option<&PyList>,
) -> PyResult<PyObject> {
    let py = trades.py();
    if trades.is_empty() { return empty(py); }

    // ── extract legs, preserving row order ──
    let mut legs: Vec<Leg> = Vec::with_capacity(trades.len());
    for obj in trades.iter() {
        let d = obj.downcast::<PyDict>()?;
        let trade = {
            let t = cell_str(d, "Trade");
            if t.is_empty() { let t2 = cell_str(d, "trade"); if t2.is_empty() { "1".into() } else { t2 } } else { t }
        };
        let typ = cell_str(d, "Type").to_uppercase();
        let is_reentry = cell_truthy(d, "ReEntryIndex") || cell_truthy(d, "ReEntryTrigger") || cell_truthy(d, "ReEntryMode");
        let is_lazy = {
            let v = cell_str(d, "Is Lazy Leg").to_lowercase();
            v == "true" || cell_truthy(d, "Lazy Leg Name")
        };
        legs.push(Leg {
            trade_i: trade.parse::<i64>().unwrap_or(1),
            trade,
            leg_i: cell_str(d, "Leg").parse::<i64>().unwrap_or_else(|_| cell_str(d, "leg").parse::<i64>().unwrap_or(1)),
            bs: cell_str(d, "B/S").to_uppercase(),
            net_pnl: cell_f64(d, "Net P&L"),
            ce_pnl: cell_f64(d, "CE P&L").or_else(|| cell_f64(d, "Call P&L")),
            pe_pnl: cell_f64(d, "PE P&L").or_else(|| cell_f64(d, "Put P&L")),
            fut_pnl: cell_f64(d, "FUT P&L"),
            entry_spot: cell_f64(d, "Entry Spot"),
            exit_spot: cell_f64(d, "Exit Spot"),
            spot_pnl: cell_f64(d, "Spot P&L"),
            mae: cell_f64(d, "MAE"),
            mfe: cell_f64(d, "MFE"),
            entry_date: cell_str(d, "Entry Date"),
            exit_date: cell_str(d, "Exit Date"),
            exit_reason: cell_str(d, "Exit Reason").trim().to_string(),
            is_dir: matches!(typ.as_str(), "CE" | "CALL" | "PE" | "PUT" | "FUT"),
            is_main: !is_reentry && !is_lazy,
            typ,
        });
    }

    // per-leg %DD source column min (for overall max_dd) + per-leg sums
    let mut src_dd: Vec<f64> = Vec::new();
    let (mut ce_sum, mut pe_sum, mut fut_sum) = (0.0f64, 0.0f64, 0.0f64);
    let (mut ce_pct_sum, mut pe_pct_sum, mut spot_pct_sum) = (0.0f64, 0.0f64, 0.0f64);
    for obj in trades.iter() {
        let d = obj.downcast::<PyDict>()?;
        if let Some(v) = cell_f64(d, "%DD") { src_dd.push(v); }
        let ce = cell_f64(d, "CE P&L").or_else(|| cell_f64(d, "Call P&L"));
        let pe = cell_f64(d, "PE P&L").or_else(|| cell_f64(d, "Put P&L"));
        let fu = cell_f64(d, "FUT P&L");
        ce_sum += ce.unwrap_or(0.0);
        pe_sum += pe.unwrap_or(0.0);
        fut_sum += fu.unwrap_or(0.0);
        let es = cell_f64(d, "Entry Spot");
        let cep = cell_f64(d, "CE P&L %").or_else(|| match (ce, es) { (Some(c), Some(e)) if e != 0.0 => Some(c / e), _ => None });
        ce_pct_sum += cep.unwrap_or(0.0);
        let pep = cell_f64(d, "PE P&L %").or_else(|| match (pe, es) { (Some(p), Some(e)) if e != 0.0 => Some(p / e), _ => None });
        pe_pct_sum += pep.unwrap_or(0.0);
        let sp = cell_f64(d, "Spot P&L");
        let spp = cell_f64(d, "Spot P&L %").or_else(|| match (sp, es) { (Some(s), Some(e)) if e != 0.0 => Some(s / e), _ => None });
        spot_pct_sum += spp.unwrap_or(0.0);
    }

    // ── group by trade (first-seen order) ──
    let mut order: Vec<String> = Vec::new();
    let mut groups: HashMap<String, Vec<usize>> = HashMap::new();
    for (i, l) in legs.iter().enumerate() {
        groups.entry(l.trade.clone()).or_insert_with(|| { order.push(l.trade.clone()); Vec::new() }).push(i);
    }

    // patch seg starts
    let mut seg_starts: Vec<i64> = Vec::new();
    if let Some(fs) = filter_segments {
        for o in fs.iter() {
            if let Ok(sd) = o.downcast::<PyDict>() {
                for key in ["start", "Start", "from", "start_date", "startdt"] {
                    let v = cell_str(sd, key);
                    if !v.is_empty() { if let Some(ms) = date_ms(&v) { seg_starts.push(ms); } break; }
                }
            }
        }
    }
    seg_starts.sort();
    let seg_idx = |entry: &str| -> i64 {
        match date_ms(entry) {
            None => -1,
            Some(em) => {
                let mut i = -1i64;
                for (j, &sm) in seg_starts.iter().enumerate() { if sm <= em { i = j as i64; } else { break; } }
                i
            }
        }
    };

    // ── per-trade aggregate (net/pct/finalMae/exitReason/main-leg fields) ──
    let mut tm: HashMap<String, TradeAgg> = HashMap::new();
    for k in &order {
        let idxs = &groups[k];
        let leg_refs: Vec<&Leg> = idxs.iter().map(|&i| &legs[i]).collect();
        let main = leg_refs.iter().find(|l| l.is_main).copied().unwrap_or(leg_refs[0]);
        let spot = main.entry_spot.unwrap_or(0.0);
        let raw_net = match main.net_pnl {
            Some(v) => v,
            None => leg_refs.iter().map(|l| l.ce_pnl.unwrap_or(0.0) + l.pe_pnl.unwrap_or(0.0) + l.fut_pnl.unwrap_or(0.0)).sum(),
        };
        let pct = if spot != 0.0 { raw_net / spot * 100.0 } else { 0.0 };
        let mae_res = calc_trade_mae(&leg_refs, pct);
        tm.insert(k.clone(), TradeAgg {
            net: raw_net, pct,
            final_mae: mae_res.map(|m| m.2),
            exit_reason: main.exit_reason.clone(),
            cumulative: 0.0, peak: 0.0, pct_dd: 0.0, actual_ldd: None,
            main_entry: main.entry_date.clone(), main_exit: main.exit_date.clone(),
            main_entry_spot: main.entry_spot, main_exit_spot: main.exit_spot, main_spot_pnl: main.spot_pnl,
        });
    }

    // ── canonical sort (parse(entry), int(Trade), int(Leg)) → sorted_keys ──
    let mut sorted_legs: Vec<usize> = (0..legs.len()).collect();
    sorted_legs.sort_by(|&a, &b| {
        let la = &legs[a]; let lb = &legs[b];
        let da = parse_date(&la.entry_date).unwrap_or(NaiveDate::MAX);
        let db = parse_date(&lb.entry_date).unwrap_or(NaiveDate::MAX);
        da.cmp(&db).then(la.trade_i.cmp(&lb.trade_i)).then(la.leg_i.cmp(&lb.leg_i))
    });
    let mut seen = std::collections::HashSet::new();
    let mut sorted_keys: Vec<String> = Vec::new();
    for &i in &sorted_legs {
        let k = &legs[i].trade;
        if !seen.contains(k) && tm.contains_key(k) { seen.insert(k.clone()); sorted_keys.push(k.clone()); }
    }

    // ── equity chain (chronological, patch-aware) ──
    let mut cumulative = 100.0f64; let mut peak = 100.0f64;
    let mut prev_key: Option<String> = None;
    for k in &sorted_keys {
        if patchwise {
            if let Some(pk) = &prev_key {
                let new_patch = if !seg_starts.is_empty() {
                    seg_idx(&tm[k].main_entry) != seg_idx(&tm[pk].main_entry)
                } else {
                    tm[pk].exit_reason.to_uppercase().split('+').any(|s| s == "FILTER_END")
                };
                if new_patch { cumulative = 100.0; peak = 100.0; }
            }
        }
        prev_key = Some(k.clone());
        let pct = tm[k].pct;
        cumulative *= 1.0 + pct / 100.0;
        if cumulative > peak { peak = cumulative; }
        let dd = if peak > cumulative { cumulative - peak } else { 0.0 };
        let pct_dd = if peak != 0.0 { dd / peak } else { 0.0 };
        let t = tm.get_mut(k).unwrap();
        t.cumulative = cumulative; t.peak = peak; t.pct_dd = pct_dd;
    }

    // ── Live DD chain (prev-peak rule, patch-aware) ──
    let mut prev_cum = 100.0f64; let mut prev_peak = 100.0f64;
    let mut prev_key2: Option<String> = None;
    for k in &sorted_keys {
        if patchwise {
            if let Some(pk) = &prev_key2 {
                let new_patch = if !seg_starts.is_empty() {
                    seg_idx(&tm[k].main_entry) != seg_idx(&tm[pk].main_entry)
                } else {
                    tm[pk].exit_reason.to_uppercase().split('+').any(|s| s == "FILTER_END")
                };
                if new_patch { prev_cum = 100.0; prev_peak = 100.0; }
            }
        }
        prev_key2 = Some(k.clone());
        let (mae, tpeak, tcum) = { let t = &tm[k]; (t.final_mae, t.peak, t.cumulative) };
        if let Some(mae) = mae {
            if prev_peak != 0.0 {
                let lowest_nav = py_round(prev_cum * (1.0 + mae / 100.0), 2);
                let actual_ldd = py_round((lowest_nav / prev_peak - 1.0) * 100.0, 2);
                tm.get_mut(k).unwrap().actual_ldd = Some(actual_ldd);
            }
        }
        prev_cum = tcum;
        prev_peak = tpeak;
    }

    // ── summary assembly ──
    let s_f64 = |k: &str| -> Option<f64> {
        summary.get_item(k).ok().flatten().and_then(|v| if v.is_none() { None } else { v.extract::<f64>().ok() })
    };

    let (mut sum_pct, mut sum_pos, mut sum_neg) = (0.0f64, 0.0f64, 0.0f64);
    let (mut win_cnt, mut loss_cnt, mut total_cnt) = (0i64, 0i64, 0i64);
    let mut sum_net = 0.0f64;
    let mut spot_sum_gated = 0.0f64;
    let (mut min_entry, mut max_exit): (Option<i64>, Option<i64>) = (None, None);
    for k in &sorted_keys {
        let t = &tm[k];
        let p = t.pct;
        if p.is_finite() {
            sum_pct += p; total_cnt += 1;
            if p > 0.0 { sum_pos += p; win_cnt += 1; } else if p < 0.0 { sum_neg += p; loss_cnt += 1; }
        }
        if t.net.is_finite() { sum_net += t.net; }
        if let Some(sp) = t.main_spot_pnl { spot_sum_gated += sp; }
        // NOTE: min/max entry use UTC ms; the offset cancels in the span difference.
        if let Some(ed) = date_ms(&t.main_entry) { if min_entry.map_or(true, |m| ed < m) { min_entry = Some(ed); } }
        if let Some(xd) = date_ms(&t.main_exit) { if max_exit.map_or(true, |m| xd > m) { max_exit = Some(xd); } }
    }

    let init_spot = sorted_keys.first().and_then(|k| tm[k].main_entry_spot);
    let final_spot = sorted_keys.last().and_then(|k| tm[k].main_exit_spot);
    let final_cum = sorted_keys.last().map(|k| tm[k].cumulative).unwrap_or(100.0);

    let avg_win_pct = if win_cnt > 0 { sum_pos / win_cnt as f64 } else { 0.0 };
    let avg_loss_pct = if loss_cnt > 0 { sum_neg / loss_cnt as f64 } else { 0.0 };
    let avg_pct = if total_cnt > 0 { sum_pct / total_cnt as f64 } else { 0.0 };

    let span_days = match (min_entry, max_exit) {
        (Some(a), Some(b)) => ((b - a) as f64 / (86400.0 * 1000.0)).round(),
        _ => 0.0,
    };
    let years = (span_days / 365.0).max(0.01);
    let opt_cagr = if years > 0.0 && final_cum > 0.0 {
        ((final_cum / 100.0).powf(1.0 / years) - 1.0).mul_add(100.0, 0.0).clamp(-99999.0, 99999.0)
    } else { -100.0 };
    let spot_cagr = match (init_spot, final_spot) {
        (Some(i), Some(f)) if years > 0.0 && i > 0.0 && f > 0.0 => 100.0 * ((f / i).powf(1.0 / years) - 1.0),
        _ => 0.0,
    };

    // max_dd: overall non-midcap → min(source %DD); patchwise → min over tm (cum/peak-1)*100
    let max_dd_pct = if !patchwise {
        src_dd.iter().cloned().filter(|v| v.is_finite()).fold(0.0f64, f64::min)
    } else {
        let mut m = 0.0f64;
        for k in &sorted_keys {
            let t = &tm[k];
            if t.peak != 0.0 { let ddp = (t.cumulative / t.peak - 1.0) * 100.0; if ddp < m { m = ddp; } }
        }
        m
    };
    let car_mdd = if max_dd_pct != 0.0 { (opt_cagr / 100.0) / max_dd_pct.abs() } else { 0.0 };

    let opt_sum = if ce_sum != 0.0 || pe_sum != 0.0 { ce_sum + pe_sum } else if fut_sum != 0.0 { fut_sum } else { sum_net };
    let _ = opt_sum;
    let scp = s_f64("spot_change_pct").filter(|&v| v != 0.0).unwrap_or(spot_pct_sum * 100.0);
    let roi_pct = if scp != 0.0 { sum_pct / scp.abs() } else { 0.0 };

    // ── outlier / Live-DD block ──
    struct Pair { pct: f64, ldd: Option<f64>, mae: Option<f64>, idx: usize, exit_reason: String, seg_idx: i64 }
    let mut pairs: Vec<Pair> = Vec::new();
    for k in &sorted_keys {
        let t = &tm[k];
        if t.pct.is_finite() {
            pairs.push(Pair {
                pct: t.pct, ldd: t.actual_ldd, mae: t.final_mae, idx: pairs.len(),
                exit_reason: tm[k].exit_reason.to_uppercase(),
                seg_idx: seg_idx(&t.main_entry),
            });
        }
    }
    let n = pairs.len();
    let mut by_desc: Vec<usize> = (0..n).collect();
    by_desc.sort_by(|&a, &b| pairs[b].pct.partial_cmp(&pairs[a].pct).unwrap_or(std::cmp::Ordering::Equal));
    let p_at = |i: usize| pairs[by_desc[i]].pct;
    let p1 = if n > 0 { p_at(0) } else { 0.0 };
    let p2 = if n > 1 { p1 + p_at(1) } else { p1 };
    let p3 = if n > 2 { p2 + p_at(2) } else { p2 };
    let n1 = if n > 0 { p_at(n - 1) } else { 0.0 };
    let n2 = if n > 1 { n1 + p_at(n - 2) } else { n1 };
    let n3 = if n > 2 { n2 + p_at(n - 3) } else { n2 };
    let total_pct_s: f64 = pairs.iter().map(|p| p.pct).sum();

    let ldd_exc = |exc_top: usize, exc_bot: usize| -> (f64, f64) {
        let mut exc = std::collections::HashSet::new();
        for i in 0..exc_top.min(n) { exc.insert(pairs[by_desc[i]].idx); }
        for i in n.saturating_sub(exc_bot)..n { exc.insert(pairs[by_desc[i]].idx); }
        let filtered: Vec<&Pair> = pairs.iter().filter(|p| !exc.contains(&p.idx)).collect();
        if filtered.is_empty() { return (0.0, 0.0); }
        let (mut cum, mut pk, mut prev_cum, mut prev_pk) = (100.0f64, 100.0f64, 100.0f64, 100.0f64);
        let mut prev_seg: Option<i64> = None;
        let mut prev_exit = String::new();
        let mut ldds: Vec<f64> = Vec::new();
        for p in &filtered {
            if patchwise {
                let reset = if !seg_starts.is_empty() {
                    prev_seg.map_or(false, |ps| p.seg_idx != ps)
                } else {
                    prev_exit.split('+').any(|s| s == "FILTER_END")
                };
                if reset { cum = 100.0; pk = 100.0; prev_cum = 100.0; prev_pk = 100.0; }
            }
            prev_seg = Some(p.seg_idx);
            prev_pk = pk;
            cum *= 1.0 + p.pct / 100.0;
            if cum > pk { pk = cum; }
            if let Some(mae) = p.mae {
                if prev_pk != 0.0 {
                    let lowest_nav = py_round(prev_cum * (1.0 + mae / 100.0), 2);
                    ldds.push(py_round((lowest_nav / prev_pk - 1.0) * 100.0, 2));
                }
            }
            prev_cum = cum;
            prev_exit = p.exit_reason.clone();
        }
        if ldds.is_empty() { return (0.0, 0.0); }
        let mn = ldds.iter().cloned().fold(f64::INFINITY, f64::min);
        let av = ldds.iter().sum::<f64>() / ldds.len() as f64;
        (py_round(mn, 4), py_round(av, 4))
    };

    let all_ldds: Vec<f64> = pairs.iter().filter_map(|p| p.ldd).collect();
    let live_dd_min = if all_ldds.is_empty() { 0.0 } else { py_round(all_ldds.iter().cloned().fold(f64::INFINITY, f64::min), 4) };
    let live_dd_avg = if all_ldds.is_empty() { 0.0 } else { py_round(all_ldds.iter().sum::<f64>() / all_ldds.len() as f64, 4) };
    let final_maes: Vec<f64> = pairs.iter().filter_map(|p| p.mae).collect();
    let avg_final_mae = if final_maes.is_empty() { 0.0 } else { py_round(final_maes.iter().sum::<f64>() / final_maes.len() as f64, 4) };
    let (o1m, o1a) = ldd_exc(1, 1);
    let (o2m, o2a) = ldd_exc(2, 2);
    let (o3m, o3a) = ldd_exc(3, 3);
    let car_mdd_live = if live_dd_min != 0.0 { (opt_cagr / 100.0) / live_dd_min.abs() } else { 0.0 };

    let spot_chg = s_f64("spot_change").unwrap_or(py_round(spot_sum_gated, 2));
    let spot_chg_pct = s_f64("spot_change_pct").unwrap_or(py_round(spot_pct_sum * 100.0, 4));

    // ── build dict ──
    let out = PyDict::new(py);
    out.set_item("cagr_options", py_round(opt_cagr, 2))?;
    out.set_item("cagr_spot", py_round(spot_cagr, 2))?;
    out.set_item("max_dd_pct", max_dd_pct)?;
    out.set_item("car_mdd", py_round(car_mdd, 4))?;
    out.set_item("roi_vs_spot", py_round(roi_pct, 4))?;
    out.set_item("avg_profit_per_trade_pct", py_round(avg_pct, 4))?;
    out.set_item("avg_win_pct", py_round(avg_win_pct, 4))?;
    out.set_item("avg_loss_pct", py_round(avg_loss_pct, 4))?;
    out.set_item("ce_pnl_total", py_round(ce_sum, 2))?;
    out.set_item("ce_pnl_pct", py_round(ce_pct_sum * 100.0, 4))?;
    out.set_item("pe_pnl_total", py_round(pe_sum, 2))?;
    out.set_item("pe_pnl_pct", py_round(pe_pct_sum * 100.0, 4))?;
    out.set_item("long_spot_pnl", spot_chg)?;
    out.set_item("long_spot_pnl_pct", spot_chg_pct)?;
    out.set_item("actual_live_dd_max", live_dd_min)?;
    out.set_item("actual_live_dd_avg", live_dd_avg)?;
    out.set_item("avg_final_mae", avg_final_mae)?;
    out.set_item("car_mdd_live", py_round(car_mdd_live, 4))?;
    out.set_item("positive_outlier_1", py_round(p1, 4))?;
    out.set_item("negative_outlier_1", py_round(n1, 4))?;
    out.set_item("positive_outlier_2", py_round(p2, 4))?;
    out.set_item("negative_outlier_2", py_round(n2, 4))?;
    out.set_item("positive_outlier_3", py_round(p3, 4))?;
    out.set_item("negative_outlier_3", py_round(n3, 4))?;
    out.set_item("outlier_dd_1", o1m)?;
    out.set_item("outlier_dd_1_avg", o1a)?;
    out.set_item("outlier_dd_2", o2m)?;
    out.set_item("outlier_dd_2_avg", o2a)?;
    out.set_item("outlier_dd_3", o3m)?;
    out.set_item("outlier_dd_3_avg", o3a)?;
    out.set_item("ce_pe_pnl_pct_without_top_1_outliers", py_round(total_pct_s - p1 - n1, 4))?;
    out.set_item("ce_pe_pnl_pct_without_top_2_outliers", py_round(total_pct_s - p2 - n2, 4))?;
    out.set_item("ce_pe_pnl_pct_without_top_3_outliers", py_round(total_pct_s - p3 - n3, 4))?;
    out.set_item("has_midcap", false)?;
    out.set_item("midcap_leg_pnl_sum", py.None())?;
    out.set_item("midcap_leg_pnl_pct_sum", py.None())?;
    out.set_item("combined_pnl_sum", py.None())?;
    out.set_item("combined_pnl_pct_sum", py.None())?;
    out.set_item("max_dd_pct_combined", py.None())?;
    Ok(out.into())
}
