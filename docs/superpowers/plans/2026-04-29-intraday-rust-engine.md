# Intraday Rust Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fat PyO3 Rust kernel that reads DaySnapshot files and runs a single-leg intraday backtest for one day at a time, returning trade records to Python which assembles Arrow IPC output.

**Architecture:** A new `intraday` sub-module in `backend/native/src/intraday/` extends the existing `algotest_native` PyO3 extension. Rust memory-maps DaySnapshot binary files, resolves strikes and entry/exit logic entirely in Rust, and returns `Vec<HashMap>` to Python. Python (`backend/services/intraday_engine.py`) assembles the result into Arrow IPC bytes via pyarrow and returns them to the Celery task.

**Tech Stack:** Rust + PyO3 0.21, memmap2 0.9, serde_json 1, chrono 0.4, maturin (build), Python pyarrow for final serialisation.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/native/src/intraday/mod.rs` | module re-exports |
| Create | `backend/native/src/intraday/snapshot.rs` | mmap reader + typed accessors |
| Create | `backend/native/src/intraday/types.rs` | serde strategy-spec + trade-record structs |
| Create | `backend/native/src/intraday/engine.rs` | per-day backtest loop |
| Create | `backend/native/src/intraday/pyfuncs.rs` | PyO3 `#[pyfunction]` binding |
| Modify | `backend/native/src/lib.rs` | register new module |
| Modify | `backend/native/Cargo.toml` | add serde + serde_json |
| Create | `backend/native/.cargo/config.toml` | target-cpu=native + LTO |
| Create | `backend/services/intraday_engine.py` | thin Python wrapper → Arrow IPC |
| Create | `backend/tests/test_intraday_engine.py` | integration tests via Python |

---

### Task 1: Add Cargo dependencies and build config

**Files:**
- Modify: `backend/native/Cargo.toml`
- Create: `backend/native/.cargo/config.toml`

- [ ] **Step 1: Write the failing build**

```bash
cd backend/native
cargo build --release 2>&1 | tail -5
```
Expected: builds fine (baseline passing before any changes).

- [ ] **Step 2: Add serde deps to Cargo.toml**

Open `backend/native/Cargo.toml` and add under `[dependencies]`:

```toml
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

Full `[dependencies]` block after edit:
```toml
[dependencies]
pyo3 = { version = "0.21", features = ["extension-module"] }
once_cell = "1.19"
memmap2 = "0.9"
chrono = { version = "0.4", features = ["clock"] }
arrow-array = "52"
arrow-ipc = "52"
arrow-schema = "52"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

- [ ] **Step 3: Create `.cargo/config.toml`**

Create `backend/native/.cargo/config.toml`:
```toml
[build]
rustflags = ["-C", "target-cpu=native", "-C", "opt-level=3", "-C", "lto=thin"]
```

(`lto=thin` not `fat` — fat LTO causes compile hangs on some PyO3 projects. Thin gives 90% of the win.)

- [ ] **Step 4: Verify build still passes**

```bash
cd backend/native
cargo build --release 2>&1 | tail -5
```
Expected: compiles successfully with no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/native/Cargo.toml backend/native/.cargo/config.toml
git commit -m "chore(native): add serde deps + native CPU build flags"
```

---

### Task 2: Snapshot binary constants and accessor (`snapshot.rs`)

**Files:**
- Create: `backend/native/src/intraday/mod.rs`
- Create: `backend/native/src/intraday/snapshot.rs`

Binary layout constants (must match `backend/services/intraday_snapshot/format.py` from Plan A):

```
HEADER   = 32 bytes
  [0..4]   magic: b"ITDS"
  [4]      version: u8
  [5..21]  symbol: [u8;16] null-padded
  [21..25] date: i32 LE  (days since Unix epoch 1970-01-01)
  [25]     expiry_count: u8
  [26..28] spot_minute_count: u16 LE  (typically 375)
  [28..32] padding

SPOT = 375 × 16 bytes
  per minute: open_x100 i32, high_x100 i32, low_x100 i32, close_x100 i32

