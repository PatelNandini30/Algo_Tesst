use axum::{
    body::Body,
    extract::{Query, State},
    http::header,
    response::Response,
};
use chrono::NaiveDate;
use serde::Deserialize;
use std::path::{Path, PathBuf};

use crate::{
    AppState,
    arrow_out::{chain_to_ipc, ohlcv_to_ipc, series_to_ipc, ARROW_CONTENT_TYPE},
    cache::{get_bytes, set_bytes_ex},
    engine::{
        data_queries::{chain_snapshot, load_expiry_map, multi_day_series, ohlcv_series, spot_series, time_to_idx},
        snapshot::Snapshot,
        types::{ExpiryMode, OptType, Resolution},
    },
    error::AppError,
};

const DATA_TTL: u64 = 30 * 24 * 3600; // 30 days

fn arrow_response(bytes: Vec<u8>) -> Response {
    Response::builder()
        .header(header::CONTENT_TYPE, ARROW_CONTENT_TYPE)
        .body(Body::from(bytes))
        .unwrap()
}

fn validate_symbol(symbol: &str) -> Result<(), AppError> {
    const VALID: &[&str] = &["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
    if VALID.contains(&symbol) { Ok(()) } else {
        Err(AppError::BadRequest(format!("symbol must be one of {:?}", VALID)))
    }
}

#[derive(Deserialize)]
pub struct SpotQuery { pub symbol: String, pub date: String }

pub async fn spot(
    State(state): State<AppState>,
    Query(q): Query<SpotQuery>,
) -> Result<Response, AppError> {
    validate_symbol(&q.symbol)?;
    NaiveDate::parse_from_str(&q.date, "%Y-%m-%d").map_err(|_| AppError::BadRequest("invalid date format".into()))?;

    let cache_key = format!("intraday:data:spot:{}:{}", q.symbol, q.date);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await {
        return Ok(arrow_response(bytes));
    }
    let snap_path = Path::new(&state.data_dir).join(&q.symbol).join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() { return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date))); }
    let bytes = tokio::task::spawn_blocking(move || {
        let snap = Snapshot::open(&snap_path)?;
        ohlcv_to_ipc(&spot_series(&snap))
    }).await.map_err(|e| AppError::Arrow(e.to_string()))??;
    set_bytes_ex(&mut redis, &cache_key, &bytes, DATA_TTL).await;
    Ok(arrow_response(bytes))
}

#[derive(Deserialize)]
pub struct OhlcvQuery {
    pub symbol: String,
    pub date: String,
    pub strike: i64,
    pub opt_type: String,
    pub expiry_date: String,
}

pub async fn ohlcv(
    State(state): State<AppState>,
    Query(q): Query<OhlcvQuery>,
) -> Result<Response, AppError> {
    validate_symbol(&q.symbol)?;
    NaiveDate::parse_from_str(&q.date, "%Y-%m-%d").map_err(|_| AppError::BadRequest("invalid date format".into()))?;

    let cache_key = format!("intraday:data:ohlcv:{}:{}:{}:{}:{}", q.symbol, q.date, q.strike, q.opt_type, q.expiry_date);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await { return Ok(arrow_response(bytes)); }

    let symbol_dir = PathBuf::from(&state.data_dir).join(&q.symbol);
    let snap_path = symbol_dir.join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() { return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date))); }

    let expiry_map = load_expiry_map(&symbol_dir)?;
    let target_date = NaiveDate::parse_from_str(&q.expiry_date, "%Y-%m-%d").map_err(|e| AppError::BadRequest(e.to_string()))?;
    let expiry_idx = expiry_map.iter().find(|(_, &d)| d == target_date).map(|(&i, _)| i)
        .ok_or_else(|| AppError::NotFound(format!("expiry {} not found", q.expiry_date)))?;
    let opt_type = OptType::from_str(&q.opt_type).ok_or_else(|| AppError::BadRequest("opt_type must be CE or PE".into()))?;
    let strike_x100 = (q.strike * 100) as i32;

    let bytes = tokio::task::spawn_blocking(move || {
        let snap = Snapshot::open(&snap_path)?;
        let bars = ohlcv_series(&snap, expiry_idx, strike_x100, opt_type)?;
        ohlcv_to_ipc(&bars)
    }).await.map_err(|e| AppError::Arrow(e.to_string()))??;
    set_bytes_ex(&mut redis, &cache_key, &bytes, DATA_TTL).await;
    Ok(arrow_response(bytes))
}

