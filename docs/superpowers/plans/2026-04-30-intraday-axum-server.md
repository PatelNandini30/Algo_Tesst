# Intraday Axum Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Rust/Axum HTTP service at `:8001` that serves all intraday backtest and market data APIs, reading directly from DaySnapshot binary files via mmap.

**Architecture:** A new `backend/intraday_server/` Rust binary. Engine code (snapshot reader, backtest loop, data queries) lives in `src/engine/`. HTTP handlers in `src/handlers/`. Arrow IPC output, Redis cache, and job store are thin modules. nginx routes `/api/intraday/*` to this service; EOD Python backend is untouched.

**Tech Stack:** Rust 1.78, Axum 0.7, Tokio, arrow-rs 52, redis-rs 0.25, memmap2, chrono, serde_json.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/intraday_server/Cargo.toml` | workspace manifest |
| Create | `backend/intraday_server/src/main.rs` | Axum router, bind :8001, AppState |
| Create | `backend/intraday_server/src/error.rs` | AppError → HTTP response |
| Create | `backend/intraday_server/src/engine/mod.rs` | module re-exports |
| Create | `backend/intraday_server/src/engine/types.rs` | all domain types |
| Create | `backend/intraday_server/src/engine/snapshot.rs` | DaySnapshot mmap reader |
| Create | `backend/intraday_server/src/engine/data_queries.rs` | spot/ohlcv/chain/series extraction |
| Create | `backend/intraday_server/src/engine/engine.rs` | per-day backtest loop |
| Create | `backend/intraday_server/src/arrow_out.rs` | Arrow IPC serialisation |
| Create | `backend/intraday_server/src/cache.rs` | redis-rs get/set helpers |
| Create | `backend/intraday_server/src/job_store.rs` | Redis-backed job lifecycle |
| Create | `backend/intraday_server/src/handlers/mod.rs` | handler re-exports |
| Create | `backend/intraday_server/src/handlers/health.rs` | GET /health |
| Create | `backend/intraday_server/src/handlers/meta.rs` | GET /meta/* |
| Create | `backend/intraday_server/src/handlers/data.rs` | GET /data/* |
| Create | `backend/intraday_server/src/handlers/backtest.rs` | POST /backtest |
| Create | `backend/intraday_server/src/handlers/jobs.rs` | GET /jobs/{id} |
| Create | `backend/intraday_server/Dockerfile` | multi-stage Rust build |
| Modify | `docker-compose.yml` | add intraday-api service |
| Modify | `frontend/nginx.conf` | route /api/intraday/ to :8001 |

---

### Task 1: Cargo project skeleton + health endpoint compiles

**Files:**
- Create: `backend/intraday_server/Cargo.toml`
- Create: `backend/intraday_server/src/main.rs`

- [ ] **Step 1: Create `backend/intraday_server/Cargo.toml`**

```toml
[package]
name = "intraday_server"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "intraday_server"
path = "src/main.rs"

[dependencies]
axum = { version = "0.7", features = ["macros"] }
tokio = { version = "1", features = ["full"] }
tower-http = { version = "0.5", features = ["cors", "trace"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
arrow-array = "52"
arrow-ipc = "52"
arrow-schema = "52"
arrow-buffer = "52"
memmap2 = "0.9"
chrono = { version = "0.4", features = ["clock", "serde"] }
redis = { version = "0.25", features = ["tokio-comp"] }
uuid = { version = "1", features = ["v4"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
thiserror = "1"
once_cell = "1.19"

[dev-dependencies]
tower = { version = "0.4", features = ["util"] }
http-body-util = "0.1"
```

- [ ] **Step 2: Create `backend/intraday_server/src/main.rs`** (minimal — just health)

```rust
use axum::{routing::get, Router};
use std::net::SocketAddr;

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
    use http::{Request, StatusCode};
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
```

- [ ] **Step 3: Verify it compiles and test passes**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test 2>&1 | tail -10
```
Expected: `test tests::test_health_returns_ok ... ok`

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/
git commit -m "feat(intraday-server): project skeleton, minimal health endpoint"
```

---

### Task 2: Error type

**Files:**
- Create: `backend/intraday_server/src/error.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/error.rs`**

```rust
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
```

- [ ] **Step 2: Add `mod error;` to `src/main.rs`**

Add after the `use axum` imports:
```rust
mod error;
```

- [ ] **Step 3: Verify compile**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo check 2>&1 | grep "^error" | head -5
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/error.rs backend/intraday_server/src/main.rs
git commit -m "feat(intraday-server): AppError with HTTP status mapping"
```

---

### Task 3: Engine domain types

**Files:**
- Create: `backend/intraday_server/src/engine/mod.rs`
- Create: `backend/intraday_server/src/engine/types.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/engine/mod.rs`**

```rust
pub mod data_queries;
pub mod engine;
pub mod snapshot;
pub mod types;
```

- [ ] **Step 2: Create `backend/intraday_server/src/engine/types.rs`**

```rust
use serde::{Deserialize, Serialize};

// ── Strategy config (inbound from API) ────────────────────────────────────

#[derive(Deserialize, Debug, Clone)]
pub struct StrategySpec {
    pub symbol: String,
    pub date_from: String,
    pub date_to: String,
    pub entry_time: String,
    pub square_off_time: String,
    pub legs: Vec<LegSpec>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct LegSpec {
    pub opt_type: String,
    pub action: String,
    pub strike_selection: StrikeSelection,
    pub expiry: String,
    pub quantity: u32,
    pub sl: Option<ExitCond>,
    pub target: Option<ExitCond>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct StrikeSelection {
    pub mode: String,
    pub value: i32,
}

#[derive(Deserialize, Debug, Clone)]
pub struct ExitCond {
    #[serde(rename = "type")]
    pub kind: String,
    pub value: f64,
}

// ── Output records ────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct TradeRecord {
    pub date: String,
    pub symbol: String,
    pub expiry: String,
    pub strike: f64,
    pub opt_type: String,
    pub action: String,
    pub entry_time: String,
    pub entry_price: f64,
    pub exit_time: String,
    pub exit_price: f64,
    pub exit_reason: String,
    pub quantity: u32,
    pub pnl: f64,
    pub mae: f64,
    pub mfe: f64,
}

#[derive(Debug, Clone)]
pub struct OhlcvBar {
    pub minute: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

#[derive(Debug, Clone)]
pub struct ChainRow {
    pub strike: f64,
    pub ce_close: f64,
    pub ce_high: f64,
    pub ce_low: f64,
    pub ce_volume: i64,
    pub pe_close: f64,
    pub pe_high: f64,
    pub pe_low: f64,
    pub pe_volume: i64,
}

#[derive(Debug, Clone)]
pub struct SeriesBar {
    pub date: String,
    pub minute: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
}

// ── Query enums ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum OptType { Ce, Pe }

impl OptType {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "CE" => Some(Self::Ce),
            "PE" => Some(Self::Pe),
            _ => None,
        }
    }
    pub fn chain_idx(self) -> usize { match self { Self::Ce => 0, Self::Pe => 1 } }
}

#[derive(Debug, Clone, Copy)]
pub enum ExpiryMode { Weekly, Monthly }

impl ExpiryMode {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "WEEKLY" => Some(Self::Weekly),
            "MONTHLY" => Some(Self::Monthly),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub enum Resolution { M1, M5, M15, D1 }

impl Resolution {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "1m" => Some(Self::M1),
            "5m" => Some(Self::M5),
            "15m" => Some(Self::M15),
            "1d" => Some(Self::D1),
            _ => None,
        }
    }
    pub fn minutes(self) -> usize {
        match self { Self::M1 => 1, Self::M5 => 5, Self::M15 => 15, Self::D1 => 375 }
    }
}

// ── Backtest job request (inbound from API) ───────────────────────────────

#[derive(Deserialize, Serialize, Debug, Clone)]
pub struct BacktestRequest {
    pub symbol: String,
    pub date_from: String,
    pub date_to: String,
    pub entry_time: String,
    pub square_off_time: String,
    pub legs: Vec<serde_json::Value>,
}

impl BacktestRequest {
    pub fn canonical_key(&self) -> String {
        let payload = serde_json::to_string(self).unwrap_or_default();
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        payload.hash(&mut h);
        format!("{:016x}", h.finish())
    }

    pub fn requires_slow_path(&self) -> bool {
        self.legs.iter().any(|leg| {
            leg.get("strike_selection")
                .and_then(|ss| ss.get("value"))
                .and_then(|v| v.as_i64())
                .map(|v| v.abs() > 5)
                .unwrap_or(false)
        })
    }

    pub fn validate(&self) -> Result<(), String> {
        let valid_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
        if !valid_symbols.contains(&self.symbol.as_str()) {
            return Err(format!("symbol must be one of {:?}", valid_symbols));
        }
        if self.legs.is_empty() { return Err("at least 1 leg required".into()); }
        if self.legs.len() > 6 { return Err("at most 6 legs allowed".into()); }
        Ok(())
    }
}
```

- [ ] **Step 3: Add `mod engine;` to `src/main.rs`**

```rust
mod engine;
```

- [ ] **Step 4: Verify compile**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo check 2>&1 | grep "^error" | head -5
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/intraday_server/src/engine/
git commit -m "feat(intraday-server): engine domain types"
```

---

### Task 4: DaySnapshot binary reader

**Files:**
- Create: `backend/intraday_server/src/engine/snapshot.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/engine/snapshot.rs`**

```rust
use memmap2::Mmap;
use std::fs::File;
use std::path::Path;

pub const MINUTES: usize = 375;
const HEADER_SIZE: usize = 32;
const SPOT_ENTRY: usize = 16;
pub const SPOT_SIZE: usize = MINUTES * SPOT_ENTRY;
pub const CHAIN_STRIKES: usize = 11;
pub const CHAIN_TYPES: usize = 2;
pub const CHAIN_FIELDS: usize = 4;
pub const EXPIRY_SIZE: usize =
    2 + MINUTES * 4 + CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES * 4;

pub struct Snapshot {
    mmap: Mmap,
    pub expiry_count: usize,
    pub date_days: i32,
    pub symbol: String,
    pub minute_count: usize,
}

impl Snapshot {
    pub fn open(path: &Path) -> std::io::Result<Self> {
        let file = File::open(path)?;
        let mmap = unsafe { Mmap::map(&file)? };
        if mmap.len() < HEADER_SIZE {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "file too small"));
        }
        if &mmap[0..4] != b"ITDS" {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "bad magic"));
        }
        let symbol = std::str::from_utf8(&mmap[5..21])
            .unwrap_or("")
            .trim_end_matches('\0')
            .to_string();
        let date_days = i32::from_le_bytes(mmap[21..25].try_into().unwrap());
        let expiry_count = mmap[25] as usize;
        let minute_count = u16::from_le_bytes(mmap[26..28].try_into().unwrap()) as usize;
        Ok(Snapshot { mmap, expiry_count, date_days, symbol, minute_count })
    }

    fn expiry_base(&self, e: usize) -> usize {
        HEADER_SIZE + SPOT_SIZE + e * EXPIRY_SIZE
    }

    pub fn spot_open_x100(&self, m: usize) -> i32 {
        let off = HEADER_SIZE + m * SPOT_ENTRY;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }
    pub fn spot_high_x100(&self, m: usize) -> i32 {
        let off = HEADER_SIZE + m * SPOT_ENTRY + 4;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }
    pub fn spot_low_x100(&self, m: usize) -> i32 {
        let off = HEADER_SIZE + m * SPOT_ENTRY + 8;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }
    pub fn spot_close_x100(&self, m: usize) -> i32 {
        let off = HEADER_SIZE + m * SPOT_ENTRY + 12;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }

    pub fn expiry_idx(&self, e: usize) -> i16 {
        let off = self.expiry_base(e);
        i16::from_le_bytes(self.mmap[off..off+2].try_into().unwrap())
    }

    pub fn atm_x100(&self, e: usize, m: usize) -> i32 {
        let off = self.expiry_base(e) + 2 + m * 4;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }

    /// field: 0=close 1=high 2=low 3=volume
    pub fn chain_val(&self, e: usize, s: usize, t: usize, field: usize, m: usize) -> i32 {
        let chain_off = self.expiry_base(e) + 2 + MINUTES * 4;
        let idx = s * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
            + t * CHAIN_FIELDS * MINUTES
            + field * MINUTES
            + m;
        let off = chain_off + idx * 4;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }

    /// Find the e index (0..expiry_count) matching a given i16 expiry_idx.
    pub fn find_expiry_e(&self, target_idx: i16) -> Option<usize> {
        (0..self.expiry_count).find(|&e| self.expiry_idx(e) == target_idx)
    }
}

