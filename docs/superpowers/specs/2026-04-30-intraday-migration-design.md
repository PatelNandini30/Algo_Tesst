# Intraday Options Data Migration — Design Spec

**Date:** 2026-04-30
**Status:** Approved
**Scope:** NIFTY, 2025 trade dates (phase 1); all 4 symbols + 2017–2023 historical data in future phases
**Hardware target:** HP 280 Pro G6, i5-10500 6C/12T, 16 GB DDR4-3200, 1 TB Toshiba HDD, Linux
**Source data:** SMB share — `192.168.4.50/share/AAKASH/zerodha-data-processed/`
**Output:** `/data/intraday/` — filesystem layout consumed by the Axum intraday server

---

## 1. Goals and non-goals

### Goals

1. Import NIFTY 2025 trade-date data from the SMB share into the hot/cold storage layout the Axum server reads.
2. Produce Parquet (cold tier) and DaySnapshot Arrow IPC (hot tier) that are byte-identical to what the server expects — no server changes required.
3. **Idempotent**: re-running on already-imported dates is a no-op (SHA-256 match). Re-running after data correction replaces atomically.
4. **Fast**: parallel read from SMB (rayon, all CPU cores), serial write to local HDD (sequential I/O, no seek thrash). Target: full NIFTY 2025 import in under 30 minutes on the target hardware.
5. Validated output: OHLCV invariants checked per date; entire date rejected (not partially written) on any violation.
6. Observable: progress bar with ETA, per-date status, end-of-run summary report.

### Non-goals

1. 2017–2023 historical data — deferred (different source format, separate migration phase).
2. BANKNIFTY / FINNIFTY / MIDCPNIFTY — same binary and schema, added by passing `--symbol` flag in future runs.
3. Real-time / incremental ingestion — this is a batch one-shot migration tool.
4. Postgres integration — manifest is SQLite only (CLI tool, no running DB required).
5. Cleaning or gap-filling of source data beyond what the DaySnapshot builder already does.

---

## 2. Source data inventory

### 2.1 Option contracts

**Path:** `NFO_PREPARED/INDICES-OPTION/{SYMBOL}/*.csv`

One file per option contract (symbol + expiry + strike + opt_type). All trading days for that contract are rows in the same file.

**Filename pattern:** `{SYMBOL}{DD}{MON}{YY}{STRIKE}{OPTTYPE}.csv`

| Segment | Example | Notes |
|---|---|---|
| SYMBOL | `NIFTY` | |
| DD | `31` | Expiry day |
| MON | `DEC` | 3-letter month |
| YY | `26` | Last 2 digits of expiry year |
| STRIKE | `16000` | Integer, no decimal |
| OPTTYPE | `CE` or `PE` | |

Example: `NIFTY31DEC2616000CE.csv` = NIFTY, expiry 2026-12-31, strike 16000, CE.

**CSV columns (current format, 2024+):**

```
Ticker,Date,Time,Expiry Date,Open,High,Low,Close,Volume,Open Interest,Padding Flag
NIFTY31DEC2616000CE.NFO,2025-01-02,09:15:00,2026-12-31,420.0,425.5,418.0,423.0,150,1200,0
```

| Column | Type | Notes |
|---|---|---|
| Ticker | string | `{SYMBOL}{EXPIRY}.NFO` — parse symbol + expiry from here |
| Date | YYYY-MM-DD | Trade date (the day this bar occurred) |
| Time | HH:MM:SS | Bar open time; seconds always `:00` |
| Expiry Date | YYYY-MM-DD | Canonical expiry; authoritative (use this, not filename) |
| Open / High / Low / Close | float | Option OHLCV in rupees |
| Volume | int | Contracts traded in this minute |
| Open Interest | int | OI as of this bar |
| Padding Flag | 0 or 1 | `1` = broker-synthesised bar (no real trade); excluded from storage |

**File counts (NIFTY):**

| Expiry year | Files | 2025 data? |
|---|---|---|
| 24 | 4,298 | No — all expired before 2025 |
| 25 | 10,021 | Yes — active through expiry in 2025 |
| 26 | 3,900 | Yes — listed in 2024–2025, active throughout 2025 |
| 27–29 | ~116 | Possibly — long-dated contracts |
| **Total to scan** | **~14,037** | Pre-filter: skip expiry year < 25 |

