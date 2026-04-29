# Intraday Options Backtest — Design Spec

**Date:** 2026-04-29
**Author:** Architecture brainstorm session
**Status:** Draft for review
**Target hardware:** Existing box (HP 280 Pro G6, i5-10500 6C/12T, 16 GB DDR4-3200, 1 TB Toshiba HDD, Linux)
**Target users:** Up to 50 in-house, single-machine deployment
**Symbols in scope:** NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY (index options only)
**Bar resolution:** 1 minute OHLCV
**Date range (eventual):** 2017-01-01 to current (2026)
**Date range (phase 1):** 2024 NIFTY only

---

## 1. Goals & non-goals

### Goals

1. Run intraday options backtests on 1-minute bars across all liquid index strikes.
2. Achieve **<1 second p95 latency for the cached / warm-mmap path** (~80–95% of requests under 50-user load).
3. Be **fully isolated from the existing EOD backtest path** — same codebase, separate engine, separate router, separate Celery queue, separate storage. Selecting `mode=intraday` on the frontend triggers the new lane; `mode=eod` is unchanged.
4. Production-ready operationally: idempotent ingest, manifest tracking, restart-safe caches, observable.
5. Forward-compatible with hardware upgrades (SSD / 32 GB) — those improve numbers without redesign.

### Non-goals

1. Real-time / streaming ingestion (this is offline historical only).
2. Tick-level data (1-minute bar is the contract).
3. Multi-machine clustering / sharding.
4. Cloud deployment.
5. Public-facing multi-tenant API.
6. Stock options (deferred until index path is stable).
7. Cleaning of 2017–2022 dirty data — captured as a separate phase, not part of MVP.

---

## 2. Honest performance contract

| Path | Frequency under 50-user load | Latency (p50 / p95) |
|---|---|---|
| Identical rerun (Redis hit) | ~50% | 80 ms / 150 ms |
| Different params, same date range as prior user (warm mmap) | ~30% | 500 ms / 900 ms |
| Fresh date range, hot year (warm mmap, cache miss) | ~15% | 600 ms / 1100 ms |
| Fresh range, cold year first-hit (HDD seek) | ~4% | 5 s / 15 s |
| ATM±5 fallback to full Parquet (rare strategy types) | ~1% | 20–60 s |

**Headline:** ~80% of requests in <1 s; ~95% in <1.2 s. Cold-disk first-access on old data is the known slow path. A nightly `vmtouch` warmup keeps recent years hot to minimize this.

If hardware is later upgraded (SSD + 32 GB RAM), cold-path latencies drop to 200–500 ms and the architecture does not change — only configuration constants do.

---

## 3. Storage architecture

### 3.1 Two storage tiers