PER EXPIRY = 133502 bytes each
  [0..2]       expiry_idx: i16 LE
  [2..1502]    atm[375]: [i32 LE; 375]
  [1502..]     chain[11][2][4][375]: i32 LE
               index: s=0..11, t=0..2(CE/PE), f=0..4(close/high/low/vol), m=0..375
```

- [ ] **Step 1: Create `backend/native/src/intraday/mod.rs`**

```rust
pub mod engine;
pub mod pyfuncs;
pub mod snapshot;
pub mod types;
```

- [ ] **Step 2: Create `backend/native/src/intraday/snapshot.rs`**

```rust
use memmap2::Mmap;
use std::fs::File;
use std::path::Path;

pub const MINUTES: usize = 375;
const HEADER_SIZE: usize = 32;
const SPOT_ENTRY: usize = 16; // 4 × i32
const SPOT_SIZE: usize = MINUTES * SPOT_ENTRY; // 6000
const CHAIN_STRIKES: usize = 11; // ATM-5 .. ATM+5
const CHAIN_TYPES: usize = 2;  // 0=CE 1=PE
const CHAIN_FIELDS: usize = 4; // 0=close 1=high 2=low 3=volume
pub const EXPIRY_SIZE: usize = 2
    + MINUTES * 4
    + CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES * 4; // 133502

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
        if &mmap[0..4] != b"ITDS" {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "bad ITDS magic"));
        }
        let symbol_bytes = &mmap[5..21];
        let symbol = std::str::from_utf8(symbol_bytes)
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

    pub fn spot_close_x100(&self, m: usize) -> i32 {
        let off = HEADER_SIZE + m * SPOT_ENTRY + 12; // close is 4th i32
        i32::from_le_bytes(self.mmap[off..off + 4].try_into().unwrap())
    }

    pub fn expiry_idx(&self, e: usize) -> i16 {
        let off = self.expiry_base(e);
        i16::from_le_bytes(self.mmap[off..off + 2].try_into().unwrap())
    }

    pub fn atm_x100(&self, e: usize, m: usize) -> i32 {
        let off = self.expiry_base(e) + 2 + m * 4;
        i32::from_le_bytes(self.mmap[off..off + 4].try_into().unwrap())
    }

    /// field: 0=close 1=high 2=low 3=volume
    pub fn chain_val(&self, e: usize, s: usize, t: usize, field: usize, m: usize) -> i32 {
        let chain_off = self.expiry_base(e) + 2 + MINUTES * 4;
        let idx = s * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
            + t * CHAIN_FIELDS * MINUTES
            + field * MINUTES
            + m;
        let off = chain_off + idx * 4;
        i32::from_le_bytes(self.mmap[off..off + 4].try_into().unwrap())
    }
}
```

- [ ] **Step 3: Verify it compiles**

Add a temporary `mod intraday;` line to `backend/native/src/lib.rs` (will be cleaned up in Task 6):
```rust
mod intraday;
```

```bash
cd backend/native && cargo check 2>&1 | grep -E "^error" | head -10
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/native/src/intraday/
git commit -m "feat(native/intraday): add snapshot binary reader"
```

---

### Task 3: Strategy spec types (`types.rs`)

**Files:**
- Create: `backend/native/src/intraday/types.rs`

- [ ] **Step 1: Create `backend/native/src/intraday/types.rs`**

```rust
use serde::Deserialize;

#[derive(Deserialize, Debug)]
pub struct StrategySpec {
    pub symbol: String,
    pub date_from: String,   // "YYYY-MM-DD"
    pub date_to: String,
    pub entry_time: String,  // "HH:MM"
    pub square_off_time: String,
    pub legs: Vec<LegSpec>,
}

#[derive(Deserialize, Debug)]
pub struct LegSpec {
    pub opt_type: String,   // "CE" | "PE"
    pub action: String,     // "BUY" | "SELL"
    pub strike_selection: StrikeSelection,
    pub expiry: String,     // "WEEKLY" | "MONTHLY" | "NEXT_WEEKLY" | "NEXT_MONTHLY"
    pub quantity: u32,
    pub sl: Option<ExitCond>,
    pub target: Option<ExitCond>,
}