### 2.2 Spot data

**Path:** `zerodha-data-processed/NIFTY 50.csv`

Single file, same column format as options. 1,028,856 rows, 2015-01-09 to 2026-02-06.
`Expiry Date` column is empty for spot. `Volume` and `Open Interest` are 0.

**For NIFTY:** use `NIFTY 50.csv`.
**Future symbol mapping:**

| Symbol | Spot file |
|---|---|
| NIFTY | `NIFTY 50.csv` |
| BANKNIFTY | `NIFTY BANK.csv` (NSE_PREPARED/INDICES/) |
| FINNIFTY | `NIFTY FIN SERVICE.csv` |
| MIDCPNIFTY | `NIFTY MID SELECT.csv` |

### 2.3 Format note for 2017–2023 (deferred)

The 2017–2023 data has a different format (not yet analysed). The migration binary will have a `--format` flag (`clean_2024` is the current default). A second format handler (`legacy_2017`) will be added in a future phase.

---

## 3. Output storage layout

```
/data/intraday/
├── NIFTY/
│   ├── expiries.json                         ← stable expiry index (append-only)
│   ├── options/
│   │   └── year=2025/
│   │       └── month=01/
│   │           ├── 2025-01-02.parquet        ← one file per trading day
│   │           ├── 2025-01-03.parquet
│   │           └── ...
│   ├── spot/
│   │   └── NIFTY-spot-2025.parquet           ← one file per year (tiny)
│   └── snapshots/
│       ├── 2025-01-02.arrow                  ← DaySnapshot, mmap'd by server
│       └── ...
└── _manifest.db                              ← SQLite, idempotency tracking
```

---

## 4. Schema

### 4.1 Options Parquet (cold tier)

**One file per trading day.** File selection is the trade-date predicate — no row-group scanning needed.

**Filters applied before writing:**
- `Padding Flag = 1` rows are **excluded entirely** (no `is_padded` column needed).
- OHLCV invariant: `high >= max(open, close) >= min(open, close) >= low`. Any row violating this causes the entire date to be rejected.

**Schema:**

| Column | Arrow type | Parquet encoding | Notes |
|---|---|---|---|
| `symbol` | `Utf8` | Dictionary | 1 unique value/file; enables cross-symbol analytics |
| `trade_date` | `Date32` | Dictionary | 1 unique value/file; makes file self-contained |
| `ts_min` | `Int16` | Delta | Minutes since midnight; 555 = 09:15, 930 = 15:30 |
| `expiry_date` | `Date32` | Dictionary | 4–8 unique values/file; authoritative expiry |
| `strike_x100` | `Int32` | Dictionary | Strike × 100; ~200 unique values/file |
| `opt_type` | `Boolean` | RLE | false = CE, true = PE |
| `open_x100` | `Int32` | Plain | Price × 100 |
| `high_x100` | `Int32` | Plain | |
| `low_x100` | `Int32` | Plain | |
| `close_x100` | `Int32` | Plain | |
| `volume` | `Int32` | Plain | Contracts per minute |
| `oi` | `Int32` | Plain | Open interest |

**Sort within file:** `(expiry_date, strike_x100, opt_type, ts_min)` — all 375 bars for one series are contiguous; slow-path query reads one contiguous slice.

**Compression:** ZSTD level 3. On HDD, better compression ratio (≈3×) reduces read bytes more than the decompression cost adds latency.

**Row group size:** single row group per file (~50–80K rows after padding exclusion). No intra-file predicate pushdown needed since the file is already one day.

**Estimated size:** ~3–5 MB per day compressed → ~750 MB–1.25 GB for NIFTY 2025.

**Writer properties (parquet-rs):**
```rust
WriterProperties::builder()
    .set_compression(Compression::ZSTD(ZstdLevel::try_new(3)?))
    .set_dictionary_enabled(true)          // applies to eligible columns
    .set_statistics_enabled(EnabledStatistics::Chunk)
    .set_max_row_group_size(1_000_000)     // effectively one row group
    .build()
```

