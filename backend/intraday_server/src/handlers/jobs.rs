use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::Response,
};
use serde_json::json;

use crate::{
    AppState,
    arrow_out::ARROW_CONTENT_TYPE,
    cache::get_bytes,
    error::AppError,
    job_store::{self, JobStatus},
};

pub async fn poll(
    State(state): State<AppState>,
    Path(job_id): Path<String>,
) -> Result<Response, AppError> {
    let mut redis = state.redis.clone();
    let job = job_store::get_job(&mut redis, &job_id).await
        .ok_or_else(|| AppError::NotFound(format!("job {job_id} not found or expired")))?;

    match job.status {
        JobStatus::Done => {
            let bytes = get_bytes(&mut redis, &job_store::result_key(&job.cache_key)).await
                .ok_or_else(|| AppError::NotFound("result expired".into()))?;
            Ok(Response::builder()
                .header(header::CONTENT_TYPE, ARROW_CONTENT_TYPE)
                .body(Body::from(bytes)).unwrap())
        }
        JobStatus::Failed => {
            Ok(Response::builder()
                .status(StatusCode::INTERNAL_SERVER_ERROR)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"status":"failed","error":job.error}).to_string())).unwrap())
        }
        _ => {
            Ok(Response::builder()
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"status": job.status}).to_string())).unwrap())
        }
    }
}