- **Cold tier (Parquet on HDD):** complete 1-min options history. Source of truth. Read only on rare wide-strike queries.
- **Hot tier (DaySnapshot Arrow files, mmap'd):** compact pre-aggregated per-day artifacts covering ATM±5 strikes for active expiries. Drives ~99% of backtest queries.

### 3.2 Filesystem layout

```
/data/intraday/
├── NIFTY/
│   ├── options/year=YYYY/month=MM/options.parquet
│   ├── spot/year=YYYY/spot.parquet
│   └── snapshots/YYYY-MM-DD.arrow
├── BANKNIFTY/
│   └── ...                  (same shape)
├── FINNIFTY/
│   └── ...
├── MIDCPNIFTY/
│   └── ...
└── _manifest/
    └── intraday_imports.parquet
```

The Postgres database holds only **the manifest mirror** (`intraday_imports` table) for ACID tracking; the actual data lives on the filesystem.

### 3.3 Parquet schema (cold tier)

Sort order: `(expiry_idx, opt_type, strike_x100, ts_min)`. This guarantees a single leg's lifecycle is one contiguous read.

| Column | Type | Notes |
|---|---|---|
| `ts_min` | `int32` | minutes since 2017-01-01 00:00 IST. Covers 100+ years; saves 4 bytes vs `int64` epoch. |
| `expiry_idx` | `int16` | index into per-symbol expiry dimension; full date stored in dim table |
| `strike_x100` | `int32` | strike price × 100 (exact integer, no float) |
| `opt_type` | `int8` | `0`=CE, `1`=PE |
| `open_x100` | `int32` | × 100 |
| `high_x100` | `int32` | |
| `low_x100` | `int32` | |
| `close_x100` | `int32` | |
| `volume` | `int32` | |
| `oi` | `int32` | |

- Compression: ZSTD level 6
- Row group size: 128 MB
- Dictionary encoding on `expiry_idx`, `opt_type`
- Statistics enabled on all columns for predicate pushdown

Estimated size: **~10–12 bytes/row compressed**. Per year per symbol ≈ 10–15 GB raw, 2–3 GB on disk.

### 3.4 Spot Parquet schema

| Column | Type |
|---|---|
| `ts_min` | `int32` |
| `open_x100` / `high_x100` / `low_x100` / `close_x100` | `int32` |
| `volume` | `int64` |

One file per symbol per year. Tiny — under 10 MB compressed.

### 3.5 Expiry dimension table

Per-symbol JSON file at `/data/intraday/{symbol}/expiries.json`:

```json
{
  "0": "2024-01-04",
  "1": "2024-01-11",
  ...
}
```

Indices are stable once assigned — never renumber. Append-only. `expiry_idx` in Parquet refers here.

### 3.6 DaySnapshot schema (hot tier)

One Arrow IPC file per `(symbol, trading_date)`. **The single most important data structure in this design.** Built once at ingest, mmap'd at runtime, never modified.

```
Header:
  magic: bytes[4]              "ITDS"
  version: u8                  1
  symbol: utf8 (padded 16B)
  date: i32 (days since epoch)
  expiry_count: u8             typically 4
  spot_minute_count: u16       typically 375 (9:15–15:30)

For each minute m in [0, 375):
  spot[m]: { open_x100, high_x100, low_x100, close_x100 }   16B

For each expiry e in [0, expiry_count):
  expiry_dim[e]: { expiry_idx: i16 }                        2B
  for each minute m:
    atm[e][m]: i32             ATM strike_x100              4B / 1500B per expiry
  for each strike s in [-5, +5] (relative to ATM range):
    for each opt_type t in [CE, PE]:
      close_x100[e][s][t][m]: i32                           4B
      high_x100[e][s][t][m]: i32                            4B
      low_x100[e][s][t][m]: i32                             4B
      volume[e][s][t][m]: i32                               4B
```

**Layout note:** all arrays are minute-major (i.e., index by minute fastest), so a leg's full-day curve is a contiguous slice — cache-line friendly, SIMD-friendly.

**Strike range note:** the chain anchor is the **ATM of the previous trading day's close** for that expiry, captured ±5 strikes (11 strikes total). This is deterministic and computable at ingest time. If today's spot moves more than 5 strikes from yesterday's close, strikes outside the captured range require slow-path Parquet reads. On NIFTY this happens on roughly 5–10% of trading days; for typical at-the-money strategies it is rarely the strike actually selected. Widen to ±7 or ±10 once hardware allows (SSD or 32 GB RAM).

| Component | Bytes |
|---|---|
| Header | ~32 |
| Spot[375] | 6,000 |
| Per expiry: ATM[375] + chain[11×2×4×375] | 1,500 + 132,000 = 133,500 |
| 4 expiries | 534,000 |
| **Total per day** | **~540 KB uncompressed** |
| **Compressed (ZSTD on disk)** | **~150–200 KB** |

Yearly footprint per symbol:
- On disk (compressed): 250 days × 180 KB ≈ **45 MB**
- In RAM (mmap'd uncompressed): 250 days × 540 KB ≈ **135 MB**

Working set for 4 symbols × 1 year (most-recent): **~540 MB in RAM** — fits in the 1.5 GB OS page cache headroom with margin.

### 3.7 Manifest (Postgres)

```sql
CREATE TABLE intraday_imports (
  id BIGSERIAL PRIMARY KEY,
  symbol VARCHAR(10) NOT NULL,
  trading_date DATE NOT NULL,
  source_format VARCHAR(20) NOT NULL,        -- 'clean_2023' | 'raw_2017' | etc
  source_sha256 CHAR(64) NOT NULL,
  parquet_path TEXT NOT NULL,
  snapshot_path TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  expiry_count SMALLINT NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (symbol, trading_date)
);
CREATE INDEX idx_intraday_imports_symbol_date ON intraday_imports(symbol, trading_date);
```

Re-ingesting a `(symbol, date)` with the same SHA256 is a no-op. With a different SHA256, the prior snapshot is replaced atomically (write new file → rename → update row → unlink old file).

---

## 4. Ingestion pipeline

### 4.1 Entry point

New Celery task in `worker-uploads`: `ingest_intraday(symbol, source_path, format_hint=None)`.

Handlers in `backend/services/intraday_ingest/`:
- `__init__.py` — registry
- `format_clean_2023.py` — for current clean format (used in phase 1)
- `format_raw_2017.py` — placeholder for the 2017–2022 dirty data (deferred)
- `base.py` — shared validation + writer

### 4.2 Steps (per source file = typically one trading day)

1. **Detect format** — header signature match → pick handler. Reject if no handler matches.
2. **Parse + clean** — handler emits a normalized in-memory Polars DataFrame matching the Parquet schema.
3. **Validate**:
   - No nulls in primary key columns (`ts_min`, `expiry_idx`, `strike_x100`, `opt_type`).
   - Monotonic `ts_min` per `(expiry_idx, opt_type, strike_x100)`.
   - Expiry dates in the future (relative to trading_date) and within 90 days.
   - Strikes are multiples of the symbol's strike step (50 for NIFTY/FINNIFTY, 25 for MIDCPNIFTY, 100 for BANKNIFTY).
   - `high >= max(open, close) >= min(open, close) >= low`.
   - Reject the whole file on any violation (no partial loads).
4. **Sort** by `(expiry_idx, opt_type, strike_x100, ts_min)`.
5. **Append/replace into monthly Parquet** — append by writing the full month from the cleaned DataFrame plus prior data if present (idempotent).
6. **Build DaySnapshot** — see §4.3.
7. **Atomic publish**:
   - Write Parquet to `*.parquet.tmp`, fsync, rename.
   - Write snapshot to `*.arrow.tmp`, fsync, rename.
   - Insert/update `intraday_imports` row in Postgres in one transaction.
8. **Invalidate Redis cache keys** matching `intraday:{symbol}:{trading_date}:*`.

### 4.3 DaySnapshot builder (`backend/services/intraday_snapshot.py`)

Pure function: takes the cleaned day's DataFrame + spot data, returns the byte buffer for the Arrow IPC file.

Pseudocode:
```python
def build_day_snapshot(symbol: str, date: date, options_df: pl.DataFrame, spot_df: pl.DataFrame) -> bytes:
    # 1. Pin minute axis to 0..374 (9:15..15:30 IST)
    minutes = expand_to_full_session(options_df)
    # 2. Pick top-4 active expiries (nearest weekly + 3 nearest monthlies)
    expiries = pick_active_expiries(options_df, date)
    # 3. For each (expiry, minute) compute ATM = strike closest to spot[m]
    atm = compute_atm_per_minute(spot_df, options_df, expiries)
    # 4. For each expiry, build chain of ATM±5 (using day-open ATM as reference)
    chains = build_chains(options_df, expiries, atm)
    # 5. Pack into the binary layout from §3.6
    return pack_arrow_ipc(symbol, date, spot_df, expiries, atm, chains)
```

Pure, deterministic, unit-testable. **Has its own golden test** (`backend/tests/test_intraday_snapshot.py`).

### 4.4 Idempotency

`(symbol, date, sha256)` → no-op. `(symbol, date)` with new sha256 → replace. Ingest is safe to re-run on a directory of CSVs without producing duplicates or partial state.

---

## 5. Engine

### 5.1 Module: `backend/engines/intraday_engine.py` (NEW FILE)

Public function: `run_intraday_backtest(spec, date_from, date_to, symbol) -> Tradesheet`.

Style: vectorized Polars + Arrow throughout. **No row-by-row Python loops in the hot path.** Per-row logic is pushed into Rust kernels.

Sketch:
```python
def run_intraday_backtest(spec, date_from, date_to, symbol):
    snapshots = load_snapshots(symbol, date_from, date_to)        # mmap'd, returns SnapshotSet
    entries   = resolve_entry_signals(spec, snapshots)            # vectorized
    legs      = materialize_leg_basket(entries, spec, snapshots)  # vectorized
    pnl       = compute_pnl_curves(legs, snapshots)               # Polars groupby
    exits     = apply_exit_logic(pnl, spec)                       # vectorized first-hit via Rust
    return build_tradesheet(entries, exits, spec)
```

### 5.2 Rust kernels (extend `backend/native/`)

The Rust extension is **opportunistic**, not mandatory. Every kernel has a Polars-vectorized fallback in `services/rust_fast_path.py`. Rust is used where data shows Polars hits a wall — which on profiling is one specific case: **stateful path-dependent exit logic** (trailing SL, breakeven moves, re-entry).

**Must-have kernels (phase 3):**

```rust
// Mmap setup — shared once per worker process, not per request
fn intraday_open_dataset(symbol_dir: &str) -> DatasetHandle;

// O(1) array indexing into the day's snapshot
fn intraday_resolve_atm(h: &DatasetHandle, date: i32, minute: u16) -> (i32, i32);

// Returns a contiguous slice of a leg's full-day close curve (zero-copy where possible)
fn intraday_leg_curve(h: &DatasetHandle, date: i32, expiry_idx: i16, strike: i32,
                      opt_type: i8, t0: u16, t1: u16) -> Float64Array;

// Stateless first-cross scan; SIMD-friendly; covers fixed SL/target exits
fn intraday_first_hit(curve: &[i32], threshold: i32, direction: i8) -> i32;
```

**Must-have kernel (phase 6 — added during stateful exit support):**

```rust
// Full leg lifecycle in ONE Rust call. Handles trailing SL, breakeven moves,
// target priority, square-off, and computes MAE/MFE inline.
// Eliminates the per-minute Python<->Rust boundary cost for stateful exits.
fn intraday_leg_lifecycle(h: &DatasetHandle, date: i32,
                          leg: LegSpec, exits: ExitSpec) -> LegResult;
//   Returns: entry_min, entry_px, exit_min, exit_px, exit_reason, mae, mfe
```

**Nice-to-have kernel (phase 7+ — only if profiling shows need):**

```rust
// Whole-day strategy run in Rust. Adds 5-10x headroom for adversarial workloads.
// Defer until perf regression test fails the budget.
fn intraday_run_day(h: &DatasetHandle, date: i32, spec: &StrategySpec) -> DayResult;
```

**Compilation flags** (mandatory — leave 30–50% perf on the table without them):

```toml
# backend/native/.cargo/config.toml
[build]
rustflags = ["-C", "target-cpu=native", "-C", "opt-level=3", "-C", "lto=fat"]
```

`target-cpu=native` lets LLVM use AVX2 instructions on the i5-10500.

Existing `services/rust_fast_path.py` infrastructure is reused for loading the extension and providing Python wrappers. **EOD path is not affected** — all new functions are additive; existing native exports are unchanged.

### 5.2.1 What stays in Python (and why)

| Concern | Implementation | Reason |
|---|---|---|
| API routing, validation | FastAPI + pydantic | Glue, not hot path; Python overhead ~50 ms total |
| Strategy spec parsing | Python | One-time per request |
| Entry signal resolution | Polars vectorized | 30–80 ms even for signal-based; well within budget |
| Per-minute P&L curve math | Polars vectorized | SIMD via Arrow; faster than naive NumPy or Rust |
| Stateless exit logic (fixed SL/target/time) | Polars `arg_max` | One expression per leg; <80 ms for 1000 legs |
| MAE/MFE for stateless exits | Polars rolling min/max | Vectorized, fast |
| **Stateful exit logic (trailing SL, breakeven)** | **Rust `intraday_leg_lifecycle`** | Path-dependent state machine; Polars `.apply()` is Python-speed |
| Tradesheet aggregation | Polars groupby | Right tool |
| Arrow IPC serialization | pyarrow (C++ underneath) | Already optimal |

The senior-engineering principle: **rewrite when data demands it, not preemptively.** Profile in phase 7 with the perf-regression test; if any step exceeds its budget, push that step into Rust. The list above is what we expect to be needed; we'll know for sure after phase 4.

### 5.3 Strategy spec (intraday)

Extends the existing leg DSL in `backend/strategies/strategy_types.py` with intraday fields. New file: `backend/strategies/intraday_strategy.py`.

Required fields:
- `symbol`: one of `NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY`
- `entry_time`: `HH:MM` (IST), e.g. `"09:20"`
- `legs[]`:
  - `type`: `CE | PE`
  - `action`: `BUY | SELL`
  - `strike_selection`:
    - `mode`: `ATM | ATM_OFFSET | DELTA | PREMIUM_CLOSEST`
    - `value`: e.g. `+2` for `ATM_OFFSET`
  - `expiry`: `WEEKLY | MONTHLY | NEXT_WEEKLY | NEXT_MONTHLY`
  - `quantity`: lot multiple
  - `sl`: optional, percent or absolute
  - `target`: optional, percent or absolute
  - `trailing_sl`: optional
- `square_off_time`: `HH:MM`, default `15:15`
- `re_entry`: optional (later phase)

`PREMIUM_CLOSEST` and far-OTM `ATM_OFFSET` (>±5) are flagged at request time as **slow-path** strategies (see §6.2).

### 5.4 Tradesheet output

Returned as **Arrow IPC bytes** (not JSON). Frontend deserializes via the `apache-arrow` JS package. Same schema as EOD tradesheet plus `entry_minute` and `exit_minute` columns.

```python
from fastapi import Response

@router.post("/intraday/backtest")
async def run_intraday_backtest(req: IntradayBacktestRequest):
    arrow_bytes = await execute_intraday(req)   # returns bytes
    return Response(
        content=arrow_bytes,
        media_type="application/vnd.apache.arrow.stream",
    )
```

For a 1-year, 4-leg backtest with ~1000 trade rows, Arrow IPC is **5–10× faster** to serialize than `JSONResponse` and ~3× smaller on the wire (gzip helps both, but Arrow's columnar layout compresses better).

MAE/MFE (already supported by the EOD path via `BACKTEST_INCLUDE_MAE_MFE`) is computed from the per-minute high/low arrays — exact, not approximated.

### 5.5 API server stack (locked)

The intraday endpoint runs on **FastAPI + granian + ORJSONResponse + Arrow IPC**. This is the chosen stack after evaluating Litestar, Hono/Bun, Go (Fiber), Rust (Axum), and an nginx-direct-read cache-hit path.

**Components:**

| Component | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** (existing) | Pydantic v2 (Rust core), OpenAPI auto-gen, mature ecosystem, Celery integration |
| ASGI server | **granian** (replaces uvicorn) | Rust-implemented; ~2× faster than uvicorn at no code cost |
| Default JSON response | **ORJSONResponse** | 3–5× faster JSON than stdlib; one-line change |
| Tradesheet response | **Arrow IPC bytes** | Bypasses JSON entirely for the largest payload |

**Migration steps (phase 4 sub-tasks):**

1. Add `granian` and `orjson` to `backend/requirements.txt`.
2. Update `docker-compose.yml` backend command from uvicorn to granian:
   ```yaml
   command:
     - granian
     - --interface
     - asgi
     - --host
     - 0.0.0.0
     - --port
     - "8000"
     - --workers
     - "1"
     - --loop
     - uvloop
     - main:app
   ```
   (Note: granian supports uvloop natively. The healthcheck stays unchanged.)
3. Update `backend/main.py`:
   ```python
   from fastapi.responses import ORJSONResponse
   app = FastAPI(
       title="AlgoTest Clone API",
       version="1.0.0",
       lifespan=lifespan,
       default_response_class=ORJSONResponse,
   )
   ```
4. Update `backend/Dockerfile` if it pre-installs uvicorn explicitly — replace with granian, keep uvloop (granian uses it).
5. Update `backend/start_backend.py` (local-dev entrypoint) to invoke granian instead of uvicorn.
6. Update the `__main__` block at the bottom of `backend/main.py` similarly.
7. Frontend: add `apache-arrow` to `frontend/package.json` and a tradesheet decoder in `frontend/src/components/`.

**Why this is forward-compatible:** granian is a drop-in replacement; the FastAPI app object is unchanged. If we ever migrate to Litestar (option 3 in the evaluation), the ORJSONResponse and Arrow IPC patterns carry over unchanged. We're not painting into a corner.

**EOD path note:** the granian + ORJSONResponse changes apply to the **whole backend** (both EOD and intraday), since they're server-level. EOD response shapes and behavior are unchanged — only the serialization is faster. EOD tradesheet endpoints can adopt Arrow IPC later as an opportunistic optimization, but it's **out of scope** for this spec.

---

## 6. API & request flow

### 6.1 New router: `backend/routers/intraday.py`

```
POST /api/intraday/backtest
{
  "mode": "intraday",
  "symbol": "NIFTY",
  "date_from": "2024-01-01",
  "date_to": "2024-12-31",
  "strategy": { ... see §5.3 ... }
}
```

Request flow:
```
FastAPI router
  → validate payload (pydantic intraday schema)
  → canonical hash of strategy+dates+symbol
  → Redis GET intraday:result:{hash}                  ← L0 cache
      hit  → return immediately (~80 ms total)
      miss → enqueue Celery task on `backtests_intraday`
            → wait for result (with sane timeout)
            → cache in Redis (TTL 7 days)
            → return
```

### 6.2 Slow-path detection

Before enqueueing, the router runs a quick `requires_full_chain(spec)` check. If true:
- Response includes `slow_path: true` and an estimated latency hint.
- Frontend shows a yellow banner: "This strategy uses far-OTM strikes; backtest may take 20–60 seconds."
- Task is enqueued on a separate Celery queue `backtests_intraday_slow` with `concurrency=1` so it doesn't block fast-path users.

### 6.3 Existing `routers/backtest.py` is unchanged

The existing `/api/backtest` endpoint stays bound to the EOD code path. The frontend selects the endpoint by mode: it calls `/api/backtest` for `mode=eod` and `/api/intraday/backtest` for `mode=intraday`. No internal redirect, no shared dispatcher — clean isolation, no risk of regressing the EOD code path.

---

## 7. Concurrency & resource model

### 7.1 New Celery workers

Added to `docker-compose.yml`:

```yaml
worker-backtests-intraday:
  build: { context: ./backend, dockerfile: Dockerfile }
  command: >
    celery -A worker.celery worker
    --queues=backtests_intraday
    --concurrency=3
    -l info
    --max-memory-per-child=2200000
    --without-gossip --without-mingle --without-heartbeat
  environment: <<: *backend-env
  volumes: *backend-volumes
  deploy:
    resources:
      limits: { memory: 2500M, cpus: "3.0" }
      reservations: { memory: 512M }

worker-backtests-intraday-slow:
  command: >
    celery -A worker.celery worker
    --queues=backtests_intraday_slow
    --concurrency=1
    -l info
    --max-memory-per-child=1300000
  deploy:
    resources:
      limits: { memory: 1500M, cpus: "1.5" }
```

### 7.2 Memory budget (revised for intraday addition)

Total physical RAM: 16 GB. Hard limits must sum to ≤ ~15.5 GB so we never trigger swap.

| Service | Limit | Δ vs current |
|---|---|---|
| Postgres | 3000 MB | −500 |
| Redis | 1500 MB | +800 (now critical for L0 cache) |
| Backend FastAPI | 1500 MB | −1000 (router-only, no engine work) |
| EOD `worker-backtests` | 2500 MB | −1000 |
| EOD `worker-backtests-fast` | 1500 MB | −700 |
| `worker-uploads` | 500 MB | unchanged |
| Frontend | 200 MB | unchanged |
| **NEW `worker-backtests-intraday`** | **2500 MB** | new |
| **NEW `worker-backtests-intraday-slow`** | **1500 MB** | new |
| OS + kernel | ~1500 MB | — |
| **Total committed** | **~16,200 MB** | |

The committed total still nudges 200 MB over physical RAM. This is **intentional and safe** because:

1. The two intraday workers share OS-mmap'd snapshot pages — their effective resident memory is shared, not summed.
2. EOD workers and intraday workers rarely peak simultaneously (a given user is in one mode at a time).
3. Docker hard-limits prevent any single service from OOMing the box; reservations are sized to guarantee a minimum.
4. The OS page cache uses whatever RAM is currently free, so committed-but-unused worker memory becomes cache automatically.

Watchdog: `/health/stats` already reports total resident memory. If aggregate ever exceeds 14.5 GB sustained, we lower `worker-backtests-fast` to 1000 MB — its workload is largely replaced once intraday is live.

### 7.3 OS page cache plan

- ~1.5 GB headroom remains for page cache.
- Most-recent year × 4 symbols = **540 MB** of DaySnapshots — stays hot easily.
- Nightly cron at 06:00 IST runs `vmtouch -t /data/intraday/*/snapshots/{current_year}/*.arrow` to pre-warm before market open.
- Optional: `vmtouch -dl <files>` to lock pages (only if eviction proves problematic).

### 7.4 Cache hierarchy

| Tier | Storage | Size | TTL | Hit ratio target |
|---|---|---|---|---|
| L0 result | Redis `services/backtest_cache.py` (extended) | up to 1.5 GB (~15K results) | 7 days | 70–85% |
| L1 daysnapshot mmap | OS page cache | up to 1.5 GB | until evicted | ~99% within current year |
| L1.5 vmtouch-pinned | OS page cache (forced) | ~500 MB | nightly refresh | covers prior 1–2 years |
| L2 Parquet | HDD | 100+ GB | always | rare path |

The L0 hash key **must** be canonical: normalized JSON, sorted leg order, ISO dates, lowercase symbol. Otherwise the 50-user hit ratio collapses.

---

## 8. Frontend changes

### 8.1 Mode toggle in `StrategyBuilder.jsx`

A toggle at the top of the strategy form: `EOD` | `Intraday`.

- `EOD` (default): existing UI, no changes.
- `Intraday`: shows extra fields:
  - Entry time (default `09:20`)
  - Square-off time (default `15:15`)
  - Per-leg SL/target (always visible, currently optional)
  - Symbol dropdown limited to the 4 supported indexes

Mode is stored on the strategy object and passed as `mode: "intraday" | "eod"` in the API payload.

### 8.2 Slow-path warning banner

Component: `IntradaySlowPathWarning.jsx`. Renders when the API response or pre-flight call indicates `slow_path: true`. Shows estimated latency and the reason (e.g., "Strike offset > ±5 falls back to full Parquet").

### 8.3 Tradesheet rendering

Existing `ResultsPanel.jsx` already handles per-trade rows with MAE/MFE. Two new columns surfaced for intraday: `entry_time` (HH:MM) and `exit_time`. P&L curve gains a 1-minute resolution mode.

---

## 9. Observability

### 9.1 Metrics (added to `/health/stats`)

- `intraday.cache.l0.hit_ratio` (Redis)
- `intraday.cache.l1.page_cache_resident_mb`
- `intraday.queue.depth` (`backtests_intraday`, `backtests_intraday_slow`)
- `intraday.engine.last_run_ms` (rolling histogram)
- `intraday.snapshot.bytes_loaded` (per request)

### 9.2 Logging

Per-request structured log:
```
event=intraday_backtest
symbol=NIFTY
date_range=2024-01-01..2024-12-31
strategy_hash=abc123...
path=cache_hit | warm_mmap | cold | slow_parquet
duration_ms=87
```

Sampled to `backend/logs/intraday.log`, rotated daily.

### 9.3 Health endpoint

`GET /api/intraday/health` returns:
```json
{
  "snapshot_count": 1042,
  "symbols_ready": ["NIFTY"],
  "earliest_date": "2024-01-01",
  "latest_date": "2024-12-31",
  "cache_warm": true
}
```

---

## 10. Testing strategy

### 10.1 Golden tests

- `test_intraday_snapshot_golden.py` — feed a known CSV through ingest, assert byte-exact DaySnapshot output.
- `test_intraday_engine_golden.py` — run a fixed straddle on a fixed day, assert tradesheet matches a checked-in expected fixture.
- `test_intraday_native_golden.py` — verify Rust kernel outputs match a Python reference implementation on a synthetic day.

### 10.2 Property tests

- `test_intraday_snapshot_invariants.py` — for any valid input, output passes the §3.6 schema invariants (header magic, byte alignment, monotonic minutes).
- `test_intraday_engine_invariants.py` — P&L ledger sums match per-leg P&L sums; no leg has exit_minute < entry_minute.

### 10.3 Performance regression test

`test_intraday_perf.py` (excluded from default `unittest discover`, run on demand):
- Single 1-year NIFTY straddle backtest with hot cache: assert p50 < 700 ms, p95 < 1100 ms.
- 10 concurrent diverse runs: assert all complete in <3 s, no OOM.

### 10.4 EOD non-regression

- Existing `backend/tests/test_*.py` must still pass with intraday wiring in place.
- Canary EOD backtest before/after: identical tradesheet hash.

---

## 11. Phase plan

Each phase is a runnable, mergeable slice. **Order matters** — later phases depend on earlier outputs.

| # | Scope | Done when |
|---|---|---|
| 0 | (Skipped — no hardware upgrade) | — |
| 1 | Parquet writer + ingest one month of NIFTY 2024 | One file at `/data/intraday/NIFTY/options/year=2024/month=03/options.parquet` validated, manifest row inserted |
| 2 | DaySnapshot builder + golden test | 22 snapshot files for March 2024; `test_intraday_snapshot_golden.py` green |
| 3 | Rust kernels (`open_dataset`, `resolve_atm`, `leg_curve`, `first_hit`) + golden tests | `test_intraday_native_golden.py` green |
| 4 | Engine for fixed-time short-straddle end-to-end. **Includes API stack swap: uvicorn→granian, ORJSONResponse default, Arrow IPC tradesheet response.** See §5.5 sub-tasks. | `POST /api/intraday/backtest` returns valid Arrow-IPC tradesheet for 1 month NIFTY in p50 < 400 ms; `granian` confirmed running via `/health`; existing EOD endpoints regression-tested green |
| 5 | Frontend mode toggle + intraday strategy form fields + slow-path warning | User can run a 1-month intraday straddle through the UI |
| 6 | Multi-leg + per-leg SL/target/trailing + square-off time. **Adds Rust `intraday_leg_lifecycle` for stateful exits.** | 4-leg iron condor 1 month NIFTY in p50 < 600 ms; trailing-SL strategy benchmarked against Polars-only fallback |
| 7 | Backfill all of 2024 NIFTY + nightly vmtouch warmup | 1-year backtest at p95 < 1100 ms with warm cache |
| 8 | Add BANKNIFTY, FINNIFTY, MIDCPNIFTY (data + ingest only) | Same engine works on all four symbols |
| 9 | Backfill 2023 (clean format) | 2 years live for all four symbols |
| 10 | 2017–2022 dirty-data cleaning + backfill (tracked separately) | 9 years live |

Phases 1–7 are MVP. Phases 8–10 are data-only and don't change the architecture.

---

## 12. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| OS page cache thrashes under 50-user load with diverse date ranges | Medium | Slow-path becomes common | vmtouch warmup, smaller snapshots, recommend SSD upgrade later |
| ATM±5 insufficient for many strategies in practice | Medium | Slow-path more frequent than estimated | Telemetry tracks slow-path %; if >5%, widen to ±7 (cost: 40% more page cache use) |
| 2017–2022 data formats more varied than expected | High | Phase 10 slips | Build cleaners incrementally per format; keep ingestion plug-in based |
| Rust extension build issues on team machines | Low | Engine slow but functional | All Rust functions have a Python reference fallback path in `services/rust_fast_path.py` |
| 50 simultaneous users overrun memory limits | Low | OOM on a worker | Hard memory limits + Celery `--max-memory-per-child` recycling; queue depth monitored |
| Redis result cache eviction under load | Low | Re-computation, slower p95 | 1.5 GB Redis with `allkeys-lru` already configured |
| Manifest/Postgres divergence from filesystem | Medium | "Ghost" snapshots not tracked | Atomic write+rename+insert in single transaction; nightly `intraday_audit` job reconciles |

---

## 13. Open questions deferred to implementation plan

These are intentionally **not** answered in this design — the implementation plan will resolve them:

1. Exact Polars expression layout for vectorized exit logic (likely `arg_max` of first-cross).
2. Whether to put the Rust-Python boundary at the per-day level or per-strategy-run level.
3. Specific JSON schema for strategy spec validation (extends pydantic models in `backend/strategies/`).
4. Exact format of the source CSVs for 2024 NIFTY (need to inspect a sample before writing the cleaner).
5. Whether to use Arrow IPC v1 or v2 for the snapshot files.
6. UI design for the intraday-specific strategy form fields (handled in frontend phase).

---

## 14. Acceptance criteria for "design done"

- [ ] All four symbols (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY) ingestible end-to-end.
- [ ] 1-year NIFTY backtest p95 < 1100 ms on warm cache, single-user.
- [ ] 50 concurrent diverse-strategy requests do not OOM, do not regress EOD path latency.
- [ ] Existing EOD tests pass unchanged.
- [ ] Cold-path slow case observable and documented (telemetry visible in `/health/stats`).
- [ ] Spec slow-path strategies correctly route to the slow queue; UI banner appears.
- [ ] `vmtouch` warmup script wired into a cron in `worker-uploads`.
- [ ] Manifest reconciliation job exists and is tested.

---

## Appendix A: Why DuckDB was *not* chosen as the primary query engine

DuckDB was the first instinct for cold-tier reads, and it's still the right answer if we ever need ad-hoc SQL over the cold Parquet — we'll keep `duckdb` in the requirements so a developer can `duckdb.sql("...")` the Parquet directly. But for the **runtime hot path**, we read snapshots through Polars + the Rust extension, not DuckDB, because:

1. The hot path is per-leg slice reads from a known offset — a direct mmap is faster than DuckDB's query planner overhead.
2. Vectorized engine logic naturally lives in Polars; switching engines mid-pipeline costs serialization.
3. DuckDB query startup is ~10–50 ms; we can't pay that 250 times in a loop.

DuckDB is retained as the **slow-path** (Layer 2) query engine when the chain falls outside ATM±5, because at that point we're already paying 20–60 s and DuckDB's predicate pushdown is the right tool.

## Appendix B: Why not Postgres / TimescaleDB / ClickHouse / QuestDB

- **Postgres heap:** 5–8× larger on disk than Parquet, random I/O on HDD makes it the wrong shape. Existing `option_data` table stays for EOD; intraday doesn't go there.
- **TimescaleDB:** ~40× slower than Parquet/QuestDB on OHLCV bar queries in published 2025 benchmarks. Operationally identical to Postgres; gains nothing on this hardware.
- **ClickHouse:** Strong on 4+ machines / SSD / multi-tenant. Adds 1.5–2 GB RAM for the server process and a separate ops surface. For 50 in-house users on one box, the marginal benefit doesn't justify it.
- **QuestDB:** Best raw OHLCV speed, but its sweet spot is high-frequency *ingestion*; we ingest once per day from CSV. Operationally a separate server. Same disqualifier as ClickHouse.

If usage grows to 200+ users or multi-machine, ClickHouse becomes the right migration target — and Parquet is the natural source data format for that migration (ClickHouse ingests Parquet natively). The architecture is forward-compatible.

## Appendix C: Backend language strategy

### Why we are NOT rewriting the backend

The hot path is already not Python — it's vectorized Polars (which is Rust under the hood, with SIMD and Arrow column layout) plus a Rust extension via `backend/native/`. Where time actually goes in a 1-year backtest:

| Layer | Language | Time budget |
|---|---|---|
| FastAPI routing, validation, gzip | Python | ~50 ms |
| Polars-vectorized engine logic | Python → Rust (Polars) | ~150–300 ms |
| Rust kernels for hot loops | Rust | ~50–150 ms |
| Arrow IPC serialization | Python → C++ (pyarrow) | ~5–15 ms |
| **Total typical** | mixed | **~250–500 ms** |

Rewriting the FastAPI/Celery layer in Go saves ~30–50 ms on a 500 ms request. Cost: 2–3 months of engineering, loss of FastAPI's automatic OpenAPI generation (the frontend depends on it), and replacing Celery's mature task ecosystem. **Wrong trade.**

### Where we DO use Rust (and what we don't)

**Use Rust for:**
- Memory-mapping snapshot files (`intraday_open_dataset`)
- O(1) array indexing into snapshots (`intraday_resolve_atm`)
- Zero-copy slice extraction (`intraday_leg_curve`)
- Stateless first-cross scans (`intraday_first_hit`)
- **Stateful exit logic — trailing SL, breakeven moves, re-entry** (`intraday_leg_lifecycle`) ← the one place Polars hits a wall

**Do NOT use Rust for:**
- Anything Polars already vectorizes (you'd be reimplementing Polars)
- API routing, validation, serialization (Python overhead is negligible there)
- Strategy spec parsing (one-time per request)
- Tradesheet aggregation (Polars groupby is the right tool)

### What we considered and rejected

| Alternative | Verdict |
|---|---|
| Full Go/Rust backend rewrite | Saves ~50 ms; costs 3 months; loses ecosystem; rejected |
| Replace Polars with raw NumPy | Polars is faster than NumPy on this workload (Arrow + parallelism); rejected |
| Replace Polars with hand-written Rust DataFrames | Reimplements Polars; rejected |
| Cython / Numba | ~10% gain over Polars; adds build dependency; existing Rust extension is cleaner; rejected |
| Move Celery to NATS / RabbitMQ | Queue is not the bottleneck; rejected |
| **Replace FastAPI with Litestar** | Saves ~2 ms per request; ~1–2 days migration touching every router/model; not justified at 50 users. Forward-compatible if needed later. |
| **Replace FastAPI with Hono/Fiber/Axum sidecar** | Saves ~3–5 ms; introduces second language, dual deploy, Celery integration friction; rejected |
| **nginx + Redis direct-read for cache hits** | Sub-millisecond cache-hit path but only for hits; complex Lua/njs config; ops surface explodes; rejected |

### What we ARE adopting at the API layer (locked — see §5.5)

| Change | Gain | Cost |
|---|---|---|
| uvicorn → **granian** | ~10–20 ms per request | docker-compose.yml + Dockerfile + requirements.txt edits |
| `default_response_class=`**`ORJSONResponse`** | ~5 ms per JSON response | one line in `main.py` |
| Tradesheet response as **Arrow IPC** bytes | 5–10× faster on large tradesheets | new endpoint shape + frontend `apache-arrow` decoder |

These three together shave ~30–40 ms off every intraday request, with no language change and no framework rewrite.

### When to revisit

If profiling in phase 7 (1-year backtest perf-regression test) shows any of these, push more into Rust:

- Polars `apply()` callbacks dominating profile → that operation moves to Rust
- Python<->Rust FFI overhead per minute > 10% of request → batch into `intraday_run_day`
- More than 30% of total time in Python orchestration → consider compiled glue (Cython for the hot dispatch only, not whole rewrite)

**Senior-engineering principle:** rewrite when data demands it, not preemptively. Most teams that "rewrite to Go/Rust for performance" find afterward that 80% of their gain came from one specific hot loop they could have ported in a week.

## Appendix D: When to revisit this design

- If concurrent load exceeds 100 users, or the box is replaced with a multi-machine setup → move cold tier to ClickHouse or DuckDB-on-shared-storage.
- If SSD is added → widen DaySnapshots to ATM±10, raise `concurrency` from 3 to 5, drop slow-path UI warnings.
- If 32 GB RAM is added → vmtouch entire history, expect <500 ms cold-path for any year.
- If we add tick data later (sub-minute) → DaySnapshot format needs a new version; bump `version: u8` byte in the header.