### 4.2 Spot Parquet

One file per symbol per year. Tiny (~1 MB uncompressed).

| Column | Type | Notes |
|---|---|---|
| `trade_date` | `Date32` | |
| `ts_min` | `Int16` | 555–930 |
| `open_x100` | `Int32` | |
| `high_x100` | `Int32` | |
| `low_x100` | `Int32` | |
| `close_x100` | `Int32` | |

Padded spot bars (Padding Flag = 1) are **included** — the DaySnapshot builder needs a continuous minute series for ATM computation. Forward-fill is applied within the builder, not here.

### 4.3 DaySnapshot (hot tier)

Binary format defined in `backend/intraday_server/src/engine/snapshot.rs`. The migrate binary writes using a new `snapshot_builder.rs` module that produces the identical binary layout the server mmap-reads via `Snapshot::open()`. Format is defined once in the crate — builder and reader share the same constants.

Key builder decisions documented here for clarity:

**Expiry selection:** 4 nearest expiries that expire strictly after `trade_date`, sorted ascending. NIFTY 2025 = 1 nearest weekly Thursday expiry + 3 nearest monthly expiries (last Thursday of month).

**ATM anchor:** Computed once at 09:15 (first session bar) using the spot close at that minute. Fixed for the entire day. ATM = nearest strike to spot, rounded to the symbol's strike step.

| Symbol | Strike step | ATM±5 range |
|---|---|---|
| NIFTY | 50 | ±250 around ATM |
| BANKNIFTY | 100 | ±500 around ATM |
| FINNIFTY | 50 | ±250 around ATM |
| MIDCPNIFTY | 25 | ±125 around ATM |

**Missing series handling:** If a (expiry, strike, opt_type) had zero real bars on a given day, the chain slot is filled with `close_x100 = prev_day_close_x100` (or 0 if no prior day), `high_x100 = 0`, `low_x100 = i32::MAX`, `volume = 0`. The server treats `volume = 0` as "untradeable at this minute."

**Spot gaps:** Forward-fill within session from last real close. If the 09:15 bar is missing, backward-fill from the first real bar in the session.

### 4.4 `expiries.json` sidecar

```json
{
  "0": "2024-12-26",
  "1": "2025-01-02",
  "2": "2025-01-09",
  ...
}
```

**Invariant:** indices are **permanent**. Once expiry `"42"` = `"2025-06-26"`, it is `42` forever across all re-runs. Append-only. The migrate binary reads the existing file before writing, appends new expiries in sorted date order, never renumbers.

### 4.5 SQLite manifest

File: `/data/intraday/_manifest.db`

```sql
CREATE TABLE IF NOT EXISTS imports (
    symbol       TEXT    NOT NULL,
    trade_date   TEXT    NOT NULL,   -- ISO8601: YYYY-MM-DD
    sha256       TEXT    NOT NULL,   -- SHA-256 of the .arrow snapshot file
    row_count    INTEGER NOT NULL,   -- real (non-padded) option bars
    ingested_at  INTEGER NOT NULL,   -- Unix timestamp (seconds)
    PRIMARY KEY (symbol, trade_date)
);
```

**Idempotency logic:**
- `SELECT sha256 WHERE (symbol, trade_date)` before processing.
- If row exists and `sha256` matches source data hash → skip.
- If row exists and `sha256` differs → re-process (data was corrected), replace atomically.
- If no row → process and insert.

---

## 5. Migration binary

### 5.1 Location and build

New `[[bin]]` entry in `backend/intraday_server/Cargo.toml`:

```toml
[[bin]]
name = "migrate"
path = "src/bin/migrate.rs"
```

Shares the existing crate (re-uses `Snapshot`, `AppError`, `engine::types`). No new crate needed.

### 5.2 New dependencies (dev/binary only)

```toml
[dependencies]
# Add to existing list:
rayon      = "1"
csv        = "1"
parquet    = "52"
clap       = { version = "4", features = ["derive"] }
indicatif  = "0.17"
rusqlite   = { version = "0.31", features = ["bundled"] }
sha2       = "0.10"
```

### 5.3 CLI interface

