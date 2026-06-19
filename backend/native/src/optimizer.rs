//! Phase 2 — Rust optimization helpers.
//!
//! What lives here today
//! ---------------------
//! * `batch_compute_metrics` — given a list of compact tradesheets (one per
//!   combination), compute the master-summary numeric metrics in parallel via
//!   rayon. This bypasses Python+pandas overhead for the per-combo metrics
//!   step in `services.optimizer.metrics`.
//!
//! What does NOT live here yet (future work)
//! -----------------------------------------
//! * Full Rust trade simulation. Porting `generic_algotest_engine` is weeks
//!   of work — see Phase 2 design in `for-this-software-i-swirling-token.md`.
//!   When that lands, this module gains `run_optimization_batch(payload, combos)`.
//!
//! Input shape for `batch_compute_metrics`
//! ---------------------------------------
//! A Python list of tradesheets. Each tradesheet is a dict with these keys
//! (float arrays — same length per tradesheet):
//!   "net_pnl"      → Vec<f64>
//!   "net_pnl_pct"  → Vec<f64>     (Net P&L %)
//!   "call_pnl"     → Vec<f64>
//!   "put_pnl"      → Vec<f64>
//!   "spot_pnl"     → Vec<f64>
//!   "lowest_nav"   → Vec<f64>     (Lowest NAV During Trade)
//!   "peak"         → Vec<f64>     (running peak from compute_analytics)
//!   "entry_spot"   → Vec<f64>
//!   "exit_spot"    → Vec<f64>
//!
//! Plus a scalar `cagr_options` per tradesheet (already computed by Python).
//!
//! Output: one dict per tradesheet, with keys matching
//! `services.optimizer.metrics.compute_optim_metrics`.

use ahash::AHashMap;
use once_cell::sync::Lazy;
use rayon::prelude::*;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

fn optim_pool() -> &'static rayon::ThreadPool {
    static POOL: Lazy<rayon::ThreadPool> = Lazy::new(|| {
        let n = std::env::var("RUST_SIM_THREADS")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|&v| v > 0)
            .unwrap_or_else(|| (num_cpus::get()).min(4));
        rayon::ThreadPoolBuilder::new()
            .num_threads(n)
            .build()
            .expect("optim rayon pool init failed")
    });
    &POOL
}

#[derive(Debug, Default, Clone)]
struct TradeBatch {
    net_pnl: Vec<f64>,
    net_pnl_pct: Vec<f64>,
    call_pnl: Vec<f64>,
    put_pnl: Vec<f64>,
    spot_pnl: Vec<f64>,
    lowest_nav: Vec<f64>,
    peak: Vec<f64>,
    entry_spot: Vec<f64>,
    exit_spot: Vec<f64>,
    cagr_options: f64,
    spot_change: f64,
}

#[derive(Debug, Default, Clone)]
struct ComboMetrics {
    ce_pnl_total: f64,
    ce_pnl_pct: f64,
    pe_pnl_total: f64,
    pe_pnl_pct: f64,
    long_spot_pnl: f64,
    long_spot_pnl_pct: f64,
    roi_vs_spot: f64,
    actual_live_dd_max: f64,
    actual_live_dd_avg: f64,
    car_mdd_live: f64,
    positive_outlier_1: f64,
    negative_outlier_1: f64,
    outlier_dd_1: f64,
    outlier_dd_1_avg: f64,
    positive_outlier_2: f64,
    negative_outlier_2: f64,
    outlier_dd_2: f64,
    outlier_dd_2_avg: f64,
    positive_outlier_3: f64,
    negative_outlier_3: f64,
    outlier_dd_3: f64,
    outlier_dd_3_avg: f64,
    ce_pe_pnl_pct_without_top_1_outliers: f64,
    ce_pe_pnl_pct_without_top_2_outliers: f64,
    ce_pe_pnl_pct_without_top_3_outliers: f64,
    ce_pnl_pct_no_outlier_1: f64,
    ce_pnl_pct_no_outlier_2: f64,
    ce_pnl_pct_no_outlier_3: f64,
    pe_pnl_pct_no_outlier_1: f64,
    pe_pnl_pct_no_outlier_2: f64,
    pe_pnl_pct_no_outlier_3: f64,
}