#[cfg(test)]
pub mod test_helpers {
    use super::*;
    use std::io::Write;

    /// Build a minimal valid DaySnapshot in memory for testing.
    pub fn synthetic_snapshot(
        date_str: &str,
        atm_x100: i32,
        entry_close_x100: i32,
        later_close_x100: i32,
        entry_minute: usize,
    ) -> Vec<u8> {
        use chrono::NaiveDate;
        let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
        let d = NaiveDate::parse_from_str(date_str, "%Y-%m-%d").unwrap();
        let date_days = (d - epoch).num_days() as i32;

        let mut buf = Vec::new();
        // Header 32 bytes
        buf.extend_from_slice(b"ITDS");
        buf.push(1); // version
        let sym = b"NIFTY\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00";
        buf.extend_from_slice(sym); // 16 bytes
        buf.extend_from_slice(&date_days.to_le_bytes());
        buf.push(1); // expiry_count
        buf.extend_from_slice(&(MINUTES as u16).to_le_bytes());
        buf.extend_from_slice(&[0u8; 4]); // padding
        assert_eq!(buf.len(), HEADER_SIZE);

        // SPOT: all bars = atm_x100
        for _ in 0..MINUTES {
            for _ in 0..4 {
                buf.extend_from_slice(&atm_x100.to_le_bytes());
            }
        }
        assert_eq!(buf.len(), HEADER_SIZE + SPOT_SIZE);

        // Expiry section
        buf.extend_from_slice(&0i16.to_le_bytes()); // expiry_idx = 0
        // ATM array
        for _ in 0..MINUTES { buf.extend_from_slice(&atm_x100.to_le_bytes()); }
        // Chain[11][2][4][375]: default = 100 (1.00 INR)
        let chain_size = CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES;
        let mut chain = vec![100i32; chain_size];
        // Set s=5 (ATM), t=0 (CE), field=0..2 (close/high/low)
        for m in 0..MINUTES {
            let px = if m <= entry_minute { entry_close_x100 } else { later_close_x100 };
            for field in 0..3 {
                let idx = 5 * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
                    + 0 * CHAIN_FIELDS * MINUTES
                    + field * MINUTES
                    + m;
                chain[idx] = px;
            }
        }
        for v in &chain { buf.extend_from_slice(&v.to_le_bytes()); }
        assert_eq!(buf.len(), HEADER_SIZE + SPOT_SIZE + EXPIRY_SIZE);
        buf
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_open_synthetic() {
        let bytes = test_helpers::synthetic_snapshot("2024-01-01", 2400000, 20000, 10000, 5);
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();
        let snap = Snapshot::open(f.path()).unwrap();
        assert_eq!(snap.symbol, "NIFTY");
        assert_eq!(snap.expiry_count, 1);
        assert_eq!(snap.minute_count, MINUTES);
        assert_eq!(snap.atm_x100(0, 0), 2400000);
        assert_eq!(snap.spot_close_x100(0), 2400000);
        // CE chain at s=5, entry minute = 5
        assert_eq!(snap.chain_val(0, 5, 0, 0, 5), 20000);
        assert_eq!(snap.chain_val(0, 5, 0, 0, 10), 10000);
    }

    #[test]
    fn test_bad_magic() {
        let mut bytes = vec![0u8; 100];
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();
        let result = Snapshot::open(f.path());
        assert!(result.is_err());
    }
}
```

- [ ] **Step 2: Add `tempfile` to dev-dependencies in Cargo.toml**

```toml
[dev-dependencies]
tower = { version = "0.4", features = ["util"] }
http-body-util = "0.1"
tempfile = "3"
```

- [ ] **Step 3: Run tests**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test engine::snapshot 2>&1 | tail -10
```
Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/
git commit -m "feat(intraday-server): DaySnapshot mmap reader + test helpers"
```

---

### Task 5: Data queries — spot, OHLCV, chain (single day)

**Files:**
- Create: `backend/intraday_server/src/engine/data_queries.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/engine/data_queries.rs`**

```rust
use crate::engine::snapshot::{Snapshot, MINUTES};
use crate::engine::types::{ChainRow, OhlcvBar, OptType};
use crate::error::AppError;

const SESSION_START: u32 = 9 * 60 + 15;

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

pub fn time_to_idx(hhmm: &str) -> usize {
    let parts: Vec<u32> = hhmm.splitn(2, ':')
        .map(|s| s.parse().unwrap_or(0))
        .collect();
    let abs_min = parts[0] * 60 + *parts.get(1).unwrap_or(&0);
    (abs_min.saturating_sub(SESSION_START)) as usize
}

pub fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY" => 10000,
        "MIDCPNIFTY" => 2500,
        _ => 5000,
    }
}