```
migrate \
  --options-dir  "/run/user/1000/gvfs/.../INDICES-OPTION/NIFTY" \
  --spot-file    "/run/user/1000/gvfs/.../NIFTY 50.csv" \
  --data-dir     /data/intraday \
  --symbol       NIFTY \
  --year         2025 \
  [--workers     N]        # default: num_cpus
  [--dry-run]              # parse + validate, no writes
  [--force]                # re-ingest even if SHA matches (e.g., after schema change)
  [--format      clean_2024]  # default; "legacy_2017" in future phase
```

### 5.4 Algorithm

```
Stage 1 — Discover option files (fast, serial)
  1. Glob all *.csv under --options-dir
  2. Pre-filter by expiry year extracted from filename:
       skip if expiry_year_2digit < (target_year % 100)
       (e.g., skip YY=24 when importing 2025)
  3. Result: ~14,037 candidate files for NIFTY 2025

Stage 2 — Parallel CSV scan (rayon, all cores)
  For each candidate file (in parallel):
    a. Open CSV reader (csv crate, no header skip needed — header detected)
    b. For each row:
       - Skip if Date not in target year
       - Skip if Padding Flag = 1
       - Parse per-row: trade_date, ts_min, expiry_date (from `Expiry Date` col),
                ohlcv ×100, volume, oi
       - Parse once per file (constant): strike, opt_type from filename
                (e.g. `NIFTY31DEC2616000CE.csv` → strike=16000, opt_type=CE)
                Regex: `[A-Z]+\d{2}[A-Z]{3}\d{2}(\d+)(CE|PE)\.csv$`
       - Accumulate into thread-local Vec<BarRow>
  Merge thread-local vecs into global Vec<BarRow>
  Sort global vec by (trade_date, expiry_date, strike_x100, opt_type, ts_min)

Stage 3 — Load spot data (single-threaded, fast — 1M rows)
  Read --spot-file, filter to target year
  Build HashMap<NaiveDate, Vec<SpotBar>> sorted by ts_min

Stage 4 — Build expiry index
  Collect all unique expiry_dates from Stage 2 result
  Load existing expiries.json (or start empty)
  Append new expiry dates in sorted order, assign next available indices
  Write updated expiries.json

Stage 5 — Serial write per trading date (HDD-friendly: sequential writes)
  Group Stage 2 result by trade_date (already sorted → linear scan)
  For each trading_date (progress bar, ETA):
    a. Check manifest: skip if sha unchanged (--force bypasses this)
    b. Validate OHLCV invariants for all rows of this date
       → on any violation: log error, skip date, continue to next
    c. Write options Parquet:
       - Build Arrow RecordBatch from rows
       - Write to {data_dir}/{symbol}/options/year=YYYY/month=MM/{date}.parquet.tmp
       - fsync → rename (atomic publish)
    d. Build DaySnapshot:
       - Get spot bars for this date from Stage 3
       - Pick 4 active expiries (nearest post-date expiries)
       - Compute ATM at 09:15 from spot close
       - Build ATM±5 chain for each expiry (forward-fill missing series)
       - Pack binary per snapshot.rs layout
       - Compute SHA-256 of packed bytes
       - Write to {data_dir}/{symbol}/snapshots/{date}.arrow.tmp
       - fsync → rename
    e. Update SQLite manifest (INSERT OR REPLACE)

Stage 6 — Write spot Parquet
  Build {data_dir}/{symbol}/spot/{symbol}-spot-{year}.parquet
  (Includes padded rows — DaySnapshot builder needs continuous series)

Stage 7 — Print summary report
  Dates processed:  XXX
  Dates skipped:    XXX  (manifest hit, SHA unchanged)
  Dates failed:     XXX  (list each with error)
  Rows ingested:    XXX,XXX,XXX  (real bars only)
  Bytes written:    XXX MB Parquet + XXX MB snapshots
  Elapsed:          XXX s
```

### 5.5 Memory budget

- Stage 2 peak: all 2025 real NIFTY bars in memory simultaneously.
  - Estimate: 250 days × ~60K real bars/day = 15M rows × ~64 bytes/row = ~960 MB.
  - Well within the 16 GB hardware target (Axum server is not running during migration).