#[derive(Deserialize, Debug)]
pub struct StrikeSelection {
    pub mode: String,   // "ATM" | "ATM_OFFSET"
    pub value: i32,     // 0 for ATM; ±1..±5 for offset
}

#[derive(Deserialize, Debug)]
pub struct ExitCond {
    #[serde(rename = "type")]
    pub kind: String,   // "percent" | "points"
    pub value: f64,
}

#[derive(Debug)]
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
```

- [ ] **Step 2: Verify parse of sample JSON**

Add a unit test at the bottom of `types.rs`:
```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_parse_strategy_spec() {
        let json = r#"{
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
                "target": null
            }]
        }"#;
        let spec: StrategySpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.symbol, "NIFTY");
        assert_eq!(spec.legs.len(), 1);
        assert_eq!(spec.legs[0].strike_selection.value, 0);
    }
}
```

```bash
cd backend/native && cargo test intraday::types::tests 2>&1 | tail -5
```
Expected: `test intraday::types::tests::test_parse_strategy_spec ... ok`

- [ ] **Step 3: Commit**

```bash
git add backend/native/src/intraday/types.rs
git commit -m "feat(native/intraday): add strategy spec serde types"
```

---

### Task 4: Per-day engine loop (`engine.rs`)

**Files:**
- Create: `backend/native/src/intraday/engine.rs`

- [ ] **Step 1: Create `backend/native/src/intraday/engine.rs`**

```rust
use crate::intraday::snapshot::Snapshot;
use crate::intraday::types::{LegSpec, StrategySpec, TradeRecord};
use std::collections::HashMap;
use std::path::Path;

const SESSION_START: u32 = 9 * 60 + 15; // 09:15 in minutes-since-midnight

/// Convert "HH:MM" to 0-based minute index within session (0 = 09:15)
fn time_to_idx(hhmm: &str) -> usize {
    let parts: Vec<u32> = hhmm.splitn(2, ':').map(|s| s.parse().unwrap_or(0)).collect();
    let abs_min = parts[0] * 60 + parts[1];
    (abs_min.saturating_sub(SESSION_START)) as usize
}

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

/// strike step in strike_x100 units per symbol
fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY" => 10000,
        "MIDCPNIFTY" => 2500,
        _ => 5000, // NIFTY, FINNIFTY
    }
}

/// Pick expiry_e index in snapshot for a given expiry type.
/// WEEKLY → expiry_e=0 (nearest), MONTHLY → expiry_e=1 (or last of month).
/// Simple heuristic for now; full calendar logic added in Plan E.
fn pick_expiry_e(expiry_str: &str) -> usize {
    match expiry_str {
        "WEEKLY" | "NEXT_WEEKLY" => 0,
        "MONTHLY" | "NEXT_MONTHLY" => 1,
        _ => 0,
    }
}

fn compute_thresholds(leg: &LegSpec, entry_x100: i32) -> (Option<i32>, Option<i32>) {
    let sl_x100 = leg.sl.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 + delta } else { entry_x100 - delta }
    });
    let tgt_x100 = leg.target.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 - delta } else { entry_x100 + delta }
    });
    (sl_x100, tgt_x100)
}

fn mae_mfe(snap: &Snapshot, e: usize, s: usize, t: usize, entry_idx: usize, exit_idx: usize, is_sell: bool) -> (f64, f64) {
    let entry_px = snap.chain_val(e, s, t, 0, entry_idx) as f64;
    let (mut min_px, mut max_px) = (entry_px, entry_px);
    for m in (entry_idx + 1)..=exit_idx {
        let lo = snap.chain_val(e, s, t, 2, m) as f64;
        let hi = snap.chain_val(e, s, t, 1, m) as f64;
        if lo < min_px { min_px = lo; }
        if hi > max_px { max_px = hi; }
    }
    if is_sell {
        ((max_px - entry_px) / 100.0, (entry_px - min_px) / 100.0)
    } else {
        ((entry_px - min_px) / 100.0, (max_px - entry_px) / 100.0)
    }
}

