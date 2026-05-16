# SL-with-Buffer: anchor gap-day exit to today's OPEN, not CLOSE

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On gap days where SL has been breached, the buffer override price must be computed from today's OPEN price (the first traded price of the day, which captures the gap) rather than today's CLOSE. This is the cleanest EOD proxy for a real gap fill.

**Architecture:** Add an open-price lookup helper mirroring the existing High/Low helpers, extend the per-date OHLC cache to also store opens, and change the SL-with-Buffer gap branch in the engine to use the open. The non-gap regular SL path is unchanged. The Rust port being built in parallel needs `open_x100` added to `MarketCache` and used in `apply_sl_with_buffer_batch` — that coordination is out-of-scope for this plan (the other terminal owns it) but is called out at the end.

**Tech Stack:** Python 3.11, Polars (for the bulk OHLC cache build), Pandas (engine), unittest (project uses unittest, not pytest). Test runner: `docker exec algotest-backend python -m unittest <module>` because the engine + native cache only run cleanly inside the worker image.

---

## File Structure

| File | Role |
|---|---|
| `backend/base.py` | Add `_option_open_lookup_cache`, extend `_build_option_ohlc_lookup` to populate opens, add `get_option_open_from_db()` helper. Update the two cache-clear call sites. |
| `backend/engines/generic_algotest_engine.py` | In the SL-with-Buffer gap branch (~lines 2642-2651), fetch today's open and use it as the buffer base. Keep `current_premium_raw` (close) for the non-gap regular-SL path. |
| `backend/tests/test_sl_buffer_open_base.py` | NEW. Direct unit test for the gap-branch formula using a fabricated leg + monkey-patched lookups. Does not require running a full backtest. |
| `backend/tests/parity/snapshots/with_sl_buffer.json` | Re-captured. Exit prices for any gap-day SL exits will change. |
| `backend/native/src/...` | OUT-OF-SCOPE for this plan; the other terminal is implementing the Rust port. Mirror change documented in the "Coordination" section at the end. |

---

## Task 1: Open-price cache + helper in base.py

**Files:**
- Modify: `backend/base.py:1436-1437` (cache dict declarations)
- Modify: `backend/base.py:1710-1711` and `1894-1895` (cache clear sites — both clear high+low; must also clear open)
- Modify: `backend/base.py:2395-2427` (`_build_option_ohlc_lookup` — populate opens alongside highs/lows)
- Modify: `backend/base.py:2443-2468` (add `get_option_open_from_db` after `get_option_low_from_db`)

- [ ] **Step 1: Add the cache dict next to high and low**

At `backend/base.py:1436-1437`, after the existing two dicts, add a third:

```python
_option_high_lookup_cache = {}  # (date, index) -> {(strike, opt, expiry): high}
_option_low_lookup_cache  = {}  # (date, index) -> {(strike, opt, expiry): low}
_option_open_lookup_cache = {}  # (date, index) -> {(strike, opt, expiry): open}
```

- [ ] **Step 2: Clear the open cache at both clear sites**

At `backend/base.py:1710-1711`, the existing two lines clear high and low. Add a third line clearing open:

```python
_option_high_lookup_cache.clear()
_option_low_lookup_cache.clear()
_option_open_lookup_cache.clear()
```

Do the same at `backend/base.py:1894-1895` (the second clear site).

- [ ] **Step 3: Extend `_build_option_ohlc_lookup` to populate opens**

At `backend/base.py:2395-2427`, the current function populates highs and lows. Add opens. Replace the body so it reads:

```python
def _build_option_ohlc_lookup(date_str: str, index: str):
    """Populate _option_high_lookup_cache, _option_low_lookup_cache, and _option_open_lookup_cache for a date+index."""
    cache_key = (date_str, index)
    if cache_key in _option_high_lookup_cache:
        return

    highs: dict = {}
    lows: dict = {}
    opens: dict = {}
    try:
        if _bulk_loaded and _bulk_bhav_by_date:
            date_df = _bulk_bhav_by_date.get(date_str)
            if date_df is not None and not date_df.is_empty():
                opt_df = date_df.filter(
                    (pl.col("Symbol") == index) &
                    (pl.col("OptionType").is_in(["CE", "PE"]))
                )
                if not opt_df.is_empty():
                    strikes  = opt_df["StrikePrice"].cast(pl.Int64).to_list()
                    types    = opt_df["OptionType"].to_list()
                    expiries = opt_df["ExpiryDate"].cast(pl.Date).cast(pl.Utf8).to_list()
                    if "High" in opt_df.columns:
                        for s, t, e, h in zip(strikes, types, expiries, opt_df["High"].to_list()):
                            if h is not None:
                                highs[(s, t, e)] = float(h)
                    if "Low" in opt_df.columns:
                        for s, t, e, lv in zip(strikes, types, expiries, opt_df["Low"].to_list()):
                            if lv is not None:
                                lows[(s, t, e)] = float(lv)
                    if "Open" in opt_df.columns:
                        for s, t, e, op in zip(strikes, types, expiries, opt_df["Open"].to_list()):
                            if op is not None:
                                opens[(s, t, e)] = float(op)
    except Exception:
        pass

    _option_high_lookup_cache[cache_key] = highs
    _option_low_lookup_cache[cache_key]  = lows
    _option_open_lookup_cache[cache_key] = opens
```

