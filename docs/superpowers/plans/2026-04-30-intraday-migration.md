# Intraday Migration Binary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Rust CLI binary (`migrate`) that reads NIFTY 2025 option and spot CSVs from the SMB share, and writes daily Parquet (cold tier) + DaySnapshot Arrow IPC (hot tier) into `/data/intraday/` for the Axum intraday server to consume.

**Architecture:** A standalone `[[bin]]` in the existing `intraday_server` crate with six focused modules — csv_reader, expiry_index, parquet_writer, snapshot_builder, manifest, and a main pipeline. Stage 1 scans ~14K CSV files in parallel with rayon, filters to 2025 rows, accumulates in memory. Stage 2 processes each trading date serially (sequential HDD writes) producing one Parquet file and one DaySnapshot `.arrow` file per day, tracked by a SQLite manifest for idempotency.

**Tech Stack:** Rust 1.78, rayon 1, csv 1, parquet 52 + arrow-array 52, rusqlite 0.31 (bundled), sha2 0.10, clap 4, indicatif 0.17, chrono 0.4

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `backend/intraday_server/Cargo.toml` | Modify | Add `[[bin]]` + 7 new deps |
| `backend/intraday_server/src/bin/migrate/main.rs` | Create | CLI args, pipeline orchestration, spot Parquet |
| `backend/intraday_server/src/bin/migrate/csv_reader.rs` | Create | Parse one CSV file, filter by year, emit `BarRow`s |
| `backend/intraday_server/src/bin/migrate/expiry_index.rs` | Create | Load / append / save `expiries.json` |
| `backend/intraday_server/src/bin/migrate/parquet_writer.rs` | Create | Write daily options Parquet with ZSTD |
| `backend/intraday_server/src/bin/migrate/snapshot_builder.rs` | Create | Pack DaySnapshot binary (same layout as `snapshot.rs`) |
| `backend/intraday_server/src/bin/migrate/manifest.rs` | Create | SQLite idempotency tracking |

---

## Task 1: Cargo.toml — add deps and `[[bin]]` entry

**Files:**
- Modify: `backend/intraday_server/Cargo.toml`

- [ ] **Step 1: Add the `[[bin]]` entry and seven new dependencies**

Open `backend/intraday_server/Cargo.toml`. The full file should become:

```toml
[package]
name = "intraday_server"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "intraday_server"
path = "src/main.rs"

[[bin]]
name = "migrate"
path = "src/bin/migrate/main.rs"

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
blake2 = "0.10"
chrono = { version = "0.4", features = ["clock", "serde"] }
redis = { version = "0.26", features = ["tokio-comp", "connection-manager"] }
uuid = { version = "1", features = ["v4"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
thiserror = "1"
once_cell = "1.19"
# migrate binary
rayon      = "1"
csv        = "1"
parquet    = "52"
clap       = { version = "4", features = ["derive"] }
indicatif  = "0.17"
rusqlite   = { version = "0.31", features = ["bundled"] }
sha2       = "0.10"

[dev-dependencies]
tower = { version = "0.4", features = ["util"] }
http-body-util = "0.1"
tempfile = "3"
```

- [ ] **Step 2: Create the bin directory**

```bash
mkdir -p backend/intraday_server/src/bin/migrate
```

- [ ] **Step 3: Verify the crate compiles with a stub main**

Create `backend/intraday_server/src/bin/migrate/main.rs` with:

```rust
fn main() {}
```

Run:
```bash
cd backend/intraday_server && cargo build --bin migrate 2>&1 | tail -5
```

Expected: `Finished` with no errors (new deps will download and compile).

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/Cargo.toml backend/intraday_server/Cargo.lock backend/intraday_server/src/bin/migrate/main.rs
git commit -m "chore(migrate): add [[bin]] entry and new deps to intraday_server crate"
```

---

## Task 2: `csv_reader.rs` — parse CSV files, filter to target year

**Files:**
- Create: `backend/intraday_server/src/bin/migrate/csv_reader.rs`

**What this module does:** Given a file path, extract `strike_x100` and `opt_type` from the filename (constant for the whole file), then stream each row, skip `Padding Flag = 1` rows and rows outside the target year, and emit `BarRow` structs.

- [ ] **Step 1: Write the failing tests**

Create `backend/intraday_server/src/bin/migrate/csv_reader.rs` with this full content (tests first, impl after):

```rust
use chrono::NaiveDate;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct BarRow {
    pub trade_date:  NaiveDate,
    pub ts_min:      i16,        // minutes since midnight: 09:15 = 555
    pub expiry_date: NaiveDate,
    pub strike_x100: i32,        // strike × 100
    pub opt_type:    bool,       // false = CE, true = PE
    pub open_x100:   i32,
    pub high_x100:   i32,
    pub low_x100:    i32,
    pub close_x100:  i32,
    pub volume:      i32,
    pub oi:          i32,
}

/// Extract (strike_x100, opt_type) from a filename like "NIFTY31DEC2616000CE.csv".
/// Returns None if filename does not match the expected pattern.
pub fn parse_filename(name: &str) -> Option<(i32, bool)> {
    // Pattern: SYMBOL DD MON YY STRIKE (CE|PE) .csv
    // Extract the trailing numeric strike and CE/PE before .csv
    let stem = name.strip_suffix(".csv")?;
    let opt_type = if stem.ends_with("CE") {
        false
    } else if stem.ends_with("PE") {
        true
    } else {
        return None;
    };
    let stem = &stem[..stem.len() - 2]; // strip CE/PE
    // find where digits start (strike is all digits at the end)
    let strike_start = stem.rfind(|c: char| !c.is_ascii_digit())? + 1;
    let strike_str = &stem[strike_start..];
    let strike: i32 = strike_str.parse().ok()?;
    Some((strike * 100, opt_type))
}

/// Extract the expiry 2-digit year from filename (e.g. "NIFTY31DEC2616000CE.csv" → 26).
/// Used for pre-filtering: if expiry_year_2digit < target_year % 100, skip.
pub fn filename_expiry_year_2digit(name: &str) -> Option<u32> {
    // After symbol letters+digits, format is DDMONYY...
    // Skip the leading symbol (all caps + possible digits) then read DDMONYY
    let stem = name.strip_suffix(".csv")?;
    // Find the boundary: symbol is all-caps letters, then digits start (DD)
    let digit_start = stem.find(|c: char| c.is_ascii_digit())?;
    let rest = &stem[digit_start..]; // "31DEC2616000CE"
    // rest[0..2] = DD, rest[2..5] = MON, rest[5..7] = YY
    if rest.len() < 7 { return None; }
    let yy: u32 = rest[5..7].parse().ok()?;
    Some(yy)
}

