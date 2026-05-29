mod intraday;
mod optimizer;
mod simulate;

use std::collections::HashMap;
use std::fs::File;
use std::io::Cursor;
use std::path::Path;
use std::sync::RwLock;

use ahash::{AHashMap, AHashSet};
use arrow_array::{
    Array, ArrayRef, Date32Array, Date64Array, Float32Array, Float64Array, Int32Array, Int64Array,
    LargeStringArray, StringArray, TimestampMillisecondArray, TimestampMicrosecondArray,
    TimestampNanosecondArray, TimestampSecondArray,
};
use arrow_ipc::reader::FileReader;
use arrow_schema::DataType;
use chrono::{Duration, NaiveDate};
use memmap2::Mmap;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyList};
use pyo3::wrap_pyfunction;

// Compact integer-keyed cache: dramatically reduces memory vs String-keyed HashMaps.
// (i32, u16, i64, u8, i32) uses ~20 bytes/key vs ~104 bytes for (String×4, i64).
// For 4.3M rows: ~172MB vs ~650MB — eliminates memory-pressure swapping on HDD boxes.
struct MarketCache {
    // (date_days, symbol_id, strike_i64, opttype_id, expiry_days) → close
    // f32 values: Indian options priced to ₹0.05; f32 gives 7 sig-digits — no precision loss.
    options: AHashMap<(i32, u16, i64, u8, i32), f32>,
    // Same key → day HIGH price. Used by SL-with-Buffer slice 4b.
    options_high: AHashMap<(i32, u16, i64, u8, i32), f32>,
    // Same key → day LOW price. Used by SL-with-Buffer slice 4b.
    options_low: AHashMap<(i32, u16, i64, u8, i32), f32>,
    // Same key → day OPEN price. Used by SL-with-Buffer gap detection.
    options_open: AHashMap<(i32, u16, i64, u8, i32), f32>,
    // Same key → day SETTLED price. Used as MAE/MFE high/low fallback when
    // High and Low are both 0 (no intraday trades but settlement published).
    options_settled: AHashMap<(i32, u16, i64, u8, i32), f32>,
    // (date_days, symbol_id) → spot_close
    spot: AHashMap<(i32, u16), f64>,
    // (date_days, symbol_id, expiry_days, opttype_id) → sorted [(strike, close)]
    strikes: AHashMap<(i32, u16, i32, u8), Vec<(f64, f64)>>,
    // Strikes with `contracts == 0` (zero turnover) — treated as untradeable by
    // the strike-shift validator (stale EOD prices carried over).  Empty when
    // the feather lacks the Contracts column (backwards compatible).
    untradeable: AHashSet<(i32, u16, i64, u8, i32)>,
    // Symbol interning: name → u16 id
    symbol_ids: AHashMap<String, u16>,
    // Reverse: id → name (for debug / serialisation)
    symbol_names: Vec<String>,
}

impl Default for MarketCache {
    fn default() -> Self {
        MarketCache {
            options: AHashMap::new(),
            options_high: AHashMap::new(),
            options_low: AHashMap::new(),
            options_open: AHashMap::new(),
            options_settled: AHashMap::new(),
            spot: AHashMap::new(),
            strikes: AHashMap::new(),
            untradeable: AHashSet::new(),
            symbol_ids: AHashMap::new(),
            symbol_names: Vec::new(),
        }
    }
}

static CACHE: Lazy<RwLock<Option<MarketCache>>> = Lazy::new(|| RwLock::new(None));

