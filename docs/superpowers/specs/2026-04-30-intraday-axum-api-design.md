# Intraday Axum API — Design Spec

**Date:** 2026-04-30
**Status:** Approved
**Supersedes:** Sections 5.5, 6, 7 of `2026-04-29-intraday-backtest-design.md`
(Plan B engine core is unchanged; only the API layer changes from PyO3/FastAPI to Axum)

---

## 1. Goal

Expose the intraday Rust engine as a standalone HTTP service that delivers:

- **Backtest API** — submit a strategy config, get an Arrow IPC tradesheet back
- **Market data APIs** — single-day OHLCV, option chain, multi-day series, spot price
- **Metadata APIs** — available dates, expiries, strikes for a symbol
- **Health API** — subsystem status for ops/monitoring

**Performance target:** market data queries < 5ms warm (Redis hit), < 10ms cold (disk). Backtest p50 < 700ms, p95 < 1100ms. All measured end-to-end at the nginx edge.

---

## 2. Architecture

### 2.1 Two services, one nginx

```
Browser / Frontend (React, port 3000)
  │
  ▼
nginx (port 80 in prod, 3000 in dev via Vite proxy)
  │
  ├── /api/*             → Python FastAPI   :8000  (EOD — 100% unchanged)
  └── /api/intraday/*    → Rust Axum        :8001  (new intraday service)
```

The EOD backend (`backend/`) is never modified. The intraday service lives in a new directory `backend/intraday_server/` and is a separate Docker container.

### 2.2 Why Axum instead of FastAPI + PyO3

Every FastAPI request that calls Rust via PyO3 pays ~11ms of language-boundary overhead (GIL acquire, asyncio.to_thread dispatch, FFI crossing, pyarrow Arrow IPC assembly). For market data queries that are only 3–5ms of real work, the framework overhead dominates.

With Axum:
- No GIL, no FFI, no asyncio workarounds
- Arrow IPC assembled natively via `arrow-rs` (~0.5ms)
- `tokio` handles backpressure and concurrency without Python's GIL
- Redis accessed via `redis-rs` directly

Benchmark impact:

| Request type              | FastAPI + PyO3 | Axum (this spec) |
|---------------------------|---------------|-----------------|
| Redis cache hit            | ~16ms         | ~1.5ms          |
| Single-day OHLCV (warm)   | ~25ms         | ~4ms            |
| Multi-day series (cold 1yr)| ~165ms       | ~155ms          |
| Backtest 1yr NIFTY         | ~720ms        | ~701ms          |

Backtest speed is unchanged (bottleneck is computation). Market data queries are 4–10× faster.

### 2.3 EOD is untouched

`backend/` Python codebase, Celery workers, Postgres, Redis — none of these change. The intraday Axum service is additive.

---

## 3. Repository layout

```
backend/intraday_server/
  Cargo.toml
  src/
    main.rs              ← axum Router, bind :8001, startup checks
    handlers/
      mod.rs
      backtest.rs        ← POST /api/intraday/backtest
      jobs.rs            ← GET  /api/intraday/jobs/{job_id}
      data.rs            ← GET  /api/intraday/data/{spot,ohlcv,chain,series}
      meta.rs            ← GET  /api/intraday/meta/{dates,expiries,strikes}
      health.rs          ← GET  /api/intraday/health
    engine/
      mod.rs
      snapshot.rs        ← DaySnapshot mmap reader (identical to Plan B)
      types.rs           ← StrategySpec, LegSpec, TradeRecord, OhlcvBar, etc.
      engine.rs          ← per-day backtest loop (identical to Plan B)
      data_queries.rs    ← NEW: OHLCV/chain/series extraction from Snapshot
      calendar.rs        ← expiry calendar (from Plan E)
    cache.rs             ← redis-rs get/set/setex helpers
    arrow_out.rs         ← Arrow IPC serialisation via arrow-rs
    job_store.rs         ← Redis-backed job state (submit/poll/store result)
    error.rs             ← AppError → axum IntoResponse
```

The `engine/snapshot.rs`, `engine/types.rs`, and `engine/engine.rs` files are **copied** from `backend/native/src/intraday/` (Plan B). They require no changes — only the binding layer changes.

