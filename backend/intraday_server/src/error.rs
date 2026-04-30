use axum::{http::StatusCode, response::{IntoResponse, Response}, Json};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum AppError {
    #[error("not found: {0}")]
    NotFound(String),
    #[error("bad request: {0}")]
    BadRequest(String),
    #[error("redis error: {0}")]
    Redis(#[from] redis::RedisError),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("arrow error: {0}")]
    Arrow(String),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, msg) = match &self {
            AppError::NotFound(m) => (StatusCode::NOT_FOUND, m.clone()),
            AppError::BadRequest(m) => (StatusCode::BAD_REQUEST, m.clone()),
            AppError::Redis(_) => (StatusCode::SERVICE_UNAVAILABLE, self.to_string()),
            AppError::Io(_) | AppError::Arrow(_) | AppError::Json(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, self.to_string())
            }
        };
        (status, Json(serde_json::json!({"error": msg}))).into_response()
    }
}