/// Extract full spot OHLCV for all minutes of the day.
pub fn spot_series(snap: &Snapshot) -> Vec<OhlcvBar> {
    (0..snap.minute_count)
        .map(|m| OhlcvBar {
            minute: idx_to_time(m),
            open: snap.spot_open_x100(m) as f64 / 100.0,
            high: snap.spot_high_x100(m) as f64 / 100.0,
            low: snap.spot_low_x100(m) as f64 / 100.0,
            close: snap.spot_close_x100(m) as f64 / 100.0,
            volume: 0,
        })
        .collect()
}

/// Extract OHLCV for one option contract (identified by expiry_idx + strike + opt_type).
pub fn ohlcv_series(
    snap: &Snapshot,
    expiry_idx: i16,
    strike_x100: i32,
    opt_type: OptType,
) -> Result<Vec<OhlcvBar>, AppError> {
    let e = snap
        .find_expiry_e(expiry_idx)
        .ok_or_else(|| AppError::NotFound(format!("expiry_idx {expiry_idx} not in snapshot")))?;

    let step = strike_step(&snap.symbol);
    let anchor = snap.atm_x100(e, 0) - 5 * step;
    let s_raw = (strike_x100 - anchor) / step;
    if s_raw < 0 || s_raw >= 11 {
        return Err(AppError::BadRequest(format!(
            "strike {} is outside ATM±5 chain range for this day",
            strike_x100 as f64 / 100.0
        )));
    }
    let s = s_raw as usize;
    let t = opt_type.chain_idx();

    let bars = (0..snap.minute_count)
        .map(|m| OhlcvBar {
            minute: idx_to_time(m),
            open: snap.chain_val(e, s, t, 0, m) as f64 / 100.0,
            high: snap.chain_val(e, s, t, 1, m) as f64 / 100.0,
            low: snap.chain_val(e, s, t, 2, m) as f64 / 100.0,
            close: snap.chain_val(e, s, t, 0, m) as f64 / 100.0,
            volume: snap.chain_val(e, s, t, 3, m) as i64,
        })
        .collect();
    Ok(bars)
}

/// Extract the full option chain at a single minute.
pub fn chain_snapshot(
    snap: &Snapshot,
    expiry_idx: i16,
    minute_idx: usize,
) -> Result<Vec<ChainRow>, AppError> {
    let e = snap
        .find_expiry_e(expiry_idx)
        .ok_or_else(|| AppError::NotFound(format!("expiry_idx {expiry_idx} not in snapshot")))?;

    let step = strike_step(&snap.symbol);
    let anchor = snap.atm_x100(e, 0) - 5 * step;

    let rows = (0..11usize)
        .map(|s| {
            let strike = (anchor + s as i32 * step) as f64 / 100.0;
            ChainRow {
                strike,
                ce_close: snap.chain_val(e, s, 0, 0, minute_idx) as f64 / 100.0,
                ce_high:  snap.chain_val(e, s, 0, 1, minute_idx) as f64 / 100.0,
                ce_low:   snap.chain_val(e, s, 0, 2, minute_idx) as f64 / 100.0,
                ce_volume: snap.chain_val(e, s, 0, 3, minute_idx) as i64,
                pe_close: snap.chain_val(e, s, 1, 0, minute_idx) as f64 / 100.0,
                pe_high:  snap.chain_val(e, s, 1, 1, minute_idx) as f64 / 100.0,
                pe_low:   snap.chain_val(e, s, 1, 2, minute_idx) as f64 / 100.0,
                pe_volume: snap.chain_val(e, s, 1, 3, minute_idx) as i64,
            }
        })
        .collect();
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::snapshot::test_helpers::synthetic_snapshot;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn make_snap() -> (Snapshot, NamedTempFile) {
        let bytes = synthetic_snapshot("2024-01-01", 2400000, 20000, 10000, 5);
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();
        let snap = Snapshot::open(f.path()).unwrap();
        (snap, f)
    }

    #[test]
    fn test_spot_series_length() {
        let (snap, _f) = make_snap();
        let bars = spot_series(&snap);
        assert_eq!(bars.len(), MINUTES);
        assert_eq!(bars[0].minute, "09:15");
        assert_eq!(bars[374].minute, "15:29");
    }

    #[test]
    fn test_spot_series_values() {
        let (snap, _f) = make_snap();
        let bars = spot_series(&snap);
        assert!((bars[0].close - 24000.0).abs() < 0.01);
    }

    #[test]
    fn test_ohlcv_series_atm_ce() {
        let (snap, _f) = make_snap();
        // ATM = 24000, anchor = 24000 - 5*50 = 23750, s=5 → strike_x100 = 23750*100 + 5*5000 = 2400000
        let bars = ohlcv_series(&snap, 0, 2400000, OptType::Ce).unwrap();
        assert_eq!(bars.len(), MINUTES);
        // minute 5 (entry) should be entry_close = 200.00
        assert!((bars[5].close - 200.0).abs() < 0.01);
        // minute 10 should be later_close = 100.00
        assert!((bars[10].close - 100.0).abs() < 0.01);
    }

    #[test]
    fn test_ohlcv_series_bad_expiry() {
        let (snap, _f) = make_snap();
        let result = ohlcv_series(&snap, 99, 2400000, OptType::Ce);
        assert!(matches!(result, Err(crate::error::AppError::NotFound(_))));
    }

    #[test]
    fn test_chain_snapshot_11_rows() {
        let (snap, _f) = make_snap();
        let rows = chain_snapshot(&snap, 0, 5).unwrap();
        assert_eq!(rows.len(), 11);
        // middle row (s=5) should have ce_close = entry_close = 200.0
        assert!((rows[5].ce_close - 200.0).abs() < 0.01);
    }

    #[test]
    fn test_time_to_idx() {
        assert_eq!(time_to_idx("09:15"), 0);
        assert_eq!(time_to_idx("09:20"), 5);
        assert_eq!(time_to_idx("15:29"), 374);
    }
}
```

- [ ] **Step 2: Run tests**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test engine::data_queries 2>&1 | tail -15
```
Expected: all 6 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/intraday_server/src/engine/data_queries.rs
git commit -m "feat(intraday-server): spot/ohlcv/chain data queries from DaySnapshot"
```

---

### Task 6: Data queries — multi-day series

**Files:**
- Modify: `backend/intraday_server/src/engine/data_queries.rs`

- [ ] **Step 1: Add multi_day_series to `data_queries.rs`**

Add these imports at the top of `data_queries.rs`:
```rust
use crate::engine::types::{ExpiryMode, Resolution, SeriesBar};
use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::Path;
```

Add these functions after `chain_snapshot`:

```rust
/// Load expiries.json → HashMap<i16 expiry_idx, NaiveDate>
pub fn load_expiry_map(symbol_dir: &Path) -> std::io::Result<HashMap<i16, NaiveDate>> {
    let text = std::fs::read_to_string(symbol_dir.join("expiries.json"))?;
    let raw: HashMap<String, String> = serde_json::from_str(&text)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string()))?;
    let map = raw.into_iter().filter_map(|(k, v)| {
        let idx = k.parse::<i16>().ok()?;
        let date = NaiveDate::parse_from_str(&v, "%Y-%m-%d").ok()?;
        Some((idx, date))
    }).collect();
    Ok(map)
}