---

## 4. Cargo dependencies

```toml
[package]
name = "intraday_server"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "intraday_server"
path = "src/main.rs"

[dependencies]
# HTTP
axum = { version = "0.7", features = ["macros"] }
tokio = { version = "1", features = ["full"] }
tower = "0.4"
tower-http = { version = "0.5", features = ["cors", "trace"] }
hyper = { version = "1", features = ["full"] }

# Serialisation
serde = { version = "1", features = ["derive"] }
serde_json = "1"

# Arrow IPC output
arrow-array = "52"
arrow-ipc = "52"
arrow-schema = "52"
arrow-buffer = "52"

# Data access
memmap2 = "0.9"
chrono = { version = "0.4", features = ["clock", "serde"] }

# Cache
redis = { version = "0.25", features = ["tokio-comp"] }

# Utilities
uuid = { version = "1", features = ["v4"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
thiserror = "1"
once_cell = "1.19"
```

---

## 5. Complete API surface

All endpoints are under prefix `/api/intraday/`.
All `/data/*` responses are `Content-Type: application/vnd.apache.arrow.stream`.
All `/meta/*` and `/health` responses are `Content-Type: application/json`.

---

### 5.1 POST /api/intraday/backtest

Submit a backtest job. Returns immediately with a `job_id`.

**Request body (JSON):**
```json
{
  "symbol":          "NIFTY",
  "date_from":       "2024-01-01",
  "date_to":         "2024-12-31",
  "entry_time":      "09:20",
  "square_off_time": "15:15",
  "legs": [
    {
      "opt_type":  "CE",
      "action":    "SELL",
      "strike_selection": { "mode": "ATM", "value": 0 },
      "expiry":    "WEEKLY",
      "quantity":  1,
      "sl":        { "type": "percent", "value": 50.0 },
      "target":    null
    }
  ]
}
```

**Validation rules:**
- `symbol` ∈ {NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY}
- `date_from` ≤ `date_to`, both `YYYY-MM-DD`
- `entry_time` format `HH:MM`, between 09:15 and 15:29
- `square_off_time` format `HH:MM`, > `entry_time`, ≤ 15:30
- At least 1 leg, at most 6 legs
- `strike_selection.value` ∈ [−10, 10] (outside ±5 → slow path flag)

