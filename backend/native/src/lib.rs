mod intraday;

use std::collections::HashMap;
use std::fs::File;
use std::io::Cursor;
use std::path::Path;
use std::sync::RwLock;

use arrow_array::{
    Array, ArrayRef, Date32Array, Date64Array, Float32Array, Float64Array, Int32Array, Int64Array,
    LargeStringArray, StringArray, TimestampMillisecondArray, TimestampMicrosecondArray,
};
use arrow_ipc::reader::FileReader;
use arrow_schema::DataType;
use chrono::{Duration, NaiveDate};
use memmap2::Mmap;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyList};
use pyo3::wrap_pyfunction;

#[derive(Default)]
struct MarketCache {
    options: HashMap<(String, String, i64, String, String), f64>,
    spot: HashMap<(String, String), f64>,
    strikes: HashMap<(String, String, String, String), Vec<(f64, f64)>>,
}

static CACHE: Lazy<RwLock<Option<MarketCache>>> = Lazy::new(|| RwLock::new(None));

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn normalize_date_str(value: &str) -> String {
    let s = value.trim();
    if s.len() >= 10 {
        s[..10].to_string()
    } else {
        s.to_string()
    }
}

fn normalize_mode(mode_str: Option<&str>) -> String {
    let m = mode_str.unwrap_or("pct").to_uppercase().replace(' ', "_").replace('-', "_");
    if matches!(
        m.as_str(),
        "PERCENT"
            | "PCT"
            | "%"
            | "PER"
            | "PERCENTAGE"
            | "PREMIUM_PCT"
            | "PREMIUM_PERCENT"
            | "PREMIUM_%"
    ) {
        return "pct".to_string();
    }
    if matches!(
        m.as_str(),
        "POINTS" | "PTS" | "POINT" | "PT" | "POINTS_PTS" | "PREMIUM_POINTS" | "PREMIUM_PTS"
            | "PREMIUM_PT" | "ABS" | "ABSOLUTE"
    ) {
        return "points".to_string();
    }
    if matches!(
        m.as_str(),
        "UNDERLYING_POINTS"
            | "UNDERLYING_PTS"
            | "UNDERLYING_PT"
            | "UNDERLYINGPOINTS"
            | "UNDERLYINGPTS"
            | "UNDERLYING_POINT"
            | "INDEX_POINTS"
            | "INDEX_PTS"
            | "SPOT_POINTS"
            | "SPOT_PTS"
    ) {
        return "underlying_pts".to_string();
    }
    if matches!(
        m.as_str(),
        "UNDERLYING_PERCENT"
            | "UNDERLYING_PCT"
            | "UNDERLYING_%"
            | "UNDERLYINGPERCENT"
            | "UNDERLYINGPCT"
            | "UNDERLYING_PERCENTAGE"
            | "INDEX_PCT"
            | "INDEX_PERCENT"
            | "SPOT_PCT"
            | "SPOT_PERCENT"
    ) {
        return "underlying_pct".to_string();
    }
    "pct".to_string()
}

fn normalize_slippage_pct(value: f64) -> f64 {
    if value.is_nan() || value < 0.0 {
        return 0.0;
    }
    if value > 100.0 {
        return 100.0;
    }
    value
}

fn apply_slippage(price: f64, position: &str, side: &str, slippage_pct: f64) -> f64 {
    let pct = normalize_slippage_pct(slippage_pct);
    if pct <= 0.0 {
        return round2(price);
    }

    let is_sell = position.trim().to_uppercase() == "SELL";
    let side_key = side.trim().to_lowercase();
    let factor = if side_key == "entry" {
        if is_sell {
            1.0 - (pct / 100.0)
        } else {
            1.0 + (pct / 100.0)
        }
    } else if is_sell {
        1.0 + (pct / 100.0)
    } else {
        1.0 - (pct / 100.0)
    };

    round2((price * factor).max(0.0))
}