/// Pick best expiry_idx for a given trade_date and expiry mode.
/// WEEKLY → nearest expiry_date >= trade_date.
/// MONTHLY → nearest expiry_date >= trade_date that is in the same or next month.
pub fn pick_expiry_idx(
    trade_date: NaiveDate,
    expiry_mode: ExpiryMode,
    expiry_map: &HashMap<i16, NaiveDate>,
) -> Option<i16> {
    let mut candidates: Vec<(i16, NaiveDate)> = expiry_map
        .iter()
        .filter(|(_, &d)| d >= trade_date)
        .map(|(&idx, &d)| (idx, d))
        .collect();
    candidates.sort_by_key(|&(_, d)| d);

    match expiry_mode {
        ExpiryMode::Weekly => candidates.first().map(|&(idx, _)| idx),
        ExpiryMode::Monthly => {
            // Find the last expiry of the current month; if none, pick first available.
            let month_end = candidates.iter()
                .filter(|(_, d)| d.month() == trade_date.month() && d.year() == trade_date.year())
                .last();
            month_end.or(candidates.first()).map(|&(idx, _)| idx)
        }
    }
}

/// Downsample bars at the given resolution (bucket by resolution windows).
fn downsample(bars: Vec<OhlcvBar>, resolution: Resolution) -> Vec<OhlcvBar> {
    let bucket = resolution.minutes();
    if bucket <= 1 { return bars; }
    bars.chunks(bucket).map(|chunk| {
        let open  = chunk[0].open;
        let high  = chunk.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
        let low   = chunk.iter().map(|b| b.low).fold(f64::INFINITY, f64::min);
        let close = chunk.last().unwrap().close;
        OhlcvBar { minute: chunk[0].minute.clone(), open, high, low, close, volume: 0 }
    }).collect()
}

/// Multi-day OHLCV series for one option across a date range.
pub fn multi_day_series(
    symbol_dir: &Path,
    date_from: NaiveDate,
    date_to: NaiveDate,
    strike_x100: i32,
    opt_type: OptType,
    expiry_mode: ExpiryMode,
    resolution: Resolution,
) -> Result<Vec<SeriesBar>, AppError> {
    let expiry_map = load_expiry_map(symbol_dir)
        .map_err(|e| AppError::Io(e))?;
    let snaps_dir = symbol_dir.join("snapshots");
    let mut result = Vec::new();
    let mut current = date_from;

    while current <= date_to {
        let date_str = current.format("%Y-%m-%d").to_string();
        let snap_path = snaps_dir.join(format!("{date_str}.arrow"));

        if snap_path.exists() {
            if let Some(expiry_idx) = pick_expiry_idx(current, expiry_mode, &expiry_map) {
                match Snapshot::open(&snap_path) {
                    Ok(snap) => {
                        match ohlcv_series(&snap, expiry_idx, strike_x100, opt_type) {
                            Ok(bars) => {
                                for bar in downsample(bars, resolution) {
                                    result.push(SeriesBar {
                                        date: date_str.clone(),
                                        minute: bar.minute,
                                        open: bar.open,
                                        high: bar.high,
                                        low: bar.low,
                                        close: bar.close,
                                    });
                                }
                            }
                            Err(_) => {} // skip days where strike is outside chain
                        }
                    }
                    Err(e) => tracing::warn!("skip {date_str}: {e}"),
                }
            }
        }

        current = current.succ_opt().unwrap_or(current);
    }

    Ok(result)
}
```

- [ ] **Step 2: Add multi-day tests to `data_queries.rs`**

```rust
#[cfg(test)]
mod multi_day_tests {
    use super::*;
    use crate::engine::snapshot::test_helpers::synthetic_snapshot;
    use std::io::Write;
    use tempfile::TempDir;

    fn setup_two_day_dir() -> TempDir {
        let dir = TempDir::new().unwrap();
        let sym_dir = dir.path().join("NIFTY");
        std::fs::create_dir_all(sym_dir.join("snapshots")).unwrap();
        // expiries.json: idx 0 → "2024-01-04" (Thursday)
        std::fs::write(
            sym_dir.join("expiries.json"),
            r#"{"0": "2024-01-04"}"#,
        ).unwrap();
        // Write two snapshot files
        for (date, entry_px, later_px) in [
            ("2024-01-02", 20000, 10000),
            ("2024-01-03", 15000, 8000),
        ] {
            let bytes = synthetic_snapshot(date, 2400000, entry_px, later_px, 5);
            let path = sym_dir.join("snapshots").join(format!("{date}.arrow"));
            std::fs::write(path, bytes).unwrap();
        }
        dir
    }

    #[test]
    fn test_multi_day_series_two_days_1m() {
        let dir = setup_two_day_dir();
        let sym_dir = dir.path().join("NIFTY");
        let from = NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        let to   = NaiveDate::from_ymd_opt(2024, 1, 3).unwrap();
        let bars = multi_day_series(
            &sym_dir, from, to, 2400000, OptType::Ce,
            ExpiryMode::Weekly, Resolution::M1,
        ).unwrap();
        assert_eq!(bars.len(), 375 * 2);
        assert_eq!(bars[0].date, "2024-01-02");
        assert_eq!(bars[375].date, "2024-01-03");
    }

    #[test]
    fn test_multi_day_series_5m_downsamples() {
        let dir = setup_two_day_dir();
        let sym_dir = dir.path().join("NIFTY");
        let from = NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        let to   = NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        let bars = multi_day_series(
            &sym_dir, from, to, 2400000, OptType::Ce,
            ExpiryMode::Weekly, Resolution::M5,
        ).unwrap();
        assert_eq!(bars.len(), 375 / 5);
    }
}
```

- [ ] **Step 3: Run all data_queries tests**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test engine::data_queries 2>&1 | tail -15
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/engine/data_queries.rs
git commit -m "feat(intraday-server): multi-day series query with downsampling"
```

---

### Task 7: Per-day backtest engine

**Files:**
- Create: `backend/intraday_server/src/engine/engine.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/engine/engine.rs`**

