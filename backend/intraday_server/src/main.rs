use axum::{routing::{get, post}, Router};
use redis::aio::ConnectionManager;
use std::net::SocketAddr;

mod arrow_out;
mod cache;
mod engine;
mod error;
mod handlers;
mod job_store;

#[derive(Clone)]
pub struct AppState {
    pub redis: ConnectionManager,
    pub data_dir: String,
}

pub fn create_router(state: AppState) -> Router {
    Router::new()
        .route("/api/intraday/health",          get(handlers::health::handler))
        .route("/api/intraday/meta/dates",      get(handlers::meta::dates))
        .route("/api/intraday/meta/expiries",   get(handlers::meta::expiries))
        .route("/api/intraday/meta/strikes",    get(handlers::meta::strikes))
        .route("/api/intraday/data/spot",       get(handlers::data::spot))
        .route("/api/intraday/data/ohlcv",      get(handlers::data::ohlcv))
        .route("/api/intraday/data/chain",      get(handlers::data::chain))
        .route("/api/intraday/data/series",     get(handlers::data::series))
        .route("/api/intraday/backtest",        post(handlers::backtest::submit))
        .route("/api/intraday/jobs/:job_id",    get(handlers::jobs::poll))
        .with_state(state)
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let redis_url = std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379/0".into());
    let data_dir  = std::env::var("INTRADAY_DATA_DIR").unwrap_or_else(|_| "/data/intraday".into());

    let client = redis::Client::open(redis_url).expect("invalid REDIS_URL");
    let redis  = ConnectionManager::new(client).await.expect("cannot connect to Redis");

    let state = AppState { redis, data_dir };
    let addr  = SocketAddr::from(([0, 0, 0, 0], 8001));
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    tracing::info!("intraday_server listening on {addr}");
    axum::serve(listener, create_router(state)).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    async fn test_state() -> AppState {
        let redis_url = std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379".into());
        let client = redis::Client::open(redis_url).unwrap();
        let redis = ConnectionManager::new(client).await
            .unwrap_or_else(|_| panic!("Redis required for integration tests"));
        AppState { redis, data_dir: "/tmp/intraday_test".into() }
    }

    #[tokio::test]
    async fn test_health_route() {
        let Ok(state) = tokio::time::timeout(
            std::time::Duration::from_millis(200),
            async { test_state().await },
        ).await else { return; };  // skip if Redis not available

        let app = create_router(state);
        let resp = app.oneshot(
            Request::builder().uri("/api/intraday/health").body(Body::empty()).unwrap()
        ).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["service"], "intraday");
    }

    #[tokio::test]
    async fn test_unknown_route_404() {
        let Ok(state) = tokio::time::timeout(
            std::time::Duration::from_millis(200),
            async { test_state().await },
        ).await else { return; };

        let app = create_router(state);
        let resp = app.oneshot(
            Request::builder().uri("/api/intraday/doesnotexist").body(Body::empty()).unwrap()
        ).await.unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }
}