fn to_iso_date_from_array(array: &ArrayRef, row: usize) -> Option<String> {
    if array.is_null(row) {
        return None;
    }
    match array.data_type() {
        DataType::Utf8 => array
            .as_any()
            .downcast_ref::<StringArray>()
            .map(|arr| normalize_date_str(arr.value(row))),
        DataType::LargeUtf8 => array
            .as_any()
            .downcast_ref::<LargeStringArray>()
            .map(|arr| normalize_date_str(arr.value(row))),
        DataType::Date32 => array.as_any().downcast_ref::<Date32Array>().and_then(|arr| {
            let days = arr.value(row) as i64;
            let base = NaiveDate::from_ymd_opt(1970, 1, 1)?;
            Some((base + Duration::days(days)).format("%Y-%m-%d").to_string())
        }),
        DataType::Date64 => array.as_any().downcast_ref::<Date64Array>().and_then(|arr| {
            let ms = arr.value(row);
            let base = NaiveDate::from_ymd_opt(1970, 1, 1)?;
            let dt = base.and_hms_opt(0, 0, 0)? + chrono::Duration::milliseconds(ms);
            Some(dt.date().format("%Y-%m-%d").to_string())
        }),
        DataType::Timestamp(_, _) => array
            .as_any()
            .downcast_ref::<TimestampMillisecondArray>()
            .map(|arr| {
                let ms = arr.value(row);
                let base = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap().and_hms_opt(0, 0, 0).unwrap();
                (base + chrono::Duration::milliseconds(ms))
                    .date()
                    .format("%Y-%m-%d")
                    .to_string()
            })
            .or_else(|| {
                array.as_any().downcast_ref::<TimestampMicrosecondArray>().map(|arr| {
                    let us = arr.value(row);
                    let base = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap().and_hms_opt(0, 0, 0).unwrap();
                    (base + chrono::Duration::microseconds(us))
                        .date()
                        .format("%Y-%m-%d")
                        .to_string()
                })
            }),
        _ => None,
    }
}

fn to_f64_from_array(array: &ArrayRef, row: usize) -> Option<f64> {
    if array.is_null(row) {
        return None;
    }
    match array.data_type() {
        DataType::Float64 => array
            .as_any()
            .downcast_ref::<Float64Array>()
            .map(|arr| arr.value(row)),
        DataType::Float32 => array
            .as_any()
            .downcast_ref::<Float32Array>()
            .map(|arr| arr.value(row) as f64),
        DataType::Int64 => array
            .as_any()
            .downcast_ref::<Int64Array>()
            .map(|arr| arr.value(row) as f64),
        DataType::Int32 => array
            .as_any()
            .downcast_ref::<Int32Array>()
            .map(|arr| arr.value(row) as f64),
        _ => None,
    }
}

fn to_i64_strike(strike: f64) -> i64 {
    (strike * 100.0).round() as i64
}

fn lookup_option_price(date: &str, index: &str, strike: f64, opt_type: &str, expiry: &str) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    cache
        .options
        .get(&(
            normalize_date_str(date),
            index.trim().to_uppercase(),
            to_i64_strike(strike),
            opt_type.trim().to_uppercase(),
            normalize_date_str(expiry),
        ))
        .copied()
}

fn lookup_spot_price(date: &str, index: &str) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    cache
        .spot
        .get(&(normalize_date_str(date), index.trim().to_uppercase()))
        .copied()
}

fn load_table_from_path(path: &str) -> PyResult<Vec<arrow_array::RecordBatch>> {
    let file = File::open(Path::new(path)).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("open {} failed: {}", path, e))
    })?;
    let mmap = unsafe { Mmap::map(&file) }.map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("mmap {} failed: {}", path, e))
    })?;
    let cursor = Cursor::new(&mmap[..]);
    let reader = FileReader::try_new(cursor, None)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("arrow read {} failed: {}", path, e)))?;

    let mut batches = Vec::new();
    for batch in reader {
        batches.push(
            batch.map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("arrow batch {} failed: {}", path, e))
            })?,
        );
    }
    Ok(batches)
}