/// Run all legs for a single trading day. Returns one TradeRecord per leg.
pub fn run_day(
    snap: &Snapshot,
    expiry_map: &HashMap<i16, String>,
    spec: &StrategySpec,
    date_str: &str,
) -> Vec<TradeRecord> {
    let step = strike_step(&spec.symbol);
    let entry_idx = time_to_idx(&spec.entry_time).min(snap.minute_count - 1);
    let sqoff_idx = time_to_idx(&spec.square_off_time).min(snap.minute_count - 1);

    let mut records = Vec::new();
    for leg in &spec.legs {
        let e = pick_expiry_e(&leg.expiry);
        if e >= snap.expiry_count { continue; }

        // ATM at entry minute
        let atm = snap.atm_x100(e, entry_idx);
        let strike = atm + leg.strike_selection.value * step;

        // Find chain offset s for this strike
        let anchor = snap.atm_x100(e, 0); // day-open ATM as chain anchor
        let s_raw = (strike - (anchor - 5 * step)) / step;
        if s_raw < 0 || s_raw >= 11 { continue; } // outside ATM±5 chain
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
            let hit_sl = sl_thr.map_or(false, |thr| if is_sell { px >= thr } else { px <= thr });
            let hit_tgt = tgt_thr.map_or(false, |thr| if is_sell { px <= thr } else { px >= thr });
            if hit_sl { exit_idx = m; exit_reason = "SL"; break; }
            if hit_tgt { exit_idx = m; exit_reason = "TARGET"; break; }
        }

        let exit_px = snap.chain_val(e, s, t, 0, exit_idx);
        let (mae, mfe) = mae_mfe(snap, e, s, t, entry_idx, exit_idx, is_sell);

        let raw_pnl = if is_sell {
            (entry_px - exit_px) as f64 / 100.0
        } else {
            (exit_px - entry_px) as f64 / 100.0
        };
        let pnl = raw_pnl * leg.quantity as f64;

        let expiry_str = expiry_map
            .get(&snap.expiry_idx(e))
            .cloned()
            .unwrap_or_else(|| "?".to_string());

        records.push(TradeRecord {
            date: date_str.to_string(),
            symbol: spec.symbol.clone(),
            expiry: expiry_str,
            strike: strike as f64 / 100.0,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_time_to_idx() {
        assert_eq!(time_to_idx("09:15"), 0);
        assert_eq!(time_to_idx("09:20"), 5);
        assert_eq!(time_to_idx("15:15"), 360);
        assert_eq!(time_to_idx("15:29"), 374);
    }

    #[test]
    fn test_idx_to_time() {
        assert_eq!(idx_to_time(0), "09:15");
        assert_eq!(idx_to_time(5), "09:20");
        assert_eq!(idx_to_time(360), "15:15");
    }

    #[test]
    fn test_strike_step() {
        assert_eq!(strike_step("NIFTY"), 5000);
        assert_eq!(strike_step("BANKNIFTY"), 10000);
        assert_eq!(strike_step("MIDCPNIFTY"), 2500);
    }

    #[test]
    fn test_compute_thresholds_sell_percent() {
        use crate::intraday::types::{ExitCond, LegSpec, StrikeSelection};
        let leg = LegSpec {
            opt_type: "CE".into(),
            action: "SELL".into(),
            strike_selection: StrikeSelection { mode: "ATM".into(), value: 0 },
            expiry: "WEEKLY".into(),
            quantity: 1,
            sl: Some(ExitCond { kind: "percent".into(), value: 50.0 }),
            target: Some(ExitCond { kind: "percent".into(), value: 50.0 }),
        };
        let (sl, tgt) = compute_thresholds(&leg, 10000); // entry = 100.00
        assert_eq!(sl, Some(15000));   // 100 + 50% = 150
        assert_eq!(tgt, Some(5000));   // 100 - 50% = 50
    }
}
```

- [ ] **Step 2: Run unit tests**

```bash
cd backend/native && cargo test intraday::engine::tests 2>&1 | tail -10
```
Expected: 4 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/native/src/intraday/engine.rs
git commit -m "feat(native/intraday): add per-day engine loop + unit tests"
```

---

### Task 5: PyO3 binding (`pyfuncs.rs`)

**Files:**
- Create: `backend/native/src/intraday/pyfuncs.rs`

- [ ] **Step 1: Create `backend/native/src/intraday/pyfuncs.rs`**

```rust
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::intraday::engine::run_day;
use crate::intraday::snapshot::Snapshot;
use crate::intraday::types::StrategySpec;

/// Load expiries.json for a symbol → HashMap<expiry_idx, date_string>
fn load_expiry_map(symbol_dir: &Path) -> std::io::Result<HashMap<i16, String>> {
    let path = symbol_dir.join("expiries.json");
    let text = std::fs::read_to_string(&path)?;
    let raw: HashMap<String, String> = serde_json::from_str(&text)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let map = raw
        .into_iter()
        .filter_map(|(k, v)| k.parse::<i16>().ok().map(|idx| (idx, v)))
        .collect();
    Ok(map)
}

/// Run intraday backtest.
///
/// config_json: JSON string matching StrategySpec
/// data_dir:    path to /data/intraday (contains NIFTY/, BANKNIFTY/, etc.)
///
/// Returns: Python list of dicts, one per trade.
#[pyfunction]
pub fn run_intraday_backtest(
    py: Python,
    config_json: &str,
    data_dir: &str,
) -> PyResult<PyObject> {
    let spec: StrategySpec = serde_json::from_str(config_json).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("bad config JSON: {e}"))
    })?;

    let symbol_dir = PathBuf::from(data_dir).join(&spec.symbol);
    let snapshots_dir = symbol_dir.join("snapshots");

    let expiry_map = load_expiry_map(&symbol_dir).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("load expiries: {e}"))
    })?;

    let date_from = NaiveDate::parse_from_str(&spec.date_from, "%Y-%m-%d").map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("bad date_from: {e}"))
    })?;
    let date_to = NaiveDate::parse_from_str(&spec.date_to, "%Y-%m-%d").map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("bad date_to: {e}"))
    })?;

    let all_trades = PyList::empty(py);
    let mut current = date_from;

    while current <= date_to {
        let date_str = current.format("%Y-%m-%d").to_string();
        let snap_path = snapshots_dir.join(format!("{}.arrow", date_str));

        if snap_path.exists() {
            match Snapshot::open(&snap_path) {
                Ok(snap) => {
                    let records = run_day(&snap, &expiry_map, &spec, &date_str);
                    for rec in records {
                        let row = PyDict::new(py);
                        row.set_item("date", &rec.date)?;
                        row.set_item("symbol", &rec.symbol)?;
                        row.set_item("expiry", &rec.expiry)?;
                        row.set_item("strike", rec.strike)?;
                        row.set_item("opt_type", &rec.opt_type)?;
                        row.set_item("action", &rec.action)?;
                        row.set_item("entry_time", &rec.entry_time)?;
                        row.set_item("entry_price", rec.entry_price)?;
                        row.set_item("exit_time", &rec.exit_time)?;
                        row.set_item("exit_price", rec.exit_price)?;
                        row.set_item("exit_reason", &rec.exit_reason)?;
                        row.set_item("quantity", rec.quantity)?;
                        row.set_item("pnl", rec.pnl)?;
                        row.set_item("mae", rec.mae)?;
                        row.set_item("mfe", rec.mfe)?;
                        all_trades.append(row)?;
                    }
                }
                Err(e) => {
                    eprintln!("[intraday] skip {date_str}: {e}");
                }
            }
        }

        current = current.succ_opt().unwrap_or(current);
    }

    Ok(all_trades.into())
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd backend/native && cargo check 2>&1 | grep "^error" | head -10
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/native/src/intraday/pyfuncs.rs
git commit -m "feat(native/intraday): add PyO3 run_intraday_backtest binding"
```

---

### Task 6: Register new module in `lib.rs`

**Files:**
- Modify: `backend/native/src/lib.rs`

- [ ] **Step 1: Add `mod intraday;` and register the function**

At the top of `backend/native/src/lib.rs`, after existing `use` statements, add:
```rust
mod intraday;
```

Find the `#[pymodule]` function (named `algotest_native` or similar) and add the new function registration inside it. The existing function looks like:

```rust
#[pymodule]
fn algotest_native(_py: Python, m: &PyModule) -> PyResult<()> {
    // ... existing registrations ...
    m.add_function(wrap_pyfunction!(some_existing_fn, m)?)?;
    Ok(())
}
```

Add this line before `Ok(())`:
```rust
m.add_function(wrap_pyfunction!(intraday::pyfuncs::run_intraday_backtest, m)?)?;
```

- [ ] **Step 2: Build the extension**

```bash
cd backend/native && cargo build --release 2>&1 | grep -E "^error|Compiling algotest" | head -10
```
Expected: `Compiling algotest_native ...` then no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/native/src/lib.rs
git commit -m "feat(native): register run_intraday_backtest in PyO3 module"
```

---

### Task 7: Python wrapper (`intraday_engine.py`)

**Files:**
- Create: `backend/services/intraday_engine.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intraday_engine.py`:
```python
import unittest
import os

class TestIntradayEngineImport(unittest.TestCase):
    def test_wrapper_importable(self):
        from backend.services import intraday_engine
        self.assertTrue(hasattr(intraday_engine, "run_intraday_backtest"))

    def test_returns_bytes_for_empty_date_range(self):
        """With no snapshot files present, should return valid Arrow IPC with 0 rows."""
        from backend.services.intraday_engine import run_intraday_backtest
        import tempfile, pyarrow as pa
        with tempfile.TemporaryDirectory() as tmp:
            import json, os
            symbol_dir = os.path.join(tmp, "NIFTY")
            os.makedirs(os.path.join(symbol_dir, "snapshots"))
            # Write empty expiries.json
            with open(os.path.join(symbol_dir, "expiries.json"), "w") as f:
                json.dump({}, f)
            config = {
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
                    "sl": {"type": "percent", "value": 50.0},
                    "target": None,
                }]
            }
            result = run_intraday_backtest(config, data_dir=tmp)
            self.assertIsInstance(result, bytes)
            reader = pa.ipc.open_stream(pa.BufferReader(result))
            table = reader.read_all()
            self.assertEqual(table.num_rows, 0)
