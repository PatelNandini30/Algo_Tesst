# Intraday API & Celery Glue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Rust engine (Plan B output) into a FastAPI endpoint at `POST /api/intraday/backtest`, backed by a dedicated Celery worker queue with Redis result caching, plus swap the ASGI server from uvicorn to granian and set ORJSONResponse as the default.

**Architecture:** A new `routers/intraday.py` receives validated pydantic requests, hashes them for Redis cache lookup, enqueues on `backtests_intraday` (or `backtests_intraday_slow`) if not cached, waits for the Arrow IPC bytes, caches them, and returns a streaming response. A new `worker/tasks_intraday.py` Celery task calls `intraday_engine.run_intraday_backtest`. Two new worker services are added to `docker-compose.yml`.

**Tech Stack:** FastAPI, pydantic v2, Celery 5, Redis, granian (replaces uvicorn), orjson (already installed), pyarrow.

**Prerequisite:** Plan B complete — `backend/services/intraday_engine.py` and the native extension exist and pass tests.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/schemas/intraday.py` | Pydantic request/response models |
| Create | `backend/routers/intraday.py` | FastAPI router: cache check → enqueue → return |
| Create | `backend/worker/tasks_intraday.py` | Celery task: call Rust engine, return bytes |
| Modify | `backend/services/backtest_cache.py` | extend for intraday key namespace |
| Modify | `backend/main.py` | mount intraday router, set ORJSONResponse default |
| Modify | `backend/requirements.txt` | replace uvicorn with granian |
| Modify | `backend/Dockerfile` | swap uvicorn → granian |
| Modify | `docker-compose.yml` | add 2 intraday workers; update backend command |

---

### Task 1: granian migration (requirements + Dockerfile + main.py)

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/Dockerfile`
- Modify: `backend/main.py`

- [ ] **Step 1: Write regression test first**

Create `backend/tests/test_server_start.py`:
```python
import unittest

class TestAppCreation(unittest.TestCase):
    def test_app_has_default_orjson_response(self):
        """FastAPI app should use ORJSONResponse by default."""
        from backend.main import app
        from fastapi.responses import ORJSONResponse
        self.assertIs(app.default_response_class, ORJSONResponse)
```

Run:
```bash
cd /home/user/Algo_Test_Software
python -m unittest backend.tests.test_server_start -v
```
Expected: FAIL (default is still `JSONResponse`).

- [ ] **Step 2: Update `backend/requirements.txt`**

Replace `uvicorn[standard]==0.24.0` with:
```
granian==1.6.0
```
Keep `uvloop==0.19.0` — granian uses it natively.

- [ ] **Step 3: Update `backend/main.py` — set ORJSONResponse default**

Find the `app = FastAPI(...)` call in `backend/main.py`. Add `default_response_class`:
```python
from fastapi.responses import ORJSONResponse

app = FastAPI(
    title="AlgoTest Clone API",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/user/Algo_Test_Software
python -m unittest backend.tests.test_server_start -v
```
Expected: PASS.

- [ ] **Step 5: Update `backend/Dockerfile`**

Find the final stage `CMD` or `ENTRYPOINT` that runs uvicorn. Replace with granian:

Find:
```dockerfile
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Replace with:
```dockerfile
CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop", "main:app"]
```

If there are multiple uvicorn references in the Dockerfile, replace them all. Also check `backend/scripts/` for any uvicorn startup scripts and update similarly.

- [ ] **Step 6: Build and verify**

```bash
docker compose build backend 2>&1 | tail -10
docker compose run --rm --entrypoint granian backend --version 2>&1
```
Expected: granian is installed and responds with its version string.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/Dockerfile backend/main.py backend/tests/test_server_start.py
git commit -m "feat(api): swap uvicorn → granian, set ORJSONResponse default"
```

---

### Task 2: Pydantic intraday request schema