fn build_cache_from_batches(options_batches: Vec<arrow_array::RecordBatch>, spot_batches: Vec<arrow_array::RecordBatch>) -> MarketCache {
    let mut cache = MarketCache::default();

    for batch in options_batches {
        let schema = batch.schema();
        let idx_date = schema.index_of("Date").ok();
        let idx_symbol = schema.index_of("Symbol").ok();
        let idx_expiry = schema.index_of("ExpiryDate").ok();
        let idx_type = schema.index_of("OptionType").ok();
        let idx_strike = schema.index_of("StrikePrice").ok();
        let idx_close = schema.index_of("Close").ok();
        let (Some(idx_date), Some(idx_symbol), Some(idx_expiry), Some(idx_type), Some(idx_strike), Some(idx_close)) =
            (idx_date, idx_symbol, idx_expiry, idx_type, idx_strike, idx_close)
        else {
            continue;
        };

        let date_col = batch.column(idx_date).clone();
        let symbol_col = batch.column(idx_symbol).clone();
        let expiry_col = batch.column(idx_expiry).clone();
        let type_col = batch.column(idx_type).clone();
        let strike_col = batch.column(idx_strike).clone();
        let close_col = batch.column(idx_close).clone();

        for row in 0..batch.num_rows() {
            let date_s = match to_iso_date_from_array(&date_col, row) {
                Some(v) => v,
                None => continue,
            };
            let expiry_s = match to_iso_date_from_array(&expiry_col, row) {
                Some(v) => v,
                None => continue,
            };
            let symbol_s = if symbol_col.is_null(row) {
                continue;
            } else {
                match symbol_col.data_type() {
                    DataType::Utf8 => symbol_col
                        .as_any()
                        .downcast_ref::<StringArray>()
                        .map(|arr| arr.value(row).trim().to_uppercase()),
                    DataType::LargeUtf8 => symbol_col
                        .as_any()
                        .downcast_ref::<LargeStringArray>()
                        .map(|arr| arr.value(row).trim().to_uppercase()),
                    _ => None,
                }
            };
            let symbol_s = match symbol_s {
                Some(v) if !v.is_empty() => v,
                _ => continue,
            };
            let opt_type_s = if type_col.is_null(row) {
                continue;
            } else {
                match type_col.data_type() {
                    DataType::Utf8 => type_col
                        .as_any()
                        .downcast_ref::<StringArray>()
                        .map(|arr| arr.value(row).trim().to_uppercase()),
                    DataType::LargeUtf8 => type_col
                        .as_any()
                        .downcast_ref::<LargeStringArray>()
                        .map(|arr| arr.value(row).trim().to_uppercase()),
                    _ => None,
                }
            };
            let opt_type_s = match opt_type_s {
                Some(v) if !v.is_empty() => v,
                _ => continue,
            };
            let strike_v = match to_f64_from_array(&strike_col, row) {
                Some(v) => v,
                None => continue,
            };
            let close_v = match to_f64_from_array(&close_col, row) {
                Some(v) => v,
                None => continue,
            };
            let strike_key = to_i64_strike(strike_v);
            cache.options.insert(
                (date_s.clone(), symbol_s.clone(), strike_key, opt_type_s.clone(), expiry_s.clone()),
                close_v,
            );
            cache
                .strikes
                .entry((date_s, symbol_s, expiry_s, opt_type_s))
                .or_default()
                .push((strike_v, close_v));
        }
    }

    for values in cache.strikes.values_mut() {
        values.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    }

    for batch in spot_batches {
        let schema = batch.schema();
        let idx_date = schema.index_of("Date").ok();
        let idx_close = schema.index_of("Close").ok();
        let (Some(idx_date), Some(idx_close)) = (idx_date, idx_close) else {
            continue;
        };
        let idx_symbol = schema.index_of("Symbol").ok();
        let date_col = batch.column(idx_date).clone();
        let close_col = batch.column(idx_close).clone();
        let symbol_col = idx_symbol.map(|i| batch.column(i).clone());
        let has_symbol = symbol_col.is_some();

        for row in 0..batch.num_rows() {
            let date_s = match to_iso_date_from_array(&date_col, row) {
                Some(v) => v,
                None => continue,
            };
            let symbol_s = if has_symbol {
                let col = symbol_col.as_ref().unwrap();
                if col.is_null(row) {
                    continue;
                }
                match col.data_type() {
                    DataType::Utf8 => col
                        .as_any()
                        .downcast_ref::<StringArray>()
                        .map(|arr| arr.value(row).trim().to_uppercase()),
                    DataType::LargeUtf8 => col
                        .as_any()
                        .downcast_ref::<LargeStringArray>()
                        .map(|arr| arr.value(row).trim().to_uppercase()),
                    _ => None,
                }
            } else {
                Some(String::new())
            };
            let symbol_s = match symbol_s {
                Some(v) => v,
                None => continue,
            };
            let close_v = match to_f64_from_array(&close_col, row) {
                Some(v) => v,
                None => continue,
            };
            cache.spot.insert((date_s, symbol_s), close_v);
        }
    }

    cache
}

#[pyfunction]
fn load_cache(options_path: String, spot_path: String) -> PyResult<()> {
    let options_batches = load_table_from_path(&options_path)?;
    let spot_batches = load_table_from_path(&spot_path)?;
    let cache = build_cache_from_batches(options_batches, spot_batches);
    let mut guard = CACHE.write().unwrap();
    *guard = Some(cache);
    Ok(())
}

#[pyfunction]
fn clear_cache() {
    if let Ok(mut guard) = CACHE.write() {
        *guard = None;
    }
}

#[pyfunction]
fn is_loaded() -> bool {
    CACHE.read().ok().and_then(|g| g.as_ref().map(|_| true)).unwrap_or(false)
}

#[pyfunction]
fn get_option_price(date: String, index: String, strike: f64, opt_type: String, expiry: String) -> Option<f64> {
    lookup_option_price(&date, &index, strike, &opt_type, &expiry)
}

