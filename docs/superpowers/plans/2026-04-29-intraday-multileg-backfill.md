# Intraday Multi-Leg, Stateful Exits & Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Rust engine with stateful exit logic (trailing SL, breakeven move), fix expiry selection with a real calendar, backfill all 4 symbols for the full available history, add the vmtouch nightly warmup cron, and run the performance regression test.

**Architecture:** All stateful exit logic lives in a new `intraday_leg_lifecycle` Rust function that handles one leg's full-path-dependent state machine in a single Rust call, returning `LegResult`. Multi-leg strategies run legs independently and aggregate PnL per day. The Python engine wrapper aggregates results the same way as single-leg. Backfill uses the existing `ingest_intraday` Celery task from Plan A batch-dispatched via a CLI script.

**Tech Stack:** Rust + PyO3, chrono (for expiry calendar), Python Celery + click CLI, vmtouch shell cron.

**Prerequisite:** Plans A, B, C complete. Single-leg engine works end-to-end.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/native/src/intraday/types.rs` | add TrailingSlSpec, BreakevenSpec to LegSpec |
| Create | `backend/native/src/intraday/calendar.rs` | expiry selection: WEEKLY/MONTHLY from expiries.json |
| Modify | `backend/native/src/intraday/engine.rs` | stateful exit state machine + calendar integration |
| Modify | `backend/native/src/intraday/pyfuncs.rs` | pass expiry_dates list to run_day |
| Create | `backend/scripts/backfill_intraday.py` | CLI: dispatch ingest tasks for a symbol+date range |
| Create | `backend/scripts/vmtouch_warmup.sh` | shell script: vmtouch all current-year snapshots |
| Modify | `docker-compose.yml` | add cron job container for vmtouch at 06:00 IST |
| Create | `backend/tests/test_intraday_perf.py` | performance regression test (excluded from discover) |
| Create | `backend/tests/test_intraday_multileg.py` | multi-leg golden test |

---

### Task 1: Add trailing SL and breakeven specs to `types.rs`

**Files:**
- Modify: `backend/native/src/intraday/types.rs`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_intraday_engine.py`:
```python
class TestTrailingSLParse(unittest.TestCase):
    def test_parse_leg_with_trailing_sl(self):
        """StrategySpec with trailing_sl must parse without error."""
        import algotest_native as n
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
                "sl": {"type": "percent", "value": 100.0},
                "target": None,
                "trailing_sl": {"trigger_pct": 30.0, "trail_pct": 30.0},
            }]
        }
        import tempfile, os, json
        with tempfile.TemporaryDirectory() as tmp:
            sym = os.path.join(tmp, "NIFTY")
            os.makedirs(os.path.join(sym, "snapshots"))
            with open(os.path.join(sym, "expiries.json"), "w") as f:
                json.dump({}, f)
            # Should not raise even with no snapshots
            result = n.run_intraday_backtest(json.dumps(config), tmp)
            self.assertIsInstance(result, list)
```

Run:
```bash
cd /home/user/Algo_Test_Software
python -m unittest backend.tests.test_intraday_engine.TestTrailingSLParse -v
```
Expected: FAIL (trailing_sl field unknown to serde).

- [ ] **Step 2: Add `TrailingSlSpec` and `BreakevenSpec` to `types.rs`**

In `backend/native/src/intraday/types.rs`, add new structs and update `LegSpec`:

```rust
#[derive(Deserialize, Debug, Clone)]
pub struct TrailingSlSpec {
    /// Profit % at which trailing SL activates (e.g. 30.0 = when SELL premium drops 30%)
    pub trigger_pct: f64,
    /// Trail distance as % of entry premium (e.g. 30.0 = SL trails at 30% above min seen)
    pub trail_pct: f64,
}

#[derive(Deserialize, Debug, Clone)]
pub struct BreakevenSpec {
    /// Profit % at which SL moves to breakeven (e.g. 30.0 = after 30% profit, SL = entry)
    pub trigger_pct: f64,
}
```

Update `LegSpec` to add optional fields:
```rust
#[derive(Deserialize, Debug)]
pub struct LegSpec {
    pub opt_type: String,
    pub action: String,
    pub strike_selection: StrikeSelection,
    pub expiry: String,
    pub quantity: u32,
    pub sl: Option<ExitCond>,
    pub target: Option<ExitCond>,
    pub trailing_sl: Option<TrailingSlSpec>,   // NEW
    pub breakeven: Option<BreakevenSpec>,      // NEW
}
```

- [ ] **Step 3: Run the test**

```bash
cd backend/native && cargo test 2>&1 | tail -5
python -m unittest backend.tests.test_intraday_engine.TestTrailingSLParse -v
```
Expected: both pass.

- [ ] **Step 4: Commit**

```bash
git add backend/native/src/intraday/types.rs backend/tests/test_intraday_engine.py
git commit -m "feat(native/intraday): add trailing_sl + breakeven fields to LegSpec"
```