fn round4(v: f64) -> f64 {
    (v * 10000.0).round() / 10000.0
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn sum(slice: &[f64]) -> f64 {
    slice.iter().copied().sum::<f64>()
}

fn mean(slice: &[f64]) -> f64 {
    if slice.is_empty() {
        0.0
    } else {
        sum(slice) / slice.len() as f64
    }
}

fn first_nonzero(slice: &[f64]) -> Option<f64> {
    slice.iter().copied().find(|v| !v.is_nan() && *v != 0.0)
}

fn outlier_block(pct: &[f64], live_dd: &[f64], n: usize) -> (f64, f64, f64, f64, f64) {
    if pct.is_empty() || n == 0 {
        return (0.0, 0.0, 0.0, 0.0, 0.0);
    }
    let mut indexed: Vec<(usize, f64)> = pct
        .iter()
        .copied()
        .enumerate()
        .collect();
    indexed.sort_unstable_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    let pos_sum: f64 = indexed.iter().take(n).map(|(_, v)| *v).sum();
    let start = indexed.len().saturating_sub(n);
    let neg_sum: f64 = indexed.iter().skip(start).map(|(_, v)| *v).sum();
    let total_sum: f64 = pct.iter().copied().sum();

    let drop_set: AHashMap<usize, ()> = indexed
        .iter()
        .take(n)
        .chain(indexed.iter().skip(start))
        .map(|(i, _)| (*i, ()))
        .collect();

    let mut kept: Vec<f64> = Vec::with_capacity(live_dd.len());
    for (i, v) in live_dd.iter().copied().enumerate() {
        if drop_set.contains_key(&i) || !v.is_finite() {
            continue;
        }
        kept.push(v);
    }
    let dd_min = if kept.is_empty() {
        0.0
    } else {
        kept.iter().copied().fold(f64::INFINITY, f64::min)
    };
    let dd_avg = mean(&kept);
    (
        round4(pos_sum),
        round4(neg_sum),
        round4(dd_min),
        round4(dd_avg),
        round4(total_sum - pos_sum - neg_sum),
    )
}

/// Top-N largest leg P&L values are stripped from the sum.
fn leg_pct_no_outliers(leg: &[f64], denom: f64, n: usize) -> f64 {
    if denom <= 0.0 || leg.is_empty() {
        return 0.0;
    }
    let mut sorted = leg.to_vec();
    sorted.sort_unstable_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    if sorted.len() <= n {
        return 0.0;
    }
    let kept: f64 = sorted.iter().skip(n).copied().sum();
    round4(kept / denom * 100.0)
}

fn compute_metrics_for_batch(batch: &TradeBatch) -> ComboMetrics {
    let denom = first_nonzero(&batch.entry_spot).unwrap_or(0.0);

    let ce_total = sum(&batch.call_pnl);
    let pe_total = sum(&batch.put_pnl);
    let spot_total = sum(&batch.spot_pnl);

    let to_pct = |v: f64| if denom > 0.0 { round4(v / denom * 100.0) } else { 0.0 };

    // Research-verified Live DD: each trade's intra-trade low is measured against
    // the PREVIOUS trade's peak (AX = AW - AV_prev), not its own peak — a trade
    // that closes at a new high is still measured against the peak it went into.
    // prev_peak is seeded at 100 for the first trade. Non-parent rows carry
    // lowest_nav = 0 and peak = 0 in the payload (only parent/first-leg rows hold
    // the per-trade values), so they contribute 0 — mirroring the Python
    // parent-row mask in `metrics.actual_live_dd`.
    let mut prev_peak = 100.0_f64;
    let mut live_pairs: Vec<f64> = Vec::with_capacity(batch.lowest_nav.len());
    for (&low, &pk) in batch.lowest_nav.iter().zip(batch.peak.iter()) {
        if pk == 0.0 {
            live_pairs.push(0.0);
        } else {
            live_pairs.push(low - prev_peak);
            prev_peak = pk;
        }
    }
    let live_max = live_pairs
        .iter()
        .copied()
        .fold(f64::INFINITY, f64::min)
        .min(0.0);
    let live_avg = mean(&live_pairs);

    let (p1, n1, d1, d1a, c1) = outlier_block(&batch.net_pnl_pct, &live_pairs, 1);
    let (p2, n2, d2, d2a, c2) = outlier_block(&batch.net_pnl_pct, &live_pairs, 2);
    let (p3, n3, d3, d3a, c3) = outlier_block(&batch.net_pnl_pct, &live_pairs, 3);

    let roi_vs_spot = if batch.spot_change != 0.0 {
        round4(sum(&batch.net_pnl) / batch.spot_change)
    } else {
        0.0
    };
    let car_mdd_live = if live_max != 0.0 {
        round4(batch.cagr_options / live_max.abs())
    } else {
        0.0
    };

    ComboMetrics {
        ce_pnl_total: round2(ce_total),
        ce_pnl_pct: to_pct(ce_total),
        pe_pnl_total: round2(pe_total),
        pe_pnl_pct: to_pct(pe_total),
        long_spot_pnl: round2(spot_total),
        long_spot_pnl_pct: to_pct(spot_total),
        roi_vs_spot,
        actual_live_dd_max: round4(if live_max.is_finite() { live_max } else { 0.0 }),
        actual_live_dd_avg: round4(live_avg),
        car_mdd_live,
        positive_outlier_1: p1,
        negative_outlier_1: n1,
        outlier_dd_1: d1,
        outlier_dd_1_avg: d1a,
        positive_outlier_2: p2,
        negative_outlier_2: n2,
        outlier_dd_2: d2,
        outlier_dd_2_avg: d2a,
        positive_outlier_3: p3,
        negative_outlier_3: n3,
        outlier_dd_3: d3,
        outlier_dd_3_avg: d3a,
        ce_pe_pnl_pct_without_top_1_outliers: c1,
        ce_pe_pnl_pct_without_top_2_outliers: c2,
        ce_pe_pnl_pct_without_top_3_outliers: c3,
        ce_pnl_pct_no_outlier_1: leg_pct_no_outliers(&batch.call_pnl, denom, 1),
        ce_pnl_pct_no_outlier_2: leg_pct_no_outliers(&batch.call_pnl, denom, 2),
        ce_pnl_pct_no_outlier_3: leg_pct_no_outliers(&batch.call_pnl, denom, 3),
        pe_pnl_pct_no_outlier_1: leg_pct_no_outliers(&batch.put_pnl, denom, 1),
        pe_pnl_pct_no_outlier_2: leg_pct_no_outliers(&batch.put_pnl, denom, 2),
        pe_pnl_pct_no_outlier_3: leg_pct_no_outliers(&batch.put_pnl, denom, 3),
    }
}

fn extract_vec(dict: &PyDict, key: &str) -> Vec<f64> {
    if let Ok(Some(item)) = dict.get_item(key) {
        if let Ok(list) = item.extract::<Vec<f64>>() {
            return list;
        }
    }
    Vec::new()
}

fn extract_scalar(dict: &PyDict, key: &str) -> f64 {
    if let Ok(Some(item)) = dict.get_item(key) {
        if let Ok(v) = item.extract::<f64>() {
            return v;
        }
    }
    0.0
}

fn dict_to_batch(dict: &PyDict) -> TradeBatch {
    TradeBatch {
        net_pnl: extract_vec(dict, "net_pnl"),
        net_pnl_pct: extract_vec(dict, "net_pnl_pct"),
        call_pnl: extract_vec(dict, "call_pnl"),
        put_pnl: extract_vec(dict, "put_pnl"),
        spot_pnl: extract_vec(dict, "spot_pnl"),
        lowest_nav: extract_vec(dict, "lowest_nav"),
        peak: extract_vec(dict, "peak"),
        entry_spot: extract_vec(dict, "entry_spot"),
        exit_spot: extract_vec(dict, "exit_spot"),
        cagr_options: extract_scalar(dict, "cagr_options"),
        spot_change: extract_scalar(dict, "spot_change"),
    }
}

fn metrics_to_dict(py: Python<'_>, m: &ComboMetrics) -> PyResult<PyObject> {
    let d = PyDict::new(py);
    d.set_item("ce_pnl_total", m.ce_pnl_total)?;
    d.set_item("ce_pnl_pct", m.ce_pnl_pct)?;
    d.set_item("pe_pnl_total", m.pe_pnl_total)?;
    d.set_item("pe_pnl_pct", m.pe_pnl_pct)?;
    d.set_item("long_spot_pnl", m.long_spot_pnl)?;
    d.set_item("long_spot_pnl_pct", m.long_spot_pnl_pct)?;
    d.set_item("roi_vs_spot", m.roi_vs_spot)?;
    d.set_item("actual_live_dd_max", m.actual_live_dd_max)?;
    d.set_item("actual_live_dd_avg", m.actual_live_dd_avg)?;
    d.set_item("car_mdd_live", m.car_mdd_live)?;
    d.set_item("positive_outlier_1", m.positive_outlier_1)?;
    d.set_item("negative_outlier_1", m.negative_outlier_1)?;
    d.set_item("outlier_dd_1", m.outlier_dd_1)?;
    d.set_item("outlier_dd_1_avg", m.outlier_dd_1_avg)?;
    d.set_item("positive_outlier_2", m.positive_outlier_2)?;
    d.set_item("negative_outlier_2", m.negative_outlier_2)?;
    d.set_item("outlier_dd_2", m.outlier_dd_2)?;
    d.set_item("outlier_dd_2_avg", m.outlier_dd_2_avg)?;
    d.set_item("positive_outlier_3", m.positive_outlier_3)?;
    d.set_item("negative_outlier_3", m.negative_outlier_3)?;
    d.set_item("outlier_dd_3", m.outlier_dd_3)?;
    d.set_item("outlier_dd_3_avg", m.outlier_dd_3_avg)?;
    d.set_item(
        "ce_pe_pnl_pct_without_top_1_outliers",
        m.ce_pe_pnl_pct_without_top_1_outliers,
    )?;
    d.set_item(
        "ce_pe_pnl_pct_without_top_2_outliers",
        m.ce_pe_pnl_pct_without_top_2_outliers,
    )?;
    d.set_item(
        "ce_pe_pnl_pct_without_top_3_outliers",
        m.ce_pe_pnl_pct_without_top_3_outliers,
    )?;
    d.set_item("ce_pnl_pct_no_outlier_1", m.ce_pnl_pct_no_outlier_1)?;
    d.set_item("ce_pnl_pct_no_outlier_2", m.ce_pnl_pct_no_outlier_2)?;
    d.set_item("ce_pnl_pct_no_outlier_3", m.ce_pnl_pct_no_outlier_3)?;
    d.set_item("pe_pnl_pct_no_outlier_1", m.pe_pnl_pct_no_outlier_1)?;
    d.set_item("pe_pnl_pct_no_outlier_2", m.pe_pnl_pct_no_outlier_2)?;
    d.set_item("pe_pnl_pct_no_outlier_3", m.pe_pnl_pct_no_outlier_3)?;
    Ok(d.into())
}

/// Compute the master-summary metrics for many tradesheets in parallel.
///
/// Python signature:
///   batch_compute_metrics(tradesheets: List[Dict[str, Any]]) -> List[Dict[str, float]]
#[pyfunction]
pub fn batch_compute_metrics(tradesheets: &PyList) -> PyResult<PyObject> {
    let py = tradesheets.py();
    let mut batches: Vec<TradeBatch> = Vec::with_capacity(tradesheets.len());
    for obj in tradesheets.iter() {
        let dict = obj.downcast::<PyDict>()?;
        batches.push(dict_to_batch(dict));
    }
    // Release the GIL while rayon processes batches via the process-local pool.
    let results: Vec<ComboMetrics> = py.allow_threads(|| {
        optim_pool().install(|| batches.par_iter().map(compute_metrics_for_batch).collect())
    });

    let out = PyList::empty(py);
    for r in &results {
        out.append(metrics_to_dict(py, r)?)?;
    }
    Ok(out.into())
}

/// Stub for the future Rust trade-simulation entry point.
/// Currently raises NotImplementedError — engine port is Phase 2b.
#[pyfunction]
pub fn run_optimization_batch(_payload: &PyAny, _combos: &PyList) -> PyResult<PyObject> {
    Err(pyo3::exceptions::PyNotImplementedError::new_err(
        "run_optimization_batch (Rust trade simulation) is scaffolded but not \
         yet implemented. Use the Python runner with OPTIMIZE_PARALLELISM \
         for now.",
    ))
}