#[pyfunction]
fn get_spot_price(date: String, index: String) -> Option<f64> {
    lookup_spot_price(&date, &index)
}

#[pyfunction]
fn get_strikes_for_date(
    date: String,
    index: String,
    expiry: String,
    opt_type: String,
) -> Vec<(f64, f64)> {
    let cache = match CACHE.read() {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    let cache = match cache.as_ref() {
        Some(v) => v,
        None => return Vec::new(),
    };
    cache
        .strikes
        .get(&(
            normalize_date_str(&date),
            index.trim().to_uppercase(),
            normalize_date_str(&expiry),
            opt_type.trim().to_uppercase(),
        ))
        .cloned()
        .unwrap_or_default()
}

fn py_any_to_string_opt(obj: Option<&PyAny>) -> Option<String> {
    obj.and_then(|v| v.extract::<String>().ok())
}

fn py_any_to_f64_opt(obj: Option<&PyAny>) -> Option<f64> {
    obj.and_then(|v| v.extract::<f64>().ok())
}

fn py_any_to_bool_opt(obj: Option<&PyAny>) -> bool {
    obj.and_then(|v| v.extract::<bool>().ok()).unwrap_or(false)
}

fn extract_leg_value<'a>(dict: &'a PyDict, key: &str) -> Option<&'a PyAny> {
    dict.get_item(key).ok().flatten()
}