```rust
use crate::engine::data_queries::{load_expiry_map, pick_expiry_idx, strike_step, time_to_idx};
use crate::engine::snapshot::Snapshot;
use crate::engine::types::{ExpiryMode, LegSpec, StrategySpec, TradeRecord};
use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const SESSION_START: u32 = 9 * 60 + 15;

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

fn compute_thresholds(leg: &LegSpec, entry_x100: i32) -> (Option<i32>, Option<i32>) {
    let sl = leg.sl.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 + delta } else { entry_x100 - delta }
    });
    let tgt = leg.target.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 - delta } else { entry_x100 + delta }
    });
    (sl, tgt)
}

fn mae_mfe(snap: &Snapshot, e: usize, s: usize, t: usize, entry_idx: usize, exit_idx: usize, is_sell: bool) -> (f64, f64) {
    let ep = snap.chain_val(e, s, t, 0, entry_idx) as f64;
    let (mut min_px, mut max_px) = (ep, ep);
    for m in (entry_idx + 1)..=exit_idx {
        let lo = snap.chain_val(e, s, t, 2, m) as f64;
        let hi = snap.chain_val(e, s, t, 1, m) as f64;
        if lo < min_px { min_px = lo; }
        if hi > max_px { max_px = hi; }
    }
    if is_sell {
        ((max_px - ep) / 100.0, (ep - min_px) / 100.0)
    } else {
        ((ep - min_px) / 100.0, (max_px - ep) / 100.0)
    }
}

fn run_day(
    snap: &Snapshot,
    expiry_map: &HashMap<i16, NaiveDate>,
    spec: &StrategySpec,
    date_str: &str,
    trade_date: NaiveDate,
) -> Vec<TradeRecord> {
    let step = strike_step(&spec.symbol);
    let entry_idx = time_to_idx(&spec.entry_time).min(snap.minute_count - 1);
    let sqoff_idx = time_to_idx(&spec.square_off_time).min(snap.minute_count - 1);

    let mut records = Vec::new();
    for leg in &spec.legs {
        let expiry_mode = match leg.expiry.as_str() {
            "MONTHLY" | "NEXT_MONTHLY" => ExpiryMode::Monthly,
            _ => ExpiryMode::Weekly,
        };
        let expiry_idx = match pick_expiry_idx(trade_date, expiry_mode, expiry_map) {
            Some(idx) => idx,
            None => continue,
        };
        let e = match snap.find_expiry_e(expiry_idx) {
            Some(e) => e,
            None => continue,
        };

        let atm = snap.atm_x100(e, entry_idx);
        let strike_x100 = atm + leg.strike_selection.value * step;
        let anchor = snap.atm_x100(e, 0) - 5 * step;
        let s_raw = (strike_x100 - anchor) / step;
        if s_raw < 0 || s_raw >= 11 { continue; }
        let s = s_raw as usize;
        let t: usize = if leg.opt_type == "CE" { 0 } else { 1 };

        let entry_px = snap.chain_val(e, s, t, 0, entry_idx);
        if entry_px <= 0 { continue; }

        let (sl_thr, tgt_thr) = compute_thresholds(leg, entry_px);
        let is_sell = leg.action == "SELL";

        let mut exit_idx = sqoff_idx;
        let mut exit_reason = "SQOFF";

        for m in (entry_idx + 1)..=sqoff_idx {
            let px = snap.chain_val(e, s, t, 0, m);
            let hit_sl  = sl_thr.map_or(false, |thr| if is_sell { px >= thr } else { px <= thr });
            let hit_tgt = tgt_thr.map_or(false, |thr| if is_sell { px <= thr } else { px >= thr });
            if hit_sl  { exit_idx = m; exit_reason = "SL";     break; }
            if hit_tgt { exit_idx = m; exit_reason = "TARGET"; break; }
        }

        let exit_px = snap.chain_val(e, s, t, 0, exit_idx);
        let (mae, mfe) = mae_mfe(snap, e, s, t, entry_idx, exit_idx, is_sell);
        let pnl = if is_sell {
            (entry_px - exit_px) as f64 / 100.0
        } else {
            (exit_px - entry_px) as f64 / 100.0
        } * leg.quantity as f64;

        let expiry_date_str = expiry_map.get(&expiry_idx)
            .map(|d| d.format("%Y-%m-%d").to_string())
            .unwrap_or_default();

        records.push(TradeRecord {
            date: date_str.to_string(),
            symbol: spec.symbol.clone(),
            expiry: expiry_date_str,
            strike: strike_x100 as f64 / 100.0,
            opt_type: leg.opt_type.clone(),
            action: leg.action.clone(),
            entry_time: idx_to_time(entry_idx),
            entry_price: entry_px as f64 / 100.0,
            exit_time: idx_to_time(exit_idx),
            exit_price: exit_px as f64 / 100.0,
            exit_reason: exit_reason.to_string(),
            quantity: leg.quantity,
            pnl,
            mae,
            mfe,
        });
    }
    records
}

pub fn run_backtest(spec: &StrategySpec, data_dir: &Path) -> Result<Vec<TradeRecord>, crate::error::AppError> {
    let symbol_dir = data_dir.join(&spec.symbol);
    let snaps_dir = symbol_dir.join("snapshots");
    let expiry_map = load_expiry_map(&symbol_dir)?;

    let date_from = NaiveDate::parse_from_str(&spec.date_from, "%Y-%m-%d")
        .map_err(|e| crate::error::AppError::BadRequest(e.to_string()))?;
    let date_to = NaiveDate::parse_from_str(&spec.date_to, "%Y-%m-%d")
        .map_err(|e| crate::error::AppError::BadRequest(e.to_string()))?;

    let mut all_records = Vec::new();
    let mut current = date_from;
    while current <= date_to {
        let date_str = current.format("%Y-%m-%d").to_string();
        let snap_path = snaps_dir.join(format!("{date_str}.arrow"));
        if snap_path.exists() {
            match Snapshot::open(&snap_path) {
                Ok(snap) => {
                    all_records.extend(run_day(&snap, &expiry_map, spec, &date_str, current));
                }
                Err(e) => tracing::warn!("skip {date_str}: {e}"),
            }
        }
        current = current.succ_opt().unwrap_or(current);
    }
    Ok(all_records)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::snapshot::test_helpers::synthetic_snapshot;
    use crate::engine::types::{ExitCond, LegSpec, StrikeSelection};
    use std::io::Write;
    use tempfile::TempDir;

    fn setup_backtest_dir() -> TempDir {
        let dir = TempDir::new().unwrap();
        let sym_dir = dir.path().join("NIFTY");
        std::fs::create_dir_all(sym_dir.join("snapshots")).unwrap();
        std::fs::write(sym_dir.join("expiries.json"), r#"{"0": "2024-01-04"}"#).unwrap();
        let bytes = synthetic_snapshot("2024-01-01", 2400000, 20000, 10000, 5);
        let path = sym_dir.join("snapshots").join("2024-01-01.arrow");
        std::fs::write(path, bytes).unwrap();
        dir
    }

    #[test]
    fn test_sell_atm_ce_hits_target() {
        let dir = setup_backtest_dir();
        let spec = StrategySpec {
            symbol: "NIFTY".into(),
            date_from: "2024-01-01".into(),
            date_to: "2024-01-01".into(),
            entry_time: "09:20".into(),
            square_off_time: "15:15".into(),
            legs: vec![LegSpec {
                opt_type: "CE".into(),
                action: "SELL".into(),
                strike_selection: StrikeSelection { mode: "ATM".into(), value: 0 },
                expiry: "WEEKLY".into(),
                quantity: 1,
                sl: None,
                target: Some(ExitCond { kind: "percent".into(), value: 50.0 }),
            }],
        };
        let records = run_backtest(&spec, dir.path()).unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].exit_reason, "TARGET");
        assert!((records[0].entry_price - 200.0).abs() < 0.01);
        assert!((records[0].exit_price - 100.0).abs() < 0.01);
        assert!((records[0].pnl - 100.0).abs() < 0.01);
    }
}
```

- [ ] **Step 2: Run engine test**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test engine::engine 2>&1 | tail -10
```
Expected: `test engine::engine::tests::test_sell_atm_ce_hits_target ... ok`

- [ ] **Step 3: Commit**

```bash
git add backend/intraday_server/src/engine/engine.rs
git commit -m "feat(intraday-server): per-day backtest engine loop with golden test"
```

---

### Task 8: Arrow IPC serialisation

**Files:**
- Create: `backend/intraday_server/src/arrow_out.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/arrow_out.rs`**

```rust
use arrow_array::{
    Float64Array, Int64Array, RecordBatch, StringArray, UInt32Array,
};
use arrow_ipc::writer::StreamWriter;
use arrow_schema::{DataType, Field, Schema};
use std::sync::Arc;

use crate::engine::types::{ChainRow, OhlcvBar, SeriesBar, TradeRecord};
use crate::error::AppError;

fn ipc_bytes(batch: RecordBatch) -> Result<Vec<u8>, AppError> {
    let mut buf = Vec::new();
    {
        let mut writer = StreamWriter::try_new(&mut buf, &batch.schema())
            .map_err(|e| AppError::Arrow(e.to_string()))?;
        writer.write(&batch).map_err(|e| AppError::Arrow(e.to_string()))?;
        writer.finish().map_err(|e| AppError::Arrow(e.to_string()))?;
    }
    Ok(buf)
}

