# AlgoTest Backend Performance Optimization — Senior Engineer Skill Guide

## Overview & Diagnosis

This document is the authoritative engineering reference for making the AlgoTest
FastAPI backend fast at scale (50+ concurrent users, backtest results in < 5 seconds,
CSV uploads in < 3 seconds). Every recommendation is grounded in the actual code
(base.py, backtest.py, generic_multi_leg.py, market_data_repository.py, database.py,
docker-compose.yml).

**Root causes of current slowness (in priority order):**

1. Backtest endpoint is synchronous and blocks the event loop for CPU-heavy work
2. Bulk data load happens per-request instead of being shared across concurrent users
3. In-process cache is per-worker — Celery workers and the API server don't share state
4. DB connection pool (size=10, overflow=5) is too small for concurrent backtests
5. `BacktestDataCache._load_date_data` calls `iterrows()` — the slowest possible Pandas loop
6. `warm-cache` creates a fresh `ThreadPoolExecutor(max_workers=1)` per request (leaks threads)
7. CSV upload runs synchronously in the API worker for large files
8. Redis cache key ignores the `strategy` dict — different strategies get the same cache hit
9. PostgreSQL `shared_buffers=2GB` is correct but `work_mem=64MB` causes disk spills for sort-heavy queries
10. No HTTP-level response compression (large trade lists serialise to MB of JSON)

---

## Part 1 — Language Choice Decision

**Keep Python. Do NOT rewrite in Go/Rust yet.**

Here is the honest engineering assessment:

| Concern | Reality |
|---------|---------|
| Python is slow for backtesting | Already using Polars + NumPy — the computation is fast. The slowness is I/O and concurrency, not CPU. |
| Go/Rust would be faster | True for pure compute, but the data stack (SQLAlchemy, Pandas, Polars, Redis) has no direct equivalent. A rewrite would take 3–6 months and introduce new bugs. |
| Better option | Fix async model, shared cache, and DB pooling in Python first. This alone will deliver 5–20× speedup within 1–2 weeks. |

**If backtest computation itself (not I/O) still takes > 10 seconds after all fixes
below are applied**, then consider rewriting the hot inner loop (option premium
lookup + P&L calculation) in Rust via PyO3 bindings. Keep Python for everything else.

---

## Part 2 — Critical Fixes (Must Do First)

### Fix 1: Stop blocking the FastAPI event loop

**Problem:** The `/backtest` endpoint calls `run_generic_multi_leg()` directly. FastAPI
is async but this is a synchronous, CPU-bound function. It blocks the event loop —
no other requests can be served while one backtest runs.

**Current code (backtest.py ~line 1654):**
```python
df, summary, pivot = execute_strategy(strategy_def, params)
```

**Fix:**
```python
import asyncio
from concurrent.futures import ProcessPoolExecutor

# Module-level pool — NOT inside the route handler
_process_pool = ProcessPoolExecutor(max_workers=4)  # One per CPU core

@router.post("/dynamic-backtest")
async def run_dynamic_backtest(request: dict):
    loop = asyncio.get_running_loop()
    # run_in_executor offloads to a separate process — event loop stays free
    result = await loop.run_in_executor(
        _process_pool,
        _run_strategy_worker,  # must be a top-level importable function
        strategy_def,
        params
    )
    return result
```

**Why ProcessPoolExecutor, not ThreadPoolExecutor?**
Python's GIL means threads don't run Python code in parallel. Backtest computation is
pure Python (Pandas/Polars loops, P&L math). Use processes for true parallelism.
Use threads only for I/O-bound work (DB queries, Redis calls).

The existing `_backtest_executor = ThreadPoolExecutor(max_workers=3)` in backtest.py
is already a step in the right direction for I/O but insufficient for CPU-bound work.

### Fix 2: Shared in-memory cache with `multiprocessing.shared_memory` or Redis

**Problem:** `_option_lookup_table` in base.py is a module-level dict. Each Uvicorn
worker has its own copy. With `--workers 4`, data is loaded 4 times, consuming 4× RAM
and 4× DB bandwidth.

**Fix A (simple, works today):** Pin Uvicorn to 1 worker + use ProcessPoolExecutor
for concurrency:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --loop uvloop
```
One process holds the shared dict. ProcessPool workers receive the needed data as
function arguments (serialised via pickle — acceptable for dict lookups).

**Fix B (production-grade):** Store the bulk-loaded lookup dict in Redis as MessagePack:
```python
import msgpack, redis