```

Run:
```bash
cd /home/user/Algo_Test_Software && python -m unittest backend.tests.test_intraday_engine.TestIntradayEngineImport.test_wrapper_importable -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.intraday_engine'`

- [ ] **Step 2: Create `backend/services/intraday_engine.py`**

```python
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pyarrow as pa

logger = logging.getLogger(__name__)

INTRADAY_DATA_DIR = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")

_TRADESHEET_SCHEMA = pa.schema([
    pa.field("date", pa.string()),
    pa.field("symbol", pa.string()),
    pa.field("expiry", pa.string()),
    pa.field("strike", pa.float64()),
    pa.field("opt_type", pa.string()),
    pa.field("action", pa.string()),
    pa.field("entry_time", pa.string()),
    pa.field("entry_price", pa.float64()),
    pa.field("exit_time", pa.string()),
    pa.field("exit_price", pa.float64()),
    pa.field("exit_reason", pa.string()),
    pa.field("quantity", pa.uint32()),
    pa.field("pnl", pa.float64()),
    pa.field("mae", pa.float64()),
    pa.field("mfe", pa.float64()),
])

_native = None


def _get_native():
    global _native
    if _native is None:
        import algotest_native as mod
        _native = mod
    return _native


def run_intraday_backtest(config: dict, *, data_dir: str | None = None) -> bytes:
    """Run intraday backtest. Returns Arrow IPC stream bytes."""
    if data_dir is None:
        data_dir = INTRADAY_DATA_DIR

    native = _get_native()
    trade_rows: list[dict] = native.run_intraday_backtest(json.dumps(config), data_dir)

    if trade_rows:
        table = pa.Table.from_pylist(trade_rows, schema=_TRADESHEET_SCHEMA)
    else:
        table = pa.table({f.name: pa.array([], type=f.type) for f in _TRADESHEET_SCHEMA},
                         schema=_TRADESHEET_SCHEMA)

    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()
    return sink.getvalue().to_pybytes()
```