pub(crate) fn round2(v: f64) -> f64 {
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

pub(crate) fn apply_slippage(price: f64, position: &str, side: &str, slippage_pct: f64) -> f64 {
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
        DataType::Timestamp(_, _) => {
            let base = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap().and_hms_opt(0, 0, 0).unwrap();
            if let Some(arr) = array.as_any().downcast_ref::<TimestampSecondArray>() {
                Some((base + chrono::Duration::seconds(arr.value(row))).date().format("%Y-%m-%d").to_string())
            } else if let Some(arr) = array.as_any().downcast_ref::<TimestampMillisecondArray>() {
                Some((base + chrono::Duration::milliseconds(arr.value(row))).date().format("%Y-%m-%d").to_string())
            } else if let Some(arr) = array.as_any().downcast_ref::<TimestampMicrosecondArray>() {
                Some((base + chrono::Duration::microseconds(arr.value(row))).date().format("%Y-%m-%d").to_string())
            } else if let Some(arr) = array.as_any().downcast_ref::<TimestampNanosecondArray>() {
                Some((base + chrono::Duration::nanoseconds(arr.value(row))).date().format("%Y-%m-%d").to_string())
            } else {
                None
            }
        }
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

// Parse "YYYY-MM-DD" (or longer) to days-since-epoch (i32).
fn date_str_to_days(s: &str) -> Option<i32> {
    let s = s.trim();
    if s.len() < 10 { return None; }
    let y: i32 = s[..4].parse().ok()?;
    let m: u32 = s[5..7].parse().ok()?;
    let d: u32 = s[8..10].parse().ok()?;
    let dt = NaiveDate::from_ymd_opt(y, m, d)?;
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1)?;
    Some((dt - epoch).num_days() as i32)
}

// Fast days-since-epoch (Date32 raw value) → "YYYY-MM-DD" string.
fn days_to_date_str(days: i32, base: NaiveDate) -> String {
    (base + Duration::days(days as i64)).format("%Y-%m-%d").to_string()
}

// "CE" → 0, anything else → 1.
fn opt_type_to_id(s: &str) -> u8 {
    if s.trim().eq_ignore_ascii_case("CE") { 0 } else { 1 }
}

/// Three-way status of a strike's data on a given (date, expiry):
///   - `Tradeable(price)` → contract exists with non-zero turnover, price is real
///   - `ZeroContracts`    → contract row exists but contracts==0 (stale close)
///   - `Missing`          → no row at all in the cache for this strike/expiry
///
/// `validate_or_shift_strike` shifts ONLY on `ZeroContracts`.  For `Missing`
/// the trade is dropped (the strike simply wasn't listed — shifting risks
/// landing on a wildly different strike).
#[derive(Debug, Clone, Copy)]
pub(crate) enum OptionDataStatus {
    Tradeable(f64),
    ZeroContracts,
    Missing,
}

pub(crate) fn lookup_option_status(date: &str, index: &str, strike: f64, opt_type: &str, expiry: &str) -> OptionDataStatus {
    let cache = match CACHE.read() {
        Ok(c) => c,
        Err(_) => return OptionDataStatus::Missing,
    };
    let cache = match cache.as_ref() { Some(c) => c, None => return OptionDataStatus::Missing };
    let date_days = match date_str_to_days(&normalize_date_str(date)) { Some(v) => v, None => return OptionDataStatus::Missing };
    let sym_id = match cache.symbol_ids.get(&index.trim().to_uppercase()).copied() { Some(v) => v, None => return OptionDataStatus::Missing };
    let strike_key = to_i64_strike(strike);
    let type_id = opt_type_to_id(opt_type);
    let expiry_days = match date_str_to_days(&normalize_date_str(expiry)) { Some(v) => v, None => return OptionDataStatus::Missing };
    let key = (date_days, sym_id, strike_key, type_id, expiry_days);
    if cache.untradeable.contains(&key) {
        return OptionDataStatus::ZeroContracts;
    }
    if let Some(px) = cache.options.get(&key).copied() {
        return OptionDataStatus::Tradeable(px as f64);
    }
    // Moved-expiry fallback — mirrors lookup_option_price. NSE sometimes lists
    // a contract under the original (pre-holiday) expiry label (e.g. 29-Jun-2023)
    // but the schedule resolves to the settlement day (28-Jun-2023). Try
    // +1..+3 days when the exact expiry misses and entry is before expiry.
    if date_days < expiry_days {
        for offset in 1i32..=3 {
            let alt_expiry = expiry_days + offset;
            let alt_key = (date_days, sym_id, strike_key, type_id, alt_expiry);
            if cache.untradeable.contains(&alt_key) {
                return OptionDataStatus::ZeroContracts;
            }
            if let Some(px) = cache.options.get(&alt_key).copied() {
                return OptionDataStatus::Tradeable(px as f64);
            }
        }
    }
    OptionDataStatus::Missing
}

/// Backwards-compatible tradeable lookup: returns Some(price) only when the
/// strike has real (non-stale) data.  Used by Python callers that just want
/// a boolean tradeable check.
pub(crate) fn lookup_option_price_tradeable(date: &str, index: &str, strike: f64, opt_type: &str, expiry: &str) -> Option<f64> {
    match lookup_option_status(date, index, strike, opt_type, expiry) {
        OptionDataStatus::Tradeable(px) => Some(px),
        _ => None,
    }
}

pub(crate) fn lookup_option_price(date: &str, index: &str, strike: f64, opt_type: &str, expiry: &str) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let date_days = date_str_to_days(&normalize_date_str(date))?;
    let sym_id = *cache.symbol_ids.get(&index.trim().to_uppercase())?;
    let strike_key = to_i64_strike(strike);
    let type_id = opt_type_to_id(opt_type);
    let expiry_days = date_str_to_days(&normalize_date_str(expiry))?;
    if let Some(px) = cache.options.get(&(date_days, sym_id, strike_key, type_id, expiry_days)).copied() {
        return Some(px as f64);
    }
    // Moved-expiry fallback.  NSE sometimes lists a contract under one expiry
    // label (e.g. Thu 29-Jun-2023 — the original weekly) but settles it on an
    // earlier day (Wed 28-Jun-2023, because Thu 29-Jun was Bakri Eid holiday).
    // The engine's get_expiry_dates returns the SETTLEMENT day (28-Jun), but
    // historical chain data is keyed by the ORIGINAL expiry (29-Jun).  When the
    // direct lookup misses AND the entry date is strictly before the requested
    // expiry, try +1..+3 days forward — the live contract was almost certainly
    // listed under the original (slightly later) weekday expiry.
    // Settlement-day pricing (date == expiry) is unaffected: it uses whatever
    // expiry the data is keyed by, which IS the moved-settlement label.
    if date_days < expiry_days {
        for offset in 1i32..=3 {
            let alt_expiry = expiry_days + offset;
            if let Some(px) = cache.options.get(&(date_days, sym_id, strike_key, type_id, alt_expiry)).copied() {
                return Some(px as f64);
            }
        }
    }
    None
}