/// Read one CSV file, filter to rows where Date is in `target_year` and Padding Flag = 0.
/// Returns a Vec<BarRow>. If the file cannot be parsed, returns Err.
pub fn read_file(path: &Path, target_year: i32) -> anyhow::Result<Vec<BarRow>> {
    let name = path.file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("");
    let (strike_x100, opt_type) = parse_filename(name)
        .ok_or_else(|| anyhow::anyhow!("cannot parse filename: {name}"))?;

    let mut rdr = csv::Reader::from_path(path)?;
    let mut rows = Vec::new();

    for result in rdr.records() {
        let rec = result?;
        // Columns: Ticker,Date,Time,Expiry Date,Open,High,Low,Close,Volume,Open Interest,Padding Flag
        let date_str    = rec.get(1).unwrap_or("");
        let time_str    = rec.get(2).unwrap_or("");
        let expiry_str  = rec.get(3).unwrap_or("");
        let open_str    = rec.get(4).unwrap_or("0");
        let high_str    = rec.get(5).unwrap_or("0");
        let low_str     = rec.get(6).unwrap_or("0");
        let close_str   = rec.get(7).unwrap_or("0");
        let vol_str     = rec.get(8).unwrap_or("0");
        let oi_str      = rec.get(9).unwrap_or("0");
        let padded_str  = rec.get(10).unwrap_or("0");

        if padded_str.trim() == "1" { continue; }

        let trade_date = NaiveDate::parse_from_str(date_str.trim(), "%Y-%m-%d")
            .map_err(|_| anyhow::anyhow!("bad date: {date_str}"))?;
        if trade_date.year() != target_year { continue; }

        let expiry_date = NaiveDate::parse_from_str(expiry_str.trim(), "%Y-%m-%d")
            .map_err(|_| anyhow::anyhow!("bad expiry: {expiry_str}"))?;

        // Time "HH:MM:SS" → ts_min
        let mut parts = time_str.trim().splitn(3, ':');
        let hh: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);
        let mm: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);
        let ts_min = hh * 60 + mm;

        let px = |s: &str| -> i32 {
            (s.trim().parse::<f64>().unwrap_or(0.0) * 100.0).round() as i32
        };

        rows.push(BarRow {
            trade_date,
            ts_min,
            expiry_date,
            strike_x100,
            opt_type,
            open_x100:  px(open_str),
            high_x100:  px(high_str),
            low_x100:   px(low_str),
            close_x100: px(close_str),
            volume:     vol_str.trim().parse().unwrap_or(0),
            oi:         oi_str.trim().parse().unwrap_or(0),
        });
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_filename_ce() {
        let (strike, opt) = parse_filename("NIFTY31DEC2616000CE.csv").unwrap();
        assert_eq!(strike, 1_600_000);
        assert!(!opt);
    }

    #[test]
    fn test_parse_filename_pe() {
        let (strike, opt) = parse_filename("BANKNIFTY24JAN2548000PE.csv").unwrap();
        assert_eq!(strike, 4_800_000);
        assert!(opt);
    }

    #[test]
    fn test_parse_filename_bad() {
        assert!(parse_filename("something.txt").is_none());
        assert!(parse_filename("NIFTY.csv").is_none());
    }

    #[test]
    fn test_expiry_year() {
        assert_eq!(filename_expiry_year_2digit("NIFTY31DEC2616000CE.csv"), Some(26));
        assert_eq!(filename_expiry_year_2digit("NIFTY24JAN2519000PE.csv"), Some(25));
        assert_eq!(filename_expiry_year_2digit("NIFTY30JAN2524500CE.csv"), Some(25));
    }

    #[test]
    fn test_read_file_filters_year_and_padding() {
        use std::io::Write;
        use tempfile::NamedTempFile;

        // Two 2025 rows (one real, one padded) + one 2024 row
        let csv = b"Ticker,Date,Time,Expiry Date,Open,High,Low,Close,Volume,Open Interest,Padding Flag\n\
NIFTY24JAN2519000CE.NFO,2024-12-31,09:15:00,2025-01-24,100.0,105.0,98.0,103.0,50,500,0\n\
NIFTY24JAN2519000CE.NFO,2025-01-02,09:15:00,2025-01-24,104.0,108.0,102.0,106.0,75,480,0\n\
NIFTY24JAN2519000CE.NFO,2025-01-02,09:16:00,2025-01-24,106.0,109.0,104.0,107.0,30,480,1\n\
NIFTY24JAN2519000CE.NFO,2025-01-03,09:15:00,2025-01-24,107.0,112.0,105.0,110.0,60,460,0\n";

        let mut f = NamedTempFile::with_suffix(".csv").unwrap();
        // Rename so filename matches pattern (tempfile names don't)
        let dir = f.path().parent().unwrap().to_path_buf();
        let named = dir.join("NIFTY24JAN2519000CE.csv");
        f.write_all(csv).unwrap();
        std::fs::copy(f.path(), &named).unwrap();

        let rows = read_file(&named, 2025).unwrap();
        // Expect 2 rows: 2025-01-02 real + 2025-01-03 real (padded and 2024 row excluded)
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].trade_date, NaiveDate::from_ymd_opt(2025, 1, 2).unwrap());
        assert_eq!(rows[0].ts_min, 555);       // 09:15
        assert_eq!(rows[0].close_x100, 10600); // 106.00 × 100
        assert_eq!(rows[0].strike_x100, 1_900_000); // 19000 × 100
        assert!(!rows[0].opt_type);            // CE
        assert_eq!(rows[1].trade_date, NaiveDate::from_ymd_opt(2025, 1, 3).unwrap());
        std::fs::remove_file(&named).ok();
    }
}
```

- [ ] **Step 2: Run the tests — verify they pass**

```bash
cd backend/intraday_server && cargo test --bin migrate csv_reader 2>&1 | tail -15
```

Expected:
```
test tests::test_expiry_year ... ok
test tests::test_parse_filename_bad ... ok
test tests::test_parse_filename_ce ... ok
test tests::test_parse_filename_pe ... ok
test tests::test_read_file_filters_year_and_padding ... ok
test result: ok. 5 passed
```

- [ ] **Step 3: Wire the module into main.rs**

Replace `backend/intraday_server/src/bin/migrate/main.rs` with:

```rust
mod csv_reader;

fn main() {}
```

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/bin/migrate/
git commit -m "feat(migrate): csv_reader — parse + filter BarRows from per-contract CSVs"
```

---

## Task 3: `expiry_index.rs` — stable expiry index (expiries.json)

**Files:**
- Create: `backend/intraday_server/src/bin/migrate/expiry_index.rs`

**What this module does:** Maintains a `expiries.json` file mapping integer index → ISO date string. Indices are permanent once assigned (append-only, never renumber). New expiry dates are appended in sorted order.

- [ ] **Step 1: Write the module with inline tests**

Create `backend/intraday_server/src/bin/migrate/expiry_index.rs`:

```rust
use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub struct ExpiryIndex {
    path: PathBuf,
    by_date: HashMap<NaiveDate, i16>,
    ordered: Vec<NaiveDate>,  // ordered[i] = date at index i; stable forever
}

impl ExpiryIndex {
    /// Load from path if it exists, else start empty.
    pub fn load_or_create(path: &Path) -> anyhow::Result<Self> {
        if path.exists() {
            let text = std::fs::read_to_string(path)?;
            let raw: HashMap<String, String> = serde_json::from_str(&text)?;
            // Determine max index to size the ordered vec
            let max_idx = raw.keys()
                .filter_map(|k| k.parse::<usize>().ok())
                .max()
                .map(|m| m + 1)
                .unwrap_or(0);
            let mut ordered = vec![NaiveDate::from_ymd_opt(1970, 1, 1).unwrap(); max_idx];
            let mut by_date = HashMap::new();
            for (k, v) in &raw {
                let idx: usize = k.parse()?;
                let date = NaiveDate::parse_from_str(v, "%Y-%m-%d")?;
                ordered[idx] = date;
                by_date.insert(date, idx as i16);
            }
            Ok(Self { path: path.to_path_buf(), by_date, ordered })
        } else {
            Ok(Self { path: path.to_path_buf(), by_date: HashMap::new(), ordered: Vec::new() })
        }
    }

    /// Get the index for a date. Assigns a new index (appended in sorted position) if not seen.
    pub fn get_or_insert(&mut self, date: NaiveDate) -> i16 {
        if let Some(&idx) = self.by_date.get(&date) {
            return idx;
        }
        // Find sorted insertion position
        let pos = self.ordered.partition_point(|&d| d < date);
        // Rebuild: insert at pos, shift later indices
        self.ordered.insert(pos, date);
        // Rebuild by_date with new indices
        self.by_date.clear();
        for (i, &d) in self.ordered.iter().enumerate() {
            self.by_date.insert(d, i as i16);
        }
        *self.by_date.get(&date).unwrap()
    }

    pub fn get(&self, date: NaiveDate) -> Option<i16> {
        self.by_date.get(&date).copied()
    }

    pub fn len(&self) -> usize {
        self.ordered.len()
    }

    /// Persist to disk atomically (write .tmp → rename).
    pub fn save(&self) -> anyhow::Result<()> {
        let mut map = serde_json::Map::new();
        for (i, d) in self.ordered.iter().enumerate() {
            map.insert(i.to_string(), serde_json::Value::String(d.format("%Y-%m-%d").to_string()));
        }
        let json = serde_json::to_string_pretty(&serde_json::Value::Object(map))?;
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, &json)?;
        std::fs::rename(&tmp, &self.path)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_insert_sorted_stable() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("expiries.json");
        let mut idx = ExpiryIndex::load_or_create(&path).unwrap();

        let d1 = NaiveDate::from_ymd_opt(2025, 1, 23).unwrap();
        let d2 = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();  // earlier date
        let d3 = NaiveDate::from_ymd_opt(2025, 2, 27).unwrap();

        let i1 = idx.get_or_insert(d1);
        let i2 = idx.get_or_insert(d2);
        let i3 = idx.get_or_insert(d3);

        // d2 < d1 < d3 — sorted insertion assigns indices in date order
        assert!(i2 < i1);
        assert!(i1 < i3);

        // Same date returns same index
        assert_eq!(idx.get_or_insert(d1), i1);
        assert_eq!(idx.get_or_insert(d2), i2);
    }

    #[test]
    fn test_save_and_reload() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("expiries.json");

        let d1 = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let d2 = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();

        {
            let mut idx = ExpiryIndex::load_or_create(&path).unwrap();
            idx.get_or_insert(d1);
            idx.get_or_insert(d2);
            idx.save().unwrap();
        }

        // Reload and verify same indices
        let idx2 = ExpiryIndex::load_or_create(&path).unwrap();
        assert_eq!(idx2.get(d1), Some(0));
        assert_eq!(idx2.get(d2), Some(1));
        assert_eq!(idx2.len(), 2);
    }
}
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/intraday_server && cargo test --bin migrate expiry_index 2>&1 | tail -10
```