pub fn ohlcv_to_ipc(rows: &[OhlcvBar]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("minute", DataType::Utf8, false),
        Field::new("open",   DataType::Float64, false),
        Field::new("high",   DataType::Float64, false),
        Field::new("low",    DataType::Float64, false),
        Field::new("close",  DataType::Float64, false),
        Field::new("volume", DataType::Int64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(StringArray::from(rows.iter().map(|r| r.minute.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.open).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.low).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.close).collect::<Vec<_>>())),
        Arc::new(Int64Array::from(rows.iter().map(|r| r.volume).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub fn chain_to_ipc(rows: &[ChainRow]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("strike",    DataType::Float64, false),
        Field::new("ce_close",  DataType::Float64, false),
        Field::new("ce_high",   DataType::Float64, false),
        Field::new("ce_low",    DataType::Float64, false),
        Field::new("ce_volume", DataType::Int64, false),
        Field::new("pe_close",  DataType::Float64, false),
        Field::new("pe_high",   DataType::Float64, false),
        Field::new("pe_low",    DataType::Float64, false),
        Field::new("pe_volume", DataType::Int64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(Float64Array::from(rows.iter().map(|r| r.strike).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.ce_close).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.ce_high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.ce_low).collect::<Vec<_>>())),
        Arc::new(Int64Array::from(rows.iter().map(|r| r.ce_volume).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pe_close).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pe_high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pe_low).collect::<Vec<_>>())),
        Arc::new(Int64Array::from(rows.iter().map(|r| r.pe_volume).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub fn series_to_ipc(rows: &[SeriesBar]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("date",   DataType::Utf8, false),
        Field::new("minute", DataType::Utf8, false),
        Field::new("open",   DataType::Float64, false),
        Field::new("high",   DataType::Float64, false),
        Field::new("low",    DataType::Float64, false),
        Field::new("close",  DataType::Float64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(StringArray::from(rows.iter().map(|r| r.date.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.minute.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.open).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.low).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.close).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub fn trades_to_ipc(rows: &[TradeRecord]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("date",         DataType::Utf8, false),
        Field::new("symbol",       DataType::Utf8, false),
        Field::new("expiry",       DataType::Utf8, false),
        Field::new("strike",       DataType::Float64, false),
        Field::new("opt_type",     DataType::Utf8, false),
        Field::new("action",       DataType::Utf8, false),
        Field::new("entry_time",   DataType::Utf8, false),
        Field::new("entry_price",  DataType::Float64, false),
        Field::new("exit_time",    DataType::Utf8, false),
        Field::new("exit_price",   DataType::Float64, false),
        Field::new("exit_reason",  DataType::Utf8, false),
        Field::new("quantity",     DataType::UInt32, false),
        Field::new("pnl",          DataType::Float64, false),
        Field::new("mae",          DataType::Float64, false),
        Field::new("mfe",          DataType::Float64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(StringArray::from(rows.iter().map(|r| r.date.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.symbol.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.expiry.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.strike).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.opt_type.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.action.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.entry_time.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.entry_price).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.exit_time.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.exit_price).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.exit_reason.as_str()).collect::<Vec<_>>())),
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.quantity).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pnl).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.mae).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.mfe).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub const ARROW_CONTENT_TYPE: &str = "application/vnd.apache.arrow.stream";

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_ipc::reader::StreamReader;

    #[test]
    fn test_ohlcv_roundtrip() {
        let rows = vec![OhlcvBar { minute: "09:15".into(), open: 100.0, high: 110.0, low: 90.0, close: 105.0, volume: 0 }];
        let bytes = ohlcv_to_ipc(&rows).unwrap();
        assert!(!bytes.is_empty());
        let mut reader = StreamReader::try_new(std::io::Cursor::new(bytes), None).unwrap();
        let batch = reader.next().unwrap().unwrap();
        assert_eq!(batch.num_rows(), 1);
        assert_eq!(batch.num_columns(), 6);
    }

    #[test]
    fn test_trades_roundtrip_empty() {
        let bytes = trades_to_ipc(&[]).unwrap();
        let mut reader = StreamReader::try_new(std::io::Cursor::new(bytes), None).unwrap();
        let batch = reader.next().unwrap().unwrap();
        assert_eq!(batch.num_rows(), 0);
        assert_eq!(batch.num_columns(), 15);
    }
}
```

- [ ] **Step 2: Add `mod arrow_out;` to `src/main.rs`**

- [ ] **Step 3: Run tests**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test arrow_out 2>&1 | tail -10
```
Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/arrow_out.rs backend/intraday_server/src/main.rs
git commit -m "feat(intraday-server): Arrow IPC serialisation for all output types"
```

---

### Task 9: Redis cache + job store

**Files:**
- Create: `backend/intraday_server/src/cache.rs`
- Create: `backend/intraday_server/src/job_store.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/cache.rs`**

```rust
use redis::AsyncCommands;

pub type RedisConn = redis::aio::ConnectionManager;

pub async fn get_bytes(conn: &mut RedisConn, key: &str) -> Option<Vec<u8>> {
    conn.get::<_, Option<Vec<u8>>>(key).await.ok().flatten()
}

pub async fn set_bytes_ex(conn: &mut RedisConn, key: &str, value: &[u8], ttl_secs: u64) {
    let _: redis::RedisResult<()> = conn.set_ex(key, value, ttl_secs).await;
}

pub async fn get_str(conn: &mut RedisConn, key: &str) -> Option<String> {
    conn.get::<_, Option<String>>(key).await.ok().flatten()
}

pub async fn set_str_ex(conn: &mut RedisConn, key: &str, value: &str, ttl_secs: u64) {
    let _: redis::RedisResult<()> = conn.set_ex(key, value, ttl_secs).await;
}

/// SET key value EX ttl_secs NX (set only if not exists). Returns true if key was set.
pub async fn setnx_ex(conn: &mut RedisConn, key: &str, value: &str, ttl_secs: u64) -> bool {
    let result: redis::RedisResult<Option<String>> = redis::cmd("SET")
        .arg(key).arg(value)
        .arg("EX").arg(ttl_secs)
        .arg("NX")
        .query_async(conn)
        .await;
    result.ok().flatten().is_some()
}

pub async fn ping(conn: &mut RedisConn) -> bool {
    let result: redis::RedisResult<String> = redis::cmd("PING").query_async(conn).await;
    result.map(|s| s == "PONG").unwrap_or(false)
}
```

- [ ] **Step 2: Create `backend/intraday_server/src/job_store.rs`**

```rust
use crate::cache::{get_bytes, get_str, set_bytes_ex, set_str_ex, setnx_ex, RedisConn};
use serde::{Deserialize, Serialize};

const JOB_TTL: u64 = 3600;       // 1 hour
const RESULT_TTL: u64 = 604800;  // 7 days
const INFLIGHT_TTL: u64 = 120;   // 2 minutes

#[derive(Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus { Queued, Running, Done, Failed }

#[derive(Serialize, Deserialize, Debug)]
pub struct JobState {
    pub status: JobStatus,
    pub cache_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,
}

pub fn result_key(cache_key: &str)   -> String { format!("intraday:result:{cache_key}") }
pub fn job_key(job_id: &str)         -> String { format!("intraday:job:{job_id}") }
pub fn inflight_key(cache_key: &str) -> String { format!("intraday:inflight:{cache_key}") }

pub async fn get_result(conn: &mut RedisConn, cache_key: &str) -> Option<Vec<u8>> {
    get_bytes(conn, &result_key(cache_key)).await
}

pub async fn store_result(conn: &mut RedisConn, cache_key: &str, bytes: &[u8]) {
    set_bytes_ex(conn, &result_key(cache_key), bytes, RESULT_TTL).await;
}

pub async fn get_job(conn: &mut RedisConn, job_id: &str) -> Option<JobState> {
    let s = get_str(conn, &job_key(job_id)).await?;
    serde_json::from_str(&s).ok()
}

pub async fn set_job(conn: &mut RedisConn, job_id: &str, state: &JobState) {
    if let Ok(s) = serde_json::to_string(state) {
        set_str_ex(conn, &job_key(job_id), &s, JOB_TTL).await;
    }
}

/// Returns existing job_id if another identical request is already in flight.
pub async fn try_claim_inflight(conn: &mut RedisConn, cache_key: &str, job_id: &str) -> Option<String> {
    let claimed = setnx_ex(conn, &inflight_key(cache_key), job_id, INFLIGHT_TTL).await;
    if claimed {
        None // we claimed it, no existing job
    } else {
        get_str(conn, &inflight_key(cache_key)).await
    }
}
```

- [ ] **Step 3: Add modules to `src/main.rs`**

```rust
mod cache;
mod job_store;
```

- [ ] **Step 4: Verify compile**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo check 2>&1 | grep "^error" | head -5
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/intraday_server/src/cache.rs backend/intraday_server/src/job_store.rs backend/intraday_server/src/main.rs
git commit -m "feat(intraday-server): Redis cache helpers and job store"
```

---

### Task 10: AppState + all handlers wired

