use axum::{
    body::Body,
    extract::State,
    http::header,
    response::Response,
    Json,
};
use std::path::PathBuf;

use crate::{
    AppState,
    arrow_out::{trades_to_ipc, ARROW_CONTENT_TYPE},
    cache::{get_bytes, set_bytes_ex},
    engine::{engine::run_backtest, types::StrategySpec},
    error::AppError,
    job_store::{self, JobState, JobStatus},
};

const RESULT_TTL: u64 = 604800;

pub async fn submit(
    State(state): State<AppState>,
    Json(req): Json<crate::engine::types::BacktestRequest>,
) -> Result<Response, AppError> {
    req.validate().map_err(AppError::BadRequest)?;
    // canonical_key now returns Result — propagate as Json error
    let cache_key = req.canonical_key().map_err(AppError::Json)?;
    let mut redis = state.redis.clone();

    // L0: result cache hit → return Arrow IPC immediately
    if let Some(bytes) = get_bytes(&mut redis, &job_store::result_key(&cache_key)).await {
        return Ok(Response::builder()
            .header(header::CONTENT_TYPE, ARROW_CONTENT_TYPE)
            .body(Body::from(bytes)).unwrap());
    }

    // Dedup: if another identical request is in-flight, return its job_id
    let job_id = uuid::Uuid::new_v4().to_string();
    if let Some(existing_id) = job_store::try_claim_inflight(&mut redis, &cache_key, &job_id).await {
        return Ok(Response::builder()
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(serde_json::json!({"job_id": existing_id, "status": "queued"}).to_string())).unwrap());
    }

    // Store initial job state
    job_store::set_job(&mut redis, &job_id, &JobState {
        status: JobStatus::Queued,
        cache_key: cache_key.clone(),
        error: None,
        started_at: None,
    }).await;

    let slow = req.requires_slow_path();
    let data_dir = PathBuf::from(&state.data_dir);
    let job_id2 = job_id.clone();

    // Spawn background task
    tokio::spawn(async move {
        let mut redis2 = state.redis.clone();

        job_store::set_job(&mut redis2, &job_id2, &JobState {
            status: JobStatus::Running,
            cache_key: cache_key.clone(),
            error: None,
            started_at: Some(chrono::Utc::now().to_rfc3339()),
        }).await;

        let spec: Result<StrategySpec, _> = serde_json::from_value(
            serde_json::to_value(&req).unwrap_or_default()
        );
        let result = match spec {
            Ok(s) => {
                tokio::task::spawn_blocking(move || run_backtest(&s, &data_dir))
                    .await
                    .map_err(|e| AppError::Arrow(e.to_string()))
                    .and_then(|r| r)
            }
            Err(e) => Err(AppError::Json(e)),
        };

        match result {
            Ok(records) => {
                match trades_to_ipc(&records) {
                    Ok(bytes) => {
                        set_bytes_ex(&mut redis2, &job_store::result_key(&cache_key), &bytes, RESULT_TTL).await;
                        job_store::set_job(&mut redis2, &job_id2, &JobState {
                            status: JobStatus::Done,
                            cache_key,
                            error: None,
                            started_at: None,
                        }).await;
                    }
                    Err(e) => {
                        job_store::set_job(&mut redis2, &job_id2, &JobState {
                            status: JobStatus::Failed,
                            cache_key,
                            error: Some(e.to_string()),
                            started_at: None,
                        }).await;
                    }
                }
            }
            Err(e) => {
                job_store::set_job(&mut redis2, &job_id2, &JobState {
                    status: JobStatus::Failed,
                    cache_key,
                    error: Some(e.to_string()),
                    started_at: None,
                }).await;
            }
        }
    });

    let mut builder = Response::builder().header(header::CONTENT_TYPE, "application/json");
    if slow { builder = builder.header("X-Slow-Path", "true"); }
    Ok(builder.body(Body::from(serde_json::json!({"job_id": job_id, "status": "queued"}).to_string())).unwrap())
}