- [ ] **Step 3: Install the built extension into the venv**

```bash
cd /home/user/Algo_Test_Software/backend/native
pip install --no-build-isolation -e . --quiet
```
Expected: installs `algotest_native` into the current Python env.

- [ ] **Step 4: Run tests**

```bash
cd /home/user/Algo_Test_Software
python -m unittest backend.tests.test_intraday_engine -v
```
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/intraday_engine.py backend/tests/test_intraday_engine.py
git commit -m "feat(intraday): Python wrapper + Arrow IPC assembly for engine output"
```

---

### Task 8: Rebuild extension via maturin (Docker path)

**Files:**
- Modify: `backend/Dockerfile` (no changes needed — maturin build already handles `native/`)

- [ ] **Step 1: Verify Docker build picks up the new module**

```bash
docker compose build backend 2>&1 | tail -20
```
Expected: `maturin build` succeeds, wheel is installed, image builds.

- [ ] **Step 2: Smoke-test the container**

```bash
docker compose run --rm --entrypoint python backend -c "import algotest_native; print(dir(algotest_native))" 2>&1
```
Expected: output includes `run_intraday_backtest`.

- [ ] **Step 3: Commit**

No code changes needed — just verify. If the Dockerfile needed tweaks, commit those:
```bash
git add backend/Dockerfile
git commit -m "chore(docker): verify maturin builds intraday native module"
```

---

### Task 9: Golden integration test with a synthetic DaySnapshot

**Files:**
- Modify: `backend/tests/test_intraday_engine.py`

This test synthesises a minimal valid DaySnapshot binary buffer, writes it to disk, runs the engine, and asserts the trade output is correct.

- [ ] **Step 1: Add golden test to `backend/tests/test_intraday_engine.py`**

Add this class:
```python
import struct, tempfile, os, json

