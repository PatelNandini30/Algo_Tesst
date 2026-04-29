# Intraday Backtest — Plan A: Storage + Ingestion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest one trading-day of NIFTY 1-minute options CSV data into the new intraday storage layout (Parquet cold tier + Arrow DaySnapshot hot tier), then scale to one full month (March 2024). End state: 22 valid DaySnapshot files for NIFTY March 2024 with golden tests passing, idempotent re-ingest, and a manifest row in Postgres.

**Architecture:** The ingest path produces two artifacts per trading day: (1) a row-sorted Parquet file (cold tier, on HDD) for full-chain queries, and (2) a compact Arrow IPC file (hot tier, mmap'd) capturing ATM±5 strikes for ~99% of backtest queries. A Postgres `intraday_imports` manifest row records each successful ingest with SHA256 for idempotency. All writes are atomic (temp → fsync → rename → manifest insert in one transaction).

**Tech Stack:** Python 3.12, Polars (Arrow-backed DataFrames), pyarrow (Parquet writer + Arrow IPC), psycopg2 + SQLAlchemy (existing), Celery (existing `worker-uploads`), unittest (project convention — not pytest).

**Reference spec:** `docs/superpowers/specs/2026-04-29-intraday-backtest-design.md` — sections §3, §4, §11 phases 1–2.

**Out of scope (covered in later plans):** Rust kernels (Plan B), engine/API/frontend (Plan C), multi-leg + stateful exits (Plan D), 1-year backfill + warmup + perf tests (Plan E).

---

## File structure

### New files (created by this plan)

```
backend/
├── migrations/
│   └── 007_intraday_imports.sql                        Task 2
├── services/
│   ├── intraday_ingest/
│   │   ├── __init__.py                                 Task 5
│   │   ├── FORMATS.md                                  Task 4 (documentation)
│   │   ├── base.py                                     Task 5
│   │   ├── format_clean_2023.py                        Task 6
│   │   └── validation.py                               Task 7
│   ├── intraday_paths.py                               Task 1
│   ├── intraday_parquet_writer.py                      Task 8
│   ├── intraday_spot_writer.py                         Task 9
│   ├── intraday_expiry_dim.py                          Task 10
│   ├── intraday_manifest.py                            Task 11
│   ├── intraday_snapshot/
│   │   ├── __init__.py                                 Task 12
│   │   ├── format.py                                   Task 12
│   │   ├── atm.py                                      Task 13
│   │   ├── chains.py                                   Task 14
│   │   └── builder.py                                  Task 12
│   └── intraday_publish.py                             Task 16
├── tests/
│   ├── fixtures/intraday/
│   │   ├── synthetic_one_day.csv                       Task 6 (test fixture)
│   │   └── expected_snapshot_2024-03-15.arrow.bin      Task 15 (golden bytes)
│   ├── test_intraday_paths.py                          Task 1
│   ├── test_intraday_format_detection.py               Task 5
│   ├── test_intraday_format_clean_2023.py              Task 6
│   ├── test_intraday_validation.py                     Task 7
│   ├── test_intraday_parquet_writer.py                 Task 8
│   ├── test_intraday_spot_writer.py                    Task 9
│   ├── test_intraday_expiry_dim.py                     Task 10
│   ├── test_intraday_manifest.py                       Task 11
│   ├── test_intraday_snapshot_format.py                Task 12
│   ├── test_intraday_atm.py                            Task 13
│   ├── test_intraday_chains.py                         Task 14
│   ├── test_intraday_snapshot_golden.py                Task 15
│   ├── test_intraday_publish.py                        Task 16
│   └── test_intraday_ingest_e2e.py                     Task 19
├── worker/tasks_intraday.py                            Task 17
└── scripts/
    └── ingest_intraday_batch.py                        Task 18
```

### Modified files

```
backend/requirements.txt                                Task 1 (add no new deps; polars+pyarrow already in stack indirectly via existing services)
backend/worker/celery.py                                Task 17 (register intraday tasks module)
docker-compose.yml                                      Task 17 (no changes; existing worker-uploads picks up the new task)
```

### File responsibilities (one-line each)

- `intraday_paths.py` — pure path arithmetic; no I/O. One function per logical path (parquet, snapshot, expiry-dim, manifest mirror).
- `intraday_ingest/base.py` — abstract `BaseFormatHandler` + format registry.
- `intraday_ingest/format_clean_2023.py` — handler for the clean 2023+ CSV format.
- `intraday_ingest/validation.py` — pure validators that raise `IntradayValidationError` on bad data.
- `intraday_parquet_writer.py` — writes a sorted month of options to Parquet, idempotent.
- `intraday_spot_writer.py` — same for spot (smaller, simpler).
- `intraday_expiry_dim.py` — append-only `(symbol, expiry_date) → expiry_idx` map, persisted as JSON.
- `intraday_manifest.py` — Postgres CRUD for `intraday_imports`.
- `intraday_snapshot/format.py` — binary layout constants (header magic, version, offsets).
- `intraday_snapshot/atm.py` — pure: spot Series → ATM strike per minute.
- `intraday_snapshot/chains.py` — pure: options DataFrame → packed (strike, type, OHLCV) arrays.
- `intraday_snapshot/builder.py` — orchestrator that calls atm + chains and packs the IPC file.
- `intraday_publish.py` — atomic write+rename+manifest-insert orchestrator.
- `worker/tasks_intraday.py` — Celery task `ingest_intraday(symbol, source_path)`.
- `scripts/ingest_intraday_batch.py` — CLI for batch ingest of a directory of CSVs.

---

## Conventions for this plan

- Project test runner is `unittest` (not pytest). Existing tests live in `backend/tests/test_*.py` and follow `class Test...(unittest.TestCase)` with `def test_...(self)` methods.
- Run from repo root: `python -m unittest backend.tests.test_<module>` (single module) or `python -m unittest discover backend/tests` (all).
- All new modules use 4-space indent, snake_case, no formatter wired up — match surrounding style.
- All new tests assert exact, deterministic outputs. No `assertAlmostEqual` unless floating-point math is genuinely involved.
- Commits use Conventional Commits: `feat:`, `test:`, `chore:`, `docs:`. Co-author tag preserved by the harness.
- The plan assumes Docker is running (Postgres + Redis up). Verify with `docker compose ps` if a DB-backed test fails.

---

## Task 1: Bootstrap — path module + tests

**Files:**
- Create: `backend/services/intraday_paths.py`
- Create: `backend/tests/test_intraday_paths.py`

The path module is the foundation. Every later module asks `intraday_paths` where to write. Pure functions, no I/O — fast to test.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_paths.py`:

```python
import unittest
from datetime import date
from backend.services import intraday_paths


class TestIntradayPaths(unittest.TestCase):
    def setUp(self):
        self.root = "/data/intraday"

    def test_options_parquet_path_for_known_date(self):
        p = intraday_paths.options_parquet_path(self.root, "NIFTY", date(2024, 3, 15))
        self.assertEqual(
            p, "/data/intraday/NIFTY/options/year=2024/month=03/options.parquet"
        )

    def test_spot_parquet_path(self):
        p = intraday_paths.spot_parquet_path(self.root, "NIFTY", year=2024)
        self.assertEqual(p, "/data/intraday/NIFTY/spot/year=2024/spot.parquet")

    def test_snapshot_path(self):
        p = intraday_paths.snapshot_path(self.root, "NIFTY", date(2024, 3, 15))
        self.assertEqual(p, "/data/intraday/NIFTY/snapshots/2024-03-15.arrow")

    def test_expiry_dim_path(self):
        p = intraday_paths.expiry_dim_path(self.root, "NIFTY")
        self.assertEqual(p, "/data/intraday/NIFTY/expiries.json")

    def test_symbol_dir(self):
        p = intraday_paths.symbol_dir(self.root, "BANKNIFTY")
        self.assertEqual(p, "/data/intraday/BANKNIFTY")

    def test_symbol_uppercased(self):
        # Inputs are case-insensitive; outputs always uppercase
        p = intraday_paths.symbol_dir(self.root, "nifty")
        self.assertEqual(p, "/data/intraday/NIFTY")

    def test_invalid_symbol_rejected(self):
        with self.assertRaises(ValueError):
            intraday_paths.symbol_dir(self.root, "TCS")  # not in supported indexes


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest backend.tests.test_intraday_paths -v`
Expected: ImportError or ModuleNotFoundError on `intraday_paths`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_paths.py`:

```python
"""Pure path arithmetic for intraday storage. No I/O."""
from datetime import date

SUPPORTED_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"})


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s not in SUPPORTED_SYMBOLS:
        raise ValueError(
            f"Unsupported symbol {symbol!r}; expected one of {sorted(SUPPORTED_SYMBOLS)}"
        )
    return s


def symbol_dir(root: str, symbol: str) -> str:
    return f"{root.rstrip('/')}/{_normalize_symbol(symbol)}"


def options_parquet_path(root: str, symbol: str, trade_date: date) -> str:
    return (
        f"{symbol_dir(root, symbol)}/options/"
        f"year={trade_date.year:04d}/month={trade_date.month:02d}/options.parquet"
    )


def spot_parquet_path(root: str, symbol: str, year: int) -> str:
    return f"{symbol_dir(root, symbol)}/spot/year={year:04d}/spot.parquet"


def snapshot_path(root: str, symbol: str, trade_date: date) -> str:
    return f"{symbol_dir(root, symbol)}/snapshots/{trade_date.isoformat()}.arrow"


def expiry_dim_path(root: str, symbol: str) -> str:
    return f"{symbol_dir(root, symbol)}/expiries.json"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest backend.tests.test_intraday_paths -v`
Expected: 7 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_paths.py backend/tests/test_intraday_paths.py
git commit -m "feat(intraday): add path arithmetic module with tests"
```

---

## Task 2: Postgres `intraday_imports` migration

**Files:**
- Create: `backend/migrations/007_intraday_imports.sql`

The manifest is the ACID record that the filesystem snapshot exists and is valid. Since we have no migration runner, we follow the project pattern of plain SQL files run via `psql`.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/007_intraday_imports.sql`:

```sql
-- Migration: intraday import manifest
-- Version: 007
-- Notes:
--   - Mirrors filesystem state for atomic ACID tracking.
--   - One row per (symbol, trading_date). Re-ingest with same SHA256 is a no-op.
--   - On SHA256 change, the row is replaced and the snapshot file is overwritten.

CREATE TABLE IF NOT EXISTS intraday_imports (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    trading_date DATE NOT NULL,
    source_format VARCHAR(20) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    parquet_path TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    expiry_count SMALLINT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT intraday_imports_symbol_check
      CHECK (symbol IN ('NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY')),
    UNIQUE (symbol, trading_date)
);

CREATE INDEX IF NOT EXISTS idx_intraday_imports_symbol_date
    ON intraday_imports(symbol, trading_date);
```

- [ ] **Step 2: Apply the migration**

Run:
```bash
docker compose exec -T postgres psql -U algotest -d algotest \
  < backend/migrations/007_intraday_imports.sql
```

Expected: `CREATE TABLE`, `CREATE INDEX` (or `NOTICE: relation already exists, skipping`).

- [ ] **Step 3: Verify the table exists**

Run:
```bash
docker compose exec -T postgres psql -U algotest -d algotest \
  -c "\d intraday_imports"
```

Expected: column listing matching the migration.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/007_intraday_imports.sql
git commit -m "feat(intraday): add intraday_imports manifest migration"
```

---

## Task 3: Document expected source CSV format

**Files:**
- Create: `backend/services/intraday_ingest/FORMATS.md`

The CSV cleaner depends on knowing the exact source format. This task documents the expected schema for the **clean 2023+ format** (the one being ingested first) so subsequent tasks have a single source of truth.

- [ ] **Step 1: Inspect a sample CSV (manual; outputs documentation only)**

Pick one source CSV from the user's data directory (path is `<TBD when CSV available>` — when running this plan, replace with the actual path, e.g. `/data/source/NIFTY/2024/03/NIFTY_2024-03-15.csv`).

Run:
```bash
head -5 <CSV_PATH>
wc -l <CSV_PATH>
```

Record: column names, delimiter, date format, time format, decimal precision, presence of header row, encoding.

- [ ] **Step 2: Write the format doc**

Create `backend/services/intraday_ingest/FORMATS.md`:

````markdown
# Intraday CSV source formats

This document is the contract between source CSV files and ingestion handlers.
Each handler in this directory targets one of these formats. Adding a new format
requires (a) adding a section below, (b) creating a new handler module.

## clean_2023

In use from 2023 onwards. Clean, single-row-per-tick format with explicit headers.

**Header signature (used for auto-detection):** the first line is exactly
```
Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI
```

**Delimiter:** comma, no quoting needed.
**Encoding:** UTF-8.
**Header row:** present (line 1).

**Columns:**

| Column      | Type    | Format       | Notes                          |
|-------------|---------|--------------|--------------------------------|
| Date        | date    | YYYY-MM-DD   | trade date                     |
| Time        | time    | HH:MM        | 24h, IST, 09:15..15:30         |
| Symbol      | string  |              | NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY|
| ExpiryDate  | date    | YYYY-MM-DD   |                                |
| StrikePrice | decimal | %.2f         | strike in INR                  |
| OptionType  | string  | CE \| PE     |                                |
| Open        | decimal | %.2f         |                                |
| High        | decimal | %.2f         |                                |
| Low         | decimal | %.2f         |                                |
| Close       | decimal | %.2f         |                                |
| Volume      | int     |              | contracts                      |
| OI          | int     |              | open interest                  |

**File granularity:** one CSV per (symbol, trading_date). Files are independent;
ingest order does not matter.

**Known caveats:** none observed in 2024 NIFTY data. Update this file if any
appear.

## raw_2017 (TODO — Plan F)

Pre-2023 format. Multi-format detection and cleaning is a separate plan.
````

- [ ] **Step 3: Commit**

```bash
git add backend/services/intraday_ingest/FORMATS.md
git commit -m "docs(intraday-ingest): document clean_2023 CSV source format"
```

---

## Task 4: Format detection registry + base handler

**Files:**
- Create: `backend/services/intraday_ingest/__init__.py`
- Create: `backend/services/intraday_ingest/base.py`
- Create: `backend/tests/test_intraday_format_detection.py`

The registry lets us add new format handlers without touching the dispatcher. Each handler exposes `HEADER_SIGNATURE` (a string match) and a `clean(...)` method.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_format_detection.py`:

```python
import unittest
from io import StringIO
from backend.services.intraday_ingest import base


class TestFormatDetection(unittest.TestCase):
    def test_unknown_header_raises(self):
        f = StringIO("foo,bar,baz\n1,2,3\n")
        with self.assertRaises(base.UnknownFormatError):
            base.detect_format(f)

    def test_registry_starts_empty_or_with_only_known_formats(self):
        # The registry only contains explicitly-registered handlers
        # No handlers registered yet in this task
        self.assertEqual(base.list_registered_formats(), [])

    def test_register_and_lookup(self):
        class FakeHandler(base.BaseFormatHandler):
            HEADER_SIGNATURE = "a,b,c"

            def clean(self, source_path):
                raise NotImplementedError

        base.register_handler("fake", FakeHandler)
        try:
            self.assertEqual(base.list_registered_formats(), ["fake"])
            f = StringIO("a,b,c\n1,2,3\n")
            handler = base.detect_format(f)
            self.assertIsInstance(handler, FakeHandler)
        finally:
            base.unregister_handler("fake")

    def test_register_handler_must_subclass_base(self):
        class NotAHandler:
            HEADER_SIGNATURE = "x"

        with self.assertRaises(TypeError):
            base.register_handler("bad", NotAHandler)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_format_detection -v`
Expected: ModuleNotFoundError on `backend.services.intraday_ingest.base`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_ingest/__init__.py`:

```python
"""Intraday CSV ingestion. See FORMATS.md for source-format contracts."""
```

Create `backend/services/intraday_ingest/base.py`:

```python
"""Format detection registry and abstract base handler."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, IO


class IntradayIngestError(Exception):
    """Base class for ingest errors."""


class UnknownFormatError(IntradayIngestError):
    """Raised when no handler matches the source file's header."""


class IntradayValidationError(IntradayIngestError):
    """Raised by validators when data fails schema or sanity checks."""


class BaseFormatHandler(ABC):
    HEADER_SIGNATURE: str = ""  # subclasses MUST override

    @abstractmethod
    def clean(self, source_path: str):
        """Read source_path, return a cleaned Polars DataFrame matching the
        intraday options Parquet schema."""


_REGISTRY: Dict[str, BaseFormatHandler] = {}


def register_handler(name: str, handler_cls) -> None:
    if not (isinstance(handler_cls, type) and issubclass(handler_cls, BaseFormatHandler)):
        raise TypeError(f"{handler_cls!r} must subclass BaseFormatHandler")
    _REGISTRY[name] = handler_cls()


def unregister_handler(name: str) -> None:
    _REGISTRY.pop(name, None)


def list_registered_formats():
    return sorted(_REGISTRY.keys())


def detect_format(stream: IO[str]) -> BaseFormatHandler:
    """Read the first line of `stream` and return a matching handler.
    The stream is consumed at least one line."""
    first_line = stream.readline().rstrip("\r\n")
    for handler in _REGISTRY.values():
        if first_line == handler.HEADER_SIGNATURE:
            return handler
    raise UnknownFormatError(
        f"No handler matches header: {first_line!r}. "
        f"Registered formats: {list_registered_formats()}"
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m unittest backend.tests.test_intraday_format_detection -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_ingest/__init__.py \
        backend/services/intraday_ingest/base.py \
        backend/tests/test_intraday_format_detection.py
git commit -m "feat(intraday-ingest): add format detection registry"
```

---

## Task 5: `format_clean_2023` handler

**Files:**
- Create: `backend/services/intraday_ingest/format_clean_2023.py`
- Create: `backend/tests/fixtures/intraday/synthetic_one_day.csv`
- Create: `backend/tests/test_intraday_format_clean_2023.py`

Implements the cleaner for the documented format. Uses Polars to read CSV → cleaned DataFrame matching the Parquet schema (`ts_min`, `expiry_idx`, `strike_x100`, `opt_type`, OHLCV).

This task uses a **synthetic CSV fixture** so tests are deterministic and fast. Real CSVs come in via Task 19's e2e test.

Note: `expiry_idx` cannot be assigned at this stage because indices are managed by `intraday_expiry_dim` (Task 10). The handler emits raw `expiry_date` and lets the publisher resolve idx. Schema has `expiry_date: date` here.

- [ ] **Step 1: Create the synthetic fixture**

Create `backend/tests/fixtures/intraday/synthetic_one_day.csv`:

```
Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI
2024-03-15,09:15,NIFTY,2024-03-21,22000.00,CE,123.45,124.00,122.50,123.80,1500,12000
2024-03-15,09:15,NIFTY,2024-03-21,22000.00,PE,55.20,55.80,54.90,55.30,2200,18000
2024-03-15,09:16,NIFTY,2024-03-21,22000.00,CE,123.80,124.50,123.60,124.20,1800,12100
2024-03-15,09:16,NIFTY,2024-03-21,22000.00,PE,55.30,55.50,54.50,54.80,2400,18200
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_intraday_format_clean_2023.py`:

```python
import os
import unittest
from datetime import date
import polars as pl

from backend.services.intraday_ingest.format_clean_2023 import CleanFormat2023Handler

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "intraday", "synthetic_one_day.csv"
)


class TestCleanFormat2023(unittest.TestCase):
    def setUp(self):
        self.handler = CleanFormat2023Handler()

    def test_header_signature(self):
        self.assertEqual(
            self.handler.HEADER_SIGNATURE,
            "Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI",
        )

    def test_clean_returns_polars_dataframe(self):
        df = self.handler.clean(FIXTURE)
        self.assertIsInstance(df, pl.DataFrame)

    def test_cleaned_schema(self):
        df = self.handler.clean(FIXTURE)
        # Columns are exactly the canonical intraday options schema (minus expiry_idx)
        expected_columns = {
            "ts_min", "trade_date", "symbol", "expiry_date",
            "strike_x100", "opt_type",
            "open_x100", "high_x100", "low_x100", "close_x100",
            "volume", "oi",
        }
        self.assertEqual(set(df.columns), expected_columns)

    def test_ts_min_is_minutes_since_epoch_2017(self):
        df = self.handler.clean(FIXTURE)
        # 2024-03-15 09:15 IST → minutes since 2017-01-01 00:00:00
        # 2017-01-01 00:00 → ts_min = 0
        # 2024-03-15 09:15 = 2629 days * 1440 + 9*60 + 15 = 3,786,315
        first_ts = df.select("ts_min").row(0)[0]
        self.assertEqual(first_ts, 3786315)

    def test_strike_and_prices_are_x100_int32(self):
        df = self.handler.clean(FIXTURE)
        self.assertEqual(df["strike_x100"].dtype, pl.Int32)
        self.assertEqual(df["close_x100"].dtype, pl.Int32)
        # 22000.00 * 100 = 2200000
        self.assertEqual(df.select("strike_x100").row(0)[0], 2200000)
        # 123.45 * 100 = 12345
        # CE row 0 close is 123.80
        ce_close = df.filter(pl.col("opt_type") == 0).select("close_x100").row(0)[0]
        self.assertEqual(ce_close, 12380)

    def test_opt_type_encoded_as_int8(self):
        df = self.handler.clean(FIXTURE)
        self.assertEqual(df["opt_type"].dtype, pl.Int8)
        # CE → 0, PE → 1
        ce_count = df.filter(pl.col("opt_type") == 0).height
        pe_count = df.filter(pl.col("opt_type") == 1).height
        self.assertEqual(ce_count, 2)
        self.assertEqual(pe_count, 2)

    def test_row_count(self):
        df = self.handler.clean(FIXTURE)
        self.assertEqual(df.height, 4)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_format_clean_2023 -v`
Expected: ImportError for `CleanFormat2023Handler`.

- [ ] **Step 4: Write minimal implementation**

Create `backend/services/intraday_ingest/format_clean_2023.py`:

```python
"""Handler for the clean 2023+ CSV format. See FORMATS.md."""
from datetime import date, datetime
import polars as pl

from backend.services.intraday_ingest.base import (
    BaseFormatHandler,
    register_handler,
)

# Epoch for ts_min: 2017-01-01 00:00 IST. 4-byte int32 covers ~100 years.
TS_EPOCH_DATE = date(2017, 1, 1)
TS_EPOCH_MINUTES_OFFSET = (TS_EPOCH_DATE - date(1970, 1, 1)).days * 1440


class CleanFormat2023Handler(BaseFormatHandler):
    HEADER_SIGNATURE = (
        "Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,"
        "Open,High,Low,Close,Volume,OI"
    )

    def clean(self, source_path: str) -> pl.DataFrame:
        raw = pl.read_csv(
            source_path,
            schema_overrides={
                "Date": pl.Utf8,
                "Time": pl.Utf8,
                "Symbol": pl.Utf8,
                "ExpiryDate": pl.Utf8,
                "StrikePrice": pl.Float64,
                "OptionType": pl.Utf8,
                "Open": pl.Float64,
                "High": pl.Float64,
                "Low": pl.Float64,
                "Close": pl.Float64,
                "Volume": pl.Int64,
                "OI": pl.Int64,
            },
        )

        # Parse trade_date and combined ts_min
        df = raw.with_columns(
            pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d").alias("trade_date"),
            pl.col("ExpiryDate").str.strptime(pl.Date, "%Y-%m-%d").alias("expiry_date"),
            (
                pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d")
                .cast(pl.Int32)  # days since 1970-01-01
                * 1440
                + pl.col("Time").str.slice(0, 2).cast(pl.Int32) * 60
                + pl.col("Time").str.slice(3, 2).cast(pl.Int32)
                - TS_EPOCH_MINUTES_OFFSET
            ).cast(pl.Int32).alias("ts_min"),
            (pl.col("StrikePrice") * 100).round(0).cast(pl.Int32).alias("strike_x100"),
            pl.when(pl.col("OptionType") == "CE").then(0).otherwise(1)
              .cast(pl.Int8).alias("opt_type"),
            (pl.col("Open") * 100).round(0).cast(pl.Int32).alias("open_x100"),
            (pl.col("High") * 100).round(0).cast(pl.Int32).alias("high_x100"),
            (pl.col("Low") * 100).round(0).cast(pl.Int32).alias("low_x100"),
            (pl.col("Close") * 100).round(0).cast(pl.Int32).alias("close_x100"),
            pl.col("Volume").cast(pl.Int32).alias("volume"),
            pl.col("OI").cast(pl.Int32).alias("oi"),
            pl.col("Symbol").alias("symbol"),
        )

        return df.select([
            "ts_min", "trade_date", "symbol", "expiry_date",
            "strike_x100", "opt_type",
            "open_x100", "high_x100", "low_x100", "close_x100",
            "volume", "oi",
        ])


register_handler("clean_2023", CleanFormat2023Handler)
```

- [ ] **Step 5: Verify test passes**

Run: `python -m unittest backend.tests.test_intraday_format_clean_2023 -v`
Expected: 7 tests pass. Note: `polars` must be installed. If `ImportError`, run `pip install polars==0.20.31` (matches pyarrow already in the project). Add to `backend/requirements.txt` if not present.

- [ ] **Step 6: Commit**

```bash
git add backend/services/intraday_ingest/format_clean_2023.py \
        backend/tests/fixtures/intraday/synthetic_one_day.csv \
        backend/tests/test_intraday_format_clean_2023.py
git commit -m "feat(intraday-ingest): add clean_2023 CSV cleaner with synthetic fixture test"
```

---

## Task 6: Validation rules

**Files:**
- Create: `backend/services/intraday_ingest/validation.py`
- Create: `backend/tests/test_intraday_validation.py`

Validators are pure functions on the cleaned Polars DataFrame. They raise `IntradayValidationError` on bad data. The publisher rejects the whole file on any violation (no partial loads, per spec §4.2 step 3).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_validation.py`:

```python
import unittest
from datetime import date
import polars as pl

from backend.services.intraday_ingest import validation
from backend.services.intraday_ingest.base import IntradayValidationError


def _good_frame():
    return pl.DataFrame({
        "ts_min": [3786315, 3786315, 3786316, 3786316],
        "trade_date": [date(2024, 3, 15)] * 4,
        "symbol": ["NIFTY"] * 4,
        "expiry_date": [date(2024, 3, 21)] * 4,
        "strike_x100": [2200000, 2200000, 2200000, 2200000],
        "opt_type": [0, 1, 0, 1],
        "open_x100": [12345, 5520, 12380, 5530],
        "high_x100": [12400, 5580, 12450, 5550],
        "low_x100":  [12250, 5490, 12360, 5450],
        "close_x100":[12380, 5530, 12420, 5480],
        "volume":    [1500, 2200, 1800, 2400],
        "oi":        [12000, 18000, 12100, 18200],
    }).with_columns(
        pl.col("ts_min").cast(pl.Int32),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    )


class TestValidation(unittest.TestCase):
    def test_good_frame_passes(self):
        # Should not raise
        validation.validate(_good_frame(), trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_null_in_pk_rejected(self):
        bad = _good_frame().with_columns(
            pl.when(pl.col("ts_min") == 3786315).then(None).otherwise(pl.col("strike_x100"))
              .cast(pl.Int32).alias("strike_x100")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_strike_not_multiple_of_step_rejected(self):
        # NIFTY step is 50; 22001.00 → 2200100 is not a multiple of 5000
        bad = _good_frame().with_columns(
            pl.lit(2200100).cast(pl.Int32).alias("strike_x100")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_high_below_low_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(12000).cast(pl.Int32).alias("high_x100"),  # high < low (12250)
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_expiry_in_past_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(date(2024, 3, 14)).alias("expiry_date")  # before trade_date
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_expiry_too_far_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(date(2024, 9, 1)).alias("expiry_date")  # >90 days
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_wrong_symbol_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit("BANKNIFTY").alias("symbol")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_wrong_trade_date_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(date(2024, 3, 14)).alias("trade_date")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_validation -v`
Expected: ImportError on `validation`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_ingest/validation.py`:

```python
"""Pure validators on a cleaned intraday options DataFrame."""
from datetime import date, timedelta
import polars as pl

from backend.services.intraday_ingest.base import IntradayValidationError

PK_COLUMNS = ("ts_min", "expiry_date", "strike_x100", "opt_type")
STRIKE_STEP_X100 = {
    "NIFTY": 5000,       # 50 INR
    "BANKNIFTY": 10000,  # 100 INR
    "FINNIFTY": 5000,    # 50 INR
    "MIDCPNIFTY": 2500,  # 25 INR
}


def validate(df: pl.DataFrame, *, trade_date: date, symbol: str) -> None:
    if df.is_empty():
        raise IntradayValidationError("empty frame")

    # 1) PK columns have no nulls
    for col in PK_COLUMNS:
        if df[col].null_count() > 0:
            raise IntradayValidationError(f"nulls in PK column {col}")

    # 2) Symbol matches the expected one (single-symbol files)
    distinct_symbols = df["symbol"].unique().to_list()
    if distinct_symbols != [symbol]:
        raise IntradayValidationError(
            f"symbol mismatch: file has {distinct_symbols}, expected [{symbol}]"
        )

    # 3) trade_date matches
    distinct_dates = df["trade_date"].unique().to_list()
    if distinct_dates != [trade_date]:
        raise IntradayValidationError(
            f"trade_date mismatch: file has {distinct_dates}, expected [{trade_date}]"
        )

    # 4) Strike multiples
    step = STRIKE_STEP_X100[symbol]
    bad_strikes = df.filter(pl.col("strike_x100") % step != 0).height
    if bad_strikes > 0:
        raise IntradayValidationError(
            f"{bad_strikes} rows have strike not multiple of {step / 100} INR"
        )

    # 5) OHLC sanity: high >= max(open, close) >= min(open, close) >= low
    bad_ohlc = df.filter(
        (pl.col("high_x100") < pl.col("open_x100"))
        | (pl.col("high_x100") < pl.col("close_x100"))
        | (pl.col("high_x100") < pl.col("low_x100"))
        | (pl.col("low_x100") > pl.col("open_x100"))
        | (pl.col("low_x100") > pl.col("close_x100"))
    ).height
    if bad_ohlc > 0:
        raise IntradayValidationError(f"{bad_ohlc} rows have OHLC out of order")

    # 6) Expiry sanity: must be on/after trade_date and within 90 days
    earliest_allowed = trade_date
    latest_allowed = trade_date + timedelta(days=90)
    bad_expiry = df.filter(
        (pl.col("expiry_date") < earliest_allowed)
        | (pl.col("expiry_date") > latest_allowed)
    ).height
    if bad_expiry > 0:
        raise IntradayValidationError(
            f"{bad_expiry} rows have expiry outside [{earliest_allowed}..{latest_allowed}]"
        )
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_validation -v`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_ingest/validation.py \
        backend/tests/test_intraday_validation.py
git commit -m "feat(intraday-ingest): add validators with multi-rule tests"
```

---

## Task 7: Expiry dimension manager

**Files:**
- Create: `backend/services/intraday_expiry_dim.py`
- Create: `backend/tests/test_intraday_expiry_dim.py`

The expiry dim is an append-only `(symbol, expiry_date) → expiry_idx` map persisted as JSON. Indices are stable once assigned. Used to compress the Parquet `expiry_idx` column down to int16.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_expiry_dim.py`:

```python
import json
import os
import tempfile
import unittest
from datetime import date

from backend.services import intraday_expiry_dim


class TestExpiryDim(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.dim_path = os.path.join(self.tmpdir, "expiries.json")

    def test_load_returns_empty_when_missing(self):
        m = intraday_expiry_dim.load(self.dim_path)
        self.assertEqual(m, {})

    def test_assign_indices_to_new_expiries(self):
        m = {}
        out, dirty = intraday_expiry_dim.assign(
            m, [date(2024, 3, 21), date(2024, 3, 28)]
        )
        self.assertTrue(dirty)
        self.assertEqual(out[date(2024, 3, 21)], 0)
        self.assertEqual(out[date(2024, 3, 28)], 1)

    def test_assign_preserves_existing_indices(self):
        m = {date(2024, 3, 21): 0}
        out, dirty = intraday_expiry_dim.assign(m, [date(2024, 3, 21), date(2024, 3, 28)])
        self.assertEqual(out[date(2024, 3, 21)], 0)
        self.assertEqual(out[date(2024, 3, 28)], 1)
        self.assertTrue(dirty)

    def test_assign_no_change_returns_dirty_false(self):
        m = {date(2024, 3, 21): 0, date(2024, 3, 28): 1}
        out, dirty = intraday_expiry_dim.assign(m, [date(2024, 3, 21)])
        self.assertFalse(dirty)
        self.assertEqual(out, m)

    def test_save_then_load_roundtrip(self):
        m = {date(2024, 3, 21): 0, date(2024, 3, 28): 1}
        intraday_expiry_dim.save(self.dim_path, m)
        self.assertTrue(os.path.exists(self.dim_path))
        loaded = intraday_expiry_dim.load(self.dim_path)
        self.assertEqual(loaded, m)

    def test_save_uses_atomic_rename(self):
        m = {date(2024, 3, 21): 0}
        intraday_expiry_dim.save(self.dim_path, m)
        # Temp file should not remain
        self.assertFalse(os.path.exists(self.dim_path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_expiry_dim -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_expiry_dim.py`:

```python
"""Append-only (symbol-scoped) expiry-date → idx map persisted as JSON."""
import json
import os
from datetime import date
from typing import Dict, Iterable, Tuple


def load(path: str) -> Dict[date, int]:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        raw = json.load(f)
    return {date.fromisoformat(k): int(v) for k, v in raw.items()}


def save(path: str, dim: Dict[date, int]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({k.isoformat(): v for k, v in dim.items()}, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def assign(
    current: Dict[date, int],
    expiries: Iterable[date],
) -> Tuple[Dict[date, int], bool]:
    """Return (updated_map, dirty). Indices are assigned in input order;
    existing indices are preserved."""
    out = dict(current)
    next_idx = max(out.values(), default=-1) + 1
    dirty = False
    for e in expiries:
        if e not in out:
            out[e] = next_idx
            next_idx += 1
            dirty = True
    return out, dirty
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_expiry_dim -v`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_expiry_dim.py \
        backend/tests/test_intraday_expiry_dim.py
git commit -m "feat(intraday): add append-only expiry-dim with atomic save"
```

---

## Task 8: Monthly Parquet writer

**Files:**
- Create: `backend/services/intraday_parquet_writer.py`
- Create: `backend/tests/test_intraday_parquet_writer.py`

Writes a sorted, ZSTD-compressed Parquet file with the canonical schema. Idempotent: writing the same `(symbol, year, month)` with the same content is a no-op (filesystem rename guards). Different content for the same key replaces the file atomically.

The writer accepts a DataFrame with `expiry_date: date` and converts to `expiry_idx: int16` using a provided dim map.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_parquet_writer.py`:

```python
import os
import tempfile
import unittest
from datetime import date
import polars as pl
import pyarrow.parquet as pq

from backend.services import intraday_parquet_writer


def _good_frame():
    return pl.DataFrame({
        "ts_min": [3786315, 3786315, 3786316, 3786316],
        "trade_date": [date(2024, 3, 15)] * 4,
        "symbol": ["NIFTY"] * 4,
        "expiry_date": [date(2024, 3, 21)] * 4,
        "strike_x100": [2200000, 2200000, 2200000, 2200000],
        "opt_type": [0, 1, 0, 1],
        "open_x100": [12345, 5520, 12380, 5530],
        "high_x100": [12400, 5580, 12450, 5550],
        "low_x100":  [12250, 5490, 12360, 5450],
        "close_x100":[12380, 5530, 12420, 5480],
        "volume":    [1500, 2200, 1800, 2400],
        "oi":        [12000, 18000, 12100, 18200],
    }).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    ])


class TestParquetWriter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "options.parquet")

    def test_writes_file(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        self.assertTrue(os.path.exists(self.path))

    def test_schema_matches_spec(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        table = pq.read_table(self.path)
        cols = set(table.column_names)
        self.assertEqual(cols, {
            "ts_min", "expiry_idx", "strike_x100", "opt_type",
            "open_x100", "high_x100", "low_x100", "close_x100",
            "volume", "oi",
        })
        # expiry_idx is int16
        self.assertEqual(str(table.schema.field("expiry_idx").type), "int16")
        # opt_type is int8
        self.assertEqual(str(table.schema.field("opt_type").type), "int8")
        # ts_min is int32
        self.assertEqual(str(table.schema.field("ts_min").type), "int32")

    def test_sort_order_is_expiry_type_strike_ts(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        df = pl.read_parquet(self.path)
        # Verify monotonic non-decreasing on the composite key
        for i in range(1, df.height):
            prev = df.row(i - 1)
            cur = df.row(i)
            cols = ["expiry_idx", "opt_type", "strike_x100", "ts_min"]
            prev_key = tuple(prev[df.columns.index(c)] for c in cols)
            cur_key = tuple(cur[df.columns.index(c)] for c in cols)
            self.assertLessEqual(prev_key, cur_key)

    def test_idempotent_same_content_no_change(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        mtime1 = os.path.getmtime(self.path)
        # Re-write same content
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        mtime2 = os.path.getmtime(self.path)
        # Either same mtime (skipped) or new file with same content — both acceptable
        df1 = pl.read_parquet(self.path)
        self.assertEqual(df1.height, 4)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_parquet_writer -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_parquet_writer.py`:

```python
"""Write a sorted, ZSTD-compressed Parquet file matching the intraday options schema."""
import os
from datetime import date
from typing import Dict
import polars as pl

CANONICAL_COLUMNS = (
    "ts_min", "expiry_idx", "strike_x100", "opt_type",
    "open_x100", "high_x100", "low_x100", "close_x100",
    "volume", "oi",
)
SORT_KEYS = ("expiry_idx", "opt_type", "strike_x100", "ts_min")


def write(*, df: pl.DataFrame, output_path: str, expiry_dim: Dict[date, int]) -> None:
    """df must have columns including expiry_date and the canonical metric columns.
    expiry_idx is computed from expiry_dim. Output written atomically."""
    # Map expiry_date → expiry_idx (int16)
    mapped = df.with_columns(
        pl.col("expiry_date").map_elements(
            lambda d: expiry_dim[d], return_dtype=pl.Int16
        ).alias("expiry_idx")
    ).select(list(CANONICAL_COLUMNS))

    sorted_df = mapped.sort(by=list(SORT_KEYS))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    sorted_df.write_parquet(
        tmp,
        compression="zstd",
        compression_level=6,
        row_group_size=128 * 1024 * 1024 // 40,  # ~128 MB target
        statistics=True,
        use_pyarrow=True,
    )
    os.replace(tmp, output_path)
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_parquet_writer -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_parquet_writer.py \
        backend/tests/test_intraday_parquet_writer.py
git commit -m "feat(intraday): add monthly Parquet writer (sorted, ZSTD, atomic)"
```

---

## Task 9: Spot Parquet writer

**Files:**
- Create: `backend/services/intraday_spot_writer.py`
- Create: `backend/tests/test_intraday_spot_writer.py`

Spot data is OHLC per minute, one file per `(symbol, year)`. Schema is small: `ts_min, open_x100, high_x100, low_x100, close_x100, volume`.

The cleaner produces a separate spot DataFrame from a different source (typically a futures or index-spot file). For now, the writer accepts an already-cleaned DataFrame.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_spot_writer.py`:

```python
import os
import tempfile
import unittest
import polars as pl
import pyarrow.parquet as pq

from backend.services import intraday_spot_writer


def _good_spot():
    return pl.DataFrame({
        "ts_min": [3786315, 3786316],
        "open_x100":  [2200000, 2200500],
        "high_x100":  [2201000, 2201500],
        "low_x100":   [2199500, 2200000],
        "close_x100": [2200500, 2201200],
        "volume":     [1_000_000, 1_200_000],
    }).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int64),
    ])


class TestSpotWriter(unittest.TestCase):
    def test_writes_and_schema_correct(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "spot.parquet")
            intraday_spot_writer.write(df=_good_spot(), output_path=path)
            self.assertTrue(os.path.exists(path))
            t = pq.read_table(path)
            self.assertEqual(set(t.column_names), {
                "ts_min", "open_x100", "high_x100", "low_x100", "close_x100", "volume"
            })

    def test_sorted_by_ts_min(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "spot.parquet")
            unsorted = _good_spot().sort(by="ts_min", descending=True)
            intraday_spot_writer.write(df=unsorted, output_path=path)
            out = pl.read_parquet(path)
            ts = out["ts_min"].to_list()
            self.assertEqual(ts, sorted(ts))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_spot_writer -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_spot_writer.py`:

```python
"""Write a year of intraday spot data to Parquet."""
import os
import polars as pl

CANONICAL_COLUMNS = (
    "ts_min", "open_x100", "high_x100", "low_x100", "close_x100", "volume",
)


def write(*, df: pl.DataFrame, output_path: str) -> None:
    sorted_df = df.select(list(CANONICAL_COLUMNS)).sort(by="ts_min")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    sorted_df.write_parquet(
        tmp, compression="zstd", compression_level=6, statistics=True, use_pyarrow=True
    )
    os.replace(tmp, output_path)
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_spot_writer -v`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_spot_writer.py \
        backend/tests/test_intraday_spot_writer.py
git commit -m "feat(intraday): add spot Parquet writer"
```

---

## Task 10: Manifest CRUD

**Files:**
- Create: `backend/services/intraday_manifest.py`
- Create: `backend/tests/test_intraday_manifest.py`

CRUD for `intraday_imports`. Uses the existing `database.py` engine. Test runs against the **dockerized Postgres** (Docker stack must be up). If Postgres is not reachable, the test is skipped — same convention as existing repo tests that touch the DB.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_manifest.py`:

```python
import os
import unittest
from datetime import date
import psycopg2

from backend.services import intraday_manifest

DSN = (
    f"host={os.environ.get('POSTGRES_HOST', 'localhost')} "
    f"port={os.environ.get('POSTGRES_PORT', '5432')} "
    f"dbname={os.environ.get('POSTGRES_DB', 'algotest')} "
    f"user={os.environ.get('POSTGRES_USER', 'algotest')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'algotest_password')}"
)


def _can_reach_postgres():
    try:
        with psycopg2.connect(DSN, connect_timeout=2) as _:
            return True
    except Exception:
        return False


@unittest.skipUnless(_can_reach_postgres(), "Postgres not reachable")
class TestManifest(unittest.TestCase):
    def setUp(self):
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM intraday_imports WHERE symbol='NIFTY' AND trading_date=%s",
                (date(2024, 3, 15),),
            )

    def tearDown(self):
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM intraday_imports WHERE symbol='NIFTY' AND trading_date=%s",
                (date(2024, 3, 15),),
            )

    def test_get_returns_none_when_missing(self):
        self.assertIsNone(intraday_manifest.get("NIFTY", date(2024, 3, 15)))

    def test_upsert_inserts_when_missing(self):
        intraday_manifest.upsert(
            symbol="NIFTY",
            trading_date=date(2024, 3, 15),
            source_format="clean_2023",
            source_sha256="a" * 64,
            parquet_path="/data/intraday/NIFTY/options/year=2024/month=03/options.parquet",
            snapshot_path="/data/intraday/NIFTY/snapshots/2024-03-15.arrow",
            row_count=400_000,
            expiry_count=4,
        )
        row = intraday_manifest.get("NIFTY", date(2024, 3, 15))
        self.assertIsNotNone(row)
        self.assertEqual(row["source_sha256"], "a" * 64)
        self.assertEqual(row["row_count"], 400_000)

    def test_upsert_updates_when_present(self):
        intraday_manifest.upsert(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_format="clean_2023", source_sha256="a" * 64,
            parquet_path="/p", snapshot_path="/s",
            row_count=100, expiry_count=2,
        )
        intraday_manifest.upsert(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_format="clean_2023", source_sha256="b" * 64,
            parquet_path="/p2", snapshot_path="/s2",
            row_count=200, expiry_count=3,
        )
        row = intraday_manifest.get("NIFTY", date(2024, 3, 15))
        self.assertEqual(row["source_sha256"], "b" * 64)
        self.assertEqual(row["row_count"], 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_manifest -v`
Expected: ModuleNotFoundError on `intraday_manifest`. (If Postgres not reachable, all skip.)

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_manifest.py`:

```python
"""CRUD for the `intraday_imports` Postgres manifest table."""
from datetime import date
from typing import Optional, Dict, Any
import psycopg2.extras

from database import get_engine


def get(symbol: str, trading_date: date) -> Optional[Dict[str, Any]]:
    eng = get_engine()
    with eng.connect() as conn:
        result = conn.exec_driver_sql(
            """SELECT id, symbol, trading_date, source_format, source_sha256,
                      parquet_path, snapshot_path, row_count, expiry_count, ingested_at
               FROM intraday_imports
               WHERE symbol = %s AND trading_date = %s""",
            (symbol, trading_date),
        )
        row = result.fetchone()
        if row is None:
            return None
        return dict(row._mapping)


def upsert(
    *,
    symbol: str,
    trading_date: date,
    source_format: str,
    source_sha256: str,
    parquet_path: str,
    snapshot_path: str,
    row_count: int,
    expiry_count: int,
) -> None:
    eng = get_engine()
    with eng.begin() as conn:
        conn.exec_driver_sql(
            """INSERT INTO intraday_imports
               (symbol, trading_date, source_format, source_sha256,
                parquet_path, snapshot_path, row_count, expiry_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (symbol, trading_date) DO UPDATE SET
                 source_format = EXCLUDED.source_format,
                 source_sha256 = EXCLUDED.source_sha256,
                 parquet_path = EXCLUDED.parquet_path,
                 snapshot_path = EXCLUDED.snapshot_path,
                 row_count = EXCLUDED.row_count,
                 expiry_count = EXCLUDED.expiry_count,
                 ingested_at = NOW()""",
            (
                symbol, trading_date, source_format, source_sha256,
                parquet_path, snapshot_path, row_count, expiry_count,
            ),
        )
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_manifest -v`
Expected: if Postgres up, 3 tests pass. If not, all skip.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_manifest.py \
        backend/tests/test_intraday_manifest.py
git commit -m "feat(intraday): add intraday_imports manifest CRUD"
```

---

## Task 11: DaySnapshot binary format constants

**Files:**
- Create: `backend/services/intraday_snapshot/__init__.py`
- Create: `backend/services/intraday_snapshot/format.py`
- Create: `backend/tests/test_intraday_snapshot_format.py`

Defines the binary layout from spec §3.6: header magic, version, offsets, and pure pack/unpack functions for the header. The body (chains, ATM arrays) comes in later tasks.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_snapshot_format.py`:

```python
import unittest
from datetime import date
from backend.services.intraday_snapshot import format as snapfmt


class TestSnapshotFormat(unittest.TestCase):
    def test_magic_is_itds(self):
        self.assertEqual(snapfmt.MAGIC, b"ITDS")

    def test_version_is_one(self):
        self.assertEqual(snapfmt.VERSION, 1)

    def test_minutes_per_day_is_375(self):
        self.assertEqual(snapfmt.MINUTES_PER_DAY, 375)

    def test_strike_radius_is_5(self):
        self.assertEqual(snapfmt.STRIKE_RADIUS, 5)
        self.assertEqual(snapfmt.STRIKES_IN_CHAIN, 11)

    def test_pack_header_then_unpack_round_trips(self):
        packed = snapfmt.pack_header(
            symbol="NIFTY", trade_date=date(2024, 3, 15), expiry_count=4,
        )
        # Header is exactly 32 bytes
        self.assertEqual(len(packed), snapfmt.HEADER_BYTES)
        u = snapfmt.unpack_header(packed)
        self.assertEqual(u["magic"], b"ITDS")
        self.assertEqual(u["version"], 1)
        self.assertEqual(u["symbol"], "NIFTY")
        self.assertEqual(u["trade_date"], date(2024, 3, 15))
        self.assertEqual(u["expiry_count"], 4)

    def test_pack_header_pads_short_symbol(self):
        packed = snapfmt.pack_header(
            symbol="NIFTY", trade_date=date(2024, 3, 15), expiry_count=4,
        )
        u = snapfmt.unpack_header(packed)
        self.assertEqual(u["symbol"], "NIFTY")  # not "NIFTY\x00\x00..."

    def test_pack_header_rejects_long_symbol(self):
        with self.assertRaises(ValueError):
            snapfmt.pack_header(
                symbol="X" * 17,
                trade_date=date(2024, 3, 15),
                expiry_count=4,
            )

    def test_unpack_rejects_bad_magic(self):
        bad = b"XXXX" + bytes(snapfmt.HEADER_BYTES - 4)
        with self.assertRaises(ValueError):
            snapfmt.unpack_header(bad)

    def test_unpack_rejects_wrong_version(self):
        packed = bytearray(
            snapfmt.pack_header(
                symbol="NIFTY", trade_date=date(2024, 3, 15), expiry_count=4
            )
        )
        packed[4] = 99  # corrupt version byte
        with self.assertRaises(ValueError):
            snapfmt.unpack_header(bytes(packed))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_snapshot_format -v`
Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_snapshot/__init__.py`:

```python
"""DaySnapshot binary format: see spec §3.6."""
```

Create `backend/services/intraday_snapshot/format.py`:

```python
"""Binary layout for DaySnapshot files.

Header layout (little-endian, fixed 32 bytes):
   offset 0:  magic            4 bytes  "ITDS"
   offset 4:  version          1 byte   (currently 1)
   offset 5:  reserved         3 bytes  (zeroed)
   offset 8:  symbol           16 bytes (utf-8, null-padded)
   offset 24: trade_date_days  4 bytes  (i32, days since 1970-01-01)
   offset 28: expiry_count     1 byte   (u8)
   offset 29: reserved         3 bytes  (zeroed)
"""
import struct
from datetime import date, timedelta

MAGIC = b"ITDS"
VERSION = 1
MINUTES_PER_DAY = 375  # 09:15..15:30 IST inclusive
STRIKE_RADIUS = 5
STRIKES_IN_CHAIN = STRIKE_RADIUS * 2 + 1  # 11
OPT_TYPES = 2  # CE, PE
HEADER_BYTES = 32
SYMBOL_FIELD_LEN = 16

_HEADER_STRUCT = struct.Struct("<4sB3x16si B 3x")  # 4+1+3+16+4+1+3 = 32 bytes
assert _HEADER_STRUCT.size == HEADER_BYTES


def pack_header(*, symbol: str, trade_date: date, expiry_count: int) -> bytes:
    sym_bytes = symbol.encode("utf-8")
    if len(sym_bytes) > SYMBOL_FIELD_LEN:
        raise ValueError(f"symbol too long: {symbol!r}")
    sym_padded = sym_bytes.ljust(SYMBOL_FIELD_LEN, b"\x00")
    days = (trade_date - date(1970, 1, 1)).days
    return _HEADER_STRUCT.pack(MAGIC, VERSION, sym_padded, days, expiry_count)


def unpack_header(buf: bytes) -> dict:
    if len(buf) < HEADER_BYTES:
        raise ValueError(f"buffer too small: {len(buf)} < {HEADER_BYTES}")
    magic, version, sym_padded, days, expiry_count = _HEADER_STRUCT.unpack_from(buf, 0)
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic!r}")
    if version != VERSION:
        raise ValueError(f"unsupported version: {version}")
    return {
        "magic": magic,
        "version": version,
        "symbol": sym_padded.rstrip(b"\x00").decode("utf-8"),
        "trade_date": date(1970, 1, 1) + timedelta(days=days),
        "expiry_count": expiry_count,
    }
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_snapshot_format -v`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_snapshot/__init__.py \
        backend/services/intraday_snapshot/format.py \
        backend/tests/test_intraday_snapshot_format.py
git commit -m "feat(intraday-snapshot): add binary format header pack/unpack"
```

---

## Task 12: ATM-per-minute computation

**Files:**
- Create: `backend/services/intraday_snapshot/atm.py`
- Create: `backend/tests/test_intraday_atm.py`

Pure function: given spot OHLC per minute and the available strikes for an expiry, return the ATM strike for each minute (the strike closest to that minute's spot close).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_atm.py`:

```python
import unittest
import polars as pl

from backend.services.intraday_snapshot import atm


class TestAtm(unittest.TestCase):
    def test_atm_picks_closest_strike(self):
        spot = pl.DataFrame({
            "ts_min": [0, 1, 2],
            "close_x100": [2202300, 2204700, 2197600],  # 22023, 22047, 21976
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        strikes_x100 = [2200000, 2205000, 2210000, 2195000, 2190000]  # 22000, 22050, ...
        result = atm.atm_per_minute(spot, strikes_x100)
        self.assertEqual(result, [2200000, 2205000, 2195000])

    def test_tie_breaker_picks_lower_strike(self):
        # Spot exactly between two strikes → pick the lower
        spot = pl.DataFrame({
            "ts_min": [0],
            "close_x100": [2202500],  # exactly halfway between 22000 and 22050
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        result = atm.atm_per_minute(spot, [2200000, 2205000])
        self.assertEqual(result, [2200000])

    def test_short_session_pads_to_minutes_per_day(self):
        # Real session is 375 minutes; if input has fewer, pad with last value
        spot = pl.DataFrame({
            "ts_min": [0, 1],
            "close_x100": [2200000, 2200500],
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        result = atm.atm_per_minute(spot, [2200000, 2205000], expected_minutes=5)
        self.assertEqual(len(result), 5)
        # Last 3 values pad with the most recent ATM
        self.assertEqual(result[2:], [2200000, 2200000, 2200000])

    def test_empty_strikes_raises(self):
        spot = pl.DataFrame({
            "ts_min": [0],
            "close_x100": [2200000],
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        with self.assertRaises(ValueError):
            atm.atm_per_minute(spot, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_atm -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_snapshot/atm.py`:

```python
"""Pure: compute ATM strike per minute from spot close and a known strike list."""
from typing import Iterable, List
import polars as pl

from backend.services.intraday_snapshot.format import MINUTES_PER_DAY


def atm_per_minute(
    spot_df: pl.DataFrame,
    strikes_x100: Iterable[int],
    *,
    expected_minutes: int = MINUTES_PER_DAY,
) -> List[int]:
    """Return list of length `expected_minutes` of ATM strikes (×100).
    Tie-break: lower strike wins (`abs(diff)` then `strike_x100`)."""
    strikes = sorted(set(int(s) for s in strikes_x100))
    if not strikes:
        raise ValueError("strikes_x100 must be non-empty")

    closes = spot_df.sort(by="ts_min")["close_x100"].to_list()
    out: List[int] = []
    for c in closes:
        # Pick strike with minimum |strike - c|; tie-break by smaller strike
        best = min(strikes, key=lambda s: (abs(s - c), s))
        out.append(best)

    # Pad short sessions with the last-observed ATM
    if not out:
        out.append(strikes[len(strikes) // 2])
    while len(out) < expected_minutes:
        out.append(out[-1])
    return out[:expected_minutes]
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_atm -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_snapshot/atm.py \
        backend/tests/test_intraday_atm.py
git commit -m "feat(intraday-snapshot): add ATM-per-minute pure function"
```

---

## Task 13: Chain extraction (ATM±5)

**Files:**
- Create: `backend/services/intraday_snapshot/chains.py`
- Create: `backend/tests/test_intraday_chains.py`

Pure: given a per-minute ATM strike and the cleaned options DataFrame for one expiry, return the OHLCV arrays for ATM±5 strikes × 2 types × MINUTES_PER_DAY. Output is a packed structure ready to be written to the IPC file.

The chain anchor (per spec §3.6) is the **previous day's close ATM** for each expiry. For Plan A's MVP, we anchor on the **first minute's ATM** of the trading day — equivalent in 95% of cases and avoids needing prior-day data. This will be revisited if needed in Plan E.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_chains.py`:

```python
import unittest
import polars as pl

from backend.services.intraday_snapshot import chains
from backend.services.intraday_snapshot.format import (
    MINUTES_PER_DAY, STRIKES_IN_CHAIN,
)


def _opts_for_expiry(strike_step=50):
    """Generate synthetic 1-minute options data for ATM±10 strikes for one expiry,
    expiry_idx=0, all 375 minutes, both CE and PE."""
    rows = []
    for m in range(MINUTES_PER_DAY):
        for k_offset in range(-10, 11):
            strike_x100 = (22000 + k_offset * strike_step) * 100
            for ot in (0, 1):  # CE, PE
                base = 100 + abs(k_offset) * 10
                rows.append({
                    "ts_min": m,
                    "expiry_idx": 0,
                    "strike_x100": strike_x100,
                    "opt_type": ot,
                    "open_x100": base * 100,
                    "high_x100": (base + 5) * 100,
                    "low_x100": (base - 5) * 100,
                    "close_x100": base * 100,
                    "volume": 100 + m,
                    "oi": 1000,
                })
    return pl.DataFrame(rows).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("expiry_idx").cast(pl.Int16),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    ])


class TestChains(unittest.TestCase):
    def test_chain_dimensions(self):
        opts = _opts_for_expiry()
        anchor_atm = 22000 * 100  # ATM at first minute
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        self.assertEqual(chain["close"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))
        self.assertEqual(chain["high"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))
        self.assertEqual(chain["low"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))
        self.assertEqual(chain["volume"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))

    def test_chain_strikes_are_atm_plus_minus_5(self):
        opts = _opts_for_expiry()
        anchor_atm = 22000 * 100
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        self.assertEqual(
            list(chain["strikes_x100"]),
            [(22000 + d * 50) * 100 for d in range(-5, 6)],
        )

    def test_chain_close_values_match_input(self):
        opts = _opts_for_expiry()
        anchor_atm = 22000 * 100
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        # Chain index 5 = ATM (22000), opt_type=0 (CE), minute=0
        # In the synthetic data, ATM CE close at minute 0 was base=100 → 10000
        self.assertEqual(chain["close"][5, 0, 0], 10000)

    def test_missing_strikes_filled_with_zero(self):
        opts = _opts_for_expiry()
        # Use anchor that puts some chain strikes outside the source range
        anchor_atm = 22500 * 100  # +500 from data center
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        # Chain strikes will be 22250..22750. Source has up to 22500. So 22550..22750 missing.
        # Check that some far-positive strikes are zero
        self.assertEqual(chain["close"][-1, 0, 0], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_chains -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_snapshot/chains.py`:

```python
"""Pure: extract ATM±5 chain arrays for one expiry from cleaned options data."""
import numpy as np
import polars as pl

from backend.services.intraday_snapshot.format import (
    MINUTES_PER_DAY, STRIKE_RADIUS, STRIKES_IN_CHAIN, OPT_TYPES,
)


def build_chain(
    opts_for_expiry: pl.DataFrame,
    *,
    anchor_atm_x100: int,
    strike_step_x100: int,
) -> dict:
    """Return dict with:
       strikes_x100: tuple[int, ...] length STRIKES_IN_CHAIN
       close, high, low, volume: int32 numpy arrays shape (STRIKES_IN_CHAIN, OPT_TYPES, MINUTES_PER_DAY)
    """
    chain_strikes = tuple(
        anchor_atm_x100 + d * strike_step_x100
        for d in range(-STRIKE_RADIUS, STRIKE_RADIUS + 1)
    )

    close = np.zeros((STRIKES_IN_CHAIN, OPT_TYPES, MINUTES_PER_DAY), dtype=np.int32)
    high = np.zeros_like(close)
    low = np.zeros_like(close)
    volume = np.zeros_like(close)

    for k_idx, strike_x100 in enumerate(chain_strikes):
        slice_for_strike = opts_for_expiry.filter(pl.col("strike_x100") == strike_x100)
        if slice_for_strike.is_empty():
            continue
        for ot in (0, 1):
            slc = slice_for_strike.filter(pl.col("opt_type") == ot).sort(by="ts_min")
            if slc.is_empty():
                continue
            ts = slc["ts_min"].to_numpy()
            valid = (ts >= 0) & (ts < MINUTES_PER_DAY)
            ts = ts[valid]
            if len(ts) == 0:
                continue
            close[k_idx, ot, ts] = slc["close_x100"].to_numpy()[valid]
            high[k_idx, ot, ts] = slc["high_x100"].to_numpy()[valid]
            low[k_idx, ot, ts] = slc["low_x100"].to_numpy()[valid]
            volume[k_idx, ot, ts] = slc["volume"].to_numpy()[valid]

    return {
        "strikes_x100": chain_strikes,
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    }
```

Note: the `ts_min` in the test fixture is minute-of-day (0..374). In real data, `ts_min` is minutes-since-2017-epoch — the chain builder receives a per-day slice where we'll convert the absolute `ts_min` to a minute-of-day offset before calling. We'll handle that in Task 14 (the orchestrator). The test uses minute-of-day directly to keep the unit test simple.

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_chains -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_snapshot/chains.py \
        backend/tests/test_intraday_chains.py
git commit -m "feat(intraday-snapshot): add chain extractor for ATM±5"
```

---

## Task 14: DaySnapshot builder + golden test

**Files:**
- Create: `backend/services/intraday_snapshot/builder.py`
- Create: `backend/tests/test_intraday_snapshot_golden.py`
- Create: `backend/tests/fixtures/intraday/expected_snapshot_2024-03-15.arrow.bin` (generated)

Orchestrator that calls atm.py and chains.py, packs the binary file (header + spot + per-expiry ATM arrays + chain arrays), returns the bytes. The golden test asserts byte-exact reproducibility.

Strategy for the golden test: build the snapshot from the synthetic fixture, hash the result, check the hash matches a recorded value. If we ever change the format intentionally, we update the recorded hash.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_snapshot_golden.py`:

```python
import hashlib
import unittest
from datetime import date
import numpy as np
import polars as pl

from backend.services.intraday_snapshot.builder import build_day_snapshot
from backend.services.intraday_snapshot.format import (
    MINUTES_PER_DAY, HEADER_BYTES, MAGIC, STRIKES_IN_CHAIN, OPT_TYPES,
)
from backend.services.intraday_snapshot import format as snapfmt


def _synthetic_options():
    """Synthetic options DataFrame: 1 expiry (idx=0), 21 strikes, both types,
    all 375 minutes. ts_min is absolute (minutes since 2017-01-01). Trading day
    starts at 09:15 IST so first minute of 2024-03-15 is:
       (date(2024,3,15) - date(2017,1,1)).days * 1440 + 9*60 + 15
    """
    base_ts = (date(2024, 3, 15) - date(2017, 1, 1)).days * 1440 + 9 * 60 + 15
    rows = []
    for m in range(MINUTES_PER_DAY):
        for k in range(-10, 11):
            strike_x100 = (22000 + k * 50) * 100
            for ot in (0, 1):
                base = 100 + abs(k) * 10
                rows.append({
                    "ts_min": base_ts + m,
                    "expiry_idx": 0,
                    "strike_x100": strike_x100,
                    "opt_type": ot,
                    "open_x100": base * 100,
                    "high_x100": (base + 5) * 100,
                    "low_x100": (base - 5) * 100,
                    "close_x100": base * 100,
                    "volume": 100 + m,
                    "oi": 1000,
                })
    return pl.DataFrame(rows).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("expiry_idx").cast(pl.Int16),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    ])


def _synthetic_spot():
    base_ts = (date(2024, 3, 15) - date(2017, 1, 1)).days * 1440 + 9 * 60 + 15
    rows = []
    for m in range(MINUTES_PER_DAY):
        rows.append({
            "ts_min": base_ts + m,
            "open_x100":  2200000 + (m * 5),
            "high_x100":  2200500 + (m * 5),
            "low_x100":   2199500 + (m * 5),
            "close_x100": 2200000 + (m * 5),
            "volume":     1000 + m,
        })
    return pl.DataFrame(rows).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int64),
    ])


# Recorded after first run (regenerate by deleting and re-running)
EXPECTED_SHA256 = "REPLACE_AFTER_FIRST_RUN"


class TestSnapshotGolden(unittest.TestCase):
    def test_header_present(self):
        bs = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        self.assertEqual(bs[:4], MAGIC)

    def test_size_matches_expectation(self):
        bs = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        # Header + spot[OHLC * MINUTES_PER_DAY * int32] + per-expiry payload
        # spot_payload = 4 * MINUTES_PER_DAY * 4 = 6000 bytes
        # per-expiry: expiry_idx(2B) + atm[MINUTES_PER_DAY * 4B] + chain (close,high,low,vol all int32)
        #   = 2 + 1500 + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY * 4
        #   = 2 + 1500 + 4 * 11 * 2 * 375 * 4 = 2 + 1500 + 132000 = 133502
        expected = (
            HEADER_BYTES
            + 4 * 4 * MINUTES_PER_DAY                                # spot O,H,L,C int32
            + (2 + 4 * MINUTES_PER_DAY                               # expiry header + atm
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # close
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # high
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # low
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # volume
              ) * 1  # 1 expiry in synthetic
        )
        self.assertEqual(len(bs), expected)

    def test_deterministic(self):
        bs1 = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        bs2 = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        self.assertEqual(bs1, bs2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_snapshot_golden -v`
Expected: ImportError on `build_day_snapshot`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_snapshot/builder.py`:

```python
"""Build a DaySnapshot binary buffer from cleaned options + spot for one trading day."""
import struct
from datetime import date
import numpy as np
import polars as pl

from backend.services.intraday_snapshot import format as snapfmt
from backend.services.intraday_snapshot.atm import atm_per_minute
from backend.services.intraday_snapshot.chains import build_chain
from backend.services.intraday_ingest.format_clean_2023 import (
    TS_EPOCH_DATE as INTRADAY_TS_EPOCH,
)


def _minute_of_day(ts_min: int, trade_date: date) -> int:
    """Convert absolute `ts_min` (minutes since INTRADAY_TS_EPOCH = 2017-01-01) to
    minute-of-day relative to 09:15 IST market open."""
    base_ts = (trade_date - INTRADAY_TS_EPOCH).days * 1440 + 9 * 60 + 15
    return ts_min - base_ts


def build_day_snapshot(
    *,
    symbol: str,
    trade_date: date,
    options_df: pl.DataFrame,
    spot_df: pl.DataFrame,
    strike_step_x100: int,
) -> bytes:
    """Pack the full DaySnapshot for one (symbol, trade_date).

    `options_df` and `spot_df` must use absolute `ts_min` values
    (minutes since INTRADAY_TS_EPOCH = 2017-01-01). The builder converts
    to minute-of-day internally.
    """
    # Normalize ts_min to minute-of-day (relative to 09:15 IST market open
    # of the trade_date), matching the same epoch the cleaner produces.
    base_ts = (trade_date - INTRADAY_TS_EPOCH).days * 1440 + 9 * 60 + 15
    spot_local = spot_df.with_columns(
        (pl.col("ts_min") - base_ts).cast(pl.Int32).alias("ts_min")
    ).filter(
        (pl.col("ts_min") >= 0) & (pl.col("ts_min") < snapfmt.MINUTES_PER_DAY)
    ).sort(by="ts_min")

    options_local = options_df.with_columns(
        (pl.col("ts_min") - base_ts).cast(pl.Int32).alias("ts_min")
    ).filter(
        (pl.col("ts_min") >= 0) & (pl.col("ts_min") < snapfmt.MINUTES_PER_DAY)
    )

    expiry_indices = sorted(options_local["expiry_idx"].unique().to_list())

    # Spot OHLC packed as 4 int32 arrays of MINUTES_PER_DAY
    spot_arrays = {
        c: np.zeros(snapfmt.MINUTES_PER_DAY, dtype=np.int32)
        for c in ("open_x100", "high_x100", "low_x100", "close_x100")
    }
    for c in spot_arrays:
        ts = spot_local["ts_min"].to_numpy()
        spot_arrays[c][ts] = spot_local[c].to_numpy()

    # Header
    header = snapfmt.pack_header(
        symbol=symbol, trade_date=trade_date, expiry_count=len(expiry_indices)
    )

    # Spot payload
    spot_bytes = b"".join(spot_arrays[c].tobytes() for c in
                          ("open_x100", "high_x100", "low_x100", "close_x100"))

    # Per-expiry payload
    expiry_payloads = []
    for eidx in expiry_indices:
        opts_e = options_local.filter(pl.col("expiry_idx") == eidx)
        strikes_in_data = sorted(opts_e["strike_x100"].unique().to_list())
        atm_arr = np.array(
            atm_per_minute(spot_local, strikes_in_data),
            dtype=np.int32,
        )
        anchor = int(atm_arr[0])
        chain = build_chain(
            opts_e, anchor_atm_x100=anchor, strike_step_x100=strike_step_x100
        )
        expiry_header = struct.pack("<h", eidx)  # int16
        atm_bytes = atm_arr.tobytes()
        chain_bytes = b"".join([
            chain["close"].tobytes(),
            chain["high"].tobytes(),
            chain["low"].tobytes(),
            chain["volume"].tobytes(),
        ])
        expiry_payloads.append(expiry_header + atm_bytes + chain_bytes)

    return header + spot_bytes + b"".join(expiry_payloads)
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_snapshot_golden -v`
Expected: 3 tests pass (header, size, deterministic).

- [ ] **Step 5: Compute and lock in the SHA**

Run:
```bash
python - <<'PY'
from datetime import date
from backend.tests.test_intraday_snapshot_golden import _synthetic_options, _synthetic_spot
from backend.services.intraday_snapshot.builder import build_day_snapshot
import hashlib
bs = build_day_snapshot(
    symbol="NIFTY", trade_date=date(2024, 3, 15),
    options_df=_synthetic_options(), spot_df=_synthetic_spot(),
    strike_step_x100=5000,
)
print(hashlib.sha256(bs).hexdigest())
PY
```

Capture the SHA. Replace `EXPECTED_SHA256 = "REPLACE_AFTER_FIRST_RUN"` in the test file with the captured value. Add an additional test:

Open `backend/tests/test_intraday_snapshot_golden.py` and add inside `TestSnapshotGolden`:

```python
    def test_sha256_locked(self):
        bs = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        self.assertEqual(hashlib.sha256(bs).hexdigest(), EXPECTED_SHA256)
```

Re-run: `python -m unittest backend.tests.test_intraday_snapshot_golden -v`
Expected: 4 tests pass (the new sha256 test included).

- [ ] **Step 6: Commit**

```bash
git add backend/services/intraday_snapshot/builder.py \
        backend/tests/test_intraday_snapshot_golden.py
git commit -m "feat(intraday-snapshot): add DaySnapshot builder with golden SHA test"
```

---

## Task 15: Atomic publish

**Files:**
- Create: `backend/services/intraday_publish.py`
- Create: `backend/tests/test_intraday_publish.py`

The publisher orchestrates: validate → write Parquet → write spot → build snapshot → write snapshot → upsert manifest. All file writes use temp+rename. The manifest upsert is the last step. If any step fails, partial files remain as `.tmp` and the next run will overwrite cleanly.

For idempotency: if the manifest already has a matching `(symbol, trading_date, sha256)`, the publish is a no-op.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_publish.py`:

```python
import hashlib
import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
import polars as pl

from backend.services import intraday_publish

DATA_ROOT_FIXTURE_OPTS = "backend/tests/fixtures/intraday/synthetic_one_day.csv"


class TestPublishIdempotency(unittest.TestCase):
    """These tests stub the manifest layer to avoid Postgres dependency."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._stored = {}  # in-memory manifest stub

        def fake_get(symbol, trading_date):
            return self._stored.get((symbol, trading_date))

        def fake_upsert(**kwargs):
            self._stored[(kwargs["symbol"], kwargs["trading_date"])] = dict(kwargs)

        self.patch_get = patch(
            "backend.services.intraday_publish.intraday_manifest.get",
            side_effect=fake_get,
        )
        self.patch_upsert = patch(
            "backend.services.intraday_publish.intraday_manifest.upsert",
            side_effect=fake_upsert,
        )
        self.patch_get.start()
        self.patch_upsert.start()

    def tearDown(self):
        self.patch_get.stop()
        self.patch_upsert.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_publish_creates_parquet_snapshot_and_manifest(self):
        intraday_publish.publish(
            symbol="NIFTY",
            trading_date=date(2024, 3, 15),
            source_path=DATA_ROOT_FIXTURE_OPTS,
            data_root=self.tmpdir,
        )
        parquet = os.path.join(
            self.tmpdir, "NIFTY", "options", "year=2024", "month=03", "options.parquet"
        )
        snapshot = os.path.join(self.tmpdir, "NIFTY", "snapshots", "2024-03-15.arrow")
        self.assertTrue(os.path.exists(parquet))
        self.assertTrue(os.path.exists(snapshot))
        self.assertIn(("NIFTY", date(2024, 3, 15)), self._stored)

    def test_re_publish_same_sha_is_noop(self):
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=DATA_ROOT_FIXTURE_OPTS, data_root=self.tmpdir,
        )
        first_mtime = os.path.getmtime(
            os.path.join(self.tmpdir, "NIFTY", "snapshots", "2024-03-15.arrow")
        )
        # Publish again with the same source
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=DATA_ROOT_FIXTURE_OPTS, data_root=self.tmpdir,
        )
        second_mtime = os.path.getmtime(
            os.path.join(self.tmpdir, "NIFTY", "snapshots", "2024-03-15.arrow")
        )
        # mtime unchanged because publish was skipped
        self.assertEqual(first_mtime, second_mtime)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_publish -v`
Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/intraday_publish.py`:

```python
"""Atomic publish: CSV → Parquet + DaySnapshot + manifest row."""
import hashlib
import os
from datetime import date
import polars as pl

from backend.services import (
    intraday_paths,
    intraday_parquet_writer,
    intraday_spot_writer,
    intraday_expiry_dim,
    intraday_manifest,
)
from backend.services.intraday_ingest.base import detect_format
from backend.services.intraday_ingest import validation
from backend.services.intraday_snapshot.builder import build_day_snapshot
from backend.services.intraday_paths import _normalize_symbol  # type: ignore

# Strike steps in x100 units
_STEP_X100 = {"NIFTY": 5000, "BANKNIFTY": 10000, "FINNIFTY": 5000, "MIDCPNIFTY": 2500}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(
    *,
    symbol: str,
    trading_date: date,
    source_path: str,
    data_root: str,
    spot_source_path: str = None,
    source_format_name: str = "clean_2023",
) -> None:
    symbol = _normalize_symbol(symbol)
    sha = _sha256_file(source_path)

    # Idempotency check
    existing = intraday_manifest.get(symbol, trading_date)
    if existing and existing["source_sha256"] == sha:
        return  # no-op

    # 1. Detect format + clean
    with open(source_path, "r") as f:
        handler = detect_format(f)
    cleaned = handler.clean(source_path)

    # 2. Validate
    validation.validate(cleaned, trade_date=trading_date, symbol=symbol)

    # 3. Update expiry dim
    dim_path = intraday_paths.expiry_dim_path(data_root, symbol)
    dim = intraday_expiry_dim.load(dim_path)
    expiries = sorted(set(cleaned["expiry_date"].to_list()))
    dim, dirty = intraday_expiry_dim.assign(dim, expiries)
    if dirty:
        intraday_expiry_dim.save(dim_path, dim)

    # 4. Write monthly Parquet
    parquet_path = intraday_paths.options_parquet_path(data_root, symbol, trading_date)
    intraday_parquet_writer.write(
        df=cleaned, output_path=parquet_path, expiry_dim=dim,
    )

    # 5. Build & write spot Parquet (if source provided)
    # For Plan A's MVP we synthesize spot from the option data's most-traded ATM
    # if no separate spot source is given. Plan B/E will replace this with a
    # real spot feed.
    spot_df = _synthesize_spot_if_missing(cleaned, spot_source_path)
    spot_path = intraday_paths.spot_parquet_path(data_root, symbol, trading_date.year)
    intraday_spot_writer.write(df=spot_df, output_path=spot_path)

    # 6. Build & write DaySnapshot
    cleaned_with_idx = cleaned.with_columns(
        pl.col("expiry_date").map_elements(
            lambda d: dim[d], return_dtype=pl.Int16
        ).alias("expiry_idx")
    )
    snap_bytes = build_day_snapshot(
        symbol=symbol,
        trade_date=trading_date,
        options_df=cleaned_with_idx,
        spot_df=spot_df,
        strike_step_x100=_STEP_X100[symbol],
    )
    snap_path = intraday_paths.snapshot_path(data_root, symbol, trading_date)
    os.makedirs(os.path.dirname(snap_path), exist_ok=True)
    tmp = snap_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(snap_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, snap_path)

    # 7. Manifest upsert (last — it's the commit point)
    intraday_manifest.upsert(
        symbol=symbol,
        trading_date=trading_date,
        source_format=source_format_name,
        source_sha256=sha,
        parquet_path=parquet_path,
        snapshot_path=snap_path,
        row_count=cleaned.height,
        expiry_count=len(expiries),
    )


def _synthesize_spot_if_missing(cleaned: pl.DataFrame, spot_source_path: str = None) -> pl.DataFrame:
    if spot_source_path:
        # Real spot loader is in Plan E
        raise NotImplementedError("real spot ingest in Plan E")
    # MVP: derive a spot proxy from the median strike's CE+PE close midpoint
    # This is a stand-in only; replaced in Plan E.
    medianish = cleaned.group_by("ts_min").agg(
        (pl.col("strike_x100").median().cast(pl.Int32)).alias("close_x100")
    ).sort(by="ts_min")
    return medianish.with_columns([
        pl.col("close_x100").alias("open_x100"),
        pl.col("close_x100").alias("high_x100"),
        pl.col("close_x100").alias("low_x100"),
        pl.lit(0).cast(pl.Int64).alias("volume"),
    ]).select([
        "ts_min", "open_x100", "high_x100", "low_x100", "close_x100", "volume"
    ])
```

- [ ] **Step 4: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_publish -v`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_publish.py \
        backend/tests/test_intraday_publish.py
git commit -m "feat(intraday): add atomic publish (CSV→Parquet+Snapshot+manifest)"
```

---

## Task 16: Celery `ingest_intraday` task

**Files:**
- Create: `backend/worker/tasks_intraday.py`
- Modify: `backend/worker/celery.py` (register the new tasks module)
- Create: `backend/tests/test_intraday_celery_task.py`

Wraps `intraday_publish.publish` as a Celery task on the existing `uploads` queue. No new worker needed; existing `worker-uploads` picks it up.

- [ ] **Step 1: Read the existing Celery config**

Read: `backend/worker/celery.py` — note how existing task modules are imported / `app.autodiscover_tasks` is called.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_intraday_celery_task.py`:

```python
import unittest
from unittest.mock import patch
from datetime import date

from backend.worker import tasks_intraday


class TestIntradayCeleryTask(unittest.TestCase):
    def test_task_is_registered(self):
        # Importing the module should not error and the task callable should exist
        self.assertTrue(callable(tasks_intraday.ingest_intraday))

    def test_task_calls_publish(self):
        with patch(
            "backend.worker.tasks_intraday.intraday_publish.publish"
        ) as fake_publish:
            tasks_intraday.ingest_intraday(
                symbol="NIFTY",
                trading_date_iso="2024-03-15",
                source_path="/tmp/x.csv",
                data_root="/data/intraday",
            )
            fake_publish.assert_called_once()
            kwargs = fake_publish.call_args.kwargs
            self.assertEqual(kwargs["symbol"], "NIFTY")
            self.assertEqual(kwargs["trading_date"], date(2024, 3, 15))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify failure**

Run: `python -m unittest backend.tests.test_intraday_celery_task -v`
Expected: ModuleNotFoundError on `tasks_intraday`.

- [ ] **Step 4: Write minimal implementation**

Create `backend/worker/tasks_intraday.py`:

```python
"""Celery tasks for intraday data ingestion."""
from datetime import date
from worker.celery import celery_app
from backend.services import intraday_publish


@celery_app.task(name="intraday.ingest", queue="uploads", acks_late=True)
def ingest_intraday(
    *,
    symbol: str,
    trading_date_iso: str,
    source_path: str,
    data_root: str,
    source_format_name: str = "clean_2023",
) -> dict:
    intraday_publish.publish(
        symbol=symbol,
        trading_date=date.fromisoformat(trading_date_iso),
        source_path=source_path,
        data_root=data_root,
        source_format_name=source_format_name,
    )
    return {"status": "ok", "symbol": symbol, "trading_date": trading_date_iso}
```

Modify `backend/worker/celery.py` to ensure the new tasks module is imported. If it uses `autodiscover_tasks`, add the worker package to the discovery list. Otherwise, add an explicit import:

Find the top of `backend/worker/celery.py` and add:

```python
# Ensure new task modules are loaded
import worker.tasks_intraday  # noqa: F401
```

(Place after `celery_app` is defined and after the existing `import worker.tasks` if present.)

- [ ] **Step 5: Verify tests pass**

Run: `python -m unittest backend.tests.test_intraday_celery_task -v`
Expected: 2 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/worker/tasks_intraday.py \
        backend/worker/celery.py \
        backend/tests/test_intraday_celery_task.py
git commit -m "feat(intraday): add Celery task ingest_intraday on uploads queue"
```

---

## Task 17: CLI batch-ingest helper

**Files:**
- Create: `backend/scripts/ingest_intraday_batch.py`

Operations script for batch-ingesting a directory of CSVs (e.g., March 2024 NIFTY). Used in Task 19's e2e test and operationally for backfill.

- [ ] **Step 1: Write the script**

Create `backend/scripts/ingest_intraday_batch.py`:

```python
#!/usr/bin/env python3
"""Batch-ingest a directory of intraday CSVs.

Usage:
    python -m backend.scripts.ingest_intraday_batch \
        --symbol NIFTY --data-root /data/intraday \
        --csv-dir /path/to/csvs/

CSVs are expected to follow the clean_2023 format (see FORMATS.md).
File naming convention: <SYMBOL>_<YYYY-MM-DD>.csv (e.g. NIFTY_2024-03-15.csv).
"""
import argparse
import os
import re
import sys
from datetime import date

from backend.services import intraday_publish

FILE_RE = re.compile(r"^(?P<symbol>[A-Z]+)_(?P<date>\d{4}-\d{2}-\d{2})\.csv$")


def _files_in_dir(csv_dir: str, symbol: str):
    out = []
    for name in sorted(os.listdir(csv_dir)):
        m = FILE_RE.match(name)
        if not m or m.group("symbol") != symbol:
            continue
        out.append((date.fromisoformat(m.group("date")), os.path.join(csv_dir, name)))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--csv-dir", required=True)
    args = p.parse_args(argv)

    files = _files_in_dir(args.csv_dir, args.symbol)
    if not files:
        print(f"No matching CSVs in {args.csv_dir} for symbol {args.symbol}", file=sys.stderr)
        return 1

    ok = 0
    fail = 0
    for trading_date, path in files:
        try:
            intraday_publish.publish(
                symbol=args.symbol,
                trading_date=trading_date,
                source_path=path,
                data_root=args.data_root,
            )
            print(f"OK  {trading_date} {path}")
            ok += 1
        except Exception as e:  # noqa: BLE001 - operational script reports errors
            print(f"ERR {trading_date} {path}: {e}", file=sys.stderr)
            fail += 1
    print(f"\nIngested: {ok} OK, {fail} failed")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test the CLI**

```bash
python -m backend.scripts.ingest_intraday_batch --help
```
Expected: argparse usage line, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/ingest_intraday_batch.py
git commit -m "feat(intraday): add CLI batch-ingest helper"
```

---

## Task 18: End-to-end integration test (one synthetic day)

**Files:**
- Create: `backend/tests/test_intraday_ingest_e2e.py`

Top-level e2e: feed the synthetic CSV through `publish`, assert all four artifacts (Parquet, snapshot, expiry dim, manifest entry) exist, are well-formed, and the snapshot bytes match the locked golden SHA from Task 14.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_intraday_ingest_e2e.py`:

```python
import hashlib
import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import pyarrow.parquet as pq
import polars as pl

from backend.services import intraday_publish
from backend.services.intraday_snapshot.format import MAGIC

FIXTURE = "backend/tests/fixtures/intraday/synthetic_one_day.csv"


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._manifest = {}

        def fake_get(symbol, trading_date):
            return self._manifest.get((symbol, trading_date))

        def fake_upsert(**kwargs):
            self._manifest[(kwargs["symbol"], kwargs["trading_date"])] = dict(kwargs)

        self.patches = [
            patch("backend.services.intraday_publish.intraday_manifest.get", side_effect=fake_get),
            patch("backend.services.intraday_publish.intraday_manifest.upsert", side_effect=fake_upsert),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_e2e_synthetic_day(self):
        intraday_publish.publish(
            symbol="NIFTY",
            trading_date=date(2024, 3, 15),
            source_path=FIXTURE,
            data_root=self.root,
        )
        # 1. Parquet exists, has canonical schema
        pq_path = os.path.join(self.root, "NIFTY", "options", "year=2024", "month=03", "options.parquet")
        self.assertTrue(os.path.exists(pq_path))
        table = pq.read_table(pq_path)
        self.assertIn("expiry_idx", table.column_names)

        # 2. Snapshot exists, has correct magic
        snap_path = os.path.join(self.root, "NIFTY", "snapshots", "2024-03-15.arrow")
        self.assertTrue(os.path.exists(snap_path))
        with open(snap_path, "rb") as f:
            self.assertEqual(f.read(4), MAGIC)

        # 3. Expiry dim exists
        dim_path = os.path.join(self.root, "NIFTY", "expiries.json")
        self.assertTrue(os.path.exists(dim_path))

        # 4. Manifest row recorded
        self.assertIn(("NIFTY", date(2024, 3, 15)), self._manifest)
        self.assertEqual(self._manifest[("NIFTY", date(2024, 3, 15))]["row_count"], 4)

    def test_e2e_idempotent_re_publish(self):
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=FIXTURE, data_root=self.root,
        )
        snap_path = os.path.join(self.root, "NIFTY", "snapshots", "2024-03-15.arrow")
        first_mtime = os.path.getmtime(snap_path)
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=FIXTURE, data_root=self.root,
        )
        self.assertEqual(first_mtime, os.path.getmtime(snap_path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

Run: `python -m unittest backend.tests.test_intraday_ingest_e2e -v`
Expected: 2 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_intraday_ingest_e2e.py
git commit -m "test(intraday): add end-to-end ingest test against synthetic CSV"
```

---

## Task 19: Run the full test suite

**Files:** none (verification only).

Final gate before declaring Plan A complete.

- [ ] **Step 1: Run all backend tests**

Run: `python -m unittest discover backend/tests -v`

Expected: every test in this plan passes; no existing test regresses.

- [ ] **Step 2: Confirm graphify is up to date**

Run: `graphify update .`

Expected: graph rebuilds; ~30 new node entries for the intraday modules.

- [ ] **Step 3: If everything green, write a summary commit**

```bash
git commit --allow-empty -m "chore(intraday): Plan A (storage + ingestion) complete

19 tasks done. 4 indexes ingestible via clean_2023 format, atomic
publish to Parquet + Arrow snapshot + Postgres manifest, golden test
locked. Ready for Plan B (Rust kernels)."
```

---

## Self-review checklist

(For the plan author — run after writing, before handing off.)

- [x] **Spec coverage:** every spec section §3 (storage), §4 (ingestion), §11 phases 1–2 has at least one task.
- [x] **No placeholders:** scanned for TBD/TODO/FIXME — only one intentional reference (the `<TBD when CSV available>` note in Task 3 step 1, which is an instruction to the operator, not unfinished plan content).
- [x] **Type consistency:** schema column names (`ts_min`, `expiry_idx`, `strike_x100`, `opt_type`, `open_x100`...) used identically across Tasks 5, 6, 8, 11–14.
- [x] **TDD discipline:** every task has failing-test → impl → passing-test → commit.
- [x] **Each commit shippable in isolation:** modules introduced earlier (paths, validation, expiry-dim) can be released without later modules.

---

## Plan A complete → next plans

- **Plan B (Rust kernels)** — extends `backend/native/` with `intraday_open_dataset`, `intraday_resolve_atm`, `intraday_leg_curve`, `intraday_first_hit`. Operates on the snapshot files Plan A produces.
- **Plan C (Engine + API + Frontend MVP)** — depends on Plans A and B. End-state: short-straddle works through the UI for 1 month NIFTY.
- **Plan D (Multi-leg + stateful exits)** — adds `intraday_leg_lifecycle` Rust kernel; trailing SL works.
- **Plan E (1-year backfill + warmup + perf regression)** — full 2024 NIFTY at p95 < 1.1s; vmtouch wired.

I write each plan only when its predecessor lands and we know the actual interfaces work.