**Response 200 — cache miss (JSON):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued"
}
```

**Response 200 — cache hit (Arrow IPC):**
```
Content-Type: application/vnd.apache.arrow.stream
Body: Arrow IPC bytes (tradesheet)
```
Same shape as `GET /jobs/{id}` when done. Frontend detects via `Content-Type` header.

**Flow:**
1. Deserialise and validate request
2. Compute `cache_key` = BLAKE2b-8 of canonical JSON
3. Check Redis `intraday:result:{cache_key}` — if hit, return Arrow IPC bytes immediately (no job needed)
4. Check Redis `intraday:inflight:{cache_key}` — if exists, return existing `job_id` (dedup)
5. Generate new `job_id` (UUIDv4)
6. Store `intraday:job:{job_id}` = `{"status":"queued","cache_key":"..."}` in Redis (TTL 1h)
7. `tokio::spawn` backtest task (see §7)
8. Return `{job_id, status:"queued"}`

---

### 5.2 GET /api/intraday/jobs/{job_id}

Poll backtest job status.

**Response 200 (JSON) while running:**
```json
{ "status": "queued" }
{ "status": "running", "started_at": "2024-01-15T09:20:01Z" }
```

**Response 200 (Arrow IPC) when done:**
```
Content-Type: application/vnd.apache.arrow.stream
Body: Arrow IPC stream bytes (tradesheet)
```

**Response 200 (JSON) on failure:**
```json
{ "status": "failed", "error": "snapshot not found for 2024-01-15" }
```

**Response 404:** job_id unknown or expired.

**Tradesheet Arrow IPC schema:**
```
date:         Utf8
symbol:       Utf8
expiry:       Utf8
strike:       Float64
opt_type:     Utf8
action:       Utf8
entry_time:   Utf8
entry_price:  Float64
exit_time:    Utf8
exit_price:   Float64
exit_reason:  Utf8        ← "SL" | "TARGET" | "SQOFF" | "TRAILING_SL"
quantity:     UInt32
pnl:          Float64
mae:          Float64
mfe:          Float64
```

---

### 5.3 GET /api/intraday/data/spot

Single-day spot (index) OHLCV timeseries.

**Query params:**
```
symbol   = NIFTY
date     = 2024-01-15
```

**Response 200 (Arrow IPC):**
```
minute:  Utf8      ← "09:15", "09:16", ..., "15:29"
open:    Float64
high:    Float64
low:     Float64
close:   Float64
```
375 rows. Values are index spot price (e.g. 22345.50).

**Cache key:** `intraday:data:spot:{symbol}:{date}` — TTL 30 days.

---

### 5.4 GET /api/intraday/data/ohlcv

Single-day OHLCV timeseries for one option contract.

**Query params:**
```
symbol       = NIFTY
date         = 2024-01-15
strike       = 24000          ← INR, integer
opt_type     = CE | PE
expiry_date  = 2024-01-18     ← YYYY-MM-DD
```

**Response 200 (Arrow IPC):**
```
minute:  Utf8
open:    Float64
high:    Float64
low:     Float64
close:   Float64
volume:  Int64
```
Up to 375 rows (fewer if option was not listed for the full day).

**Cache key:** `intraday:data:ohlcv:{symbol}:{date}:{strike}:{opt_type}:{expiry_date}` — TTL 30 days.

---

### 5.5 GET /api/intraday/data/chain

Full option chain snapshot at a single minute.

**Query params:**
```
symbol       = NIFTY
date         = 2024-01-15
minute       = 09:30          ← HH:MM
expiry_date  = 2024-01-18
```

**Response 200 (Arrow IPC):**
```
strike:     Float64
ce_close:   Float64
ce_high:    Float64
ce_low:     Float64
ce_volume:  Int64
pe_close:   Float64
pe_high:    Float64
pe_low:     Float64
pe_volume:  Int64
```
11 rows (ATM−5 to ATM+5 strikes). Strike = 0 if that chain slot is empty.

**Cache key:** `intraday:data:chain:{symbol}:{date}:{minute}:{expiry_date}` — TTL 30 days.

---

### 5.6 GET /api/intraday/data/series

Multi-day price series for one option series across a date range. Supports downsampling.

**Query params:**
```
symbol       = NIFTY
date_from    = 2024-01-01
date_to      = 2024-12-31
strike       = 24000
opt_type     = CE | PE
expiry_mode  = WEEKLY | MONTHLY      ← rolls automatically on expiry
resolution   = 1m | 5m | 15m | 1d   ← default 5m
```

**Downsampling:** for each `resolution` bucket, OHLC = open of first bar, high of all bars, low of all bars, close of last bar.

`expiry_mode=WEEKLY` automatically follows the nearest weekly expiry on each day; on expiry day itself it rolls to the next week's ATM strike at the close of the expiring contract.

**Response 200 (Arrow IPC):**
```
date:    Utf8
minute:  Utf8       ← "09:15" for 1d resolution, actual minute otherwise
open:    Float64
high:    Float64
low:     Float64
close:   Float64
```
Max ~93,750 rows (250 days × 375 minutes at 1m). At 5m: ~18,750 rows.

**Cache key:** `intraday:data:series:{symbol}:{date_from}:{date_to}:{strike}:{opt_type}:{expiry_mode}:{resolution}` — TTL 30 days.

---

### 5.7 GET /api/intraday/meta/dates

Available trading dates for a symbol (i.e. dates where a snapshot file exists).

**Query params:** `symbol=NIFTY`

**Response 200 (JSON):**
```json
{
  "symbol": "NIFTY",
  "dates": ["2023-01-02", "2023-01-03", ...],
  "count": 248
}
```

---

### 5.8 GET /api/intraday/meta/expiries

Available expiry dates for a symbol on a given date.

**Query params:** `symbol=NIFTY&date=2024-01-15`

**Response 200 (JSON):**
```json
{
  "symbol": "NIFTY",
  "date": "2024-01-15",
  "expiries": ["2024-01-18", "2024-01-25", "2024-02-29"],
  "nearest_weekly": "2024-01-18",
  "nearest_monthly": "2024-02-29"
}
```

---

### 5.9 GET /api/intraday/meta/strikes

Available strikes for a symbol on a given date for a given expiry.

**Query params:** `symbol=NIFTY&date=2024-01-15&expiry_date=2024-01-18`

**Response 200 (JSON):**
```json
{
  "symbol": "NIFTY",
  "date": "2024-01-15",
  "expiry_date": "2024-01-18",
  "atm": 22050,
  "step": 50,
  "strikes": [21800, 21850, ..., 22300]
}
```

Strikes returned are the full chain stored in the DaySnapshot (ATM−5 to ATM+5 at day open, extended to full listing if available).

---

### 5.10 GET /api/intraday/health

Full subsystem status.

**Response 200 (JSON):**
```json
{
  "service": "intraday",
  "redis_ok": true,
  "snapshot_counts": {
    "NIFTY": 248,
    "BANKNIFTY": 248,
    "FINNIFTY": 248,
    "MIDCPNIFTY": 248
  },
  "date_range": {
    "earliest": "2023-01-02",
    "latest":   "2024-12-31"
  },
  "jobs": {
    "queued":  0,
    "running": 1
  },
  "uptime_seconds": 3600
}
```

---

## 6. Data access layer — new Rust functions

These functions live in `engine/data_queries.rs`. They read DaySnapshot files using the same `Snapshot` struct from Plan B.

```rust
// Single-day spot OHLCV — reads HEADER + SPOT section only
pub fn spot_series(snap: &Snapshot) -> Vec<OhlcvBar>