#[derive(Deserialize)]
pub struct ChainQuery {
    pub symbol: String,
    pub date: String,
    pub minute: String,
    pub expiry_date: String,
}

pub async fn chain(
    State(state): State<AppState>,
    Query(q): Query<ChainQuery>,
) -> Result<Response, AppError> {
    validate_symbol(&q.symbol)?;
    NaiveDate::parse_from_str(&q.date, "%Y-%m-%d").map_err(|_| AppError::BadRequest("invalid date format".into()))?;

    let cache_key = format!("intraday:data:chain:{}:{}:{}:{}", q.symbol, q.date, q.minute, q.expiry_date);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await { return Ok(arrow_response(bytes)); }

    let symbol_dir = PathBuf::from(&state.data_dir).join(&q.symbol);
    let snap_path = symbol_dir.join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() { return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date))); }

    let expiry_map = load_expiry_map(&symbol_dir)?;
    let target_date = NaiveDate::parse_from_str(&q.expiry_date, "%Y-%m-%d").map_err(|e| AppError::BadRequest(e.to_string()))?;
    let expiry_idx = expiry_map.iter().find(|(_, &d)| d == target_date).map(|(&i, _)| i)
        .ok_or_else(|| AppError::NotFound(format!("expiry {} not found", q.expiry_date)))?;
    let minute_str = q.minute.clone();

    let bytes = tokio::task::spawn_blocking(move || {
        let snap = Snapshot::open(&snap_path)?;
        // time_to_idx returns Result — propagate error as BadRequest
        let minute_idx = time_to_idx(&minute_str)?.min(snap.minute_count.saturating_sub(1));
        let rows = chain_snapshot(&snap, expiry_idx, minute_idx)?;
        chain_to_ipc(&rows)
    }).await.map_err(|e| AppError::Arrow(e.to_string()))??;
    set_bytes_ex(&mut redis, &cache_key, &bytes, DATA_TTL).await;
    Ok(arrow_response(bytes))
}

#[derive(Deserialize)]
pub struct SeriesQuery {
    pub symbol: String,
    pub date_from: String,
    pub date_to: String,
    pub strike: i64,
    pub opt_type: String,
    pub expiry_mode: String,
    #[serde(default = "default_resolution")]
    pub resolution: String,
}
fn default_resolution() -> String { "5m".into() }

pub async fn series(
    State(state): State<AppState>,
    Query(q): Query<SeriesQuery>,
) -> Result<Response, AppError> {
    validate_symbol(&q.symbol)?;

    let cache_key = format!("intraday:data:series:{}:{}:{}:{}:{}:{}:{}", q.symbol, q.date_from, q.date_to, q.strike, q.opt_type, q.expiry_mode, q.resolution);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await { return Ok(arrow_response(bytes)); }

    let symbol_dir = PathBuf::from(&state.data_dir).join(&q.symbol);
    let date_from = NaiveDate::parse_from_str(&q.date_from, "%Y-%m-%d").map_err(|e| AppError::BadRequest(e.to_string()))?;
    let date_to   = NaiveDate::parse_from_str(&q.date_to, "%Y-%m-%d").map_err(|e| AppError::BadRequest(e.to_string()))?;
    let opt_type   = OptType::from_str(&q.opt_type).ok_or_else(|| AppError::BadRequest("opt_type must be CE or PE".into()))?;
    let expiry_mode = ExpiryMode::from_str(&q.expiry_mode).ok_or_else(|| AppError::BadRequest("expiry_mode must be WEEKLY or MONTHLY".into()))?;
    let resolution  = Resolution::from_str(&q.resolution).ok_or_else(|| AppError::BadRequest("resolution must be 1m|5m|15m|1d".into()))?;
    let strike_x100 = (q.strike * 100) as i32;

    let bars = tokio::task::spawn_blocking(move || {
        multi_day_series(&symbol_dir, date_from, date_to, strike_x100, opt_type, expiry_mode, resolution)
    }).await.map_err(|e| AppError::Arrow(e.to_string()))??;

    let bytes = series_to_ipc(&bars)?;
    set_bytes_ex(&mut redis, &cache_key, &bytes, DATA_TTL).await;
    Ok(arrow_response(bytes))
}