**Files:**
- Create: `backend/intraday_server/src/handlers/mod.rs`
- Create: `backend/intraday_server/src/handlers/health.rs`
- Create: `backend/intraday_server/src/handlers/meta.rs`
- Create: `backend/intraday_server/src/handlers/data.rs`
- Create: `backend/intraday_server/src/handlers/backtest.rs`
- Create: `backend/intraday_server/src/handlers/jobs.rs`
- Modify: `backend/intraday_server/src/main.rs`

- [ ] **Step 1: Create `backend/intraday_server/src/handlers/mod.rs`**

```rust
pub mod backtest;
pub mod data;
pub mod health;
pub mod jobs;
pub mod meta;
```

- [ ] **Step 2: Create `backend/intraday_server/src/handlers/health.rs`**

```rust
use axum::{extract::State, Json};
use crate::{AppState, cache};
use serde_json::{json, Value};
use std::path::Path;

pub async fn handler(State(state): State<AppState>) -> Json<Value> {
    let data_dir = Path::new(&state.data_dir);
    let symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
    let mut snapshot_counts = std::collections::HashMap::new();
    let mut earliest: Option<String> = None;
    let mut latest: Option<String> = None;

    for sym in &symbols {
        let snaps = data_dir.join(sym).join("snapshots");
        if snaps.exists() {
            let count = std::fs::read_dir(&snaps)
                .map(|rd| rd.filter_map(|e| e.ok())
                    .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("arrow"))
                    .count())
                .unwrap_or(0);
            snapshot_counts.insert(*sym, count);
            if count > 0 {
                let mut dates: Vec<String> = std::fs::read_dir(&snaps)
                    .unwrap()
                    .filter_map(|e| e.ok())
                    .filter_map(|e| {
                        let p = e.path();
                        if p.extension()?.to_str()? == "arrow" {
                            p.file_stem()?.to_str().map(|s| s.to_string())
                        } else { None }
                    })
                    .collect();
                dates.sort();
                if let Some(d) = dates.first() {
                    if earliest.as_ref().map_or(true, |e| d < e) { earliest = Some(d.clone()); }
                }
                if let Some(d) = dates.last() {
                    if latest.as_ref().map_or(true, |l| d > l) { latest = Some(d.clone()); }
                }
            }
        } else {
            snapshot_counts.insert(*sym, 0);
        }
    }

    let mut redis = state.redis.clone();
    let redis_ok = cache::ping(&mut redis).await;

    Json(json!({
        "service": "intraday",
        "redis_ok": redis_ok,
        "snapshot_counts": snapshot_counts,
        "date_range": { "earliest": earliest, "latest": latest },
    }))
}
```

- [ ] **Step 3: Create `backend/intraday_server/src/handlers/meta.rs`**

```rust
use axum::{extract::{Query, State}, Json};
use crate::{AppState, error::AppError};
use crate::engine::data_queries::load_expiry_map;
use chrono::NaiveDate;
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::Path;

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
    let symbol_dir = Path::new(&state.data_dir).join(&q.symbol);
    let expiry_map = load_expiry_map(&symbol_dir)?;
    let trade_date = NaiveDate::parse_from_str(&q.date, "%Y-%m-%d")
        .map_err(|e| AppError::BadRequest(e.to_string()))?;

    let mut exp_dates: Vec<NaiveDate> = expiry_map.values()
        .filter(|&&d| d >= trade_date)
        .copied()
        .collect();
    exp_dates.sort();
    exp_dates.dedup();

    let nearest_weekly  = exp_dates.first().map(|d| d.format("%Y-%m-%d").to_string());
    let nearest_monthly = exp_dates.iter()
        .filter(|d| d.month() == trade_date.month() || d > &&trade_date)
        .last()
        .map(|d| d.format("%Y-%m-%d").to_string());

    let dates_str: Vec<String> = exp_dates.iter().map(|d| d.format("%Y-%m-%d").to_string()).collect();
    Ok(Json(json!({
        "symbol": q.symbol,
        "date": q.date,
        "expiries": dates_str,
        "nearest_weekly": nearest_weekly,
        "nearest_monthly": nearest_monthly,
    })))
}

pub async fn strikes(
    State(state): State<AppState>,
    Query(q): Query<StrikesQuery>,
) -> Result<Json<Value>, AppError> {
    use crate::engine::snapshot::Snapshot;
    let symbol_dir = Path::new(&state.data_dir).join(&q.symbol);
    let snap_path = symbol_dir.join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() {
        return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date)));
    }

    let expiry_map = load_expiry_map(&symbol_dir)?;
    let target_date = NaiveDate::parse_from_str(&q.expiry_date, "%Y-%m-%d")
        .map_err(|e| AppError::BadRequest(e.to_string()))?;
    let expiry_idx = expiry_map.iter()
        .find(|(_, &d)| d == target_date)
        .map(|(&idx, _)| idx)
        .ok_or_else(|| AppError::NotFound(format!("expiry {} not found", q.expiry_date)))?;

    let snap = Snapshot::open(&snap_path)?;
    let e = snap.find_expiry_e(expiry_idx)
        .ok_or_else(|| AppError::NotFound("expiry not in snapshot".into()))?;

    let step = crate::engine::data_queries::strike_step(&q.symbol);
    let atm_x100 = snap.atm_x100(e, 0);
    let atm = atm_x100 as f64 / 100.0;
    let anchor_x100 = atm_x100 - 5 * step;
    let strikes: Vec<f64> = (0..11).map(|s| (anchor_x100 + s * step) as f64 / 100.0).collect();
    let step_inr = step as f64 / 100.0;

    Ok(Json(json!({
        "symbol": q.symbol,
        "date": q.date,
        "expiry_date": q.expiry_date,
        "atm": atm,
        "step": step_inr,
        "strikes": strikes,
    })))
}
```

- [ ] **Step 4: Create `backend/intraday_server/src/handlers/data.rs`**

```rust
use axum::{
    body::Body,
    extract::{Query, State},
    http::header,
    response::Response,
};
use chrono::NaiveDate;
use serde::Deserialize;
use std::path::Path;

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

#[derive(Deserialize)]
pub struct SpotQuery { pub symbol: String, pub date: String }

pub async fn spot(
    State(state): State<AppState>,
    Query(q): Query<SpotQuery>,
) -> Result<Response, AppError> {
    let cache_key = format!("intraday:data:spot:{}:{}", q.symbol, q.date);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await {
        return Ok(arrow_response(bytes));
    }
    let snap_path = Path::new(&state.data_dir).join(&q.symbol).join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() { return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date))); }
    let snap = Snapshot::open(&snap_path)?;
    let bytes = ohlcv_to_ipc(&spot_series(&snap))?;
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
    let cache_key = format!("intraday:data:ohlcv:{}:{}:{}:{}:{}", q.symbol, q.date, q.strike, q.opt_type, q.expiry_date);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await { return Ok(arrow_response(bytes)); }

    let symbol_dir = Path::new(&state.data_dir).join(&q.symbol);
    let snap_path = symbol_dir.join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() { return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date))); }

    let expiry_map = load_expiry_map(&symbol_dir)?;
    let target_date = NaiveDate::parse_from_str(&q.expiry_date, "%Y-%m-%d").map_err(|e| AppError::BadRequest(e.to_string()))?;
    let expiry_idx = expiry_map.iter().find(|(_, &d)| d == target_date).map(|(&i, _)| i)
        .ok_or_else(|| AppError::NotFound(format!("expiry {} not found", q.expiry_date)))?;
    let opt_type = OptType::from_str(&q.opt_type).ok_or_else(|| AppError::BadRequest("opt_type must be CE or PE".into()))?;

    let snap = Snapshot::open(&snap_path)?;
    let strike_x100 = (q.strike * 100) as i32;
    let bars = ohlcv_series(&snap, expiry_idx, strike_x100, opt_type)?;
    let bytes = ohlcv_to_ipc(&bars)?;
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
    let cache_key = format!("intraday:data:chain:{}:{}:{}:{}", q.symbol, q.date, q.minute, q.expiry_date);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await { return Ok(arrow_response(bytes)); }

    let symbol_dir = Path::new(&state.data_dir).join(&q.symbol);
    let snap_path = symbol_dir.join("snapshots").join(format!("{}.arrow", q.date));
    if !snap_path.exists() { return Err(AppError::NotFound(format!("no snapshot for {} on {}", q.symbol, q.date))); }

    let expiry_map = load_expiry_map(&symbol_dir)?;
    let target_date = NaiveDate::parse_from_str(&q.expiry_date, "%Y-%m-%d").map_err(|e| AppError::BadRequest(e.to_string()))?;
    let expiry_idx = expiry_map.iter().find(|(_, &d)| d == target_date).map(|(&i, _)| i)
        .ok_or_else(|| AppError::NotFound(format!("expiry {} not found", q.expiry_date)))?;

    let snap = Snapshot::open(&snap_path)?;
    let minute_idx = time_to_idx(&q.minute).min(snap.minute_count - 1);
    let rows = chain_snapshot(&snap, expiry_idx, minute_idx)?;
    let bytes = chain_to_ipc(&rows)?;
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
    let cache_key = format!("intraday:data:series:{}:{}:{}:{}:{}:{}:{}", q.symbol, q.date_from, q.date_to, q.strike, q.opt_type, q.expiry_mode, q.resolution);
    let mut redis = state.redis.clone();
    if let Some(bytes) = get_bytes(&mut redis, &cache_key).await { return Ok(arrow_response(bytes)); }

    let symbol_dir = Path::new(&state.data_dir).join(&q.symbol);
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
```

