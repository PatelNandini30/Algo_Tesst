use axum::{extract::{Query, State}, Json};
use crate::{AppState, error::AppError};
use crate::engine::data_queries::load_expiry_map;
use chrono::{Datelike, NaiveDate};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

fn validate_symbol(symbol: &str) -> Result<(), AppError> {
    const VALID: &[&str] = &["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
    if VALID.contains(&symbol) { Ok(()) } else {
        Err(AppError::BadRequest(format!("symbol must be one of {:?}", VALID)))
    }
}

#[derive(Deserialize)]
pub struct DatesQuery { pub symbol: String }

#[derive(Deserialize)]
pub struct ExpiriesQuery { pub symbol: String, pub date: String }

#[derive(Deserialize)]
pub struct StrikesQuery { pub symbol: String, pub date: String, pub expiry_date: String }

pub async fn dates(
    State(state): State<AppState>,
    Query(q): Query<DatesQuery>,
) -> Result<Json<Value>, AppError> {
    validate_symbol(&q.symbol)?;

    let snaps_dir = Path::new(&state.data_dir).join(&q.symbol).join("snapshots");
    if !snaps_dir.exists() {
        return Err(AppError::NotFound(format!("no snapshots for {}", q.symbol)));
    }
    let mut dates: Vec<String> = std::fs::read_dir(&snaps_dir)?
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let p = e.path();
            if p.extension()?.to_str()? == "arrow" {
                p.file_stem()?.to_str().map(|s| s.to_string())
            } else { None }
        })
        .collect();
    dates.sort();
    let count = dates.len();
    Ok(Json(json!({ "symbol": q.symbol, "dates": dates, "count": count })))
}

pub async fn expiries(
    State(state): State<AppState>,
    Query(q): Query<ExpiriesQuery>,
) -> Result<Json<Value>, AppError> {
    validate_symbol(&q.symbol)?;
    NaiveDate::parse_from_str(&q.date, "%Y-%m-%d").map_err(|_| AppError::BadRequest("invalid date format".into()))?;

    let symbol_dir = PathBuf::from(&state.data_dir).join(&q.symbol);
    let date_str = q.date.clone();
    let symbol_str = q.symbol.clone();

    let result = tokio::task::spawn_blocking(move || -> Result<Value, AppError> {
        let expiry_map = load_expiry_map(&symbol_dir)?;
        let trade_date = NaiveDate::parse_from_str(&date_str, "%Y-%m-%d")
            .map_err(|e| AppError::BadRequest(e.to_string()))?;

        let mut exp_dates: Vec<NaiveDate> = expiry_map.values()
            .filter(|&&d| d >= trade_date)
            .copied()
            .collect();
        exp_dates.sort();
        exp_dates.dedup();

        let nearest_weekly  = exp_dates.first().map(|d| d.format("%Y-%m-%d").to_string());
        let nearest_monthly = exp_dates.iter()
            .filter(|d| d.month() == trade_date.month() && d.year() == trade_date.year())
            .last()
            .or_else(|| exp_dates.first())
            .map(|d| d.format("%Y-%m-%d").to_string());

        let dates_str: Vec<String> = exp_dates.iter().map(|d| d.format("%Y-%m-%d").to_string()).collect();
        Ok(json!({
            "symbol": symbol_str,
            "date": date_str,
            "expiries": dates_str,
            "nearest_weekly": nearest_weekly,
            "nearest_monthly": nearest_monthly,
        }))
    }).await.map_err(|e| AppError::Arrow(e.to_string()))??;

    Ok(Json(result))
}

pub async fn strikes(
    State(state): State<AppState>,
    Query(q): Query<StrikesQuery>,
) -> Result<Json<Value>, AppError> {
    use crate::engine::snapshot::Snapshot;

    validate_symbol(&q.symbol)?;
    NaiveDate::parse_from_str(&q.date, "%Y-%m-%d").map_err(|_| AppError::BadRequest("invalid date format".into()))?;

    let symbol_dir = PathBuf::from(&state.data_dir).join(&q.symbol);
    let snap_path = symbol_dir.join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() {
        return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date)));
    }

    let symbol_str = q.symbol.clone();
    let date_str = q.date.clone();
    let expiry_date_str = q.expiry_date.clone();

    let result = tokio::task::spawn_blocking(move || -> Result<Value, AppError> {
        let expiry_map = load_expiry_map(&symbol_dir)?;
        let target_date = NaiveDate::parse_from_str(&expiry_date_str, "%Y-%m-%d")
            .map_err(|e| AppError::BadRequest(e.to_string()))?;
        let expiry_idx = expiry_map.iter()
            .find(|(_, &d)| d == target_date)
            .map(|(&idx, _)| idx)
            .ok_or_else(|| AppError::NotFound(format!("expiry {} not found", expiry_date_str)))?;

        let snap = Snapshot::open(&snap_path)?;
        let e = snap.find_expiry_e(expiry_idx)
            .ok_or_else(|| AppError::NotFound("expiry not in snapshot".into()))?;

        let step = crate::engine::data_queries::strike_step(&symbol_str);
        let atm_x100 = snap.atm_x100(e, 0);
        let atm = atm_x100 as f64 / 100.0;
        let anchor_x100 = atm_x100 - 5 * step;
        let strikes: Vec<f64> = (0..11).map(|s| (anchor_x100 + s * step) as f64 / 100.0).collect();
        let step_inr = step as f64 / 100.0;

        Ok(json!({
            "symbol": symbol_str,
            "date": date_str,
            "expiry_date": expiry_date_str,
            "atm": atm,
            "step": step_inr,
            "strikes": strikes,
        }))
    }).await.map_err(|e| AppError::Arrow(e.to_string()))??;

    Ok(Json(result))
}