class TestIntradayEngineGolden(unittest.TestCase):
    """End-to-end: synthetic snapshot → engine → tradesheet."""

    # Snapshot constants (must match snapshot.rs)
    MINUTES = 375
    HEADER_SIZE = 32
    SPOT_ENTRY = 16
    SPOT_SIZE = MINUTES * SPOT_ENTRY        # 6000
    EXPIRY_SIZE = 2 + MINUTES*4 + 11*2*4*MINUTES*4  # 133502

    def _make_snapshot(self, date_str: str, atm_x100: int, entry_close_x100: int, later_close_x100: int) -> bytes:
        """Build a minimal 1-expiry DaySnapshot where all chain values are either
        entry_close_x100 (bars 0..entry_idx) or later_close_x100 (bars entry_idx+1..).
        """
        import datetime
        epoch = datetime.date(1970, 1, 1)
        d = datetime.date.fromisoformat(date_str)
        date_days = (d - epoch).days

        # Header (32 bytes)
        symbol_bytes = b"NIFTY\x00" + b"\x00" * 10  # 16 bytes
        header = (
            b"ITDS"                                   # magic
            + struct.pack("<B", 1)                    # version
            + symbol_bytes                            # symbol 16B
            + struct.pack("<i", date_days)            # date i32
            + struct.pack("<B", 1)                    # expiry_count=1
            + struct.pack("<H", self.MINUTES)         # minute_count
            + b"\x00\x00\x00\x00"                    # padding to 32
        )
        assert len(header) == self.HEADER_SIZE

        # Spot (375×16 bytes): close = atm_x100 for all minutes
        spot = b""
        for _ in range(self.MINUTES):
            # open high low close (all same for simplicity)
            spot += struct.pack("<iiii", atm_x100, atm_x100, atm_x100, atm_x100)
        assert len(spot) == self.SPOT_SIZE

        # Expiry section
        expiry_idx_val = 0  # mapped to "2024-01-04" in expiries.json
        expiry_hdr = struct.pack("<h", expiry_idx_val)  # i16

        # ATM array: atm_x100 for all minutes
        atm_arr = struct.pack(f"<{self.MINUTES}i", *([atm_x100] * self.MINUTES))

        # Chain[11][2][4][375]: all prices for chain entry s=5 (ATM offset 0),
        # CE (t=0), close (field 0) set to entry_close_x100 up to minute 5, then later_close_x100.
        # All other values = 1 (nonzero, irrelevant).
        entry_minute_idx = 5  # 09:20 = idx 5
        chain_size = 11 * 2 * 4 * self.MINUTES
        chain = bytearray(chain_size * 4)

        # Fill all to 1 first
        for i in range(chain_size):
            struct.pack_into("<i", chain, i * 4, 100)  # 100 = 1.00 INR default

        # Set s=5 (ATM+0), t=0 (CE), field=0 (close), all minutes
        def chain_offset(s, t, field, m):
            return (s*2*4*self.MINUTES + t*4*self.MINUTES + field*self.MINUTES + m) * 4

        for m in range(self.MINUTES):
            px = entry_close_x100 if m <= entry_minute_idx else later_close_x100
            struct.pack_into("<i", chain, chain_offset(5, 0, 0, m), px)
            struct.pack_into("<i", chain, chain_offset(5, 0, 1, m), px)  # high
            struct.pack_into("<i", chain, chain_offset(5, 0, 2, m), px)  # low

        expiry_section = expiry_hdr + atm_arr + bytes(chain)
        assert len(expiry_section) == self.EXPIRY_SIZE

        return header + spot + expiry_section

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        symbol_dir = os.path.join(self.tmpdir, "NIFTY")
        snaps_dir = os.path.join(symbol_dir, "snapshots")
        os.makedirs(snaps_dir)
        # expiries.json: index 0 → "2024-01-04"
        with open(os.path.join(symbol_dir, "expiries.json"), "w") as f:
            json.dump({"0": "2024-01-04"}, f)
        # Write snapshot for 2024-01-01
        snap_bytes = self._make_snapshot(
            "2024-01-01",
            atm_x100=2400000,    # ATM = 24000.00
            entry_close_x100=20000,  # entry price = 200.00
            later_close_x100=10000,  # price falls to 100.00 (50% drop)
        )
        with open(os.path.join(snaps_dir, "2024-01-01.arrow"), "wb") as f:
            f.write(snap_bytes)
        self.symbol_dir_parent = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sell_hits_target(self):
        """SELL CE at ATM with 50% target. Price drops from 200 to 100. Should hit target."""
        from backend.services.intraday_engine import run_intraday_backtest
        import pyarrow as pa

        config = {
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
                "target": {"type": "percent", "value": 50.0},
            }]
        }
        result = run_intraday_backtest(config, data_dir=self.tmpdir)
        reader = pa.ipc.open_stream(pa.BufferReader(result))
        table = reader.read_all()

        self.assertEqual(table.num_rows, 1)
        row = {col: table.column(col)[0].as_py() for col in table.schema.names}
        self.assertEqual(row["exit_reason"], "TARGET")
        self.assertAlmostEqual(row["entry_price"], 200.0)
        self.assertAlmostEqual(row["exit_price"], 100.0)
        self.assertAlmostEqual(row["pnl"], 100.0)   # SELL: entry - exit = 200 - 100