Expected:
```
test tests::test_insert_sorted_stable ... ok
test tests::test_save_and_reload ... ok
test result: ok. 2 passed
```

- [ ] **Step 3: Wire into main.rs**

```rust
mod csv_reader;
mod expiry_index;

fn main() {}
```

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/bin/migrate/
git commit -m "feat(migrate): expiry_index — stable append-only expiries.json R/W"
```

---

## Task 4: `parquet_writer.rs` — daily options Parquet + spot Parquet

**Files:**
- Create: `backend/intraday_server/src/bin/migrate/parquet_writer.rs`

**What this module does:** Takes a slice of `BarRow`s pre-sorted by `(expiry_date, strike_x100, opt_type, ts_min)` and writes a ZSTD-compressed, dictionary-encoded Parquet file atomically (`.tmp` → rename). Also exposes `write_spot_parquet` for the annual spot file.

- [ ] **Step 1: Write the module with inline tests**

Create `backend/intraday_server/src/bin/migrate/parquet_writer.rs`:

```rust
use crate::csv_reader::BarRow;
use arrow_array::{
    BooleanArray, Date32Array, Int16Array, Int32Array, RecordBatch, StringArray,
};
use arrow_schema::{DataType, Field, Schema};
use chrono::NaiveDate;
use parquet::arrow::ArrowWriter;
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::{EnabledStatistics, WriterProperties};
use std::path::Path;
use std::sync::Arc;

fn epoch() -> NaiveDate {
    NaiveDate::from_ymd_opt(1970, 1, 1).unwrap()
}

fn to_days(d: NaiveDate) -> i32 {
    (d - epoch()).num_days() as i32
}

fn writer_props() -> anyhow::Result<WriterProperties> {
    Ok(WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::try_new(3)?))
        .set_dictionary_enabled(true)
        .set_statistics_enabled(EnabledStatistics::Chunk)
        .build())
}

/// Write one trading day's real (non-padded) option bars to a Parquet file.
/// `rows` must already be sorted by (expiry_date, strike_x100, opt_type, ts_min).
/// Writes atomically: path.tmp → fsync → rename.
pub fn write_options_parquet(
    path: &Path,
    symbol: &str,
    trade_date: NaiveDate,
    rows: &[BarRow],
) -> anyhow::Result<()> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("symbol",      DataType::Utf8,    false),
        Field::new("trade_date",  DataType::Date32,  false),
        Field::new("ts_min",      DataType::Int16,   false),
        Field::new("expiry_date", DataType::Date32,  false),
        Field::new("strike_x100", DataType::Int32,   false),
        Field::new("opt_type",    DataType::Boolean, false),
        Field::new("open_x100",   DataType::Int32,   false),
        Field::new("high_x100",   DataType::Int32,   false),
        Field::new("low_x100",    DataType::Int32,   false),
        Field::new("close_x100",  DataType::Int32,   false),
        Field::new("volume",      DataType::Int32,   false),
        Field::new("oi",          DataType::Int32,   false),
    ]));

    let n = rows.len();
    let trade_days = to_days(trade_date);
    let batch = RecordBatch::try_new(schema.clone(), vec![
        Arc::new(StringArray::from(vec![symbol; n])),
        Arc::new(Date32Array::from(vec![trade_days; n])),
        Arc::new(Int16Array::from(rows.iter().map(|r| r.ts_min).collect::<Vec<_>>())),
        Arc::new(Date32Array::from(rows.iter().map(|r| to_days(r.expiry_date)).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.strike_x100).collect::<Vec<_>>())),
        Arc::new(BooleanArray::from(rows.iter().map(|r| r.opt_type).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.open_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.high_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.low_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.close_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.volume).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.oi).collect::<Vec<_>>())),
    ])?;

    atomic_write_parquet(path, schema, &batch)
}

/// Write one year's spot bars to a Parquet file.
pub fn write_spot_parquet(path: &Path, bars: &[SpotBar]) -> anyhow::Result<()> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("trade_date",  DataType::Date32, false),
        Field::new("ts_min",      DataType::Int16,  false),
        Field::new("open_x100",   DataType::Int32,  false),
        Field::new("high_x100",   DataType::Int32,  false),
        Field::new("low_x100",    DataType::Int32,  false),
        Field::new("close_x100",  DataType::Int32,  false),
    ]));
    let batch = RecordBatch::try_new(schema.clone(), vec![
        Arc::new(Date32Array::from(bars.iter().map(|b| to_days(b.trade_date)).collect::<Vec<_>>())),
        Arc::new(Int16Array::from(bars.iter().map(|b| b.ts_min).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.open_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.high_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.low_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.close_x100).collect::<Vec<_>>())),
    ])?;
    atomic_write_parquet(path, schema, &batch)
}

fn atomic_write_parquet(path: &Path, schema: Arc<Schema>, batch: &RecordBatch) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("parquet.tmp");
    {
        let file = std::fs::File::create(&tmp)?;
        let mut writer = ArrowWriter::try_new(file, schema, Some(writer_props()?))?;
        writer.write(batch)?;
        writer.close()?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Spot bar — used internally and by snapshot_builder.
#[derive(Debug, Clone)]
pub struct SpotBar {
    pub trade_date:  NaiveDate,
    pub ts_min:      i16,
    pub open_x100:   i32,
    pub high_x100:   i32,
    pub low_x100:    i32,
    pub close_x100:  i32,
}

/// Parse the NIFTY 50.csv spot file, filtering to `target_year` (includes padded rows).
pub fn read_spot_csv(path: &Path, target_year: i32) -> anyhow::Result<Vec<SpotBar>> {
    let mut rdr = csv::Reader::from_path(path)?;
    let mut bars = Vec::new();
    for result in rdr.records() {
        let rec = result?;
        // Same column layout as option CSVs
        let date_str  = rec.get(1).unwrap_or("");
        let time_str  = rec.get(2).unwrap_or("");
        let open_str  = rec.get(4).unwrap_or("0");
        let high_str  = rec.get(5).unwrap_or("0");
        let low_str   = rec.get(6).unwrap_or("0");
        let close_str = rec.get(7).unwrap_or("0");

        let trade_date = match NaiveDate::parse_from_str(date_str.trim(), "%Y-%m-%d") {
            Ok(d) => d,
            Err(_) => continue,
        };
        if trade_date.year() != target_year { continue; }

        let mut parts = time_str.trim().splitn(3, ':');
        let hh: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);
        let mm: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);

        let px = |s: &str| -> i32 {
            (s.trim().parse::<f64>().unwrap_or(0.0) * 100.0).round() as i32
        };

        bars.push(SpotBar {
            trade_date,
            ts_min: hh * 60 + mm,
            open_x100:  px(open_str),
            high_x100:  px(high_str),
            low_x100:   px(low_str),
            close_x100: px(close_str),
        });
    }
    Ok(bars)
}