fn build_result(py: Python<'_>, triggered: bool, exit_date: &str, exit_reason: &str) -> PyResult<PyObject> {
    let d = PyDict::new(py);
    d.set_item("triggered", triggered)?;
    d.set_item("exit_date", exit_date)?;
    d.set_item("exit_reason", exit_reason)?;
    Ok(d.into())
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn check_leg_stop_loss_target(
    py: Python<'_>,
    entry_date: String,
    exit_date: String,
    expiry_date: String,
    entry_spot: f64,
    legs_config: &PyAny,
    index: String,
    trading_calendar: Vec<String>,
    square_off_mode: String,
    slippage_pct: f64,
) -> PyResult<PyObject> {
    let legs_list: &PyList = legs_config.downcast()?;
    let exit_date = normalize_date_str(&exit_date);
    let entry_date = normalize_date_str(&entry_date);
    let expiry_date = normalize_date_str(&expiry_date);
    let holding_days: Vec<String> = trading_calendar
        .into_iter()
        .filter(|d| d > &entry_date && d <= &exit_date)
        .collect();

    let mut leg_results: Vec<(bool, String, String)> = legs_list
        .iter()
        .map(|_| (false, exit_date.clone(), "EXPIRY".to_string()))
        .collect();

    #[derive(Clone)]
    struct TrailState {
        x_pts: f64,
        y_pts: f64,
        best_prem: f64,
        current_sl_level: f64,
        triggers_fired: i32,
        entry_prem: f64,
    }

    let mut tsl_state: HashMap<usize, TrailState> = HashMap::new();
    for (li, leg_any) in legs_list.iter().enumerate() {
        let dict: &PyDict = match leg_any.downcast() {
            Ok(v) => v,
            Err(_) => continue,
        };
        if !py_any_to_bool_opt(extract_leg_value(dict, "trail_sl_enabled")) {
            continue;
        }
        let segment = py_any_to_string_opt(extract_leg_value(dict, "segment")).unwrap_or_else(|| "OPTION".to_string()).to_uppercase();
        let entry_prem = if matches!(segment.as_str(), "FUTURES" | "FUTURE") {
            py_any_to_f64_opt(extract_leg_value(dict, "entry_price"))
        } else {
            py_any_to_f64_opt(extract_leg_value(dict, "entry_premium"))
        };
        let Some(entry_prem) = entry_prem else { continue };
        let position = py_any_to_string_opt(extract_leg_value(dict, "position")).unwrap_or_else(|| "SELL".to_string()).to_uppercase();
        let tsl_mode = py_any_to_string_opt(extract_leg_value(dict, "trail_sl_mode")).unwrap_or_else(|| "points".to_string()).to_lowercase();
        let x_raw = py_any_to_f64_opt(extract_leg_value(dict, "trail_sl_trigger"));
        let y_raw = py_any_to_f64_opt(extract_leg_value(dict, "trail_sl_move"));
        let (Some(x_raw), Some(y_raw)) = (x_raw, y_raw) else { continue };
        if x_raw <= 0.0 || y_raw <= 0.0 {
            continue;
        }
        let (x_pts, y_pts) = if tsl_mode == "pct" {
            let base = entry_prem.abs();
            if base <= 0.0 {
                continue;
            }
            (base * (x_raw / 100.0), base * (y_raw / 100.0))
        } else {
            (x_raw, y_raw)
        };
        if x_pts <= 0.0 || y_pts <= 0.0 {
            continue;
        }
        let sl_val = py_any_to_f64_opt(extract_leg_value(dict, "stop_loss"));
        let sl_type = normalize_mode(py_any_to_string_opt(extract_leg_value(dict, "stop_loss_type")).as_deref());
        let mut sl_pts = None;
        if let Some(sl_val) = sl_val {
            let sl_abs = sl_val.abs();
            if sl_type == "pct" {
                let base = entry_prem.abs();
                if base > 0.0 {
                    sl_pts = Some(base * (sl_abs / 100.0));
                }
            } else if sl_type == "points" {
                sl_pts = Some(sl_abs);
            }
        }
        let sl_pts = sl_pts.unwrap_or(x_pts);
        let current_sl_level = if position == "SELL" {
            entry_prem + sl_pts
        } else {
            entry_prem - sl_pts
        };
        tsl_state.insert(
            li,
            TrailState {
                x_pts,
                y_pts,
                best_prem: entry_prem,
                current_sl_level,
                triggers_fired: 0,
                entry_prem,
            },
        );
    }

    for check_date in holding_days.iter() {
        if leg_results.iter().all(|r| r.0) {
            break;
        }
        let mut newly_triggered_this_day: Vec<(usize, String, String)> = Vec::new();

        for (li, leg_any) in legs_list.iter().enumerate() {
            if leg_results[li].0 {
                continue;
            }
            let dict: &PyDict = match leg_any.downcast() {
                Ok(v) => v,
                Err(_) => continue,
            };
            let sl_val = py_any_to_f64_opt(extract_leg_value(dict, "stop_loss"));
            let sl_type = normalize_mode(py_any_to_string_opt(extract_leg_value(dict, "stop_loss_type")).as_deref());
            let tgt_val = py_any_to_f64_opt(extract_leg_value(dict, "target"));
            let tgt_type = normalize_mode(py_any_to_string_opt(extract_leg_value(dict, "target_type")).as_deref());
            if sl_val.is_none() && tgt_val.is_none() && !py_any_to_bool_opt(extract_leg_value(dict, "trail_sl_enabled")) {
                continue;
            }
            let position = py_any_to_string_opt(extract_leg_value(dict, "position")).unwrap_or_else(|| "SELL".to_string()).to_uppercase();
            let lot_size = py_any_to_f64_opt(extract_leg_value(dict, "lot_size")).unwrap_or(1.0);
            let lots = py_any_to_f64_opt(extract_leg_value(dict, "lots")).unwrap_or(1.0);
            let segment = py_any_to_string_opt(extract_leg_value(dict, "segment")).unwrap_or_else(|| "OPTION".to_string()).to_uppercase();
            let option_type = py_any_to_string_opt(extract_leg_value(dict, "option_type")).unwrap_or_else(|| "CE".to_string());
            let mut cp = None;
            let mut adverse_premium_pts = 0.0;
            let mut favorable_premium_pts = 0.0;
            let mut adverse_pct = 0.0;
            let mut favorable_pct = 0.0;
            let mut adverse_spot_pts = 0.0;
            let mut adverse_spot_pct = 0.0;

            if matches!(segment.as_str(), "FUTURE" | "FUTURES") {
                continue;
            } else {
                let strike = py_any_to_f64_opt(extract_leg_value(dict, "strike"));
                let Some(strike) = strike else { continue };
                let expiry = py_any_to_string_opt(extract_leg_value(dict, "_resolved_expiry"))
                    .or_else(|| Some(expiry_date.clone()))
                    .unwrap_or_else(|| expiry_date.clone());
                let current_premium_raw = lookup_option_price(check_date, &index, strike, &option_type, &expiry);
                let Some(current_premium_raw) = current_premium_raw else { continue };
                let current_premium = apply_slippage(current_premium_raw, &position, "exit", slippage_pct);
                cp = Some(current_premium);
                let entry_premium = py_any_to_f64_opt(extract_leg_value(dict, "entry_premium"));
                let Some(entry_premium) = entry_premium else { continue };
                let premium_move = current_premium - entry_premium;
                adverse_premium_pts = if position == "SELL" { premium_move } else { -premium_move };
                favorable_premium_pts = -adverse_premium_pts;
                adverse_pct = if entry_premium != 0.0 {
                    adverse_premium_pts / entry_premium * 100.0
                } else {
                    0.0
                };
                favorable_pct = -adverse_pct;
            }

            if sl_type == "underlying_pts" || sl_type == "underlying_pct" || tgt_type == "underlying_pts" || tgt_type == "underlying_pct" {
                if let Some(current_spot) = lookup_spot_price(check_date, &index) {
                    if entry_spot != 0.0 {
                        let spot_move = current_spot - entry_spot;
                        let opt = option_type.to_uppercase();
                        if matches!(opt.as_str(), "CE" | "CALL" | "C") {
                            adverse_spot_pts = if position == "SELL" { spot_move } else { -spot_move };
                        } else {
                            adverse_spot_pts = if position == "SELL" { -spot_move } else { spot_move };
                        }
                        adverse_spot_pct = adverse_spot_pts / entry_spot * 100.0;
                    }
                }
            }

            let skip_plain_sl = py_any_to_bool_opt(extract_leg_value(dict, "trail_sl_enabled")) && tsl_state.contains_key(&li);
            let mut hit_sl = false;
            if let Some(sl_val) = sl_val {
                if !skip_plain_sl {
                    let sl_abs = sl_val.abs();
                    match sl_type.as_str() {
                        "pct" => hit_sl = adverse_pct >= sl_abs,
                        "points" => hit_sl = adverse_premium_pts >= sl_abs,
                        "underlying_pts" => hit_sl = adverse_spot_pts >= sl_abs,
                        "underlying_pct" => hit_sl = adverse_spot_pct >= sl_abs,
                        _ => {}
                    }
                }
            }

            let mut hit_tgt = false;
            if let Some(tgt_val) = tgt_val {
                let tgt_abs = tgt_val.abs();
                match tgt_type.as_str() {
                    "pct" => hit_tgt = favorable_pct >= tgt_abs,
                    "points" => hit_tgt = favorable_premium_pts >= tgt_abs,
                    "underlying_pts" => hit_tgt = (-adverse_spot_pts) >= tgt_abs,
                    "underlying_pct" => hit_tgt = (-adverse_spot_pct) >= tgt_abs,
                    _ => {}
                }
            }

            let mut hit_tsl = false;
            if py_any_to_bool_opt(extract_leg_value(dict, "trail_sl_enabled")) {
                if let Some(ts) = tsl_state.get_mut(&li) {
                    if let Some(current_premium) = cp {
                        if ts.x_pts > 0.0 && ts.y_pts > 0.0 {
                            if position == "SELL" {
                                if current_premium < ts.best_prem {
                                    ts.best_prem = current_premium;
                                }
                                let favorable_move = ts.entry_prem - ts.best_prem;
                                let new_triggers = (favorable_move / ts.x_pts).floor() as i32;
                                if new_triggers > ts.triggers_fired {
                                    let delta_triggers = new_triggers - ts.triggers_fired;
                                    ts.triggers_fired = new_triggers;
                                    ts.current_sl_level -= (delta_triggers as f64) * ts.y_pts;
                                }
                                if current_premium >= ts.current_sl_level {
                                    hit_tsl = true;
                                }
                            } else {
                                if current_premium > ts.best_prem {
                                    ts.best_prem = current_premium;
                                }
                                let favorable_move = ts.best_prem - ts.entry_prem;
                                let new_triggers = (favorable_move / ts.x_pts).floor() as i32;
                                if new_triggers > ts.triggers_fired {
                                    let delta_triggers = new_triggers - ts.triggers_fired;
                                    ts.triggers_fired = new_triggers;
                                    ts.current_sl_level += (delta_triggers as f64) * ts.y_pts;
                                }
                                if current_premium <= ts.current_sl_level {
                                    hit_tsl = true;
                                }
                            }
                        }
                    }
                }
            }

            if hit_sl || hit_tgt {
                let reason = if hit_sl { "STOP_LOSS" } else { "TARGET" };
                newly_triggered_this_day.push((li, check_date.clone(), reason.to_string()));
            } else if hit_tsl {
                newly_triggered_this_day.push((li, check_date.clone(), "TRAIL_SL".to_string()));
            }
        }

        if !newly_triggered_this_day.is_empty() {
            if square_off_mode.to_lowercase() == "complete" {
                let trigger_date = newly_triggered_this_day[0].1.clone();
                let trigger_reason = newly_triggered_this_day[0].2.clone();
                let triggered_indices: std::collections::HashSet<usize> =
                    newly_triggered_this_day.iter().map(|(li, _, _)| *li).collect();
                for li2 in 0..leg_results.len() {
                    if !leg_results[li2].0 {
                        if triggered_indices.contains(&li2) {
                            leg_results[li2] = (true, trigger_date.clone(), trigger_reason.clone());
                        } else {
                            leg_results[li2] = (true, trigger_date.clone(), format!("COMPLETE_{}", trigger_reason));
                        }
                    }
                }
                break;
            } else {
                for (li, tdate, treason) in newly_triggered_this_day {
                    leg_results[li] = (true, tdate, treason);
                }
            }
        }
    }

    let py_list = PyList::empty(py);
    for (triggered, exit_date, exit_reason) in leg_results {
        py_list.append(build_result(py, triggered, &exit_date, &exit_reason)?)?;
    }
    Ok(py_list.into())
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (
    entry_date,
    exit_date,
    expiry_date,
    trade_legs,
    index,
    trading_calendar,
    sl_threshold_rs=None,
    tgt_threshold_rs=None,
    per_leg_results=None,
    overall_sl_type=None,
    overall_target_type=None,
    slippage_pct=0.0
))]
fn check_overall_stop_loss_target(
    py: Python<'_>,
    entry_date: String,
    exit_date: String,
    expiry_date: String,
    trade_legs: &PyAny,
    index: String,
    trading_calendar: Vec<String>,
    sl_threshold_rs: Option<f64>,
    tgt_threshold_rs: Option<f64>,
    per_leg_results: Option<&PyAny>,
    overall_sl_type: Option<String>,
    overall_target_type: Option<String>,
    slippage_pct: f64,
) -> PyResult<PyObject> {
    let legs_list: &PyList = trade_legs.downcast()?;
    let exit_date = normalize_date_str(&exit_date);
    let entry_date = normalize_date_str(&entry_date);
    let _expiry_date = normalize_date_str(&expiry_date);
    let holding_days: Vec<String> = trading_calendar
        .into_iter()
        .filter(|d| d > &entry_date && d <= &exit_date)
        .collect();

    if sl_threshold_rs.is_none() && tgt_threshold_rs.is_none() {
        let none = PyDict::new(py);
        none.set_item("exit_date", None::<String>)?;
        none.set_item("exit_reason", None::<String>)?;
        return Ok(none.into());
    }

    let sl_mode = normalize_mode(overall_sl_type.as_deref());
    let tgt_mode = normalize_mode(overall_target_type.as_deref());
    let sl_is_underlying = matches!(sl_mode.as_str(), "underlying_pts" | "underlying_pct");
    let tgt_is_underlying = matches!(tgt_mode.as_str(), "underlying_pts" | "underlying_pct");

    let mut closed_leg_indices = std::collections::HashSet::new();
    if let Some(results) = per_leg_results {
        if let Ok(res_list) = results.downcast::<PyList>() {
            for (li, res_any) in res_list.iter().enumerate() {
                if let Ok(res_dict) = res_any.downcast::<PyDict>() {
                    if py_any_to_bool_opt(extract_leg_value(res_dict, "triggered")) {
                        closed_leg_indices.insert(li);
                    }
                }
            }
        }
    }

    let mut entry_spot_val = None;
    if sl_is_underlying || tgt_is_underlying {
        entry_spot_val = lookup_spot_price(&entry_date, &index);
    }

    let mut combined_live_pnl = 0.0_f64;
    for check_date in holding_days.iter() {
        combined_live_pnl = 0.0;
        let mut has_data = false;
        for (leg_idx, leg_any) in legs_list.iter().enumerate() {
            if closed_leg_indices.contains(&leg_idx) {
                continue;
            }
            let dict: &PyDict = match leg_any.downcast() {
                Ok(v) => v,
                Err(_) => continue,
            };
            let seg = py_any_to_string_opt(extract_leg_value(dict, "segment")).unwrap_or_else(|| "OPTION".to_string()).to_uppercase();
            let position = py_any_to_string_opt(extract_leg_value(dict, "position")).unwrap_or_else(|| "SELL".to_string()).to_uppercase();
            let lots = py_any_to_f64_opt(extract_leg_value(dict, "lots")).unwrap_or(1.0);
            let lot_size = py_any_to_f64_opt(extract_leg_value(dict, "lot_size")).unwrap_or(1.0);
            if matches!(seg.as_str(), "FUTURE" | "FUTURES") {
                continue;
            }

            let option_type = py_any_to_string_opt(extract_leg_value(dict, "option_type")).unwrap_or_else(|| "CE".to_string());
            let strike = py_any_to_f64_opt(extract_leg_value(dict, "strike"));
            let entry_premium = py_any_to_f64_opt(extract_leg_value(dict, "entry_premium"));
            let (Some(strike), Some(entry_premium)) = (strike, entry_premium) else { continue };
            let expiry = py_any_to_string_opt(extract_leg_value(dict, "_resolved_expiry")).unwrap_or_else(|| expiry_date.clone());
            let current_premium_raw = lookup_option_price(check_date, &index, strike, &option_type, &expiry);
            let Some(current_premium_raw) = current_premium_raw else { continue };
            let current_premium = apply_slippage(current_premium_raw, &position, "exit", slippage_pct);
            has_data = true;
            let leg_live_pnl = if position == "BUY" {
                (current_premium - entry_premium) * lots * lot_size
            } else {
                (entry_premium - current_premium) * lots * lot_size
            };
            combined_live_pnl += leg_live_pnl;
        }

        if !has_data {
            continue;
        }

        if sl_is_underlying || tgt_is_underlying {
            if let Some(current_spot) = lookup_spot_price(check_date, &index) {
                if let Some(entry_spot_val) = entry_spot_val {
                    let spot_move = current_spot - entry_spot_val;
                    let spot_move_pct = if entry_spot_val != 0.0 {
                        spot_move / entry_spot_val * 100.0
                    } else {
                        0.0
                    };
                    let first_leg = legs_list.iter().enumerate().find_map(|(i, leg_any)| {
                        if closed_leg_indices.contains(&i) {
                            return None;
                        }
                        let dict: &PyDict = leg_any.downcast().ok()?;
                        Some(dict)
                    });
                    if let Some(first_leg) = first_leg {
                        let fl_pos = py_any_to_string_opt(extract_leg_value(first_leg, "position")).unwrap_or_else(|| "SELL".to_string()).to_uppercase();
                        let fl_opt = py_any_to_string_opt(extract_leg_value(first_leg, "option_type")).unwrap_or_else(|| "CE".to_string()).to_uppercase();
                        let (adverse_spot_pts, adverse_spot_pct) = if (fl_opt == "CE" && fl_pos == "SELL") || (fl_opt == "PE" && fl_pos == "BUY") {
                            (spot_move, spot_move_pct)
                        } else {
                            (-spot_move, -spot_move_pct)
                        };

                        if sl_is_underlying {
                            if let Some(threshold) = sl_threshold_rs {
                                let check_val = if sl_mode == "underlying_pts" {
                                    adverse_spot_pts
                                } else {
                                    adverse_spot_pct
                                };
                                if check_val >= threshold {
                                    let d = PyDict::new(py);
                                    d.set_item("exit_date", check_date.clone())?;
                                    d.set_item("exit_reason", "OVERALL_SL")?;
                                    return Ok(d.into());
                                }
                            }
                        }
                        if tgt_is_underlying {
                            if let Some(threshold) = tgt_threshold_rs {
                                let check_val = if tgt_mode == "underlying_pts" {
                                    -adverse_spot_pts
                                } else {
                                    -adverse_spot_pct
                                };
                                if check_val >= threshold {
                                    let d = PyDict::new(py);
                                    d.set_item("exit_date", check_date.clone())?;
                                    d.set_item("exit_reason", "OVERALL_TARGET")?;
                                    return Ok(d.into());
                                }
                            }
                        }
                    }
                }
            }
        }

        if !sl_is_underlying {
            if let Some(threshold) = sl_threshold_rs {
                if combined_live_pnl <= -threshold {
                    let d = PyDict::new(py);
                    d.set_item("exit_date", check_date.clone())?;
                    d.set_item("exit_reason", "OVERALL_SL")?;
                    return Ok(d.into());
                }
            }
        }
        if !tgt_is_underlying {
            if let Some(threshold) = tgt_threshold_rs {
                if combined_live_pnl >= threshold {
                    let d = PyDict::new(py);
                    d.set_item("exit_date", check_date.clone())?;
                    d.set_item("exit_reason", "OVERALL_TARGET")?;
                    return Ok(d.into());
                }
            }
        }
    }

    let d = PyDict::new(py);
    d.set_item("exit_date", None::<String>)?;
    d.set_item("exit_reason", None::<String>)?;
    Ok(d.into())
}

#[pymodule]
fn algotest_native(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(load_cache, m)?)?;
    m.add_function(wrap_pyfunction!(clear_cache, m)?)?;
    m.add_function(wrap_pyfunction!(is_loaded, m)?)?;
    m.add_function(wrap_pyfunction!(get_option_price, m)?)?;
    m.add_function(wrap_pyfunction!(get_spot_price, m)?)?;
    m.add_function(wrap_pyfunction!(get_strikes_for_date, m)?)?;
    m.add_function(wrap_pyfunction!(check_leg_stop_loss_target, m)?)?;
    m.add_function(wrap_pyfunction!(check_overall_stop_loss_target, m)?)?;
    m.add_function(wrap_pyfunction!(intraday::pyfuncs::run_intraday_backtest, m)?)?;
    Ok(())
}