```

- [ ] **Step 2: Run the golden test**

```bash
cd /home/user/Algo_Test_Software
python -m unittest backend.tests.test_intraday_engine.TestIntradayEngineGolden -v
```
Expected: `test_sell_hits_target ... ok`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_intraday_engine.py
git commit -m "test(intraday): golden integration test with synthetic DaySnapshot"
```

---

## Self-Review

**Spec coverage:**
- §5.2 Rust kernels: covered — Tasks 2–6 build `run_intraday_backtest` as a fat PyO3 kernel.
- §3.6 DaySnapshot format: Task 2 implements exact binary layout accessors.
- §5.3 Strategy spec: Task 3 implements all required fields (symbol, entry_time, legs, SL, target, square_off_time).
- §5.4 Arrow IPC tradesheet: Task 7 wraps output in Arrow IPC via pyarrow.
- §10.3 Perf regression test: deferred to Plan E (needs real data).
- Compilation flags `target-cpu=native`: Task 1 creates `.cargo/config.toml`.
- Multi-leg support: Plan B handles multiple legs in `run_day` (loops over `spec.legs`). Stateful exits (trailing SL) are Plan E.
- `WEEKLY` vs `MONTHLY` expiry selection: simplified heuristic (e=0/e=1). Full calendar logic is Plan E.

**No placeholders found.**

**Type consistency verified:** `Snapshot::chain_val(e,s,t,field,m)` used consistently in engine.rs and the golden test.