**Files:**
- Create: `backend/schemas/__init__.py` (if it doesn't exist)
- Create: `backend/schemas/intraday.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_schema.py`:
```python
import unittest

class TestIntradaySchema(unittest.TestCase):
    def test_valid_single_leg_request(self):
        from backend.schemas.intraday import IntradayBacktestRequest
        req = IntradayBacktestRequest.model_validate({
            "symbol": "NIFTY",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "entry_time": "09:20",
            "square_off_time": "15:15",
            "legs": [{
                "opt_type": "CE",
                "action": "SELL",
                "strike_selection": {"mode": "ATM", "value": 0},
                "expiry": "WEEKLY",
                "quantity": 1,
                "sl": {"type": "percent", "value": 50.0},
                "target": None,
            }]
        })
        self.assertEqual(req.symbol, "NIFTY")
        self.assertEqual(len(req.legs), 1)

    def test_rejects_unsupported_symbol(self):
        from backend.schemas.intraday import IntradayBacktestRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            IntradayBacktestRequest.model_validate({
                "symbol": "RELIANCE",
                "date_from": "2024-01-01",
                "date_to": "2024-01-31",
                "entry_time": "09:20",
                "square_off_time": "15:15",
                "legs": [],
            })

    def test_canonical_hash_stable(self):
        from backend.schemas.intraday import IntradayBacktestRequest
        req = IntradayBacktestRequest.model_validate({
            "symbol": "NIFTY",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "entry_time": "09:20",
            "square_off_time": "15:15",
            "legs": [{
                "opt_type": "CE",
                "action": "SELL",
                "strike_selection": {"mode": "ATM", "value": 0},
                "expiry": "WEEKLY",
                "quantity": 1,
                "sl": None,
                "target": None,
            }]
        })
        h1 = req.canonical_hash()
        h2 = req.canonical_hash()
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)  # 8-byte hex = 16 chars
```

Run:
```bash
python -m unittest backend.tests.test_intraday_schema -v
```
Expected: FAIL with ImportError.

- [ ] **Step 2: Create `backend/schemas/__init__.py`** (empty)

```python
```

- [ ] **Step 3: Create `backend/schemas/intraday.py`**

```python
from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

SUPPORTED_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"})
SLOW_PATH_STRIKE_LIMIT = 5  # |ATM_OFFSET| > 5 → slow path


class ExitCond(BaseModel):
    type: Literal["percent", "points"]
    value: float


class StrikeSelection(BaseModel):
    mode: Literal["ATM", "ATM_OFFSET"]
    value: int = 0


class LegSpec(BaseModel):
    opt_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    strike_selection: StrikeSelection
    expiry: Literal["WEEKLY", "MONTHLY", "NEXT_WEEKLY", "NEXT_MONTHLY"]
    quantity: int = 1
    sl: Optional[ExitCond] = None
    target: Optional[ExitCond] = None


class IntradayBacktestRequest(BaseModel):
    symbol: str
    date_from: str   # "YYYY-MM-DD"
    date_to: str
    entry_time: str  # "HH:MM"
    square_off_time: str = "15:15"
    legs: list[LegSpec]

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_supported(cls, v: str) -> str:
        if v not in SUPPORTED_SYMBOLS:
            raise ValueError(f"symbol must be one of {sorted(SUPPORTED_SYMBOLS)}, got '{v}'")
        return v

    def requires_slow_path(self) -> bool:
        return any(
            abs(leg.strike_selection.value) > SLOW_PATH_STRIKE_LIMIT
            for leg in self.legs
        )

    def to_engine_config(self) -> dict:
        return self.model_dump(mode="json")

    def canonical_hash(self) -> str:
        """Stable 16-char hex hash for Redis cache key."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.blake2b(payload.encode(), digest_size=8).hexdigest()
```

- [ ] **Step 4: Run tests**

```bash
python -m unittest backend.tests.test_intraday_schema -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/schemas/ backend/tests/test_intraday_schema.py
git commit -m "feat(intraday): pydantic request schema + canonical hash"
```

---

### Task 3: Celery intraday task

**Files:**
- Create: `backend/worker/tasks_intraday.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_intraday_schema.py` (or create a new file `backend/tests/test_intraday_task.py`):
```python
import unittest
from unittest.mock import patch, MagicMock

class TestIntradayTask(unittest.TestCase):
    def test_task_calls_engine_and_returns_bytes(self):
        fake_bytes = b"FAKE_ARROW_IPC"
        with patch("backend.services.intraday_engine.run_intraday_backtest",
                   return_value=fake_bytes) as mock_engine:
            from backend.worker.tasks_intraday import execute_intraday_backtest
            result = execute_intraday_backtest({"symbol": "NIFTY", "date_from": "2024-01-01",
                                                "date_to": "2024-01-01", "entry_time": "09:20",
                                                "square_off_time": "15:15", "legs": []})
            mock_engine.assert_called_once()
            self.assertEqual(result, fake_bytes)
```

Run:
```bash
python -m unittest backend.tests.test_intraday_task -v
```
Expected: FAIL with ImportError.

- [ ] **Step 2: Create `backend/worker/tasks_intraday.py`**

```python
from __future__ import annotations

import logging

from backend.services.intraday_engine import run_intraday_backtest
from backend.worker.celery import app as celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.tasks_intraday.execute_intraday_backtest",
    bind=True,
    max_retries=0,
    acks_late=True,
    track_started=True,
)
def execute_intraday_backtest(self, config: dict) -> bytes:
    """Celery task: run intraday backtest and return Arrow IPC bytes."""
    symbol = config.get("symbol", "?")
    date_from = config.get("date_from", "?")
    date_to = config.get("date_to", "?")
    logger.info("[intraday] start symbol=%s range=%s..%s", symbol, date_from, date_to)
    result = run_intraday_backtest(config)
    logger.info("[intraday] done symbol=%s bytes=%d", symbol, len(result))
    return result
```

- [ ] **Step 3: Run the test**

```bash
python -m unittest backend.tests.test_intraday_task -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/worker/tasks_intraday.py backend/tests/test_intraday_task.py
git commit -m "feat(worker): Celery task for intraday backtest execution"
```

---

### Task 4: Redis cache extension for intraday

**Files:**
- Modify: `backend/services/backtest_cache.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_cache.py`:
```python
import unittest
from unittest.mock import MagicMock, patch

class TestIntradayCacheKeys(unittest.TestCase):
    def test_intraday_cache_key_format(self):
        from backend.services.backtest_cache import intraday_cache_key
        key = intraday_cache_key("abc123ff")
        self.assertEqual(key, "intraday:result:abc123ff")

    def test_get_returns_none_on_miss(self):
        with patch("backend.services.backtest_cache.get_redis") as mock_redis:
            mock_redis.return_value.get.return_value = None
            from backend.services.backtest_cache import get_intraday_result
            result = get_intraday_result("abc123ff")
            self.assertIsNone(result)

    def test_set_and_get_roundtrip(self):
        fake_bytes = b"FAKE_ARROW"
        store = {}
        mock_r = MagicMock()
        mock_r.get.side_effect = lambda k: store.get(k)
        mock_r.setex.side_effect = lambda k, ttl, v: store.update({k: v})
        with patch("backend.services.backtest_cache.get_redis", return_value=mock_r):
            from backend.services import backtest_cache
            backtest_cache.set_intraday_result("abc123ff", fake_bytes)
            result = backtest_cache.get_intraday_result("abc123ff")
            self.assertEqual(result, fake_bytes)
```

Run:
```bash
python -m unittest backend.tests.test_intraday_cache -v
```
Expected: FAIL with ImportError (functions don't exist yet).

- [ ] **Step 2: Add to `backend/services/backtest_cache.py`**

Open the existing file and add at the bottom (after existing functions):
```python
# ── Intraday result cache ──────────────────────────────────────────────────

INTRADAY_RESULT_TTL = 7 * 24 * 3600  # 7 days


def intraday_cache_key(hash_hex: str) -> str:
    return f"intraday:result:{hash_hex}"


def get_intraday_result(hash_hex: str) -> bytes | None:
    """Return cached Arrow IPC bytes or None on miss."""
    r = get_redis()
    if r is None:
        return None
    return r.get(intraday_cache_key(hash_hex))


def set_intraday_result(hash_hex: str, arrow_bytes: bytes) -> None:
    """Cache Arrow IPC bytes with 7-day TTL."""
    r = get_redis()
    if r is None:
        return
    r.setex(intraday_cache_key(hash_hex), INTRADAY_RESULT_TTL, arrow_bytes)
```

(If `get_redis()` is named differently in the existing file, match the existing convention.)

- [ ] **Step 3: Run the tests**

```bash
python -m unittest backend.tests.test_intraday_cache -v
```
Expected: all 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/services/backtest_cache.py backend/tests/test_intraday_cache.py
git commit -m "feat(cache): intraday result cache in Redis with 7-day TTL"
```

---

### Task 5: FastAPI intraday router

**Files:**
- Create: `backend/routers/intraday.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_router.py`:
```python
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

class TestIntradayRouter(unittest.TestCase):
    def _make_client(self):
        from backend.main import app
        return TestClient(app)

    def test_health_returns_200(self):
        client = self._make_client()
        resp = client.get("/api/intraday/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("symbols_ready", data)

    def test_backtest_returns_422_on_bad_symbol(self):
        client = self._make_client()
        resp = client.post("/api/intraday/backtest", json={
            "symbol": "RELIANCE",
            "date_from": "2024-01-01",
            "date_to": "2024-01-01",
            "entry_time": "09:20",
            "legs": []
        })
        self.assertEqual(resp.status_code, 422)

    def test_backtest_returns_arrow_on_cache_hit(self):
        fake_arrow = b"\x00\x00\x00\x00"  # placeholder bytes
        with patch("backend.services.backtest_cache.get_intraday_result",
                   return_value=fake_arrow):
            client = self._make_client()
            resp = client.post("/api/intraday/backtest", json={
                "symbol": "NIFTY",
                "date_from": "2024-01-01",
                "date_to": "2024-01-01",
                "entry_time": "09:20",
                "square_off_time": "15:15",
                "legs": [{
                    "opt_type": "CE",
                    "action": "SELL",
                    "strike_selection": {"mode": "ATM", "value": 0},
                    "expiry": "WEEKLY",
                    "quantity": 1,
                    "sl": None,
                    "target": None,
                }]
            })
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, fake_arrow)
            self.assertEqual(resp.headers["content-type"],
                             "application/vnd.apache.arrow.stream")
```

Run:
```bash
python -m unittest backend.tests.test_intraday_router -v
```
Expected: FAIL (router not yet mounted).

- [ ] **Step 2: Create `backend/routers/intraday.py`**

```python
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from celery.result import AsyncResult

from backend.schemas.intraday import IntradayBacktestRequest
from backend.services.backtest_cache import get_intraday_result, set_intraday_result
from backend.worker.celery import app as celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intraday", tags=["intraday"])

INTRADAY_DATA_DIR = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")
CELERY_TIMEOUT_S = int(os.environ.get("INTRADAY_CELERY_TIMEOUT", "60"))

ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.stream"


@router.get("/health")
def intraday_health():
    data_dir = Path(INTRADAY_DATA_DIR)
    symbols_ready = []
    earliest, latest = None, None
    if data_dir.exists():
        for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            snaps = data_dir / sym / "snapshots"
            if snaps.exists() and any(snaps.glob("*.arrow")):
                symbols_ready.append(sym)
                dates = sorted(f.stem for f in snaps.glob("*.arrow"))
                if dates:
                    if earliest is None or dates[0] < earliest:
                        earliest = dates[0]
                    if latest is None or dates[-1] > latest:
                        latest = dates[-1]
    snap_count = sum(
        len(list((data_dir / sym / "snapshots").glob("*.arrow")))
        for sym in symbols_ready
        if (data_dir / sym / "snapshots").exists()
    )
    return {
        "snapshot_count": snap_count,
        "symbols_ready": symbols_ready,
        "earliest_date": earliest,
        "latest_date": latest,
        "cache_warm": bool(symbols_ready),
    }


@router.post("/backtest")
async def run_intraday_backtest(req: IntradayBacktestRequest):
    cache_key = req.canonical_hash()
    slow_path = req.requires_slow_path()

    # L0: Redis cache check
    cached = get_intraday_result(cache_key)
    if cached is not None:
        logger.info("[intraday] cache HIT key=%s", cache_key)
        return Response(content=cached, media_type=ARROW_CONTENT_TYPE)

    # Choose queue
    queue = "backtests_intraday_slow" if slow_path else "backtests_intraday"
    logger.info("[intraday] cache MISS key=%s queue=%s", cache_key, queue)

    # Enqueue and wait
    task = celery_app.send_task(
        "worker.tasks_intraday.execute_intraday_backtest",
        args=[req.to_engine_config()],
        queue=queue,
    )
    try:
        arrow_bytes: bytes = task.get(timeout=CELERY_TIMEOUT_S, propagate=True)
    except Exception as exc:
        logger.error("[intraday] task failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Cache result
    set_intraday_result(cache_key, arrow_bytes)

    headers = {}
    if slow_path:
        headers["X-Slow-Path"] = "true"

    return Response(content=arrow_bytes, media_type=ARROW_CONTENT_TYPE, headers=headers)
```

- [ ] **Step 3: Mount router in `backend/main.py`**

Find where existing routers are included (look for `app.include_router`) and add:
```python
from backend.routers.intraday import router as intraday_router
app.include_router(intraday_router)
```

- [ ] **Step 4: Run the tests**

```bash
python -m unittest backend.tests.test_intraday_router -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/intraday.py backend/tests/test_intraday_router.py backend/main.py
git commit -m "feat(api): intraday FastAPI router with Redis cache + Celery dispatch"
```

---

### Task 6: docker-compose.yml — new workers + updated backend command

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Read the current docker-compose.yml to understand existing structure**

```bash
grep -n "command\|worker\|memory\|cpus" /home/user/Algo_Test_Software/docker-compose.yml | head -40
```

- [ ] **Step 2: Update backend `command` from uvicorn to granian**

Find the `backend:` service `command:` entry (or entrypoint) and replace uvicorn with:
```yaml
command:
  - granian
  - --interface
  - asgi
  - --host
  - "0.0.0.0"
  - --port
  - "8000"
  - --workers
  - "1"
  - --loop
  - uvloop
  - main:app
```

- [ ] **Step 3: Add two intraday worker services**

Add after the existing worker definitions (keeping the YAML anchor/alias pattern that's already in the file):
```yaml
  worker-backtests-intraday:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: >
      celery -A worker.celery worker
      --queues=backtests_intraday
      --concurrency=3
      -l info
      --max-memory-per-child=2200000
      --without-gossip --without-mingle --without-heartbeat
    environment: *backend-env
    volumes: *backend-volumes
    depends_on:
      - redis
      - postgres
    deploy:
      resources:
        limits:
          memory: 2500M
          cpus: "3.0"
        reservations:
          memory: 512M

  worker-backtests-intraday-slow:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: >
      celery -A worker.celery worker
      --queues=backtests_intraday_slow
      --concurrency=1
      -l info
      --max-memory-per-child=1300000
      --without-gossip --without-mingle --without-heartbeat
    environment: *backend-env
    volumes: *backend-volumes
    depends_on:
      - redis
      - postgres
    deploy:
      resources:
        limits:
          memory: 1500M
          cpus: "1.5"
        reservations:
          memory: 256M
```

(Use the exact same YAML anchor names as the existing file for `*backend-env` and `*backend-volumes`.)

- [ ] **Step 4: Verify the compose file is valid**

```bash
docker compose config 2>&1 | grep -E "^Error|worker-backtests-intraday" | head -10
```
Expected: no errors; the two new workers appear.

- [ ] **Step 5: Smoke-test full stack starts**

```bash
docker compose up -d --build 2>&1 | tail -10
docker compose ps 2>&1 | grep -E "intraday|backend"
```
Expected: `backend`, `worker-backtests-intraday`, `worker-backtests-intraday-slow` all show `Up` or `running`.

- [ ] **Step 6: Confirm granian is serving**

```bash
curl -s http://localhost:8000/health | python3 -m json.tool | head -5
curl -s http://localhost:8000/api/intraday/health | python3 -m json.tool
```
Expected: `/health` returns existing EOD health JSON; `/api/intraday/health` returns snapshot count.

- [ ] **Step 7: Commit**

```bash
docker compose down
git add docker-compose.yml
git commit -m "feat(docker): add intraday workers + switch backend to granian"
```

---

### Task 7: EOD regression test

**Files:**
- No new files — run existing tests to verify EOD path is intact.

- [ ] **Step 1: Run existing test suite**

```bash
cd /home/user/Algo_Test_Software
python -m unittest discover backend/tests -v 2>&1 | tail -20
```
Expected: all existing tests PASS. No regressions from granian swap or ORJSONResponse change.

- [ ] **Step 2: Run a canary EOD backtest via the API**

```bash
docker compose up -d
sleep 5
curl -s -X POST http://localhost:8000/api/backtest \
  -H "Content-Type: application/json" \
  -d '{"symbol":"NIFTY","date_from":"2024-01-01","date_to":"2024-01-05","strategy_type":"short_straddle"}' \
  | python3 -m json.tool | head -10
docker compose down
```
Expected: valid JSON response (same shape as before). If EOD strategy_type conventions differ, use a request that was previously working.

- [ ] **Step 3: Commit nothing** (tests passed — no changes needed).

---

## Self-Review

**Spec coverage:**
- §5.5 granian + ORJSONResponse: Task 1 ✓
- §6.1 POST /api/intraday/backtest + Redis L0 cache: Task 5 ✓
- §6.2 Slow-path detection → separate queue: Task 5 (requires_slow_path + queue routing) ✓
- §7.1 Two new Celery workers: Task 6 ✓
- §7.4 Cache hierarchy L0: Task 4 (Redis, 7-day TTL) ✓
- §9.3 /api/intraday/health: Task 5 ✓
- §6.3 EOD path unchanged: Task 7 regression test ✓
- Memory limits per §7.2: Task 6 (2500M intraday, 1500M slow) ✓

**No placeholders found.**