/// Day HIGH for one option contract on one date. None if absent from cache
/// (older feathers may not include High). Used by SL-with-Buffer (slice 4b).
pub(crate) fn lookup_option_high(
    date: &str,
    index: &str,
    strike: f64,
    opt_type: &str,
    expiry: &str,
) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let date_days = date_str_to_days(&normalize_date_str(date))?;
    let sym_id = *cache.symbol_ids.get(&index.trim().to_uppercase())?;
    let strike_key = to_i64_strike(strike);
    let type_id = opt_type_to_id(opt_type);
    let expiry_days = date_str_to_days(&normalize_date_str(expiry))?;
    cache.options_high.get(&(date_days, sym_id, strike_key, type_id, expiry_days)).copied().map(|v| v as f64)
}

/// Day LOW for one option contract on one date. None if absent.
pub(crate) fn lookup_option_low(
    date: &str,
    index: &str,
    strike: f64,
    opt_type: &str,
    expiry: &str,
) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let date_days = date_str_to_days(&normalize_date_str(date))?;
    let sym_id = *cache.symbol_ids.get(&index.trim().to_uppercase())?;
    let strike_key = to_i64_strike(strike);
    let type_id = opt_type_to_id(opt_type);
    let expiry_days = date_str_to_days(&normalize_date_str(expiry))?;
    cache.options_low.get(&(date_days, sym_id, strike_key, type_id, expiry_days)).copied().map(|v| v as f64)
}

/// Day OPEN for one option contract on one date. None if absent. Used by
/// SL-with-Buffer to detect gap-past-SL at the open.
pub(crate) fn lookup_option_open(
    date: &str,
    index: &str,
    strike: f64,
    opt_type: &str,
    expiry: &str,
) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let date_days = date_str_to_days(&normalize_date_str(date))?;
    let sym_id = *cache.symbol_ids.get(&index.trim().to_uppercase())?;
    let strike_key = to_i64_strike(strike);
    let type_id = opt_type_to_id(opt_type);
    let expiry_days = date_str_to_days(&normalize_date_str(expiry))?;
    cache.options_open.get(&(date_days, sym_id, strike_key, type_id, expiry_days)).copied().map(|v| v as f64)
}

/// Return the full strike chain for one (date, index, expiry, opt_type) as
/// a sorted `Vec<(strike, close_premium)>`. None if the cache lacks data.
pub(crate) fn lookup_strikes_for_date(
    date: &str,
    index: &str,
    expiry: &str,
    opt_type: &str,
) -> Option<Vec<(f64, f64)>> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let date_days = date_str_to_days(&normalize_date_str(date))?;
    let sym_id = *cache.symbol_ids.get(&index.trim().to_uppercase())?;
    let expiry_days = date_str_to_days(&normalize_date_str(expiry))?;
    let type_id = opt_type_to_id(opt_type);
    cache.strikes
        .get(&(date_days, sym_id, expiry_days, type_id))
        .cloned()
}