r = redis.Redis.from_url(REDIS_URL)

def store_lookup_in_redis(symbol, from_date, to_date, lookup_dict):
    key = f"bulk:{symbol}:{from_date}:{to_date}"
    r.set(key, msgpack.packb(lookup_dict), ex=86400)  # 24h TTL

def get_lookup_from_redis(symbol, from_date, to_date):
    key = f"bulk:{symbol}:{from_date}:{to_date}"
    data = r.get(key)
    if data:
        return msgpack.unpackb(data)
    return None
```
All workers and Celery tasks share the same Redis-backed cache. First request pays
the load cost; all subsequent requests (same symbol/range) are instant.

### Fix 3: Fix the cache key bug in BacktestCache

**Problem (backtest.py lines 36–44):**
```python
serializable = {k: v for k, v in params.items() if k != 'strategy'}
```
The strategy definition (legs, strikes, expiry types) is excluded from the cache key!
Different strategies with the same date range return the same cached result — this is
a correctness bug, not just a performance issue.

**Fix:**
```python
def _make_key(self, params: dict) -> str:
    try:
        key_str = json.dumps(params, sort_keys=True, default=str)
    except Exception:
        key_str = repr(params)
    return hashlib.sha256(key_str.encode()).hexdigest()
```
Use sha256 (not md5 — md5 has collision risk for security-sensitive keys).
Include the full params dict. `default=str` handles datetime, Enum, Pydantic models.

### Fix 4: Fix thread leak in warm-cache endpoint

**Problem (backtest.py lines 152–161):**
```python
def _load():
    ...
executor = ThreadPoolExecutor(max_workers=1)
executor.submit(_load)   # executor is never shut down — leaks OS threads
```

**Fix:** Use the module-level `_backtest_executor` that already exists:
```python
_backtest_executor.submit(_load)
```
Or use `asyncio.create_task` if the warm-cache function is made async.

### Fix 5: Replace `iterrows()` in BacktestDataCache with vectorized code

**Problem (base.py lines 105–128):**
```python
for _, row in df.iterrows():   # O(N) Python loop over millions of rows
    symbol = row['SYMBOL']
    ...
```
`iterrows()` is 100–1000× slower than vectorized Pandas or Polars operations.

**Fix (Polars vectorized dict build — already partially done in bulk_load_options):**
```python
# Use the same pattern already in bulk_load_options (base.py lines 2566–2580)
opt_only = pl.from_pandas(df).filter(pl.col("INSTRUMENT").is_in(["OPTIDX","OPTSTK"]))
dates_l    = opt_only["DATE"].dt.strftime("%Y-%m-%d").to_list()
symbols_l  = opt_only["SYMBOL"].to_list()
strikes_l  = opt_only["STRIKE_PR"].cast(pl.Int64).to_list()
opt_l      = opt_only["OPTION_TYP"].to_list()
expiries_l = opt_only["EXPIRY_DT"].to_list()
closes_l   = opt_only["CLOSE"].to_list()

self.option_cache[date_str] = {
    (s, k, o, e): c
    for s, k, o, e, c in zip(symbols_l, strikes_l, opt_l, expiries_l, closes_l)
}
```

---

## Part 3 — Database Optimisation

### DB Pool sizing

**Current (database.py):**
```python
POOL_SIZE = 10
MAX_OVERFLOW = 5
```
Total = 15 connections. With 50 concurrent users each running a backtest that
does multiple DB round-trips, this pool exhausts in seconds.

**Fix:**
```python
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
```
Also increase PostgreSQL's `max_connections` in docker-compose.yml:
```yaml
command: >
  postgres
  -c max_connections=200
  -c shared_buffers=2GB
  -c work_mem=16MB        # Reduce from 64MB — more sessions, less RAM each
  -c effective_cache_size=6GB
  -c maintenance_work_mem=256MB
  -c random_page_cost=1.1
  -c effective_io_concurrency=200
  -c max_parallel_workers_per_gather=2
  -c max_parallel_workers=8
  -c wal_buffers=16MB
  -c checkpoint_completion_target=0.9
```
Note: `work_mem=64MB × 200 connections × 2 sort ops = 25 GB` — that crashes the host.
Lower `work_mem` to 16MB when max_connections is high.

### Required PostgreSQL indexes (add to migration 005)

```sql
-- option_data: the most frequently queried table
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_symbol_date
    ON option_data (symbol, trade_date);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_symbol_date_expiry
    ON option_data (symbol, trade_date, expiry_date);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_option_symbol_date_expiry_strike_type
    ON option_data (symbol, trade_date, expiry_date, strike_price, option_type);

