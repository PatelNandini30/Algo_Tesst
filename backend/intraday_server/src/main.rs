use axum::{routing::get, Router};
use std::net::SocketAddr;

mod error;

async fn health() -> axum::Json<serde_json::Value> {
    axum::Json(serde_json::json!({"service": "intraday", "status": "ok"}))
}

pub fn create_router() -> Router {
    Router::new().route("/api/intraday/health", get(health))
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let addr = SocketAddr::from(([0, 0, 0, 0], 8001));
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    tracing::info!("intraday_server listening on {addr}");
    axum::serve(listener, create_router()).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{Request, StatusCode};
    use http_body_util::BodyExt;
    use tower::ServiceExt;
    use axum::body::Body;

    #[tokio::test]
    async fn test_health_returns_ok() {
        let app = create_router();
        let resp = app
            .oneshot(Request::builder().uri("/api/intraday/health").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["service"], "intraday");
    }
}