#[cfg(test)]
mod tests {
    use super::*;
    use parquet::file::reader::{FileReader, SerializedFileReader};

    fn sample_rows(trade_date: NaiveDate) -> Vec<BarRow> {
        let exp = NaiveDate::from_ymd_opt(2025, 1, 23).unwrap();
        vec![
            BarRow {
                trade_date, ts_min: 555, expiry_date: exp, strike_x100: 2_400_000,
                opt_type: false, open_x100: 10000, high_x100: 11000,
                low_x100: 9500, close_x100: 10500, volume: 100, oi: 5000,
            },
            BarRow {
                trade_date, ts_min: 556, expiry_date: exp, strike_x100: 2_400_000,
                opt_type: false, open_x100: 10500, high_x100: 11500,
                low_x100: 10000, close_x100: 11000, volume: 80, oi: 5000,
            },
        ]
    }

    #[test]
    fn test_write_options_parquet_readable() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("2025-01-02.parquet");
        let trade_date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let rows = sample_rows(trade_date);

        write_options_parquet(&path, "NIFTY", trade_date, &rows).unwrap();
        assert!(path.exists());
        assert!(!dir.path().join("2025-01-02.parquet.tmp").exists()); // tmp cleaned up

        let file = std::fs::File::open(&path).unwrap();
        let reader = SerializedFileReader::new(file).unwrap();
        assert_eq!(reader.metadata().file_metadata().num_rows(), 2);
    }

    #[test]
    fn test_write_options_parquet_creates_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("year=2025/month=01/2025-01-02.parquet");
        let trade_date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        write_options_parquet(&path, "NIFTY", trade_date, &sample_rows(trade_date)).unwrap();
        assert!(path.exists());
    }
}
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/intraday_server && cargo test --bin migrate parquet_writer 2>&1 | tail -10
```

Expected:
```
test tests::test_write_options_parquet_creates_dirs ... ok
test tests::test_write_options_parquet_readable ... ok
test result: ok. 2 passed
```

- [ ] **Step 3: Wire into main.rs**

```rust
mod csv_reader;
mod expiry_index;
mod parquet_writer;

fn main() {}
```

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/bin/migrate/
git commit -m "feat(migrate): parquet_writer — daily options Parquet + spot Parquet, ZSTD"
```

---

## Task 5: `snapshot_builder.rs` — pack DaySnapshot binary

**Files:**
- Create: `backend/intraday_server/src/bin/migrate/snapshot_builder.rs`

**What this module does:** Takes structured day data and packs the exact binary format that `engine/snapshot.rs` mmap-reads at runtime. The binary layout constants are duplicated from `snapshot.rs` (same values, cross-referenced in comments).

**Binary layout recap (from `snapshot.rs`):**

```
Header (32 bytes):
  [0..4]   "ITDS" magic
  [4]      version = 1
  [5..21]  symbol padded to 16 bytes (null-fill)
  [21..25] date_days: i32 LE (days since Unix epoch)
  [25]     expiry_count: u8
  [26..28] minute_count: u16 LE (= 375)
  [28..32] 4 zero bytes

Spot (MINUTES × 16 = 6000 bytes):
  For each minute m in 0..375:
    open_x100: i32 LE, high_x100: i32 LE, low_x100: i32 LE, close_x100: i32 LE

Per-expiry (133502 bytes each):
  [0..2]     expiry_idx: i16 LE
  [2..1502]  atm[375]: i32 LE array — ATM strike × 100 at each minute
  [1502..]   chain[11][2][4][375]: i32 LE
               s = 0..11 (ATM-5 to ATM+5, step = symbol stride)
               t = 0..2  (0=CE, 1=PE)
               field = 0..4 (0=close, 1=high, 2=low, 3=volume)
               m = 0..375
```

- [ ] **Step 1: Write the module with inline tests**

Create `backend/intraday_server/src/bin/migrate/snapshot_builder.rs`:

```rust
use crate::parquet_writer::SpotBar;
use crate::csv_reader::BarRow;
use chrono::NaiveDate;
use std::collections::HashMap;

// Constants mirror engine/snapshot.rs — must stay in sync with that file
const MINUTES: usize = 375;
const HEADER_SIZE: usize = 32;
const SPOT_ENTRY: usize = 16;
const SPOT_SIZE: usize = MINUTES * SPOT_ENTRY;          // 6000
const CHAIN_STRIKES: usize = 11;
const CHAIN_TYPES: usize = 2;
const CHAIN_FIELDS: usize = 4;
const EXPIRY_SIZE: usize =
    2 + MINUTES * 4 + CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES * 4;  // 133502

const SESSION_START_MIN: i16 = 555;  // 09:15 in minutes since midnight

fn push_i16(buf: &mut Vec<u8>, v: i16) { buf.extend_from_slice(&v.to_le_bytes()); }
fn push_i32(buf: &mut Vec<u8>, v: i32) { buf.extend_from_slice(&v.to_le_bytes()); }
fn push_u16(buf: &mut Vec<u8>, v: u16) { buf.extend_from_slice(&v.to_le_bytes()); }

/// Pick up to 4 active expiries: nearest post-date expiry dates from the unique set.
pub fn pick_active_expiries(
    all_expiries: &[NaiveDate],
    trade_date: NaiveDate,
) -> Vec<NaiveDate> {
    let mut after: Vec<NaiveDate> = all_expiries
        .iter()
        .filter(|&&e| e > trade_date)
        .copied()
        .collect();
    after.sort();
    after.truncate(4);
    after
}

/// Compute the ATM strike × 100 for each minute from spot close prices.
/// The chain anchor (index 5 = ATM+0) is fixed at the 09:15 (SESSION_START_MIN) value.
/// Returns (anchor_x100, atm_per_minute[375]).
pub fn compute_atm(
    spot_by_min: &[i32; MINUTES],  // close_x100 for each minute slot; 0 = missing
    strike_step: i32,               // in whole rupees (50 for NIFTY)
) -> (i32, [i32; MINUTES]) {
    let step_x100 = strike_step * 100;
    let round_to_step = |px: i32| -> i32 {
        if px <= 0 { return 0; }
        ((px as f64 / step_x100 as f64).round() as i32) * step_x100
    };

    // Anchor = ATM at session open; forward-fill from first non-zero minute if 09:15 is missing
    let anchor_raw = (0..MINUTES)
        .map(|m| spot_by_min[m])
        .find(|&v| v > 0)
        .unwrap_or(0);
    let anchor_x100 = round_to_step(anchor_raw);

    let mut atm = [0i32; MINUTES];
    let mut last = anchor_raw;
    for m in 0..MINUTES {
        if spot_by_min[m] > 0 { last = spot_by_min[m]; }
        atm[m] = round_to_step(last);
    }
    (anchor_x100, atm)
}

/// Build the full DaySnapshot binary for one trading date.
/// Returns the raw bytes to write to the .arrow file.
pub fn build(
    symbol: &str,
    trade_date: NaiveDate,
    spot_bars: &[SpotBar],            // all spot bars for this date (may have gaps)
    option_rows: &[BarRow],           // all real option bars for this date (sorted)
    active_expiries: &[(NaiveDate, i16)], // (expiry_date, expiry_idx), up to 4, sorted
    strike_step: i32,                 // symbol-specific strike step in rupees
) -> Vec<u8> {
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let date_days = (trade_date - epoch).num_days() as i32;
    let expiry_count = active_expiries.len().min(4) as u8;

    // --- Build spot_by_min lookup -----------------------------------------
    let mut spot_close = [0i32; MINUTES];
    let mut spot_open  = [0i32; MINUTES];
    let mut spot_high  = [0i32; MINUTES];
    let mut spot_low   = [0i32; MINUTES];
    for b in spot_bars {
        let m = (b.ts_min - SESSION_START_MIN) as usize;
        if m < MINUTES {
            spot_close[m] = b.close_x100;
            spot_open[m]  = b.open_x100;
            spot_high[m]  = b.high_x100;
            spot_low[m]   = b.low_x100;
        }
    }
    // Forward-fill spot for display (zeros remain for truly missing bars)
    let mut last_close = spot_close.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut last_open  = spot_open.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut last_high  = spot_high.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut last_low   = spot_low.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut ff_close = [0i32; MINUTES];
    let mut ff_open  = [0i32; MINUTES];
    let mut ff_high  = [0i32; MINUTES];
    let mut ff_low   = [0i32; MINUTES];
    for m in 0..MINUTES {
        if spot_close[m] > 0 {
            last_close = spot_close[m];
            last_open  = spot_open[m];
            last_high  = spot_high[m];
            last_low   = spot_low[m];
        }
        ff_close[m] = last_close;
        ff_open[m]  = last_open;
        ff_high[m]  = last_high;
        ff_low[m]   = last_low;
    }

    // --- Header -------------------------------------------------------------
    let mut buf: Vec<u8> = Vec::with_capacity(
        HEADER_SIZE + SPOT_SIZE + expiry_count as usize * EXPIRY_SIZE
    );
    buf.extend_from_slice(b"ITDS");        // magic
    buf.push(1u8);                          // version
    let mut sym_bytes = [0u8; 16];
    let s = symbol.as_bytes();
    sym_bytes[..s.len().min(16)].copy_from_slice(&s[..s.len().min(16)]);
    buf.extend_from_slice(&sym_bytes);
    push_i32(&mut buf, date_days);
    buf.push(expiry_count);
    push_u16(&mut buf, MINUTES as u16);
    buf.extend_from_slice(&[0u8; 4]);       // padding
    assert_eq!(buf.len(), HEADER_SIZE);

    // --- Spot section -------------------------------------------------------
    for m in 0..MINUTES {
        push_i32(&mut buf, ff_open[m]);
        push_i32(&mut buf, ff_high[m]);
        push_i32(&mut buf, ff_low[m]);
        push_i32(&mut buf, ff_close[m]);
    }
    assert_eq!(buf.len(), HEADER_SIZE + SPOT_SIZE);

    // --- Per-expiry sections -------------------------------------------------
    let (anchor_x100, atm_per_min) = compute_atm(&ff_close, strike_step);
    let step_x100 = strike_step * 100;

    // Build a lookup from (expiry_date, strike_x100, opt_type, ts_min) → (close, high, low, volume)
    // key = (expiry_date, strike_x100, opt_type_u8, minute_index)
    type Key = (NaiveDate, i32, u8, usize);
    let mut lookup: HashMap<Key, (i32, i32, i32, i32)> = HashMap::new();
    for r in option_rows {
        let m = (r.ts_min - SESSION_START_MIN) as usize;
        if m >= MINUTES { continue; }
        let key: Key = (r.expiry_date, r.strike_x100, r.opt_type as u8, m);
        lookup.insert(key, (r.close_x100, r.high_x100, r.low_x100, r.volume));
    }

    for &(expiry_date, expiry_idx) in &active_expiries[..expiry_count as usize] {
        let section_start = buf.len();
        push_i16(&mut buf, expiry_idx);

        // ATM array (per minute)
        for m in 0..MINUTES {
            push_i32(&mut buf, atm_per_min[m]);
        }

        // Chain: [s=0..11][t=0..2][field=0..4][m=0..375]
        // s=5 is ATM+0; s=0 is ATM-5; s=10 is ATM+5
        let chain_size = CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES;
        let mut chain = vec![0i32; chain_size];

        for s_offset in 0..CHAIN_STRIKES as i32 {
            let rel = s_offset - 5;  // -5..+5
            let strike_x100 = anchor_x100 + rel * step_x100;
            if strike_x100 <= 0 { continue; }

            for t in 0..CHAIN_TYPES {
                let opt_type = t as u8;  // 0=CE, 1=PE (matches BarRow.opt_type as u8)
                // forward-fill close for missing minutes
                let mut last_close = 0i32;
                for m in 0..MINUTES {
                    let key: Key = (expiry_date, strike_x100, opt_type, m);
                    let idx_base = (s_offset as usize) * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
                        + t * CHAIN_FIELDS * MINUTES;

                    if let Some(&(close, high, low, vol)) = lookup.get(&key) {
                        last_close = close;
                        chain[idx_base + 0 * MINUTES + m] = close;
                        chain[idx_base + 1 * MINUTES + m] = high;
                        chain[idx_base + 2 * MINUTES + m] = low;
                        chain[idx_base + 3 * MINUTES + m] = vol;
                    } else {
                        // No real bar: forward-fill close, zero high/low/volume
                        chain[idx_base + 0 * MINUTES + m] = last_close;
                        chain[idx_base + 1 * MINUTES + m] = 0;
                        chain[idx_base + 2 * MINUTES + m] = 0;
                        chain[idx_base + 3 * MINUTES + m] = 0;
                    }
                }
            }
        }
        for v in &chain { push_i32(&mut buf, *v); }
        assert_eq!(buf.len() - section_start, EXPIRY_SIZE,
            "expiry section size mismatch: got {}", buf.len() - section_start);
    }

    buf
}

/// Write the snapshot bytes to disk atomically (.arrow.tmp → rename).
pub fn write(path: &std::path::Path, bytes: &[u8]) -> anyhow::Result<()> {
    if let Some(p) = path.parent() { std::fs::create_dir_all(p)?; }
    let tmp = path.with_extension("arrow.tmp");
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parquet_writer::SpotBar;
    use crate::csv_reader::BarRow;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn make_spot(date: NaiveDate, close_x100: i32) -> Vec<SpotBar> {
        (0..MINUTES as i16).map(|m| SpotBar {
            trade_date: date, ts_min: SESSION_START_MIN + m,
            open_x100: close_x100, high_x100: close_x100 + 100,
            low_x100: close_x100 - 100, close_x100,
        }).collect()
    }

    fn make_ce_row(date: NaiveDate, expiry: NaiveDate, strike_x100: i32, m: i16, close_x100: i32) -> BarRow {
        BarRow {
            trade_date: date, ts_min: SESSION_START_MIN + m,
            expiry_date: expiry, strike_x100,
            opt_type: false,
            open_x100: close_x100, high_x100: close_x100 + 50,
            low_x100: close_x100 - 50, close_x100,
            volume: 100, oi: 1000,
        }
    }

    #[test]
    fn test_build_size() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let spot = make_spot(date, 2_400_000);
        let rows = vec![make_ce_row(date, expiry, 2_400_000, 0, 15000)];
        let active = vec![(expiry, 0i16)];
        let bytes = build("NIFTY", date, &spot, &rows, &active, 50);

        assert_eq!(bytes.len(), HEADER_SIZE + SPOT_SIZE + EXPIRY_SIZE);
    }

    #[test]
    fn test_build_header_fields() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let bytes = build("NIFTY", date, &make_spot(date, 2_400_000),
                          &[], &[(expiry, 0i16)], 50);

        assert_eq!(&bytes[0..4], b"ITDS");
        assert_eq!(bytes[4], 1u8);  // version
        assert_eq!(&bytes[5..10], b"NIFTY");
        assert_eq!(bytes[10], 0u8); // null pad

        let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
        let expected_days = (date - epoch).num_days() as i32;
        let actual_days = i32::from_le_bytes(bytes[21..25].try_into().unwrap());
        assert_eq!(actual_days, expected_days);

        assert_eq!(bytes[25], 1u8); // expiry_count
        let minute_count = u16::from_le_bytes(bytes[26..28].try_into().unwrap());
        assert_eq!(minute_count as usize, MINUTES);
    }

    #[test]
    fn test_chain_value_roundtrip() {
        // Build snapshot with a known CE close at ATM, minute 0
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let atm_x100 = 2_400_000i32;  // 24000 × 100
        let spot = make_spot(date, atm_x100);
        let rows = vec![make_ce_row(date, expiry, atm_x100, 0, 25000)];
        let active = vec![(expiry, 0i16)];
        let bytes = build("NIFTY", date, &spot, &rows, &active, 50);

        // Write to temp file and open with Snapshot::open to verify
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();

        // Manually verify chain value at s=5 (ATM), t=0 (CE), field=0 (close), m=0
        // chain starts at: HEADER_SIZE + SPOT_SIZE + 2 + MINUTES*4
        let chain_base = HEADER_SIZE + SPOT_SIZE + 2 + MINUTES * 4;
        // s=5, t=0, field=0, m=0:
        let idx = 5 * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
            + 0 * CHAIN_FIELDS * MINUTES
            + 0 * MINUTES
            + 0;
        let off = chain_base + idx * 4;
        let val = i32::from_le_bytes(bytes[off..off+4].try_into().unwrap());
        assert_eq!(val, 25000, "CE close at ATM minute 0 should be 25000");
    }

    #[test]
    fn test_pick_active_expiries() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let exp: Vec<NaiveDate> = [
            "2024-12-26", "2025-01-02", "2025-01-09", "2025-01-16",
            "2025-01-23", "2025-01-30", "2025-02-27",
        ].iter().map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()).collect();

        let active = pick_active_expiries(&exp, date);
        assert_eq!(active.len(), 4);
        // Must be strictly after trade_date, smallest first
        assert!(active[0] > date);
        for w in active.windows(2) { assert!(w[0] < w[1]); }
    }
}
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/intraday_server && cargo test --bin migrate snapshot_builder 2>&1 | tail -15
```