- If memory exceeds 1.5 GB (configurable constant), emit a warning and process in two year-half passes automatically.

### 5.6 Error handling policy

| Error type | Action |
|---|---|
| One CSV file unreadable | Log warning, skip file, continue |
| OHLCV invariant violation | Log error with date + row, skip entire date |
| Missing spot data for date | Log warning, build snapshot with zero-spot (ATM = 0, chain empty) |
| Parquet write failure | Log error, skip date, do not update manifest |
| Snapshot write failure | Log error, skip date, do not update manifest |
| SQLite manifest write failure | Fatal — abort run (manifest is source of truth for idempotency) |

All non-fatal failures are collected; summary report prints the complete failed-date list at the end so a targeted re-run is possible.

---

## 6. Source file pre-filtering logic

Expiry year is encoded in the filename as a 2-digit suffix (e.g., `NIFTY...26...CE.csv` → expiry year 2026).

A contract file has rows from when it was **listed** through its **expiry date**. To filter files that could contain 2025 trade-date rows:

```
skip if expiry_year < target_year
```

Example for target_year = 2025:
- `YY = 24` (expires 2024) → all rows are pre-2025 → **skip**
- `YY = 25` (expires 2025) → has 2023/2024/2025 trade rows → **scan**
- `YY = 26+` → may have 2025 rows if listed before 2025 → **scan**

This eliminates ~4,298 NIFTY files (expiry year 24) from the scan, reducing candidate files from 18,335 to ~14,037.

---

## 7. Validation rules

Applied per-date before any writes. A single violation rejects the entire date.

| Rule | Check |
|---|---|
| OHLCV invariant | `high >= open`, `high >= close`, `low <= open`, `low <= close`, `high >= low` |
| Non-negative prices | All price columns ≥ 0 |
| Non-negative volume/OI | `volume >= 0`, `oi >= 0` |
| Valid ts_min | 555 ≤ ts_min ≤ 930 |
| Valid expiry | `expiry_date > trade_date` |
| Strike positive | `strike > 0` |
| Strike alignment | `strike % step == 0` where step = 50 for NIFTY |

---

## 8. Atomic write protocol

All output files follow this sequence:

```
1. Write to {path}.tmp  (in same directory as final path)
2. fsync the file descriptor
3. rename({path}.tmp → {path})   ← atomic on Linux (same filesystem)
4. Update SQLite manifest
```

If the process is killed between steps 1–3, the `.tmp` file is left on disk. On next run, the manifest has no entry for this date → the date is re-processed from scratch → the `.tmp` is overwritten.

---

## 9. Future phases

### Phase 2: All 4 symbols (same year)

Re-run with `--symbol BANKNIFTY/FINNIFTY/MIDCPNIFTY`. Schema is identical. Strike steps differ (see §4.3).

### Phase 3: 2017–2023 legacy data

The legacy format has a different column layout. Add `--format legacy_2017` handler in `src/bin/migrate/format_legacy.rs`. The output schema and write pipeline are unchanged — only the CSV parser differs. The idempotency mechanism handles re-runs safely.

### Phase 4: Incremental daily ingest

Once production, new daily files will arrive. A `--date` flag (single day) and a watch-mode (`--watch DIR`) can be added without redesigning the pipeline.

---

## 10. Files created / modified

| Path | Action |
|---|---|
| `backend/intraday_server/src/bin/migrate.rs` | Create — main binary entry point |
| `backend/intraday_server/src/bin/migrate/` | Create — submodules |
| `backend/intraday_server/src/bin/migrate/csv_reader.rs` | Create — CSV parse + filter |
| `backend/intraday_server/src/bin/migrate/parquet_writer.rs` | Create — Parquet write |
| `backend/intraday_server/src/bin/migrate/snapshot_builder.rs` | Create — DaySnapshot pack |
| `backend/intraday_server/src/bin/migrate/manifest.rs` | Create — SQLite ops |
| `backend/intraday_server/src/bin/migrate/expiry_index.rs` | Create — expiries.json R/W |
| `backend/intraday_server/Cargo.toml` | Modify — add rayon, csv, parquet, clap, indicatif, rusqlite, sha2 |