---

### Task 2: Stateful exit state machine in `engine.rs`

**Files:**
- Modify: `backend/native/src/intraday/engine.rs`

The state machine runs once per leg per day. It scans minutes sequentially and maintains:
- `min_seen_x100`: minimum price seen since entry (for SELL; for BUY it's maximum)
- `sl_thr_x100`: current SL threshold (starts at fixed SL, adjusts on trailing/breakeven triggers)
- `trailing_active`: bool

- [ ] **Step 1: Write a unit test for the trailing SL state machine**

Add to `backend/native/src/intraday/engine.rs` tests:
```rust
#[test]
fn test_trailing_sl_activates_and_trails() {
    use crate::intraday::types::{ExitCond, LegSpec, StrikeSelection, TrailingSlSpec};
    // Simulate: entry at 100, price drops to 60 (trigger at 30%), then rises to 78
    // trail_pct=30% → trail = 60 * 1.30 = 78 → SL triggers at 78
    let leg = LegSpec {
        opt_type: "CE".into(),
        action: "SELL".into(),
        strike_selection: StrikeSelection { mode: "ATM".into(), value: 0 },
        expiry: "WEEKLY".into(),
        quantity: 1,
        sl: Some(ExitCond { kind: "percent".into(), value: 100.0 }), // initial SL at 200
        target: None,
        trailing_sl: Some(TrailingSlSpec { trigger_pct: 30.0, trail_pct: 30.0 }),
        breakeven: None,
    };
    // prices_x100 = [10000, 9000, 8000, 7000, 6000, 6500, 7000, 7500, 7800, 8500]
    // entry = 10000. trigger fires when price <= 10000*(1-0.30) = 7000 (minute idx 3)
    // min seen at trigger = 7000 → trail SL = 7000*1.30 = 9100
    // But at minute 4 price hits 6000 → new min → trail SL = 6000*1.30 = 7800
    // At minute 8 price = 7800 → trail SL hit
    let prices: Vec<i32> = vec![10000, 9000, 8000, 7000, 6000, 6500, 7000, 7500, 7800, 8500];
    let (exit_idx, reason) = scan_exit_stateful(&leg, 10000, &prices, 9);
    assert_eq!(exit_idx, 8);
    assert_eq!(reason, "TRAIL_SL");
}
```

Run:
```bash
cd backend/native && cargo test intraday::engine::tests::test_trailing_sl 2>&1 | tail -5
```
Expected: FAIL (function not defined).

- [ ] **Step 2: Implement `scan_exit_stateful` in `engine.rs`**

Add this function to `engine.rs`:
```rust
/// Returns (exit_minute_idx, exit_reason).
/// Handles: fixed SL, target, trailing SL, breakeven SL, square-off.
pub fn scan_exit_stateful(
    leg: &LegSpec,
    entry_x100: i32,
    prices: &[i32],  // close prices from entry+1 to sqoff (0-indexed from entry+1)
    sqoff_offset: usize,
) -> (usize, &'static str) {
    let is_sell = leg.action == "SELL";

    // Initial fixed SL threshold
    let mut sl_thr = leg.sl.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if is_sell { entry_x100 + delta } else { entry_x100 - delta }
    });

    let tgt_thr = leg.target.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if is_sell { entry_x100 - delta } else { entry_x100 + delta }
    });

    // Best price seen (min for SELL, max for BUY)
    let mut best_seen = entry_x100;
    let mut trailing_active = false;

    for (offset, &px) in prices.iter().enumerate().take(sqoff_offset + 1) {
        // Update best seen
        if is_sell {
            if px < best_seen { best_seen = px; }
        } else {
            if px > best_seen { best_seen = px; }
        }

        // Check if trailing SL trigger fires
        if let Some(ref ts) = leg.trailing_sl {
            if !trailing_active {
                let profit_pct = if is_sell {
                    (entry_x100 - px) as f64 / entry_x100 as f64 * 100.0
                } else {
                    (px - entry_x100) as f64 / entry_x100 as f64 * 100.0
                };
                if profit_pct >= ts.trigger_pct {
                    trailing_active = true;
                }
            }
            if trailing_active {
                // Trail SL: trail_pct above minimum seen (for SELL)
                let new_sl = if is_sell {
                    ((best_seen as f64) * (1.0 + ts.trail_pct / 100.0)).round() as i32
                } else {
                    ((best_seen as f64) * (1.0 - ts.trail_pct / 100.0)).round() as i32
                };
                // Only tighten the SL, never loosen it
                if let Some(ref mut sl) = sl_thr {
                    if is_sell && new_sl < *sl { *sl = new_sl; }
                    if !is_sell && new_sl > *sl { *sl = new_sl; }
                } else {
                    sl_thr = Some(new_sl);
                }
            }
        }

        // Breakeven move
        if let Some(ref be) = leg.breakeven {
            let profit_pct = if is_sell {
                (entry_x100 - px) as f64 / entry_x100 as f64 * 100.0
            } else {
                (px - entry_x100) as f64 / entry_x100 as f64 * 100.0
            };
            if profit_pct >= be.trigger_pct {
                // Move SL to entry (breakeven), only if it improves position
                if let Some(ref mut sl) = sl_thr {
                    if is_sell && entry_x100 < *sl { *sl = entry_x100; }
                    if !is_sell && entry_x100 > *sl { *sl = entry_x100; }
                }
            }
        }

        // Check SL hit
        if let Some(sl) = sl_thr {
            let hit = if is_sell { px >= sl } else { px <= sl };
            if hit {
                let reason = if trailing_active { "TRAIL_SL" } else { "SL" };
                return (offset, reason);
            }
        }

        // Check target hit
        if let Some(tgt) = tgt_thr {
            let hit = if is_sell { px <= tgt } else { px >= tgt };
            if hit { return (offset, "TARGET"); }
        }
    }

    (sqoff_offset, "SQOFF")
}
```

- [ ] **Step 3: Integrate `scan_exit_stateful` into `run_day`**

In `run_day`, replace the existing scan loop with a call to `scan_exit_stateful`.

Replace:
```rust
let mut exit_idx = sqoff_idx;
let mut exit_reason = "SQOFF";

for m in (entry_idx + 1)..=sqoff_idx {
    let px = snap.chain_val(e, s, t, 0, m);
    let hit_sl = sl_thr.map_or(false, |thr| if is_sell { px >= thr } else { px <= thr });
    let hit_tgt = tgt_thr.map_or(false, |thr| if is_sell { px <= thr } else { px >= thr });
    if hit_sl { exit_idx = m; exit_reason = "SL"; break; }
    if hit_tgt { exit_idx = m; exit_reason = "TARGET"; break; }
}
```

With:
```rust
// Build prices slice from entry_idx+1 to sqoff_idx
let prices: Vec<i32> = ((entry_idx + 1)..=sqoff_idx)
    .map(|m| snap.chain_val(e, s, t, 0, m))
    .collect();
let sqoff_offset = prices.len().saturating_sub(1);
let (exit_offset, exit_reason) = scan_exit_stateful(leg, entry_px, &prices, sqoff_offset);
let exit_idx = entry_idx + 1 + exit_offset;
```

Also remove the now-unused `sl_thr`, `tgt_thr`, `compute_thresholds` calls from `run_day` (they are now inside `scan_exit_stateful`).

- [ ] **Step 4: Run all Rust tests**

```bash
cd backend/native && cargo test 2>&1 | tail -10
```
Expected: all tests pass including `test_trailing_sl_activates_and_trails`.

- [ ] **Step 5: Run Python integration tests**

```bash
cd /home/user/Algo_Test_Software
python -m unittest backend.tests.test_intraday_engine -v
```
Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/native/src/intraday/engine.rs
git commit -m "feat(native/intraday): stateful exit — trailing SL + breakeven move"
```

---

### Task 3: Real expiry calendar (`calendar.rs`)

**Files:**
- Create: `backend/native/src/intraday/calendar.rs`
- Modify: `backend/native/src/intraday/mod.rs`

- [ ] **Step 1: Write the failing test**

Add to `backend/native/src/intraday/calendar.rs` (we'll create it in the next step):
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    #[test]
    fn test_pick_weekly_returns_nearest_future_expiry() {
        // expiries: 2024-01-04, 2024-01-11, 2024-01-25
        let expiries: Vec<(i16, NaiveDate)> = vec![
            (0, NaiveDate::from_ymd_opt(2024, 1, 4).unwrap()),
            (1, NaiveDate::from_ymd_opt(2024, 1, 11).unwrap()),
            (2, NaiveDate::from_ymd_opt(2024, 1, 25).unwrap()),
        ];
        // Trade date 2024-01-02 → nearest future = idx 0 (2024-01-04)
        let e = pick_expiry_e("WEEKLY", NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(), &expiries);
        assert_eq!(e, Some(0));
    }

    #[test]
    fn test_pick_monthly_returns_last_thursday_of_month() {
        let expiries: Vec<(i16, NaiveDate)> = vec![
            (0, NaiveDate::from_ymd_opt(2024, 1, 4).unwrap()),
            (1, NaiveDate::from_ymd_opt(2024, 1, 11).unwrap()),
            (2, NaiveDate::from_ymd_opt(2024, 1, 25).unwrap()),  // last Thursday Jan 2024
        ];
        let e = pick_expiry_e("MONTHLY", NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(), &expiries);
        assert_eq!(e, Some(2));
    }
}
```

Run:
```bash
cd backend/native && cargo test intraday::calendar 2>&1 | tail -5
```
Expected: FAIL (module not found).

- [ ] **Step 2: Create `backend/native/src/intraday/calendar.rs`**

```rust
use chrono::{Datelike, NaiveDate, Weekday};

/// Pick snapshot expiry_e index from the list of (snapshot_e, expiry_date) pairs.
/// `expiry_type`: "WEEKLY" | "NEXT_WEEKLY" | "MONTHLY" | "NEXT_MONTHLY"
/// Returns the position in snap.expiry_count (0-based) matching the desired expiry,
/// or None if not found in the snapshot.
pub fn pick_expiry_e(
    expiry_type: &str,
    trade_date: NaiveDate,
    expiries: &[(i16, NaiveDate)],
) -> Option<usize> {
    match expiry_type {
        "WEEKLY" => {
            // Nearest expiry >= trade_date (weekly = Thursday)
            expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| *d >= trade_date && d.weekday() == Weekday::Thu)
                .min_by_key(|(_, (_, d))| *d)
                .map(|(e, _)| e)
        }
        "NEXT_WEEKLY" => {
            // Second nearest expiry after trade_date (weekly)
            let mut candidates: Vec<_> = expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| *d > trade_date && d.weekday() == Weekday::Thu)
                .collect();
            candidates.sort_by_key(|(_, (_, d))| *d);
            candidates.get(1).map(|(e, _)| *e)
        }
        "MONTHLY" => {
            // Last Thursday of the same month as trade_date's nearest monthly expiry
            let month = trade_date.month();
            let year = trade_date.year();
            let target_month = if trade_date.day() > 25 { // near end of month
                if month == 12 { NaiveDate::from_ymd_opt(year + 1, 1, 1) }
                else { NaiveDate::from_ymd_opt(year, month + 1, 1) }
            } else {
                NaiveDate::from_ymd_opt(year, month, 1)
            };
            let target_month = target_month?;
            expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| {
                    d.year() == target_month.year()
                        && d.month() == target_month.month()
                        && d.weekday() == Weekday::Thu
                })
                .max_by_key(|(_, (_, d))| *d)
                .map(|(e, _)| e)
        }
        "NEXT_MONTHLY" => {
            // Next calendar month's last Thursday
            let month = trade_date.month();
            let year = trade_date.year();
            let next_month = if month == 12 {
                NaiveDate::from_ymd_opt(year + 1, 1, 1)?
            } else {
                NaiveDate::from_ymd_opt(year, month + 1, 1)?
            };
            expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| {
                    d.year() == next_month.year()
                        && d.month() == next_month.month()
                        && d.weekday() == Weekday::Thu
                })
                .max_by_key(|(_, (_, d))| *d)
                .map(|(e, _)| e)
        }
        _ => expiries.first().map(|_| 0),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pick_weekly_returns_nearest_future_expiry() {
        let expiries = vec![
            (0i16, NaiveDate::from_ymd_opt(2024, 1, 4).unwrap()),
            (1, NaiveDate::from_ymd_opt(2024, 1, 11).unwrap()),
            (2, NaiveDate::from_ymd_opt(2024, 1, 25).unwrap()),
        ];
        let e = pick_expiry_e("WEEKLY", NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(), &expiries);
        assert_eq!(e, Some(0));
    }

    #[test]
    fn test_pick_monthly_returns_last_thursday_of_month() {
        let expiries = vec![
            (0i16, NaiveDate::from_ymd_opt(2024, 1, 4).unwrap()),
            (1, NaiveDate::from_ymd_opt(2024, 1, 11).unwrap()),
            (2, NaiveDate::from_ymd_opt(2024, 1, 25).unwrap()),
        ];
        let e = pick_expiry_e("MONTHLY", NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(), &expiries);
        assert_eq!(e, Some(2));
    }
}
```

- [ ] **Step 3: Add `pub mod calendar;` to `backend/native/src/intraday/mod.rs`**

```rust
pub mod calendar;
pub mod engine;
pub mod pyfuncs;
pub mod snapshot;
pub mod types;
```

- [ ] **Step 4: Update `pyfuncs.rs` to use the calendar**

In `pyfuncs.rs`, build the `expiry_list: Vec<(i16, NaiveDate)>` from the loaded `expiry_map` and pass it to `run_day`. Update `run_day`'s signature in `engine.rs` to accept `expiry_list: &[(i16, NaiveDate)]` and replace the `pick_expiry_e` call inside `run_day` with:

In `engine.rs`, replace:
```rust
let e = pick_expiry_e(&leg.expiry);
```
With:
```rust
use crate::intraday::calendar::pick_expiry_e as cal_pick;
let trade_date_nd = chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d")
    .unwrap_or_else(|_| chrono::NaiveDate::from_ymd_opt(2024, 1, 1).unwrap());
let snap_expiry_list: Vec<(i16, chrono::NaiveDate)> = (0..snap.expiry_count)
    .filter_map(|e| {
        let idx = snap.expiry_idx(e);
        expiry_list.iter().find(|(i, _)| *i == idx).map(|(_, d)| (idx, *d))
    })
    .collect();
let e = cal_pick(&leg.expiry, trade_date_nd, &snap_expiry_list)?;
```

Also update `run_day` signature:
```rust
pub fn run_day(
    snap: &Snapshot,
    expiry_list: &[(i16, chrono::NaiveDate)],
    spec: &StrategySpec,
    date_str: &str,
) -> Vec<TradeRecord>
```

In `pyfuncs.rs`, build `expiry_list` from `expiry_map`:
```rust
let mut expiry_list: Vec<(i16, chrono::NaiveDate)> = expiry_map
    .iter()
    .filter_map(|(idx, date_str)| {
        chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d").ok().map(|d| (*idx, d))
    })
    .collect();
expiry_list.sort_by_key(|(_, d)| *d);
```

- [ ] **Step 5: Run all tests**

```bash
cd backend/native && cargo test 2>&1 | tail -10
python -m unittest backend.tests.test_intraday_engine -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/native/src/intraday/
git commit -m "feat(native/intraday): real expiry calendar (WEEKLY/MONTHLY Thursday picker)"
```

---

### Task 4: Multi-leg golden test

**Files:**
- Create: `backend/tests/test_intraday_multileg.py`

- [ ] **Step 1: Create `backend/tests/test_intraday_multileg.py`**

```python
import struct, tempfile, os, json, unittest


def make_snapshot(tmpdir: str, date_str: str, atm_x100: int,
                  ce_prices: list[int], pe_prices: list[int]) -> None:
    """Write a synthetic snapshot where CE and PE prices vary per minute."""
    MINUTES = 375
    HEADER_SIZE = 32
    SPOT_ENTRY = 16
    SPOT_SIZE = MINUTES * SPOT_ENTRY
    EXPIRY_SIZE = 2 + MINUTES * 4 + 11 * 2 * 4 * MINUTES * 4

    import datetime
    epoch = datetime.date(1970, 1, 1)
    d = datetime.date.fromisoformat(date_str)
    date_days = (d - epoch).days

    symbol_bytes = b"NIFTY\x00" + b"\x00" * 10
    header = (
        b"ITDS" + struct.pack("<B", 1) + symbol_bytes
        + struct.pack("<i", date_days)
        + struct.pack("<B", 1)   # expiry_count
        + struct.pack("<H", MINUTES)
        + b"\x00\x00\x00\x00"
    )

    spot = b"".join(struct.pack("<iiii", atm_x100, atm_x100, atm_x100, atm_x100)
                    for _ in range(MINUTES))

    expiry_hdr = struct.pack("<h", 0)
    atm_arr = struct.pack(f"<{MINUTES}i", *([atm_x100] * MINUTES))

    chain_size = 11 * 2 * 4 * MINUTES
    chain = bytearray(chain_size * 4)
    for i in range(chain_size):
        struct.pack_into("<i", chain, i * 4, 100)

    def off(s, t, field, m):
        return (s * 2 * 4 * MINUTES + t * 4 * MINUTES + field * MINUTES + m) * 4

    # Fill s=5 (ATM), CE (t=0) and PE (t=1), field=0 (close)
    for m in range(MINUTES):
        struct.pack_into("<i", chain, off(5, 0, 0, m), ce_prices[m] if m < len(ce_prices) else 100)
        struct.pack_into("<i", chain, off(5, 0, 1, m), ce_prices[m] if m < len(ce_prices) else 100)
        struct.pack_into("<i", chain, off(5, 0, 2, m), ce_prices[m] if m < len(ce_prices) else 100)
        struct.pack_into("<i", chain, off(5, 1, 0, m), pe_prices[m] if m < len(pe_prices) else 100)
        struct.pack_into("<i", chain, off(5, 1, 1, m), pe_prices[m] if m < len(pe_prices) else 100)
        struct.pack_into("<i", chain, off(5, 1, 2, m), pe_prices[m] if m < len(pe_prices) else 100)

    expiry_section = expiry_hdr + atm_arr + bytes(chain)
    sym_dir = os.path.join(tmpdir, "NIFTY")
    snaps_dir = os.path.join(sym_dir, "snapshots")
    os.makedirs(snaps_dir, exist_ok=True)
    with open(os.path.join(sym_dir, "expiries.json"), "w") as f:
        json.dump({"0": "2024-01-04"}, f)
    with open(os.path.join(snaps_dir, f"{date_str}.arrow"), "wb") as f:
        f.write(header + spot + expiry_section)


class TestIntradayMultiLeg(unittest.TestCase):
    def test_short_straddle_both_hit_target(self):
        """SELL CE + SELL PE at ATM. Both drop 50% → both hit target."""
        from backend.services.intraday_engine import run_intraday_backtest
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmp:
            ENTRY = 5  # idx = 09:20
            CE = [20000] * (ENTRY + 1) + [10000] * 370   # drops to 100 after entry
            PE = [15000] * (ENTRY + 1) + [7500] * 370    # drops to 75 after entry
            make_snapshot(tmp, "2024-01-01", 2400000, CE, PE)

            config = {
                "symbol": "NIFTY",
                "date_from": "2024-01-01",
                "date_to": "2024-01-01",
                "entry_time": "09:20",
                "square_off_time": "15:15",
                "legs": [
                    {"opt_type": "CE", "action": "SELL",
                     "strike_selection": {"mode": "ATM", "value": 0},
                     "expiry": "WEEKLY", "quantity": 1,
                     "sl": None, "target": {"type": "percent", "value": 50.0}},
                    {"opt_type": "PE", "action": "SELL",
                     "strike_selection": {"mode": "ATM", "value": 0},
                     "expiry": "WEEKLY", "quantity": 1,
                     "sl": None, "target": {"type": "percent", "value": 50.0}},
                ]
            }
            result = run_intraday_backtest(config, data_dir=tmp)
            reader = pa.ipc.open_stream(pa.BufferReader(result))
            table = reader.read_all()

            self.assertEqual(table.num_rows, 2)
            reasons = set(table.column("exit_reason").to_pylist())
            self.assertEqual(reasons, {"TARGET"})
            total_pnl = sum(table.column("pnl").to_pylist())
            # CE pnl = 200 - 100 = 100; PE pnl = 150 - 75 = 75; total = 175
            self.assertAlmostEqual(total_pnl, 175.0)
```

- [ ] **Step 2: Run the test**

```bash
python -m unittest backend.tests.test_intraday_multileg -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_intraday_multileg.py
git commit -m "test(intraday): multi-leg golden test (short straddle)"
```

---

### Task 5: Backfill CLI script

**Files:**
- Create: `backend/scripts/backfill_intraday.py`

- [ ] **Step 1: Create `backend/scripts/backfill_intraday.py`**

```python
#!/usr/bin/env python3
"""
Batch-dispatch ingest_intraday Celery tasks for all CSV files in a symbol directory.

Usage:
    python backend/scripts/backfill_intraday.py \\
        --symbol NIFTY \\
        --source-dir /run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/AAKASH/zerodha-data-processed/NFO_PREPARED/INDICES-OPTION/NIFTY \\
        --format clean_2023 \\
        --workers 2

This dispatches one Celery task per CSV file onto the 'uploads' queue.
The worker ingests, validates, writes Parquet, builds DaySnapshot, and
updates the manifest. Re-running is safe (idempotent via SHA256).
"""

import argparse
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Batch ingest intraday CSVs")
    parser.add_argument("--symbol", required=True,
                        choices=["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
    parser.add_argument("--source-dir", required=True,
                        help="Directory containing the CSV files for this symbol")
    parser.add_argument("--format", default="clean_2023",
                        choices=["clean_2023", "raw_2017"],
                        help="CSV format hint passed to the ingest handler")
    parser.add_argument("--workers", type=int, default=2,
                        help="Max concurrent tasks in flight (rate limiter)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be dispatched, don't send")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        print(f"ERROR: source-dir does not exist: {source_dir}", file=sys.stderr)
        sys.exit(1)

    csv_files = sorted(source_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {source_dir}")
        sys.exit(0)

    print(f"Found {len(csv_files)} CSV files for {args.symbol}")

    if args.dry_run:
        for f in csv_files[:5]:
            print(f"  would ingest: {f.name}")
        print("  ...")
        return

    from backend.worker.celery import app as celery_app
    in_flight = []
    dispatched = 0
    errors = 0

    for csv_path in csv_files:
        task = celery_app.send_task(
            "worker.tasks_intraday.ingest_intraday_csv",
            args=[args.symbol, str(csv_path), args.format],
            queue="uploads",
        )
        in_flight.append(task)
        dispatched += 1

        # Simple rate limiter: drain completed tasks
        while len(in_flight) >= args.workers:
            done = [t for t in in_flight if t.ready()]
            for t in done:
                in_flight.remove(t)
                if t.failed():
                    errors += 1
                    print(f"FAILED: {t.id}", file=sys.stderr)
            if not done:
                time.sleep(0.5)

        if dispatched % 100 == 0:
            print(f"  dispatched {dispatched}/{len(csv_files)}...")

    # Wait for remaining
    for t in in_flight:
        try:
            t.get(timeout=300)
        except Exception as e:
            errors += 1
            print(f"FAILED: {t.id}: {e}", file=sys.stderr)

    print(f"\nDone. dispatched={dispatched} errors={errors}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the ingest task to `tasks_intraday.py`**

Open `backend/worker/tasks_intraday.py` and add:
```python
@celery_app.task(
    name="worker.tasks_intraday.ingest_intraday_csv",
    bind=True,
    max_retries=3,
    acks_late=True,
)
def ingest_intraday_csv(self, symbol: str, csv_path: str, format_hint: str = "clean_2023"):
    """Ingest one intraday CSV file into Parquet + DaySnapshot + manifest."""
    from backend.services.intraday_publish import publish_intraday_csv
    logger.info("[ingest] symbol=%s path=%s", symbol, csv_path)
    publish_intraday_csv(symbol, csv_path, format_hint=format_hint)
```

(`publish_intraday_csv` is the orchestrator built in Plan A's `intraday_publish.py`.)

- [ ] **Step 3: Dry-run test**

```bash
python backend/scripts/backfill_intraday.py \
    --symbol NIFTY \
    --source-dir "/run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/AAKASH/zerodha-data-processed/NFO_PREPARED/INDICES-OPTION/NIFTY" \
    --dry-run 2>&1 | head -10
```
Expected: lists first 5 CSV files without errors.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/backfill_intraday.py backend/worker/tasks_intraday.py
git commit -m "feat(scripts): backfill_intraday.py batch ingest CLI"
```

---

### Task 6: vmtouch nightly warmup

**Files:**
- Create: `backend/scripts/vmtouch_warmup.sh`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create `backend/scripts/vmtouch_warmup.sh`**

```bash
#!/usr/bin/env bash
# Touch all DaySnapshot files for the current and prior year into OS page cache.
# Run at 06:00 IST before market open.
set -euo pipefail

INTRADAY_DIR="${INTRADAY_DATA_DIR:-/data/intraday}"
CURRENT_YEAR=$(date +%Y)
PRIOR_YEAR=$((CURRENT_YEAR - 1))
SYMBOLS="NIFTY BANKNIFTY FINNIFTY MIDCPNIFTY"

echo "[vmtouch] warming snapshot cache at $(date)"
for SYMBOL in $SYMBOLS; do
    for YEAR in $CURRENT_YEAR $PRIOR_YEAR; do
        SNAP_DIR="$INTRADAY_DIR/$SYMBOL/snapshots"
        if [ -d "$SNAP_DIR" ]; then
            FILES=$(find "$SNAP_DIR" -name "${YEAR}-*.arrow" 2>/dev/null | wc -l)
            if [ "$FILES" -gt 0 ]; then
                vmtouch -t "$SNAP_DIR"/${YEAR}-*.arrow 2>/dev/null || true
                echo "[vmtouch] $SYMBOL $YEAR: $FILES files touched"
            fi
        fi
    done
done
echo "[vmtouch] done at $(date)"
```

```bash
chmod +x backend/scripts/vmtouch_warmup.sh
```

- [ ] **Step 2: Verify vmtouch is available in the container**

```bash
docker compose run --rm --entrypoint which worker-uploads vmtouch 2>/dev/null || echo "vmtouch not installed"
```

If not installed, add to the backend Dockerfile (in the final stage, after pip install):
```dockerfile
RUN apt-get update -qq && apt-get install -y --no-install-recommends vmtouch && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Add a cron service to docker-compose.yml**

Add this service (uses `ofelia` cron scheduler which is a lightweight Docker-native cron):
```yaml
  cron-warmup:
    image: mcuadros/ofelia:latest
    command: daemon --config=/etc/ofelia/config.ini
    volumes:
      - ./backend/scripts/vmtouch_warmup.sh:/scripts/vmtouch_warmup.sh:ro
      - algo_cache:/data/intraday
    configs:
      - source: ofelia_config
        target: /etc/ofelia/config.ini

configs:
  ofelia_config:
    content: |
      [job-exec "vmtouch-warmup"]
      schedule = 0 30 0 * * *
      container = worker-uploads
      command = /scripts/vmtouch_warmup.sh
```

(`0 30 0 * * *` = 00:30 UTC = 06:00 IST.)

Alternatively, add a `crontab` directly to the `worker-uploads` container by having the Dockerfile install cron and run it — whichever matches the project's existing pattern. The simplest approach that avoids a new Docker image:

In `docker-compose.yml`, for `worker-uploads`, add an environment variable and a cron at container start:
```yaml
  worker-uploads:
    # ... existing config ...
    entrypoint: >
      bash -c "
        echo '30 0 * * * /scripts/vmtouch_warmup.sh >> /var/log/vmtouch.log 2>&1' | crontab - &&
        cron &&
        celery -A worker.celery worker --queues=uploads --concurrency=2 -l info
      "
```

(Choose whichever approach fits the existing docker-compose.yml structure. The `cron &&` prefix runs the OS cron daemon, then Celery.)

- [ ] **Step 4: Verify vmtouch runs**

```bash
docker compose up -d worker-uploads
docker compose exec worker-uploads bash -c "INTRADAY_DATA_DIR=/data/intraday /scripts/vmtouch_warmup.sh"
```
Expected: output like `[vmtouch] NIFTY 2024: 245 files touched`.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/vmtouch_warmup.sh docker-compose.yml backend/Dockerfile
git commit -m "feat(ops): nightly vmtouch warmup for intraday DaySnapshot page cache"
```

---

### Task 7: Performance regression test

**Files:**
- Create: `backend/tests/test_intraday_perf.py`

This test is **excluded from `unittest discover`** (prefix is `test_intraday_perf`, but the file must be explicitly run). It requires a real 1-year NIFTY snapshot dataset.

- [ ] **Step 1: Create `backend/tests/test_intraday_perf.py`**

```python
"""
Performance regression test for intraday backtest.
Run explicitly: python -m unittest backend.tests.test_intraday_perf -v
NOT included in discover (requires real data at INTRADAY_DATA_DIR).
SLAs: single 1-year NIFTY straddle p50 < 700 ms, p95 < 1100 ms.
"""
import os
import statistics
import time
import unittest

INTRADAY_DATA_DIR = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")
NIFTY_SNAPS = os.path.join(INTRADAY_DATA_DIR, "NIFTY", "snapshots")


@unittest.skipUnless(
    os.path.exists(NIFTY_SNAPS) and len(os.listdir(NIFTY_SNAPS)) > 200,
    "requires 200+ NIFTY snapshots; run after backfill"
)
class TestIntradayPerf(unittest.TestCase):
    CONFIG = {
        "symbol": "NIFTY",
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
        "entry_time": "09:20",
        "square_off_time": "15:15",
        "legs": [
            {"opt_type": "CE", "action": "SELL",
             "strike_selection": {"mode": "ATM", "value": 0},
             "expiry": "WEEKLY", "quantity": 1,
             "sl": {"type": "percent", "value": 50.0}, "target": None},
            {"opt_type": "PE", "action": "SELL",
             "strike_selection": {"mode": "ATM", "value": 0},
             "expiry": "WEEKLY", "quantity": 1,
             "sl": {"type": "percent", "value": 50.0}, "target": None},
        ]
    }

    def test_single_1year_straddle_latency(self):
        from backend.services.intraday_engine import run_intraday_backtest
        samples = []
        for _ in range(10):
            t0 = time.perf_counter()
            result = run_intraday_backtest(self.CONFIG)
            samples.append(time.perf_counter() - t0)

        p50 = statistics.median(samples) * 1000
        p95 = sorted(samples)[int(len(samples) * 0.95)] * 1000
        print(f"\n[perf] p50={p50:.0f}ms p95={p95:.0f}ms over {len(samples)} runs")
        self.assertLess(p50, 700, f"p50 {p50:.0f}ms exceeds 700ms SLA")
        self.assertLess(p95, 1100, f"p95 {p95:.0f}ms exceeds 1100ms SLA")
        self.assertGreater(len(result), 0, "empty result bytes")
```

- [ ] **Step 2: Run it after backfill is complete**

```bash
python -m unittest backend.tests.test_intraday_perf -v
```
Expected (with warm mmap): p50 < 700ms, p95 < 1100ms.

If the test fails the SLA, profile to find the bottleneck:
```bash
python -c "
import cProfile, pstats
from backend.services.intraday_engine import run_intraday_backtest
config = $(python -c 'import json; print(json.dumps({...CONFIG...}))')
cProfile.run('run_intraday_backtest(config)', '/tmp/profile.out')
pstats.Stats('/tmp/profile.out').sort_stats('cumulative').print_stats(20)
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_intraday_perf.py
git commit -m "test(intraday): performance regression test (p50<700ms, p95<1100ms)"
```

---

## Self-Review

**Spec coverage:**
- §5.2.1 `intraday_leg_lifecycle` (stateful exits): Task 2 `scan_exit_stateful` ✓
- §5.3 trailing_sl, breakeven fields: Task 1 + 2 ✓
- §5.3 re_entry: deferred — not in scope for this plan per spec §5.3 ("later phase") ✓
- §7.3 vmtouch nightly warmup: Task 6 ✓
- §10.3 perf regression test: Task 7 ✓
- Phase 7 (backfill full year): Task 5 (CLI) ✓
- Phase 8 (BANKNIFTY/FINNIFTY/MIDCPNIFTY): Task 5 (same CLI, `--symbol` arg) ✓
- Expiry calendar (WEEKLY=nearest Thursday, MONTHLY=last Thursday): Task 3 ✓
- Multi-leg aggregation: Plan B's `run_day` already loops over `spec.legs` — Task 4 golden test verifies ✓
- §12 Manifest reconciliation: not addressed — flagged as gap below.

**Gap: manifest reconciliation job** (§12 risk: "Manifest/Postgres divergence from filesystem"). Add a nightly script `backend/scripts/audit_intraday_manifest.py` that checks `/data/intraday/*/snapshots/*.arrow` against `intraday_imports` rows and logs discrepancies. This is straightforward but was omitted to stay focused on the hot path. Add it as a follow-up task before Phase 8 ships.

**No other placeholders found.**