-- spot_data
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_spot_symbol_date
    ON spot_data (symbol, trade_date);

-- expiry_calendar
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_expiry_symbol_type
    ON expiry_calendar (symbol, expiry_type, current_expiry);
```

`CONCURRENTLY` means the index builds without locking the table — safe in production.

### Eliminate chunked queries for bulk loads

`market_data_repository.get_spot_data()` still uses `_chunk_date_ranges()` which fires
40+ sequential DB round-trips for a 7-year range. The `get_options_bulk()` already fixed
this. Apply the same single-query pattern to `get_spot_data()`:

```python
def get_spot_data(self, symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
    ...
    # Single query, no chunking
    with self.engine.begin() as conn:
        df = pd.read_sql(q, conn, params={"symbol": symbol.upper(),
                                           "from_date": from_date,
                                           "to_date": to_date},
                         chunksize=50_000)
    return pd.concat(df, ignore_index=True)
```

---

## Part 4 — FastAPI & Uvicorn Configuration

### Use uvloop (2–4× faster event loop)

```bash
pip install uvloop
uvicorn main:app --loop uvloop --workers 1 --host 0.0.0.0 --port 8000
```

Or in code (main.py):
```python
import uvloop
uvloop.install()  # Must be called before any asyncio usage
```

### Add response compression

Large backtest results (1000s of trades) serialise to 2–5 MB of JSON. GZip compression
reduces this to 200–500 KB — a 5–10× bandwidth saving.

```python
# main.py
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

### Add proper request timeouts

```python
from starlette.middleware.timeout import TimeoutMiddleware
app.add_middleware(TimeoutMiddleware, timeout=120)  # 2 min max per request
```

### Stream large backtest results

Instead of collecting all trades into memory and serialising as one JSON blob, stream
the response:

```python
from fastapi.responses import StreamingResponse
import json

@router.post("/backtest/stream")
async def stream_backtest(request: dict):
    async def generate():
        # yield header
        yield '{"trades":['
        first = True
        for trade in trade_generator(params):
            if not first:
                yield ','
            yield json.dumps(trade, default=str)
            first = False
        yield f'],"summary":{json.dumps(summary)}' + '}'
    
    return StreamingResponse(generate(), media_type="application/json")
```

---

## Part 5 — CSV Upload Performance

**Problem:** Large CSV files (option_data, 500MB+) are uploaded synchronously. The
API worker is blocked while reading the file and migrating to PostgreSQL.

**Fix: Async chunked upload + Celery background task**

```python
# upload.py — revised flow
@router.post("/data/upload")
async def upload_csv(data_type: str = Form(...), file: UploadFile = File(...)):
    # 1. Save file to disk asynchronously (non-blocking)
    temp_path = await _save_upload_async(file)
    
    # 2. Queue the heavy migration as a Celery task
    task = migrate_csv_task.apply_async(args=[str(temp_path), data_type])
    
    # 3. Return job ID immediately — don't wait
    return {"status": "queued", "job_id": task.id, "file": file.filename}

@router.get("/data/upload/{job_id}")
async def check_upload_status(job_id: str):
    task = celery_app.AsyncResult(job_id)
    ...
```

Add a new Celery task in tasks.py:
```python
@celery_app.task(bind=True)
def migrate_csv_task(self, temp_path: str, data_type: str):
    self.update_state(state='PROCESSING', meta={'progress': 0})
    migrator = Migrator(force=True)
    import_fn = getattr(migrator, DATA_TYPE_METHODS[data_type])
    result = import_fn(Path(temp_path))
    os.unlink(temp_path)
    return result
```

For truly fast CSV ingestion into PostgreSQL, use `COPY` instead of row-by-row INSERT:
```python
# migrate_data.py
import io
from sqlalchemy import text

def _bulk_insert_via_copy(conn, df: pd.DataFrame, table: str):
    """10–100× faster than INSERT for large CSVs."""
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    raw = conn.connection
    cursor = raw.cursor()
    cols = ", ".join(df.columns)
    cursor.copy_expert(
        f"COPY {table} ({cols}) FROM STDIN WITH CSV",
        buffer
    )
```

---

## Part 6 — Redis Caching Strategy

### Cache backtest results correctly

```python
# services/backtest_cache.py
import redis, msgpack, hashlib, json

class RedisBacktestCache:
    def __init__(self, redis_url: str, ttl: int = 86400):
        self.r = redis.Redis.from_url(redis_url, decode_responses=False)
        self.ttl = ttl

    def _key(self, params: dict) -> str:
        raw = json.dumps(params, sort_keys=True, default=str).encode()
        return "bt:" + hashlib.sha256(raw).hexdigest()

    def get(self, params: dict):
        data = self.r.get(self._key(params))
        return msgpack.unpackb(data, raw=False) if data else None

    def set(self, params: dict, result: dict):
        self.r.set(self._key(params), msgpack.packb(result), ex=self.ttl)
```

`msgpack` is 3–5× faster than `json` and produces 30–50% smaller payloads.
`redis.Redis` with connection pooling handles all workers sharing one cache.

### Cache warming on startup

```python
# main.py startup event
@app.on_event("startup")
async def startup():
    # Pre-load commonly used symbol/range combos in background
    asyncio.create_task(_warm_common_data())

async def _warm_common_data():
    await asyncio.sleep(5)  # Let server finish starting
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_backtest_executor, bulk_load_options,
                                "NIFTY", "2020-01-01", "2024-12-31")
```

---

## Part 7 — Celery Worker Tuning

### Current config issues (celery.py):
- `worker_concurrency=2` — too low; workers sit idle most of the time
- No task routing — fast tasks (cache check) and slow tasks (backtest) share the same queue

**Fix: Separate queues + higher concurrency**

```python
# celery.py
celery_app.conf.update(
    task_routes={
        'worker.tasks.run_backtest_task': {'queue': 'backtests'},
        'worker.tasks.migrate_csv_task':  {'queue': 'uploads'},
        'worker.tasks.health_check':       {'queue': 'fast'},
    },
    worker_prefetch_multiplier=1,   # Don't grab more tasks than you can run
    task_acks_late=True,            # Only ack after success — prevents lost tasks on crash
    task_reject_on_worker_lost=True,
)
```

**docker-compose.yml — separate worker containers:**
```yaml
worker-backtests:
  command: celery -A worker.celery worker -Q backtests -c 4 -l info
  ...

worker-uploads:
  command: celery -A worker.celery worker -Q uploads -c 2 -l info
  ...
```

---

## Part 8 — Monitoring & Observability

Without metrics you cannot prove the fixes are working. Add these:

### Prometheus + Grafana (5 minutes to set up)

```python
# main.py
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)
```

Add to docker-compose.yml:
```yaml
prometheus:
  image: prom/prometheus
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml

grafana:
  image: grafana/grafana
  ports:
    - "3001:3000"
```

### Key metrics to watch:
- `http_request_duration_seconds{path="/api/backtest"}` — must be < 5s at P95
- `db_pool_checked_out` — must stay below `POOL_SIZE + MAX_OVERFLOW`
- Redis `hit_rate` — should be > 60% for repeated backtest params
- Celery `task_runtime_seconds` — should be < 30s for backtests

### Add pool status to health endpoint

```python
@app.get("/health/db")
async def db_health():
    return get_pool_status()  # already implemented in database.py
```

---

## Part 9 — Docker Compose Production Hardening

```yaml
# docker-compose.yml additions

backend:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
  environment:
    - DB_POOL_SIZE=20
    - DB_MAX_OVERFLOW=10
    - DB_STATEMENT_TIMEOUT=120000   # 2 min (down from 5 min)
    - UVICORN_WORKERS=1             # Single worker + ProcessPool
    - UVICORN_LOOP=uvloop

worker-backtests:
  build:
    context: ./backend
    dockerfile: Dockerfile
  command: celery -A worker.celery worker -Q backtests -c 4 -P prefork -l info
  deploy:
    resources:
      limits:
        cpus: '4'
        memory: 8G   # Bulk data dict can be large

redis:
  command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru
  # allkeys-lru evicts least-recently-used keys under memory pressure
```

---

## Part 10 — Step-by-Step Implementation Roadmap

Work through these in order. Each step can be deployed independently.

### Week 1 — Critical (10–20× speedup expected)

| Day | Task | File | Impact |
|-----|------|------|--------|
| 1 | Fix cache key bug (Fix 3) | backtest.py | Correctness + cache validity |
| 1 | Fix thread leak in warm-cache (Fix 4) | backtest.py | Prevents OOM crash under load |
| 2 | Switch to `ProcessPoolExecutor` for backtests (Fix 1) | backtest.py | Unblocks event loop |
| 2 | Switch Uvicorn to 1 worker + uvloop | Dockerfile / startup | Reduces RAM, enables shared cache |
| 3 | Replace `iterrows()` with vectorized Polars (Fix 5) | base.py | 10–100× dict build speedup |
| 4 | Add GZip middleware | main.py | 5× smaller API responses |
| 4 | Fix spot_data chunking | market_data_repository.py | Eliminates 40 DB round-trips |
| 5 | Add missing DB indexes (migration 005) | SQL migration | 5–50× faster queries |

### Week 2 — High priority (additional 3–5× speedup)

| Day | Task | File | Impact |
|-----|------|------|--------|
| 6–7 | Redis-backed shared lookup cache (Fix 2B) | services/ | Cross-worker cache sharing |
| 8 | Async CSV upload + Celery task (Part 5) | upload.py, tasks.py | Upload returns instantly |
| 9 | `COPY`-based bulk insert for CSV migration | migrate_data.py | 10–100× faster CSV import |
| 10 | Separate Celery queues (Part 7) | celery.py, docker-compose | Prevents upload blocking backtest |

### Week 3 — Production hardening

| Day | Task |
|-----|------|
| 11–12 | Prometheus + Grafana setup |
| 13 | Startup cache warming |
| 14 | Load test with Locust, verify P95 < 5s |

---

## Part 11 — Quick Reference: What NOT to Do

| Temptation | Why it's wrong |
|-----------|----------------|
| Rewrite in Go/Rust now | 3–6 month cost for 2–3× gain; Python fixes will give 10–20× gain in 2 weeks |
| Increase `work_mem` further | Already at 64MB × max_connections = potential OOM; reduce it |
| Add more Uvicorn workers | More workers = more independent in-memory caches = more RAM + DB load |
| Use `asyncio.sleep(0)` to yield the event loop in backtest | The backtest is CPU-bound; yielding doesn't help — use ProcessPool |
| Cache strategy results in-process dict only | Doesn't survive worker restart and isn't shared between workers |
| Use `ThreadPoolExecutor` for backtest computation | GIL prevents true parallelism for CPU-bound Python code |

---

## Part 12 — Rust/C Extension Consideration (Future)

If after all Python-level fixes the inner backtest loop (iterating trading days,
computing P&L, selecting strikes) still takes > 5 seconds for a 5-year NIFTY backtest,
then the hot path can be rewritten in Rust via PyO3:

**Candidate functions for Rust:**
- `calculate_strike_from_closest_premium()` — scans all strikes
- `calculate_strike_from_premium_range()` — scans all strikes  
- The core trading day loop in `generic_multi_leg.py`

**PyO3 skeleton:**
```rust
use pyo3::prelude::*;

#[pyfunction]
fn find_closest_strike(strikes: Vec<f64>, premiums: Vec<f64>, target: f64) -> PyResult<f64> {
    let best = strikes.iter().zip(premiums.iter())
        .min_by(|(_, a), (_, b)| ((*a - target).abs()).partial_cmp(&((*b - target).abs())).unwrap())
        .map(|(s, _)| *s)
        .unwrap_or(f64::NAN);
    Ok(best)
}

#[pymodule]
fn algotest_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(find_closest_strike, m)?)?;
    Ok(())
}
```

Build with `maturin` inside the Docker image. The Python code calls it like any
other import: `from algotest_core import find_closest_strike`.

This is a future step — implement only after Week 1 + Week 2 fixes are deployed and
profiled with Prometheus.

---

## Summary Checklist

```
[ ] Fix BacktestCache._make_key to include full strategy dict
[ ] Fix warm-cache thread leak (use module-level executor)
[ ] Move backtest execution to ProcessPoolExecutor
[ ] Switch Uvicorn to --workers 1 --loop uvloop
[ ] Replace iterrows() with Polars vectorized ops in BacktestDataCache
[ ] Add GZipMiddleware to main.py
[ ] Remove 60-day chunking from get_spot_data()
[ ] Add 005_add_performance_indexes.sql migration
[ ] Increase DB pool: POOL_SIZE=20, MAX_OVERFLOW=10, max_connections=200
[ ] Lower work_mem to 16MB in docker-compose postgres command
[ ] Implement Redis-backed shared lookup cache (msgpack)
[ ] Implement async CSV upload → Celery task flow
[ ] Use PostgreSQL COPY for bulk CSV import
[ ] Add separate Celery queues for backtests vs uploads
[ ] Add Prometheus + Grafana monitoring
[ ] Add startup cache warming for common symbols
[ ] Load test and verify P95 backtest latency < 5 seconds
```