pub(crate) fn lookup_spot_price(date: &str, index: &str) -> Option<f64> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let date_days = date_str_to_days(&normalize_date_str(date))?;
    let sym_upper = index.trim().to_uppercase();
    // Try named symbol first, then fallback to id 0 (single-symbol feathers store under id 0 with empty name)
    if let Some(&sym_id) = cache.symbol_ids.get(&sym_upper) {
        if let Some(v) = cache.spot.get(&(date_days, sym_id)).copied() {
            return Some(v);
        }
    }
    // Fallback: empty-string symbol (feathers without Symbol column stored under id u16::MAX)
    cache.spot.get(&(date_days, u16::MAX)).copied()
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

// Get or insert a symbol into the interning table, returning its u16 id.
fn intern_symbol(ids: &mut AHashMap<String, u16>, names: &mut Vec<String>, sym: &str) -> u16 {
    if let Some(&id) = ids.get(sym) {
        return id;
    }
    let id = names.len() as u16;
    names.push(sym.to_string());
    ids.insert(sym.to_string(), id);
    id
}

// Arrow columns can be either Utf8 (StringArray) or LargeUtf8 (LargeStringArray).
// Polars writes LargeStringArray by default; handle both to avoid silently skipping batches.
enum AnyStrArray<'a> {
    Sm(&'a StringArray),
    Lg(&'a LargeStringArray),
}
impl<'a> AnyStrArray<'a> {
    fn from_col(col: &'a dyn Array) -> Option<Self> {
        if let Some(a) = col.as_any().downcast_ref::<StringArray>() {
            return Some(AnyStrArray::Sm(a));
        }
        if let Some(a) = col.as_any().downcast_ref::<LargeStringArray>() {
            return Some(AnyStrArray::Lg(a));
        }
        None
    }
    fn is_null(&self, i: usize) -> bool {
        match self { AnyStrArray::Sm(a) => a.is_null(i), AnyStrArray::Lg(a) => a.is_null(i) }
    }
    fn value(&self, i: usize) -> &str {
        match self { AnyStrArray::Sm(a) => a.value(i), AnyStrArray::Lg(a) => a.value(i) }
    }
}

fn build_cache_from_batches(options_batches: Vec<arrow_array::RecordBatch>, spot_batches: Vec<arrow_array::RecordBatch>) -> MarketCache {
    let mut cache = MarketCache::default();
    let base_date = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();

    // Pre-allocate capacity to avoid rehashing on 4M+ rows
    let total_rows: usize = options_batches.iter().map(|b| b.num_rows()).sum();
    cache.options.reserve(total_rows);
    cache.strikes.reserve(total_rows / 8);  // rough estimate of unique (date,sym,exp,type) keys

    // Date-days string cache: avoids repeated chrono formatting for ~2000 unique dates
    let mut date_str_cache: AHashMap<i32, String> = AHashMap::with_capacity(4096);

    for batch in &options_batches {
        let schema = batch.schema();
        let (idx_date, idx_symbol, idx_expiry, idx_type, idx_strike, idx_close) = match (
            schema.index_of("Date").ok(),
            schema.index_of("Symbol").ok(),
            schema.index_of("ExpiryDate").ok(),
            schema.index_of("OptionType").ok(),
            schema.index_of("StrikePrice").ok(),
            schema.index_of("Close").ok(),
        ) {
            (Some(a), Some(b), Some(c), Some(d), Some(e), Some(f)) => (a, b, c, d, e, f),
            _ => continue,
        };
        // Open/High/Low are optional — older feathers may not have them.
        // SL-with-Buffer features that need these will return None when missing.
        let idx_high = schema.index_of("High").ok();
        let idx_low = schema.index_of("Low").ok();
        let idx_open = schema.index_of("Open").ok();
        // Contracts is optional — older feathers don't include it.  When present,
        // the strike-shift validator uses it to skip stale-price records.
        let idx_contracts = schema.index_of("Contracts").ok();
        // SettledPrice is optional — MAE/MFE falls back to this when High==Low==0.
        let idx_settled = schema.index_of("SettledPrice").ok();

        // Downcast columns once outside the row loop — critical for performance.
        // Accept both Utf8 (StringArray) and LargeUtf8 (LargeStringArray) — Polars writes large_string.
        let Some(date_arr) = batch.column(idx_date).as_any().downcast_ref::<Date32Array>() else { continue };
        let Some(expiry_arr) = batch.column(idx_expiry).as_any().downcast_ref::<Date32Array>() else { continue };
        let Some(symbol_arr) = AnyStrArray::from_col(batch.column(idx_symbol).as_ref()) else { continue };
        let Some(type_arr) = AnyStrArray::from_col(batch.column(idx_type).as_ref()) else { continue };
        let Some(strike_arr) = batch.column(idx_strike).as_any().downcast_ref::<Float64Array>() else { continue };
        let Some(close_arr) = batch.column(idx_close).as_any().downcast_ref::<Float64Array>() else { continue };
        let high_arr = idx_high.and_then(|i| batch.column(i).as_any().downcast_ref::<Float64Array>());
        let low_arr = idx_low.and_then(|i| batch.column(i).as_any().downcast_ref::<Float64Array>());
        let open_arr = idx_open.and_then(|i| batch.column(i).as_any().downcast_ref::<Float64Array>());
        let settled_arr = idx_settled.and_then(|i| batch.column(i).as_any().downcast_ref::<Float64Array>());
        // Contracts can be stored as Int64 or Float64 depending on the writer.
        // Try Int64 first (preferred), fall back to Float64 (Polars sometimes
        // promotes ints when mixed with nulls).
        let contracts_i64 = idx_contracts.and_then(|i| batch.column(i).as_any().downcast_ref::<arrow_array::Int64Array>());
        let contracts_f64 = if contracts_i64.is_none() {
            idx_contracts.and_then(|i| batch.column(i).as_any().downcast_ref::<Float64Array>())
        } else {
            None
        };
        if high_arr.is_some() {
            cache.options_high.reserve(batch.num_rows());
        }
        if low_arr.is_some() {
            cache.options_low.reserve(batch.num_rows());
        }
        if open_arr.is_some() {
            cache.options_open.reserve(batch.num_rows());
        }

        for row in 0..batch.num_rows() {
            if date_arr.is_null(row) || expiry_arr.is_null(row)
                || symbol_arr.is_null(row) || type_arr.is_null(row)
                || strike_arr.is_null(row) || close_arr.is_null(row) { continue; }

            let date_days = date_arr.value(row);
            let expiry_days = expiry_arr.value(row);
            let strike_v = strike_arr.value(row);
            let close_v = close_arr.value(row);

            let sym_raw = symbol_arr.value(row).trim();
            if sym_raw.is_empty() { continue; }
            // intern_symbol needs mut refs; we use a temporary uppercase string
            let sym_upper_owned;
            let sym_upper = if sym_raw.bytes().all(|b| b.is_ascii_uppercase()) {
                sym_raw
            } else {
                sym_upper_owned = sym_raw.to_uppercase();
                &sym_upper_owned
            };
            let sym_id = intern_symbol(&mut cache.symbol_ids, &mut cache.symbol_names, sym_upper);

            let type_raw = type_arr.value(row).trim();
            let type_id = opt_type_to_id(type_raw);

            let strike_key = to_i64_strike(strike_v);
            let key = (date_days, sym_id, strike_key, type_id, expiry_days);

            cache.options.insert(key, close_v as f32);
            if let Some(h) = high_arr {
                if !h.is_null(row) {
                    cache.options_high.insert(key, h.value(row) as f32);
                }
            }
            if let Some(l) = low_arr {
                if !l.is_null(row) {
                    cache.options_low.insert(key, l.value(row) as f32);
                }
            }
            if let Some(o) = open_arr {
                if !o.is_null(row) {
                    cache.options_open.insert(key, o.value(row) as f32);
                }
            }
            if let Some(s) = settled_arr {
                if !s.is_null(row) {
                    let sv = s.value(row);
                    if sv > 0.0 {
                        cache.options_settled.insert(key, sv as f32);
                    }
                }
            }
            // Mark this (date, sym, strike, type, expiry) as untradeable if the
            // row has Contracts=0 (zero turnover — close is a stale carry-over).
            // Strike-shift validator uses this to skip the strike.
            let contracts_val: Option<i64> = if let Some(arr) = contracts_i64 {
                if arr.is_null(row) { None } else { Some(arr.value(row)) }
            } else if let Some(arr) = contracts_f64 {
                if arr.is_null(row) { None } else { Some(arr.value(row) as i64) }
            } else {
                None
            };
            if let Some(c) = contracts_val {
                if c <= 0 {
                    cache.untradeable.insert(key);
                }
            }
            cache.strikes
                .entry((date_days, sym_id, expiry_days, type_id))
                .or_default()
                .push((strike_v, close_v));

            // Populate date_str_cache lazily (used only for debug/logging, not critical path)
            date_str_cache.entry(date_days).or_insert_with(|| days_to_date_str(date_days, base_date));
        }
    }

    for values in cache.strikes.values_mut() {
        values.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    }

    // Spot batches
    let total_spot: usize = spot_batches.iter().map(|b| b.num_rows()).sum();
    cache.spot.reserve(total_spot + 16);

    for batch in &spot_batches {
        let schema = batch.schema();
        let (Some(idx_date), Some(idx_close)) = (schema.index_of("Date").ok(), schema.index_of("Close").ok()) else { continue };
        let idx_symbol = schema.index_of("Symbol").ok();

        let Some(date_arr) = batch.column(idx_date).as_any().downcast_ref::<Date32Array>() else {
            // Fallback for non-Date32 spot (legacy)
            let date_col = batch.column(idx_date).clone();
            let close_col = batch.column(idx_close).clone();
            let sym_col = idx_symbol.map(|i| batch.column(i).clone());
            for row in 0..batch.num_rows() {
                let date_s = match to_iso_date_from_array(&date_col, row) { Some(v) => v, None => continue };
                let date_days = match date_str_to_days(&date_s) { Some(v) => v, None => continue };
                let close_v = match to_f64_from_array(&close_col, row) { Some(v) => v, None => continue };
                let sym_id = if let Some(ref sc) = sym_col {
                    if sc.is_null(row) { continue; }
                    let sym = match sc.data_type() {
                        DataType::Utf8 => sc.as_any().downcast_ref::<StringArray>().map(|a| a.value(row).trim().to_uppercase()),
                        _ => None,
                    };
                    match sym {
                        Some(s) if !s.is_empty() => intern_symbol(&mut cache.symbol_ids, &mut cache.symbol_names, &s),
                        _ => u16::MAX,
                    }
                } else { u16::MAX };
                cache.spot.insert((date_days, sym_id), close_v);
            }
            continue;
        };

        let Some(close_arr) = batch.column(idx_close).as_any().downcast_ref::<Float64Array>() else { continue };
        let sym_arr = idx_symbol.and_then(|i| batch.column(i).as_any().downcast_ref::<StringArray>());

        for row in 0..batch.num_rows() {
            if date_arr.is_null(row) || close_arr.is_null(row) { continue; }
            let date_days = date_arr.value(row);
            let close_v = close_arr.value(row);
            let sym_id = if let Some(arr) = sym_arr {
                if arr.is_null(row) { u16::MAX } else {
                    let sym_raw = arr.value(row).trim();
                    if sym_raw.is_empty() { u16::MAX } else {
                        let sym_upper_owned;
                        let sym_upper = if sym_raw.bytes().all(|b| b.is_ascii_uppercase()) {
                            sym_raw
                        } else {
                            sym_upper_owned = sym_raw.to_uppercase();
                            &sym_upper_owned
                        };
                        intern_symbol(&mut cache.symbol_ids, &mut cache.symbol_names, sym_upper)
                    }
                }
            } else {
                u16::MAX  // no Symbol column: store under sentinel id
            };
            cache.spot.insert((date_days, sym_id), close_v);
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

/// Tradeable variant: returns None for zero-turnover (stale) records.
/// Used by the Python cascade-reentry strike validator.
#[pyfunction]
fn get_option_price_tradeable(date: String, index: String, strike: f64, opt_type: String, expiry: String) -> Option<f64> {
    lookup_option_price_tradeable(&date, &index, strike, &opt_type, &expiry)
}

/// Three-way data status as a string: "tradeable", "zero_contracts", or "missing".
/// Used by the Python strike-shift validator to differentiate "no record at all"
/// (don't shift) from "stale-price record" (do shift).
#[pyfunction]
fn get_option_status(date: String, index: String, strike: f64, opt_type: String, expiry: String) -> &'static str {
    match lookup_option_status(&date, &index, strike, &opt_type, &expiry) {
        OptionDataStatus::Tradeable(px) if px > 0.0 => "tradeable",
        OptionDataStatus::Tradeable(_) => "missing",
        OptionDataStatus::ZeroContracts => "zero_contracts",
        OptionDataStatus::Missing => "missing",
    }
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
    let date_days = match date_str_to_days(&normalize_date_str(&date)) { Some(v) => v, None => return Vec::new() };
    let expiry_days = match date_str_to_days(&normalize_date_str(&expiry)) { Some(v) => v, None => return Vec::new() };
    let sym_id = match cache.symbol_ids.get(&index.trim().to_uppercase()) { Some(&id) => id, None => return Vec::new() };
    let type_id = opt_type_to_id(&opt_type);
    cache.strikes.get(&(date_days, sym_id, expiry_days, type_id)).cloned().unwrap_or_default()
}

/// Return (max_high, min_low) for one option leg over a date range using the
/// in-memory MarketCache. O(days-in-range) per call — avoids DB/disk for MAE/MFE.
#[pyfunction]
fn get_ohlc_range(
    from_date: String,
    to_date: String,
    index: String,
    strike: f64,
    opt_type: String,
    expiry: String,
) -> Option<(f64, f64)> {
    let cache = CACHE.read().ok()?;
    let cache = cache.as_ref()?;
    let sym_id = *cache.symbol_ids.get(&index.trim().to_uppercase())?;
    let type_id = opt_type_to_id(&opt_type);
    let expiry_days = date_str_to_days(&normalize_date_str(&expiry))?;
    let from_days = date_str_to_days(&normalize_date_str(&from_date))?;
    let to_days = date_str_to_days(&normalize_date_str(&to_date))?;
    let strike_key = to_i64_strike(strike);

    let mut max_high: Option<f64> = None;
    let mut min_low: Option<f64> = None;
    for d in from_days..=to_days {
        let key = (d, sym_id, strike_key, type_id, expiry_days);
        let h_raw = cache.options_high.get(&key).copied().map(|v| v as f64);
        let l_raw = cache.options_low.get(&key).copied().map(|v| v as f64);
        let settled = cache.options_settled.get(&key).copied().map(|v| v as f64).filter(|&v| v > 0.0);
        // Per-VALUE SettledPrice substitution:
        //   - high > 0  → use high as-is (pre-existing behavior, untouched)
        //   - high == 0 → substitute settled_price for high (if available)
        //   - same rule applied independently to low
        // Asymmetric case (high>0, low=0) keeps the real high and replaces
        // only the zero low — matches the user's "where there is zero, take
        // settled there only" rule.
        let h_eff = match h_raw {
            Some(h) if h > 0.0 => Some(h),
            _ => settled,
        };
        let l_eff = match l_raw {
            Some(l) if l > 0.0 => Some(l),
            _ => settled,
        };
        if let Some(h) = h_eff {
            max_high = Some(max_high.map_or(h, |prev: f64| prev.max(h)));
        }
        if let Some(l) = l_eff {
            min_low = Some(min_low.map_or(l, |prev: f64| prev.min(l)));
        }
    }
    match (max_high, min_low) {
        (Some(h), Some(l)) => Some((h, l)),
        _ => None,
    }
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
    m.add_function(wrap_pyfunction!(get_option_price_tradeable, m)?)?;
    m.add_function(wrap_pyfunction!(get_option_status, m)?)?;
    m.add_function(wrap_pyfunction!(get_spot_price, m)?)?;
    m.add_function(wrap_pyfunction!(get_strikes_for_date, m)?)?;
    m.add_function(wrap_pyfunction!(check_leg_stop_loss_target, m)?)?;
    m.add_function(wrap_pyfunction!(check_overall_stop_loss_target, m)?)?;
    m.add_function(wrap_pyfunction!(intraday::pyfuncs::run_intraday_backtest, m)?)?;
    m.add_function(wrap_pyfunction!(optimizer::batch_compute_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(optimizer::run_optimization_batch, m)?)?;
    m.add_function(wrap_pyfunction!(simulate::simulate_trades_batch, m)?)?;
    m.add_function(wrap_pyfunction!(simulate::resolve_trade_specs, m)?)?;
    m.add_function(wrap_pyfunction!(simulate::apply_sl_with_buffer_batch, m)?)?;
    m.add_function(wrap_pyfunction!(get_ohlc_range, m)?)?;
    Ok(())
}