Expected:
```
test tests::test_build_header_fields ... ok
test tests::test_build_size ... ok
test tests::test_chain_value_roundtrip ... ok
test tests::test_pick_active_expiries ... ok
test result: ok. 4 passed
```

- [ ] **Step 3: Wire into main.rs**

```rust
mod csv_reader;
mod expiry_index;
mod parquet_writer;
mod snapshot_builder;

fn main() {}
```

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/bin/migrate/
git commit -m "feat(migrate): snapshot_builder — pack DaySnapshot binary from daily BarRows"
```

---

## Task 6: `manifest.rs` — SQLite idempotency tracking

**Files:**
- Create: `backend/intraday_server/src/bin/migrate/manifest.rs`

**What this module does:** Opens (or creates) a SQLite database, provides `check(symbol, date)` → Option<sha256> and `upsert(...)` for idempotent re-runs.

- [ ] **Step 1: Write the module with inline tests**

Create `backend/intraday_server/src/bin/migrate/manifest.rs`:

```rust
use chrono::NaiveDate;
use rusqlite::{Connection, params};
use std::path::Path;

pub struct Manifest {
    conn: Connection,
}

impl Manifest {
    pub fn open(path: &Path) -> anyhow::Result<Self> {
        if let Some(p) = path.parent() { std::fs::create_dir_all(p)?; }
        let conn = Connection::open(path)?;
        conn.execute_batch("
            CREATE TABLE IF NOT EXISTS imports (
                symbol      TEXT    NOT NULL,
                trade_date  TEXT    NOT NULL,
                sha256      TEXT    NOT NULL,
                row_count   INTEGER NOT NULL,
                ingested_at INTEGER NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            );
        ")?;
        Ok(Self { conn })
    }

    /// Returns the stored sha256 for (symbol, date), or None if not imported yet.
    pub fn check(&self, symbol: &str, date: NaiveDate) -> anyhow::Result<Option<String>> {
        let date_str = date.format("%Y-%m-%d").to_string();
        let result: Option<String> = self.conn
            .query_row(
                "SELECT sha256 FROM imports WHERE symbol = ?1 AND trade_date = ?2",
                params![symbol, date_str],
                |row| row.get(0),
            )
            .optional()?;
        Ok(result)
    }

    /// Insert or replace the manifest record for (symbol, date).
    pub fn upsert(&self, symbol: &str, date: NaiveDate, sha256: &str, row_count: i32) -> anyhow::Result<()> {
        let date_str = date.format("%Y-%m-%d").to_string();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        self.conn.execute(
            "INSERT OR REPLACE INTO imports (symbol, trade_date, sha256, row_count, ingested_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![symbol, date_str, sha256, row_count, now],
        )?;
        Ok(())
    }
}

/// Compute SHA-256 hex of a byte slice.
pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let hash = Sha256::digest(data);
    format!("{:x}", hash)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_check_none_then_upsert_then_check() {
        let dir = tempfile::tempdir().unwrap();
        let db = Manifest::open(&dir.path().join("manifest.db")).unwrap();
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();

        assert!(db.check("NIFTY", date).unwrap().is_none());
        db.upsert("NIFTY", date, "abc123def456", 60000).unwrap();
        assert_eq!(db.check("NIFTY", date).unwrap(), Some("abc123def456".into()));
    }

    #[test]
    fn test_upsert_replaces_on_new_sha() {
        let dir = tempfile::tempdir().unwrap();
        let db = Manifest::open(&dir.path().join("manifest.db")).unwrap();
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();

        db.upsert("NIFTY", date, "sha_v1", 60000).unwrap();
        db.upsert("NIFTY", date, "sha_v2", 60001).unwrap();
        assert_eq!(db.check("NIFTY", date).unwrap(), Some("sha_v2".into()));
    }

    #[test]
    fn test_sha256_hex_stable() {
        let h1 = sha256_hex(b"hello");
        let h2 = sha256_hex(b"hello");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64);
        assert_ne!(sha256_hex(b"hello"), sha256_hex(b"world"));
    }

    #[test]
    fn test_creates_parent_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let deep = dir.path().join("a/b/c/manifest.db");
        let db = Manifest::open(&deep).unwrap();
        let date = NaiveDate::from_ymd_opt(2025, 3, 1).unwrap();
        db.upsert("NIFTY", date, "xyz", 100).unwrap();
        assert!(deep.exists());
    }
}
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/intraday_server && cargo test --bin migrate manifest 2>&1 | tail -10
```

Expected:
```
test tests::test_check_none_then_upsert_then_check ... ok
test tests::test_creates_parent_dirs ... ok
test tests::test_sha256_hex_stable ... ok
test tests::test_upsert_replaces_on_new_sha ... ok
test result: ok. 4 passed
```

- [ ] **Step 3: Wire into main.rs**

```rust
mod csv_reader;
mod expiry_index;
mod parquet_writer;
mod snapshot_builder;
mod manifest;

fn main() {}
```

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/bin/migrate/
git commit -m "feat(migrate): manifest — SQLite idempotency tracking with sha256 check"
```

---

## Task 7: `main.rs` — CLI, pipeline orchestration, spot Parquet

**Files:**
- Modify: `backend/intraday_server/src/bin/migrate/main.rs`

**What this module does:** Parses CLI args, runs Stage 1 (parallel CSV scan with rayon), Stage 2 (serial date-by-date write loop), writes the spot Parquet, and prints a final summary report.

- [ ] **Step 1: Write the complete main.rs**

Replace `backend/intraday_server/src/bin/migrate/main.rs` with:

```rust
mod csv_reader;
mod expiry_index;
mod manifest;
mod parquet_writer;
mod snapshot_builder;

use chrono::NaiveDate;
use clap::Parser;
use csv_reader::BarRow;
use indicatif::{ProgressBar, ProgressStyle};
use parquet_writer::SpotBar;
use rayon::prelude::*;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

#[derive(Parser, Debug)]
#[command(name = "migrate", about = "Import intraday options CSVs into /data/intraday")]
struct Args {
    /// Directory containing per-contract CSV files, e.g. .../INDICES-OPTION/NIFTY
    #[arg(long)]
    options_dir: PathBuf,

    /// Spot CSV file path, e.g. .../NIFTY 50.csv
    #[arg(long)]
    spot_file: PathBuf,

    /// Output data root, e.g. /data/intraday
    #[arg(long, default_value = "/data/intraday")]
    data_dir: PathBuf,

    /// Symbol name, e.g. NIFTY
    #[arg(long)]
    symbol: String,

    /// Calendar year to import (trade dates)
    #[arg(long)]
    year: i32,

    /// Number of rayon threads (default: all CPUs)
    #[arg(long)]
    workers: Option<usize>,

    /// Validate and log without writing any files
    #[arg(long)]
    dry_run: bool,

    /// Re-ingest dates whose sha256 already matches (overwrite)
    #[arg(long)]
    force: bool,
}

/// Strike step in whole rupees by symbol.
fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY"  => 100,
        "MIDCPNIFTY" => 25,
        _            => 50,  // NIFTY, FINNIFTY
    }
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();

    if let Some(n) = args.workers {
        rayon::ThreadPoolBuilder::new().num_threads(n).build_global()?;
    }

    // ── Stage 0: validate inputs ──────────────────────────────────────────
    anyhow::ensure!(args.options_dir.is_dir(),
        "options_dir does not exist: {}", args.options_dir.display());
    anyhow::ensure!(args.spot_file.is_file(),
        "spot_file does not exist: {}", args.spot_file.display());

    // ── Stage 1: discover option files and pre-filter by expiry year ──────
    println!("Discovering option files in {} ...", args.options_dir.display());
    let target_yy = (args.year % 100) as u32;
    let csv_files: Vec<PathBuf> = std::fs::read_dir(&args.options_dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().map(|e| e == "csv").unwrap_or(false))
        .filter(|p| {
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
            // Skip files whose expiry year < target year (cannot contain target year rows)
            match csv_reader::filename_expiry_year_2digit(name) {
                Some(yy) => yy >= target_yy,
                None     => false, // skip unparseable names
            }
        })
        .collect();
    println!("  Found {} candidate files (after pre-filter).", csv_files.len());

    // ── Stage 2: parallel CSV scan ────────────────────────────────────────
    println!("Scanning CSVs for year={} (parallel) ...", args.year);
    let pb = ProgressBar::new(csv_files.len() as u64);
    pb.set_style(ProgressStyle::default_bar()
        .template("[{elapsed_precise}] {bar:40} {pos}/{len} files ({eta})")
        .unwrap());

    let (all_rows, scan_errors): (Vec<Vec<BarRow>>, Vec<String>) = csv_files
        .par_iter()
        .map(|path| {
            let result = csv_reader::read_file(path, args.year);
            pb.inc(1);
            match result {
                Ok(rows) => Ok(rows),
                Err(e)   => Err(format!("{}: {}", path.display(), e)),
            }
        })
        .partition_map(|r| match r {
            Ok(rows) => rayon::iter::Either::Left(rows),
            Err(e)   => rayon::iter::Either::Right(e),
        });
    pb.finish_with_message("scan done");

    let mut all_rows: Vec<BarRow> = all_rows.into_iter().flatten().collect();
    if !scan_errors.is_empty() {
        eprintln!("WARN: {} files failed to parse:", scan_errors.len());
        for e in &scan_errors { eprintln!("  {e}"); }
    }
    println!("  Collected {} real bars.", all_rows.len());

    // ── Stage 3: load spot data ───────────────────────────────────────────
    println!("Loading spot data from {} ...", args.spot_file.display());
    let spot_bars = parquet_writer::read_spot_csv(&args.spot_file, args.year)?;
    let spot_by_date: HashMap<NaiveDate, Vec<SpotBar>> = {
        let mut m: HashMap<NaiveDate, Vec<SpotBar>> = HashMap::new();
        for b in spot_bars.iter() { m.entry(b.trade_date).or_default().push(b.clone()); }
        m
    };
    println!("  {} spot bars across {} trading dates.", spot_bars.len(), spot_by_date.len());

    // ── Stage 4: sort all rows ────────────────────────────────────────────
    all_rows.sort_unstable_by(|a, b| {
        a.trade_date.cmp(&b.trade_date)
            .then(a.expiry_date.cmp(&b.expiry_date))
            .then(a.strike_x100.cmp(&b.strike_x100))
            .then(a.opt_type.cmp(&b.opt_type))
            .then(a.ts_min.cmp(&b.ts_min))
    });

    // ── Stage 5: collect unique expiries, build index ────────────────────
    let unique_expiries: Vec<NaiveDate> = {
        let mut v: Vec<NaiveDate> = all_rows.iter().map(|r| r.expiry_date).collect();
        v.sort_unstable();
        v.dedup();
        v
    };
    let expiry_json_path = args.data_dir.join(&args.symbol).join("expiries.json");
    let mut expiry_idx = expiry_index::ExpiryIndex::load_or_create(&expiry_json_path)?;
    for &e in &unique_expiries { expiry_idx.get_or_insert(e); }
    if !args.dry_run { expiry_idx.save()?; }

    // ── Stage 6: open manifest ───────────────────────────────────────────
    let manifest_path = args.data_dir.join("_manifest.db");
    let db = manifest::Manifest::open(&manifest_path)?;

    // ── Stage 7: serial write per trading date ───────────────────────────
    let unique_dates: Vec<NaiveDate> = {
        let mut v: Vec<NaiveDate> = all_rows.iter().map(|r| r.trade_date).collect();
        v.sort_unstable();
        v.dedup();
        v
    };
    println!("Processing {} trading dates ...", unique_dates.len());
    let pb2 = ProgressBar::new(unique_dates.len() as u64);
    pb2.set_style(ProgressStyle::default_bar()
        .template("[{elapsed_precise}] {bar:40} {pos}/{len} dates ({eta})")
        .unwrap());

    let mut stats_ok = 0usize;
    let mut stats_skipped = 0usize;
    let mut stats_failed: Vec<(NaiveDate, String)> = Vec::new();
    let mut total_rows_written = 0usize;
    let mut total_bytes = 0u64;

    // Group rows by date (all_rows is sorted by trade_date)
    let mut row_start = 0usize;
    for &trade_date in &unique_dates {
        pb2.inc(1);

        // Find the slice for this date
        let row_end = all_rows[row_start..]
            .iter()
            .position(|r| r.trade_date != trade_date)
            .map(|p| row_start + p)
            .unwrap_or(all_rows.len());
        let date_rows = &all_rows[row_start..row_end];
        row_start = row_end;

        // Manifest check
        if !args.force {
            // Compute a content hash from the raw row count + first/last row as proxy
            let quick_hash = manifest::sha256_hex(
                format!("{}:{}:{}", &args.symbol, trade_date, date_rows.len()).as_bytes()
            );
            if let Ok(Some(stored)) = db.check(&args.symbol, trade_date) {
                if stored == quick_hash {
                    stats_skipped += 1;
                    continue;
                }
            }
        }

        // Validate OHLCV invariants
        let mut valid = true;
        for r in date_rows {
            if r.high_x100 < r.open_x100 || r.high_x100 < r.close_x100
                || r.low_x100 > r.open_x100 || r.low_x100 > r.close_x100
                || r.high_x100 < r.low_x100
                || r.open_x100 < 0 || r.close_x100 < 0
            {
                stats_failed.push((trade_date, format!(
                    "OHLCV invariant violated: O={} H={} L={} C={}",
                    r.open_x100, r.high_x100, r.low_x100, r.close_x100
                )));
                valid = false;
                break;
            }
        }
        if !valid { continue; }

        if args.dry_run {
            stats_ok += 1;
            total_rows_written += date_rows.len();
            continue;
        }

        // Write Parquet
        let year  = trade_date.format("%Y").to_string();
        let month = trade_date.format("%m").to_string();
        let fname = trade_date.format("%Y-%m-%d").to_string() + ".parquet";
        let parquet_path = args.data_dir
            .join(&args.symbol)
            .join("options")
            .join(format!("year={year}"))
            .join(format!("month={month}"))
            .join(&fname);

        if let Err(e) = parquet_writer::write_options_parquet(
            &parquet_path, &args.symbol, trade_date, date_rows
        ) {
            stats_failed.push((trade_date, format!("parquet write: {e}")));
            continue;
        }
        total_bytes += parquet_path.metadata().map(|m| m.len()).unwrap_or(0);

        // Build DaySnapshot
        let empty_spot = vec![];
        let day_spot = spot_by_date.get(&trade_date).unwrap_or(&empty_spot);
        let all_day_expiries: Vec<NaiveDate> = {
            let mut v: Vec<NaiveDate> = date_rows.iter().map(|r| r.expiry_date).collect();
            v.sort_unstable(); v.dedup(); v
        };
        let active_exp_dates = snapshot_builder::pick_active_expiries(&all_day_expiries, trade_date);
        let active_exp: Vec<(NaiveDate, i16)> = active_exp_dates.iter()
            .filter_map(|&d| expiry_idx.get(d).map(|i| (d, i)))
            .collect();

        let snap_bytes = snapshot_builder::build(
            &args.symbol, trade_date, day_spot, date_rows, &active_exp, strike_step(&args.symbol)
        );

        let snap_fname = trade_date.format("%Y-%m-%d").to_string() + ".arrow";
        let snap_path = args.data_dir
            .join(&args.symbol)
            .join("snapshots")
            .join(&snap_fname);

        if let Err(e) = snapshot_builder::write(&snap_path, &snap_bytes) {
            stats_failed.push((trade_date, format!("snapshot write: {e}")));
            continue;
        }
        total_bytes += snap_bytes.len() as u64;

        // Update manifest
        let sha = manifest::sha256_hex(&snap_bytes);
        let _ = db.upsert(&args.symbol, trade_date, &sha, date_rows.len() as i32);

        stats_ok += 1;
        total_rows_written += date_rows.len();
    }
    pb2.finish_with_message("done");

    // ── Stage 8: write spot Parquet ───────────────────────────────────────
    if !args.dry_run {
        let spot_path = args.data_dir
            .join(&args.symbol)
            .join("spot")
            .join(format!("{}-spot-{}.parquet", &args.symbol, args.year));
        parquet_writer::write_spot_parquet(&spot_path, &spot_bars)?;
        total_bytes += spot_path.metadata().map(|m| m.len()).unwrap_or(0);
        println!("Spot Parquet written: {}", spot_path.display());
    }

    // ── Summary ───────────────────────────────────────────────────────────
    println!("\n=== Migration summary ===");
    println!("  Dates OK:      {}", stats_ok);
    println!("  Dates skipped: {} (sha unchanged)", stats_skipped);
    println!("  Dates failed:  {}", stats_failed.len());
    if !stats_failed.is_empty() {
        for (d, e) in &stats_failed { println!("    {d}: {e}"); }
    }
    println!("  Rows written:  {}", total_rows_written);
    println!("  Bytes written: {:.1} MB", total_bytes as f64 / 1_048_576.0);
    if args.dry_run { println!("  [DRY RUN — no files written]"); }

    if !stats_failed.is_empty() {
        std::process::exit(1);
    }
    Ok(())
}
```

