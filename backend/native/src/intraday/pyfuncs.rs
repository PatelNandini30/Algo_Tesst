use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::intraday::engine::run_day;
use crate::intraday::snapshot::Snapshot;
use crate::intraday::types::StrategySpec;

/// Load expiries.json for a symbol → HashMap<expiry_idx, date_string>
fn load_expiry_map(symbol_dir: &Path) -> std::io::Result<HashMap<i16, String>> {
    let path = symbol_dir.join("expiries.json");
    let text = std::fs::read_to_string(&path)?;
    let raw: HashMap<String, String> = serde_json::from_str(&text)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let map = raw
        .into_iter()
        .filter_map(|(k, v)| k.parse::<i16>().ok().map(|idx| (idx, v)))
        .collect();
    Ok(map)
}

/// Run intraday backtest.
///
/// config_json: JSON string matching StrategySpec
/// data_dir:    path to /data/intraday (contains NIFTY/, BANKNIFTY/, etc.)
///
/// Returns: Python list of dicts, one per trade.
#[pyfunction]
pub fn run_intraday_backtest(
    py: Python,
    config_json: &str,
    data_dir: &str,
) -> PyResult<PyObject> {
    let spec: StrategySpec = serde_json::from_str(config_json).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("bad config JSON: {e}"))
    })?;

    let symbol_dir = PathBuf::from(data_dir).join(&spec.symbol);
    let snapshots_dir = symbol_dir.join("snapshots");

    let expiry_map = load_expiry_map(&symbol_dir).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("load expiries: {e}"))
    })?;

    let mut expiry_list: Vec<(i16, NaiveDate)> = expiry_map
        .iter()
        .filter_map(|(idx, date_str)| {
            NaiveDate::parse_from_str(date_str, "%Y-%m-%d").ok().map(|d| (*idx, d))
        })
        .collect();
    expiry_list.sort_by_key(|(_, d)| *d);

    let date_from = NaiveDate::parse_from_str(&spec.date_from, "%Y-%m-%d").map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("bad date_from: {e}"))
    })?;
    let date_to = NaiveDate::parse_from_str(&spec.date_to, "%Y-%m-%d").map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("bad date_to: {e}"))
    })?;

    let all_trades = PyList::empty(py);
    let mut current = date_from;

    while current <= date_to {
        let date_str = current.format("%Y-%m-%d").to_string();
        let snap_path = snapshots_dir.join(format!("{}.arrow", date_str));

        if snap_path.exists() {
            match Snapshot::open(&snap_path) {
                Ok(snap) => {
                    let records = run_day(&snap, &expiry_map, &expiry_list, &spec, &date_str);
                    for rec in records {
                        let row = PyDict::new(py);
                        row.set_item("date", &rec.date)?;
                        row.set_item("symbol", &rec.symbol)?;
                        row.set_item("expiry", &rec.expiry)?;
                        row.set_item("strike", rec.strike)?;
                        row.set_item("opt_type", &rec.opt_type)?;
                        row.set_item("action", &rec.action)?;
                        row.set_item("entry_time", &rec.entry_time)?;
                        row.set_item("entry_price", rec.entry_price)?;
                        row.set_item("exit_time", &rec.exit_time)?;
                        row.set_item("exit_price", rec.exit_price)?;
                        row.set_item("exit_reason", &rec.exit_reason)?;
                        row.set_item("quantity", rec.quantity)?;
                        row.set_item("pnl", rec.pnl)?;
                        row.set_item("mae", rec.mae)?;
                        row.set_item("mfe", rec.mfe)?;
                        all_trades.append(row)?;
                    }
                }
                Err(e) => {
                    eprintln!("[intraday] skip {date_str}: {e}");
                }
            }
        }

        current = current.succ_opt().unwrap_or(current);
    }

    Ok(all_trades.into())
}