// Single-day option OHLCV — reads one chain slot across all minutes
pub fn ohlcv_series(snap: &Snapshot, expiry_idx: i16,
                    strike_x100: i32, opt_type: OptType) -> Vec<OhlcvBar>

// Option chain at one minute — reads all 11 chain slots for one expiry
pub fn chain_snapshot(snap: &Snapshot, expiry_idx: i16,
                      minute_idx: usize) -> Vec<ChainRow>

// Multi-day series — opens N snapshots sequentially, downsamples
pub fn multi_day_series(symbol_dir: &Path, date_from: NaiveDate,
                        date_to: NaiveDate, strike_x100: i32,
                        opt_type: OptType, expiry_mode: ExpiryMode,
                        resolution: Resolution) -> Vec<SeriesBar>
```

Supporting types:
```rust
pub struct OhlcvBar  { pub minute: String, pub open: f64, pub high: f64,
                       pub low: f64, pub close: f64, pub volume: i64 }
pub struct ChainRow  { pub strike: f64,
                       pub ce_close: f64, pub ce_high: f64, pub ce_low: f64, pub ce_volume: i64,
                       pub pe_close: f64, pub pe_high: f64, pub pe_low: f64, pub pe_volume: i64 }
pub struct SeriesBar { pub date: String, pub minute: String,
                       pub open: f64, pub high: f64, pub low: f64, pub close: f64 }

pub enum OptType    { CE, PE }
pub enum ExpiryMode { Weekly, Monthly }
pub enum Resolution { M1, M5, M15, D1 }
```

---

## 7. Job execution model

Backtests are CPU-intensive (up to 60s for multi-year). They run in a dedicated `tokio::spawn` task and store state in Redis.

### 7.1 Job lifecycle

```
POST /backtest
  → validate request
  → check Redis intraday:result:{cache_key}  (cache hit → return Arrow IPC immediately)
  → check Redis intraday:inflight:{cache_key} (in-flight → return existing job_id)
  → generate job_id (UUIDv4)
  → Redis SETNX intraday:inflight:{cache_key} = job_id  EX 120
  → Redis SET intraday:job:{job_id} = {"status":"queued","cache_key":...} EX 3600
  → tokio::spawn(run_backtest_job(job_id, config, data_dir, redis))
  → return {job_id, status:"queued"}