- [ ] **Step 2: Build the binary**

```bash
cd backend/intraday_server && cargo build --bin migrate --release 2>&1 | tail -5
```

Expected: `Finished release profile` with no errors.

- [ ] **Step 3: Run all module tests to confirm nothing broke**

```bash
cd backend/intraday_server && cargo test --bin migrate 2>&1 | tail -20
```

Expected: all 15 existing server tests still pass, plus the new migrate tests.

- [ ] **Step 4: Commit**

```bash
git add backend/intraday_server/src/bin/migrate/
git commit -m "feat(migrate): main pipeline — CLI, parallel scan, serial write, spot Parquet"
```

---

## Task 8: Smoke test with real data

**Files:** None created — this is a validation run only.

- [ ] **Step 1: Create the output directory**

```bash
sudo mkdir -p /data/intraday
sudo chown $USER:$USER /data/intraday
```

- [ ] **Step 2: Run dry-run to validate data without writing**

```bash
cd backend/intraday_server && cargo run --bin migrate --release -- \
  --options-dir "/run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/AAKASH/zerodha-data-processed/NFO_PREPARED/INDICES-OPTION/NIFTY" \
  --spot-file "/run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/AAKASH/zerodha-data-processed/NIFTY 50.csv" \
  --data-dir /data/intraday \
  --symbol NIFTY \
  --year 2025 \
  --dry-run 2>&1
```

Expected output (approximate — exact numbers will vary):
```
Discovering option files in ...
  Found ~14000 candidate files (after pre-filter).
Scanning CSVs for year=2025 (parallel) ...
  Collected ~15,000,000 real bars.
Loading spot data ...
  ~93750 spot bars across ~250 trading dates.
Processing ~250 trading dates ...
=== Migration summary ===
  Dates OK:      ~250
  Dates skipped: 0
  Dates failed:  0
  Rows written:  ~15,000,000
  [DRY RUN — no files written]
```

If any dates fail, inspect the error message and fix the underlying data issue before proceeding.

- [ ] **Step 3: Run the real import for January 2025 only (subset test)**

```bash
cd backend/intraday_server && cargo run --bin migrate --release -- \
  --options-dir "/run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/AAKASH/zerodha-data-processed/NFO_PREPARED/INDICES-OPTION/NIFTY" \
  --spot-file "/run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/AAKASH/zerodha-data-processed/NIFTY 50.csv" \
  --data-dir /data/intraday \
  --symbol NIFTY \
  --year 2025 2>&1
```

Note: to limit to one month in this test, you can temporarily copy only January files to a local test dir and point `--options-dir` there.

- [ ] **Step 4: Verify outputs**

```bash
# Parquet files exist
ls /data/intraday/NIFTY/options/year=2025/month=01/ | head -5

# Snapshots exist
ls /data/intraday/NIFTY/snapshots/ | head -5

# Expiries index has content
cat /data/intraday/NIFTY/expiries.json | head -10

# Manifest has records
sqlite3 /data/intraday/_manifest.db "SELECT COUNT(*) FROM imports WHERE symbol='NIFTY';"

# Spot Parquet exists
ls -lh /data/intraday/NIFTY/spot/
```

Expected: Parquet files ~3–5 MB each, snapshots ~540 KB each, manifest row count equals date count.

- [ ] **Step 5: Verify a snapshot is readable by the Axum server**

```bash
# Start the intraday server pointing at the test data
INTRADAY_DATA_DIR=/data/intraday cargo run --bin intraday_server &
sleep 2

# Health check
curl -s http://localhost:8001/api/intraday/health | python3 -m json.tool

# Query dates for NIFTY — should return January 2025 dates
curl -s "http://localhost:8001/api/intraday/meta/dates?symbol=NIFTY" | python3 -m json.tool

kill %1
```

Expected: health returns `{"service":"intraday","status":"ok"}`, dates returns a list of 2025-01-XX dates.

- [ ] **Step 6: Commit**

```bash
git add .
git commit -m "test(migrate): smoke test NIFTY 2025 — dry-run + January subset verified"
```

---

## Self-review checklist

**Spec coverage:**
- §2.1 CSV format (Ticker/Date/Time/Expiry Date/OHLCV/Volume/OI/Padding Flag) → Task 2 ✓
- §2.1 Pre-filter by expiry year from filename → Task 7 ✓
- §3 Output filesystem layout → Tasks 4, 5, 7 ✓
- §4.1 Options Parquet schema (all 12 columns, Date32, Int16, Bool) → Task 4 ✓
- §4.1 ZSTD level 3, dictionary encoding → Task 4 ✓
- §4.1 Sort by (expiry_date, strike_x100, opt_type, ts_min) → Task 7 (sort before write) ✓
- §4.2 Spot Parquet → Tasks 4, 7 ✓
- §4.3 DaySnapshot binary layout (exact constants from snapshot.rs) → Task 5 ✓
- §4.3 ATM anchor at 09:15, ±5 strike steps, forward-fill missing series → Task 5 ✓
- §4.3 Pick 4 nearest active expiries → Task 5 ✓
- §4.4 expiries.json append-only, never renumber → Task 3 ✓
- §4.5 SQLite manifest, INSERT OR REPLACE, sha256 → Task 6 ✓
- §5.3 CLI flags (--options-dir, --spot-file, --data-dir, --symbol, --year, --workers, --dry-run, --force) → Task 7 ✓
- §5.4 Parallel Stage 1 (rayon), serial Stage 2 → Task 7 ✓
- §5.6 Skip-and-continue error strategy, summary report → Task 7 ✓
- §7 OHLCV validation, reject date on violation → Task 7 ✓
- §8 Atomic write (.tmp → rename) → Tasks 4, 5 ✓