- [ ] **Step 5: Create `backend/intraday_server/src/handlers/backtest.rs`**

```rust
use axum::{
    body::Body,
    extract::State,
    http::header,
    response::Response,
    Json,
};
use serde_json::{json, Value};
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
    let cache_key = req.canonical_key();
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
            .body(Body::from(json!({"job_id": existing_id, "status": "queued"}).to_string())).unwrap());
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
    Ok(builder.body(Body::from(json!({"job_id": job_id, "status": "queued"}).to_string())).unwrap())
}
```

- [ ] **Step 6: Create `backend/intraday_server/src/handlers/jobs.rs`**

```rust
use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::Response,
    Json,
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
```

- [ ] **Step 7: Rewrite `backend/intraday_server/src/main.rs` to wire all routes**

```rust
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
    use http::{Request, StatusCode};
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    async fn test_state() -> AppState {
        // Use a mock state with a temp dir — Redis is not available in unit tests
        // so we test only the routing layer; Redis calls will fail gracefully.
        let redis_url = std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379".into());
        let client = redis::Client::open(redis_url).unwrap();
        // If Redis isn't available, skip gracefully in CI
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
```

- [ ] **Step 8: Compile and run all tests**

```bash
cd /home/user/Algo_Test_Software/backend/intraday_server
cargo test 2>&1 | tail -20
```
Expected: all engine + arrow_out tests pass. Integration tests may skip if Redis is not running locally (that's fine).

- [ ] **Step 9: Commit**

```bash
git add backend/intraday_server/src/
git commit -m "feat(intraday-server): all handlers wired — health, meta, data, backtest, jobs"
```

---

### Task 11: Docker + nginx routing

**Files:**
- Create: `backend/intraday_server/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `frontend/nginx.conf`

- [ ] **Step 1: Create `backend/intraday_server/Dockerfile`**

```dockerfile
# ── Build stage ────────────────────────────────────────────────────────────
FROM rust:1.78-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml .
# Pre-cache dependencies
RUN mkdir -p src && echo "fn main(){}" > src/main.rs && cargo build --release && rm -f src/main.rs
COPY src ./src
RUN touch src/main.rs && cargo build --release

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y libssl3 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/intraday_server /usr/local/bin/
EXPOSE 8001
ENV RUST_LOG=info
CMD ["intraday_server"]
```

- [ ] **Step 2: Add `intraday-api` service to `docker-compose.yml`**

Read `docker-compose.yml` first to find the right insertion point (after the `worker-backtests-fast` service, before `volumes:`). Add:

```yaml
  intraday-api:
    build:
      context: ./backend/intraday_server
      dockerfile: Dockerfile
    ports:
      - "8001:8001"
    environment:
      INTRADAY_DATA_DIR: /data/intraday
      REDIS_URL: redis://redis:6379/0
      RUST_LOG: info
    volumes:
      - algo_cache:/data
    depends_on:
      - redis
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8001/api/intraday/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "4.0"
        reservations:
          memory: 128M
```

- [ ] **Step 3: Add nginx routing to `frontend/nginx.conf`**

Insert before the `location /api/backtest` block (line 49). Add:

```nginx
        location /api/intraday/ {
            set $intraday_upstream http://intraday-api:8001;
            limit_req zone=api_backtest burst=20 nodelay;
            proxy_pass $intraday_upstream;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 120s;
            proxy_connect_timeout 5s;
            proxy_send_timeout 120s;
            proxy_buffering on;
            proxy_buffer_size 16k;
            proxy_buffers 8 16k;
        }

```

- [ ] **Step 4: Verify docker compose config is valid**

```bash
cd /home/user/Algo_Test_Software
docker compose config 2>&1 | grep -E "^Error|intraday-api" | head -10
```
Expected: `intraday-api:` appears, no errors.

- [ ] **Step 5: Build and smoke-test**

```bash
docker compose build intraday-api 2>&1 | tail -15
docker compose up -d intraday-api redis 2>&1 | tail -10
sleep 5
curl -sf http://localhost:8001/api/intraday/health | python3 -m json.tool
```
Expected: JSON with `"service": "intraday"` and `"redis_ok": true`.

- [ ] **Step 6: Verify EOD backend is unaffected**

```bash
docker compose up -d backend
sleep 5
curl -sf http://localhost:8000/health | python3 -m json.tool | head -5
docker compose down
```
Expected: EOD `/health` returns its normal JSON — no regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/intraday_server/Dockerfile docker-compose.yml frontend/nginx.conf
git commit -m "feat(docker): intraday-api service + nginx /api/intraday/ routing"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task |
|---|---|
| §2 Architecture — two services, nginx routing | Task 11 |
| §3 Repository layout | All tasks |
| §4 Cargo deps | Task 1 |
| §5.1 POST /backtest — submit, cache hit, dedup | Task 10 (backtest.rs) |
| §5.2 GET /jobs/{id} — poll, Arrow IPC on done | Task 10 (jobs.rs) |
| §5.3 GET /data/spot | Task 10 (data.rs) |
| §5.4 GET /data/ohlcv | Task 10 (data.rs) |
| §5.5 GET /data/chain | Task 10 (data.rs) |
| §5.6 GET /data/series + resolution | Tasks 6, 10 |
| §5.7 GET /meta/dates | Task 10 (meta.rs) |
| §5.8 GET /meta/expiries | Task 10 (meta.rs) |
| §5.9 GET /meta/strikes | Task 10 (meta.rs) |
| §5.10 GET /health | Task 10 (health.rs) |
| §6 Data access — spot_series, ohlcv_series, chain_snapshot, multi_day_series | Tasks 5, 6 |
| §7 Job lifecycle — queued/running/done/failed, dedup, slow-path header | Task 10 (backtest.rs), Task 9 |
| §8 Redis cache — all key patterns, TTLs | Tasks 9, 10 |
| §9 Arrow IPC — all 4 types | Task 8 |
| §10 Error handling — AppError → HTTP | Task 2 |
| §11 Docker — 512M limit, algo_cache volume | Task 11 |
| §12 Performance SLAs | Verified by Task 7 golden test; perf regression test deferred (needs real data) |
| §14 Relationship to Plans A-E | Notes inline |

**Placeholder scan:** None found.

**Type consistency:**
- `OhlcvBar`, `ChainRow`, `SeriesBar`, `TradeRecord` defined in Task 3 (`types.rs`), used consistently in Tasks 5–8, 10.
- `AppError` defined in Task 2, used as `Result<_, AppError>` throughout Tasks 5–10.
- `AppState` defined in Task 10 (`main.rs`), injected via `State<AppState>` in all handlers.
- `job_store::result_key()`, `job_key()`, `inflight_key()` defined in Task 9, used in Tasks 10 (backtest.rs, jobs.rs).
- `cache::get_bytes()`, `set_bytes_ex()` defined in Task 9, used in Tasks 10 (data.rs, backtest.rs).
- `ARROW_CONTENT_TYPE` constant defined in Task 8 (`arrow_out.rs`), used in Tasks 10 (data.rs, backtest.rs, jobs.rs).