run_backtest_job (background task):
  → Redis SET intraday:job:{job_id} = {"status":"running","started_at":...}
  → call engine::run_backtest(config, data_dir) → Vec<TradeRecord>
  → serialise to Arrow IPC bytes
  → Redis SET intraday:result:{cache_key} = bytes  EX 604800 (7 days)
  → Redis SET intraday:job:{job_id} = {"status":"done","cache_key":...} EX 3600
  → on error: Redis SET intraday:job:{job_id} = {"status":"failed","error":...}

GET /jobs/{job_id}
  → Redis GET intraday:job:{job_id}
  → if status=="done": Redis GET intraday:result:{cache_key} → return Arrow IPC
  → else: return JSON status
```

### 7.2 Slow-path detection

If `|strike_selection.value| > 5` for any leg, the job is tagged `slow=true`. No separate queue is needed (tokio thread pool handles concurrency), but the response header `X-Slow-Path: true` is set so the frontend can show a warning.

### 7.3 Request deduplication

Before spawning a new job, check Redis for an existing in-flight job with the same `cache_key`:

```
Redis SETNX intraday:inflight:{cache_key} {job_id}  EX 120
→ if SETNX returns 0 (key already existed): GET the existing job_id and return it
→ if SETNX returns 1: proceed to spawn new job
```

This prevents 10 identical concurrent requests from spawning 10 jobs.

---

## 8. Caching strategy

All historical intraday data is immutable. Cache misses trigger a disk read; hits are served from Redis memory.

| Key pattern | Content | TTL |
|---|---|---|
| `intraday:result:{blake2b8_hex}` | Arrow IPC backtest tradesheet | 7 days |
| `intraday:data:spot:{sym}:{date}` | Arrow IPC spot OHLCV | 30 days |
| `intraday:data:ohlcv:{sym}:{date}:{strike}:{type}:{expiry}` | Arrow IPC option OHLCV | 30 days |
| `intraday:data:chain:{sym}:{date}:{minute}:{expiry}` | Arrow IPC chain snapshot | 30 days |
| `intraday:data:series:{sym}:{from}:{to}:{strike}:{type}:{mode}:{res}` | Arrow IPC multi-day | 30 days |
| `intraday:job:{job_id}` | JSON job state | 1 hour |
| `intraday:inflight:{cache_key}` | job_id string | 2 minutes |

**Redis memory budget:** Each single-day Arrow IPC response is 6–30KB. Multi-day series (1yr, 5m) is ~150KB. Assume 500 cached series max = ~75MB. Well within the shared Redis budget.

---

## 9. Arrow IPC serialisation

All `/data/*` responses are Arrow IPC streams. The `arrow_out.rs` module exposes:

```rust
pub fn to_arrow_ipc<T: ArrowSerialise>(rows: &[T]) -> Result<Vec<u8>, AppError>
```

`ArrowSerialise` is a trait implemented for `OhlcvBar`, `ChainRow`, `SeriesBar`, and `TradeRecord`. It maps each struct to a `RecordBatch` using `arrow-array` and serialises to an IPC stream via `arrow-ipc::writer::StreamWriter`.

The frontend decodes with `apache-arrow` (npm): `tableFromIPC(buffer)` — same as the backtest tradesheet decoder already in Plan D.

---

## 10. Error handling

All errors map to HTTP status codes via the `AppError` type:

```rust
#[derive(thiserror::Error, Debug)]
pub enum AppError {
    #[error("snapshot not found: {0}")]    NotFound(String),      // → 404
    #[error("invalid parameter: {0}")]     BadRequest(String),    // → 400
    #[error("redis error: {0}")]           Redis(redis::RedisError), // → 503
    #[error("io error: {0}")]              Io(std::io::Error),    // → 500
    #[error("arrow error: {0}")]           Arrow(String),         // → 500
}
```

All errors return JSON: `{"error": "message"}`.

Redis failures on cache reads/writes are non-fatal — the handler falls through to disk and returns data without caching.

---

## 11. Docker setup

New service in `docker-compose.yml`:

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
  deploy:
    resources:
      limits:
        memory: 512M
        cpus: "4.0"
      reservations:
        memory: 128M
```

`Dockerfile` for the Rust service:
```dockerfile
FROM rust:1.78-slim AS builder
WORKDIR /app
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y libssl3 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/intraday_server /usr/local/bin/
EXPOSE 8001
CMD ["intraday_server"]
```

nginx route addition in `docker-compose.yml` nginx config:
```nginx
location /api/intraday/ {
    proxy_pass http://intraday-api:8001;
    proxy_set_header Host $host;
    proxy_read_timeout 120s;
}
```

**Memory budget:** 512MB for the Axum service. At 3 concurrent backtests × ~100MB each (1yr NIFTY DaySnapshot data in mmap) = 300MB peak. Fits comfortably. mmap pages are shared with the OS page cache — vmtouch pre-warming benefits this service directly.

---

## 12. Performance SLAs

| Endpoint | Warm (Redis) | Cold (disk, vmtouch'd) | Cold (HDD, not warm) |
|---|---|---|---|
| `/data/spot` | <2ms | <5ms | <20ms |
| `/data/ohlcv` | <2ms | <5ms | <20ms |
| `/data/chain` | <2ms | <5ms | <20ms |
| `/data/series` 1yr 5m | <5ms | <200ms | <1500ms |
| `/jobs/{id}` cache hit | <2ms | — | — |
| Backtest p50 (1yr NIFTY) | <5ms (cached) | 700ms | 700ms |
| Backtest p95 (1yr NIFTY) | <5ms (cached) | 1100ms | 1100ms |
| `/meta/*` | — | <10ms | <50ms |
| `/health` | — | <20ms | <50ms |

vmtouch warms current + prior year snapshots at 06:00 IST daily (Plan E). After warmup, "cold disk" numbers apply for everything within the warm window.

---

## 13. Testing approach

**Unit tests** (in `src/engine/`, `cargo test`):
- `snapshot.rs` — binary format parsing with a synthetic buffer
- `data_queries.rs` — OHLCV/chain extraction from synthetic Snapshot
- `arrow_out.rs` — Arrow IPC round-trip (serialise → deserialise → compare)
- `job_store.rs` — Redis mock (mockito or test container)

**Integration tests** (`tests/` directory):
- Start Axum server on a random port
- Write a synthetic DaySnapshot to a temp dir
- Hit all 10 endpoints via `reqwest`
- Verify Arrow IPC schema and row counts

**Golden test:**
- Synthetic 1-expiry DaySnapshot for NIFTY 2024-01-01
- SELL ATM CE at 09:20, price falls 50% → TARGET exit
- Verify tradesheet via `/backtest` + `/jobs/{id}`

**Performance regression test** (`@ignore` unless ≥200 real snapshots exist):
- 1-year NIFTY backtest, 10 runs, assert p50 < 700ms, p95 < 1100ms
- Single-day OHLCV, 100 runs, assert p50 < 10ms

---

## 14. Relationship to existing plans

| Plan | Status with this spec |
|---|---|
| Plan A (Storage + ingestion) | **Unchanged.** DaySnapshot format and ingestion pipeline are identical. |
| Plan B (Rust engine) | **Partially superseded.** `snapshot.rs`, `engine.rs`, `types.rs` are copied verbatim. `pyfuncs.rs` and `intraday_engine.py` are dropped. |
| Plan C (FastAPI + Celery) | **Superseded.** The intraday router, Celery task, and cache extension are replaced by this Axum service. granian migration and EOD changes in Plan C still apply. |
| Plan D (Frontend) | **Unchanged.** Arrow IPC decoder, IntradayFields, StrategyBuilder, ResultsPanel — all unchanged. |
| Plan E (Multi-leg + trailing SL) | **Unchanged.** Trailing SL, breakeven, expiry calendar, backfill CLI, vmtouch — all unchanged. |

---

## 15. Out of scope

- WebSocket streaming of live prices (no live data source)
- Authentication / API keys (internal tool, same as EOD)
- Pagination of Arrow IPC responses (payload sizes are bounded: max ~150KB for 1yr 5m series)
- Rate limiting (single-user deployment, same as EOD)