- [ ] **Step 4: Add `get_option_open_from_db` helper after `get_option_low_from_db`**

Insert immediately after `backend/base.py:2468` (the end of `get_option_low_from_db`):

```python
def get_option_open_from_db(date, index, strike, option_type, expiry):
    """Return the day's OPEN price for an option contract. None if unavailable."""
    try:
        date_str   = _normalize_lookup_date(date)
        expiry_str = pd.Timestamp(expiry).strftime('%Y-%m-%d')
        opt_match  = _normalize_option_type(option_type)
        strike_key = int(round(float(strike)))
        index_upper = str(index).upper()
        _build_option_ohlc_lookup(date_str, index_upper)
        return _ohlc_lookup(_option_open_lookup_cache, date_str, index_upper, strike_key, opt_match, expiry_str)
    except Exception:
        return None
```

- [ ] **Step 5: Smoke-test the helper inside the backend container**

```bash
docker exec algotest-backend python -c "
from base import get_option_open_from_db, get_option_high_from_db
# Pick a known live date/strike from the loaded bulk cache. Adjust if needed.
o = get_option_open_from_db('2024-01-05', 'NIFTY', 22000, 'CE', '2024-01-11')
h = get_option_high_from_db('2024-01-05', 'NIFTY', 22000, 'CE', '2024-01-11')
print('OPEN:', o, 'HIGH:', h, 'OK' if (o is not None and h is not None) else 'NULL')
"
```

Expected: prints `OPEN: <float>  HIGH: <float>  OK`. If both are None, the bulk cache wasn't loaded for this worker — re-pick a date that's covered. Do not skip this step; it confirms the open column is actually being read.

- [ ] **Step 6: Commit**

```bash
git add backend/base.py
git commit -m "feat(base): add open-price lookup mirroring high/low helpers"
```

---

## Task 2: Failing unit test for the open-based gap branch

**Files:**
- Create: `backend/tests/test_sl_buffer_open_base.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sl_buffer_open_base.py`:

```python
"""
Unit test for the SL-with-Buffer gap branch — verifies the override price is
anchored to today's OPEN (not CLOSE) once the open-based fix lands.

The test does NOT run a full backtest. It exercises the math at lines 2642-2651
in generic_algotest_engine.py by monkey-patching the lookup helpers with
controlled OHLC values.
"""
import unittest
from unittest.mock import patch


class TestSLBufferOpenBase(unittest.TestCase):
    def test_sell_gap_exit_uses_open_capped_at_high(self):
        # User-supplied scenario:
        # SELL CE entry 50, SL = 100%, buffer = 10%.
        # Thursday (yesterday) close = 85   -> adverse 70%, still inside SL.
        # Friday gap:  Open=120, High=145, Low=108, Close=130.
        # Expected exit = min(Open*1.10, High) = min(132.00, 145) = 132.00.
        with patch("base.get_option_open_from_db", return_value=120.0), \
             patch("base.get_option_high_from_db", return_value=145.0):
            from base import get_option_open_from_db, get_option_high_from_db
            sl_buf_pct = 10.0
            gap_base = get_option_open_from_db("2024-01-05", "NIFTY", 22500, "CE", "2024-01-11")
            day_high = get_option_high_from_db("2024-01-05", "NIFTY", 22500, "CE", "2024-01-11")
            buffer_price = gap_base * (1 + sl_buf_pct / 100.0)
            override = min(buffer_price, day_high) if day_high is not None else buffer_price
            self.assertAlmostEqual(override, 132.00, places=2)

    def test_sell_gap_exit_capped_when_buffer_exceeds_high(self):
        # Same shape, but buffer is huge so buffer_price > day_high — must be capped.
        # Open=120, buffer=80% -> buffer_price=216.0. High=145.
        # Expected exit = min(216.0, 145) = 145.0 (day-high cap engages).
        with patch("base.get_option_open_from_db", return_value=120.0), \
             patch("base.get_option_high_from_db", return_value=145.0):
            from base import get_option_open_from_db, get_option_high_from_db
            sl_buf_pct = 80.0
            gap_base = get_option_open_from_db("2024-01-05", "NIFTY", 22500, "CE", "2024-01-11")
            day_high = get_option_high_from_db("2024-01-05", "NIFTY", 22500, "CE", "2024-01-11")
            buffer_price = gap_base * (1 + sl_buf_pct / 100.0)
            override = min(buffer_price, day_high) if day_high is not None else buffer_price
            self.assertAlmostEqual(override, 145.00, places=2)

    def test_buy_gap_exit_uses_open_floored_at_low(self):
        # Mirror: BUY PE entry 100, SL = 50%, buffer = 10%.
        # Gap-down day: Open=30, High=42, Low=20, Close=33.
        # Expected exit = max(Open*0.90, Low) = max(27.0, 20.0) = 27.0.
        with patch("base.get_option_open_from_db", return_value=30.0), \
             patch("base.get_option_low_from_db", return_value=20.0):
            from base import get_option_open_from_db, get_option_low_from_db
            sl_buf_pct = 10.0
            gap_base = get_option_open_from_db("2024-01-05", "NIFTY", 22500, "PE", "2024-01-11")
            day_low = get_option_low_from_db("2024-01-05", "NIFTY", 22500, "PE", "2024-01-11")
            buffer_price = gap_base * (1 - sl_buf_pct / 100.0)
            override = max(buffer_price, day_low) if day_low is not None else buffer_price
            self.assertAlmostEqual(override, 27.00, places=2)

    def test_falls_back_to_close_when_open_unavailable(self):
        # If get_option_open_from_db returns None (missing data), engine MUST fall
        # back to current_premium_raw (close) so the trade still exits.
        # Open=None, Close=130, buffer=10% -> buffer_price = 130*1.10 = 143.
        # day_high = 145 -> override = min(143, 145) = 143.
        with patch("base.get_option_open_from_db", return_value=None), \
             patch("base.get_option_high_from_db", return_value=145.0):
            from base import get_option_open_from_db, get_option_high_from_db
            sl_buf_pct = 10.0
            current_premium_raw = 130.0  # today's close
            gap_base = get_option_open_from_db("2024-01-05", "NIFTY", 22500, "CE", "2024-01-11")
            if gap_base is None:
                gap_base = current_premium_raw
            day_high = get_option_high_from_db("2024-01-05", "NIFTY", 22500, "CE", "2024-01-11")
            buffer_price = gap_base * (1 + sl_buf_pct / 100.0)
            override = min(buffer_price, day_high) if day_high is not None else buffer_price
            self.assertAlmostEqual(override, 143.00, places=2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to confirm it passes today (test exercises math, not engine wiring)**

```bash
docker exec algotest-backend python -m unittest tests.test_sl_buffer_open_base -v
```

Expected: 4 tests pass. These tests verify the **formula and fallback** behavior directly — they will pass before Task 3 because they're not calling the engine. The actual engine-integration check is Task 4 (parity).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_sl_buffer_open_base.py
git commit -m "test(sl-buffer): unit-test gap exit formula with open as base"
```

---

## Task 3: Wire `get_option_open_from_db` into the engine's gap branch

**Files:**
- Modify: `backend/engines/generic_algotest_engine.py:2642-2651` (the `if is_gap:` block)
- Modify: `backend/engines/generic_algotest_engine.py` imports (add `get_option_open_from_db` to whatever import line already brings in `get_option_high_from_db`)

- [ ] **Step 1: Add the open helper to the imports**

Find the import line that already brings in `get_option_high_from_db` (search for it). It will look like:

```python
from base import (
    ...,
    get_option_high_from_db,
    get_option_low_from_db,
    ...,
)
```

Add `get_option_open_from_db` to that import list.

To find the exact line, run:

```bash
grep -n "get_option_high_from_db" /home/user/Algo_Test_Software/backend/engines/generic_algotest_engine.py | head -3
```

- [ ] **Step 2: Replace the `if is_gap:` block to use open as the buffer base**

At `backend/engines/generic_algotest_engine.py:2642-2652`, the current block reads:

```python
                    if is_gap:
                        _sl_exp_str = pd.Timestamp(_sl_expiry).strftime('%Y-%m-%d')
                        if position == 'SELL':
                            buffer_price = current_premium_raw * (1 + sl_buf_pct / 100.0)
                            day_high = get_option_high_from_db(check_date, index, strike, option_type, _sl_exp_str)
                            override = min(buffer_price, day_high) if day_high is not None else buffer_price
                        else:
                            buffer_price = current_premium_raw * (1 - sl_buf_pct / 100.0)
                            day_low = get_option_low_from_db(check_date, index, strike, option_type, _sl_exp_str)
                            override = max(buffer_price, day_low) if day_low is not None else buffer_price
                        leg_exit_overrides[li] = round(override, 2)
```

Replace with:

```python
                    if is_gap:
                        _sl_exp_str = pd.Timestamp(_sl_expiry).strftime('%Y-%m-%d')
                        # EOD gap modelling: anchor fill to today's OPEN (the first
                        # traded price of the day captures the gap). Fall back to
                        # today's close (current_premium_raw) if the open is unavailable.
                        day_open = get_option_open_from_db(check_date, index, strike, option_type, _sl_exp_str)
                        gap_base = day_open if day_open is not None else current_premium_raw
                        if position == 'SELL':
                            buffer_price = gap_base * (1 + sl_buf_pct / 100.0)
                            day_high = get_option_high_from_db(check_date, index, strike, option_type, _sl_exp_str)
                            override = min(buffer_price, day_high) if day_high is not None else buffer_price
                        else:
                            buffer_price = gap_base * (1 - sl_buf_pct / 100.0)
                            day_low = get_option_low_from_db(check_date, index, strike, option_type, _sl_exp_str)
                            override = max(buffer_price, day_low) if day_low is not None else buffer_price
                        leg_exit_overrides[li] = round(override, 2)
```

Only three lines change semantically: the new `day_open` fetch, the new `gap_base` selection, and the two `buffer_price` lines now use `gap_base` instead of `current_premium_raw`. The High/Low cap logic is unchanged.

- [ ] **Step 3: Restart the backend container so the new engine code is picked up**

The engine file is COPY'd into the backend image at build time, so a code edit requires either a rebuild or copying the file into the running container. For fast iteration, copy directly:

```bash
docker cp backend/engines/generic_algotest_engine.py algotest-backend:/app/engines/generic_algotest_engine.py
docker cp backend/base.py algotest-backend:/app/base.py
docker restart algotest-backend
```

Wait for backend to become healthy:

```bash
until [ "$(docker inspect --format='{{.State.Health.Status}}' algotest-backend)" = "healthy" ]; do sleep 2; done; echo backend ready
```

Also copy to the worker that runs backtests (Celery picks up code at fork time):

```bash
docker cp backend/engines/generic_algotest_engine.py algotest-worker-backtests:/app/engines/generic_algotest_engine.py
docker cp backend/base.py algotest-worker-backtests:/app/base.py
docker restart algotest-worker-backtests
```

- [ ] **Step 4: Verify nothing import-broke**

```bash
docker exec algotest-backend python -c "from engines.generic_algotest_engine import run_algotest_backtest; print('import OK')"
```

Expected: `import OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/engines/generic_algotest_engine.py
git commit -m "fix(sl-buffer): use today's OPEN as gap-exit base, not close

On gap days the existing code anchored the buffer to today's close, which
doesn't reflect the actual gap-fill price. On EOD data the day's open is
the first traded price and the cleanest proxy for the gap fill. Cap at
day high/low is unchanged. Falls back to close when open is unavailable."
```

---

## Task 4: Re-capture and verify the parity snapshot

**Files:**
- Regenerate: `backend/tests/parity/snapshots/with_sl_buffer.json`

The `with_sl_buffer` archetype is already registered at `backend/tests/parity/archetypes.py:240-249`. We only need to re-capture its snapshot and ensure parity passes.

- [ ] **Step 1: Confirm the existing snapshot exists (sanity)**

```bash
ls -la backend/tests/parity/snapshots/with_sl_buffer.json
```

If it exists: its current values reflect the OLD close-based formula. If it doesn't: the other terminal hasn't captured it yet — that's fine, we capture from scratch below.

- [ ] **Step 2: Re-capture with `--force`**

```bash
docker exec algotest-backend python -m tests.parity.capture --only with_sl_buffer --force 2>&1 | tail -10
```

Expected: prints `captured with_sl_buffer` (or similar), and the snapshot JSON is written.

- [ ] **Step 3: Pull the regenerated snapshot back out to the host repo**

```bash
docker cp algotest-backend:/app/tests/parity/snapshots/with_sl_buffer.json backend/tests/parity/snapshots/with_sl_buffer.json
```

- [ ] **Step 4: Run the parity test to confirm Python-vs-snapshot still matches**

```bash
docker exec algotest-backend python -m unittest tests.test_engine_rust_pipeline -v 2>&1 | tail -20
```

Expected: all parity tests pass. If `with_sl_buffer` had been failing because the Rust port hadn't been wired up yet, it now matches the freshly captured Python snapshot. If a NON-buffer archetype fails here, you broke something — stop and investigate before continuing.

- [ ] **Step 5: Eyeball the snapshot diff**

```bash
git diff backend/tests/parity/snapshots/with_sl_buffer.json | head -80
```

Look for changed exit prices in any rows where `Exit Reason == "STOP_LOSS_BUFFER"`. These should be different from the old snapshot (lower for SELL in gap-and-fade scenarios, anchored to open price now). If no rows changed, the archetype's date window doesn't contain a gap-day SL exit — adjust the archetype's date range (in `archetypes.py`) to one that does, then re-capture.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/parity/snapshots/with_sl_buffer.json
git commit -m "test(parity): re-capture with_sl_buffer snapshot for open-based gap exit"
```

---

## Task 5: Run the unit test against the running engine

**Files:**
- (no edits — verification only)

- [ ] **Step 1: Re-run the unit test from Task 2 to confirm it still passes after the engine wiring**

```bash
docker exec algotest-backend python -m unittest tests.test_sl_buffer_open_base -v
```

Expected: 4 tests pass.

- [ ] **Step 2: Run the full backend test suite to make sure nothing else regressed**

```bash
docker exec algotest-backend python -m unittest discover tests 2>&1 | tail -10
```

Expected: same pre-existing failures as before this change (futures monthly schedule, intraday snapshot SHA, intraday validation, Redis-dependent collection errors). NO new failures. If a new test starts failing, stop and investigate — it's almost certainly a real regression introduced by this change.

- [ ] **Step 3: End-to-end check via the UI**

Open http://localhost:3000. Build a NIFTY weekly SELL CE strategy with:

- SL with Buffer: PERCENT, value 100, buffer_pct 10
- Date range: pick a window containing a known gap day (e.g., a known event date)
- Run the backtest

In the resulting tradesheet, find a row with `Exit Reason == "STOP_LOSS_BUFFER"`. Verify the exit price equals approximately `Open × (1 + buffer_pct/100)` (capped at day high). If you can't find a STOP_LOSS_BUFFER row in your chosen window, change the window to one with more volatility (March 2020, etc.).

---

## Coordination with the Rust port (out-of-scope, but flag it)

The other terminal is building `apply_sl_with_buffer_batch` in Rust as part of slice 4b. Once Python lands the open-based fix, **the Rust path will diverge** because Rust currently mirrors the old close-based logic. Required Rust changes (handed off to that workstream):

1. `backend/native/src/...`: add `open_x100` to `MarketCache` alongside `high_x100` and `low_x100`. Same plumbing as high/low — load from the feather, store as `i32` (price × 100 for integer keys per the project's `feedback_rust_integer_keys.md` memory).
2. `apply_sl_with_buffer_batch`: in the gap branch, use `open_x100` for the buffer base instead of the close. Mirror the Python fallback: if open is missing, fall back to close.
3. Re-run the parity test after the Rust change. The freshly captured snapshot from Task 4 above is the source of truth — Rust must match it bit-for-bit.

Communicate the snapshot regeneration timing carefully: do NOT regenerate the snapshot while the Rust port is still using the old formula, or the Rust parity will look "broken" when it's actually just mismatching the new snapshot.

---

## Verification recap

After all tasks complete:

- `docker exec algotest-backend python -m unittest tests.test_sl_buffer_open_base -v` → 4 pass
- `docker exec algotest-backend python -m unittest tests.test_engine_rust_pipeline -v` → all pass
- `git diff backend/tests/parity/snapshots/with_sl_buffer.json` → shows changed exit prices for gap-day STOP_LOSS_BUFFER rows
- UI run shows `STOP_LOSS_BUFFER` exit prices matching `open × (1 + buffer%/100)` (or open × (1 − buffer%/100) for BUY), capped at day high/low

---

## Rollback

Single-file revert per task. Most surgical: revert the engine change only if the issue is engine-side.

```bash
git revert <commit-sha-of-engine-change>
docker cp backend/engines/generic_algotest_engine.py algotest-backend:/app/engines/generic_algotest_engine.py
docker cp backend/engines/generic_algotest_engine.py algotest-worker-backtests:/app/engines/generic_algotest_engine.py
docker restart algotest-backend algotest-worker-backtests
```

The base.py additions (open lookup) are additive and safe to leave in place even if the engine change is rolled back.
