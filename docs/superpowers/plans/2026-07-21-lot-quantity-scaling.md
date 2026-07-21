# Lot-Quantity Scaling of Net P&L Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a user-entered per-leg lot quantity scale Net P&L everywhere in the EOD backtester, matching the intraday product's rule `net = points × lots`.

**Architecture:** Net P&L is currently stored in per-unit premium points and `lots` never multiplies it. We apply `× leg.lots` at the exact point where each leg's entry/exit prices are first converted into a P&L value — in the Rust engine (authoritative), the three Python tradesheet builders, the legacy Python engine, the multi-leg engine, and the charges recalc. `lot_size` is deliberately untouched: it stays per-index and feeds only the display `Qty` column. Everything downstream (NAV, Max DD, CAGR, WOW/MOM, patch-wise, optimizer objectives) reads Net P&L and scales automatically with no code change.

**Tech Stack:** Rust (PyO3 + Rayon) in `backend/native/`, Python 3 / FastAPI / Polars, `unittest` (not pytest).

## Global Constraints

- **Scale exactly once.** Apply `× lots` only where points are *first* converted into a leg P&L. **Never** scale a value read back out of a stored `pnl` / `ce_pnl` / `pe_pnl` / `net_pnl` field, and never scale an aggregate that sums already-scaled legs. Double-scaling yields `lots²` and is the primary risk in this plan.
- **`lot_size` is not part of P&L.** It stays per-index and feeds only `Qty = lots × lot_size`. Authoritative table is `get_lot_size_for_index` at `backend/services/index_metadata.py:86` — **NIFTY = 65** (flat, no date branching), **MIDCPNIFTY = 75** before 2024-11-20 then 120 / 140 / 120. Do not hardcode 75 for NIFTY anywhere.
- **Per-leg.** Each leg scales by its own `leg.lots`. Legs may carry different lot counts (a 2×1 ratio spread is valid). Index-agnostic.
- **lots = 1 must stay byte-identical.** Multiplying by 1 is a no-op; any diff at 1 lot is a bug.
- **Do not touch the Overall SL/Target threshold machinery.** `backend/services/engine_rust.py:240-268` and `backend/native/src/lib.rs:1920` are explicitly out of scope per the spec.
- **MAE/MFE DO scale — see Task 7.** (This constraint originally said the opposite. That was wrong: `summary_metrics.rs:336` compounds NAV by the now-scaled `% P&L` while `:362` applies MAE to that same NAV, so leaving MAE unscaled understates Live DD / Max DD by ~1/lots.) Tasks 1–6 do NOT touch MAE/MFE; Task 7 scales them at the column-write sites only. `native/src/mae.rs` itself stays a pure ratio.
- **Do not scale entry/exit prices.** They are per-unit by definition.
- **Tests are `unittest`, not pytest:** `python -m unittest backend.tests.test_x`
- **Rebuilding needs sudo:** build artifacts are root-owned; rebuild via `sudo ./start.sh`.
- **Never restart workers while jobs are running.** Check the queue, `algotest:mem_gate` and active tasks are empty first — killing a job strands a mem-gate reservation for ~40 minutes.

**Reference spec:** `docs/superpowers/specs/2026-07-21-lot-quantity-scaling-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/native/src/simulate.rs` | Authoritative per-leg pricing + trade-total aggregation | Scale `net_pnl` in `simulate_one` |
| `backend/services/engine_rust.py` | Live Python tradesheet builders (3 sites) | Scale `net_pnl` / `per_leg_pnl` |
| `backend/engines/generic_multi_leg.py` | Multi-leg engine rows | Scale `leg_pnl`; fix `Qty` |
| `backend/engines/generic_algotest_engine.py` | Legacy engine behind `/algotest` | Scale 8 P&L sites |
| `backend/routers/backtest.py` | Charge-adjusted P&L recalc | Scale after charge adjustment |
| `backend/tests/test_lot_quantity_scaling.py` | **New** — all scaling tests | Create |

---

### Task 1: Scale Net P&L in the Rust engine

`simulate_one` computes each leg's P&L; `simulate_trades_batch_core` then sums per-leg values into the lowest-leg row (`simulate.rs:1764` `entry.0 += r.net_pnl`). Because the aggregate sums already-scaled legs, scaling inside `simulate_one` is correct and does **not** double-scale.

**Files:**
- Modify: `backend/native/src/simulate.rs:1645-1656`
- Test: `backend/tests/test_lot_quantity_scaling.py`

**Interfaces:**
- Consumes: `TradeSpec.lots: i64` (field declared `simulate.rs:193`, parsed `:1600` via `extract_i64(dict, "lots")`)
- Produces: `simulate_trades_batch(specs)` returns dicts whose `net_pnl` is `points × lots`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_lot_quantity_scaling.py`:

```python
"""Lot-quantity scaling: Net P&L = points x lots, applied per leg.

lot_size is NOT part of P&L - it feeds only the display Qty column.
At lots=1 every value must be byte-identical to the pre-change engine.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import json
from pathlib import Path


class TestRustNetPnlScalesWithLots(unittest.TestCase):
    """Rust net_pnl scales by the leg's own lots; prices must not.

    Reuses the captured parity snapshot + its cache-warming helper, the same
    way tests/test_simulate_rust.py does. A synthetic spec cannot work here:
    simulate_one prices against the in-process Rust cache, which is cold in a
    bare unittest process, so every row comes back `missing`.
    """

    def setUp(self):
        try:
            import algotest_native  # type: ignore
            self.native = algotest_native
        except ImportError:
            self.skipTest("algotest_native not installed in this environment")

        from tests.test_simulate_rust import _bulk_load_for_snapshot, _trade_to_spec

        snap_path = (
            Path(__file__).parent / "parity" / "snapshots" / "single_leg_ce_atm_sell.json"
        )
        if not snap_path.exists():
            self.skipTest("snapshot single_leg_ce_atm_sell not captured yet")

        snap = json.loads(snap_path.read_text())
        self.payload = snap["payload"]
        self.trades = snap["trades"]
        self.assertGreater(len(self.trades), 0)

        try:
            _bulk_load_for_snapshot(self.payload)
        except Exception as exc:
            self.skipTest(f"could not load market data: {exc}")
        self._to_spec = _trade_to_spec

    def _run(self, lots: int) -> list:
        """Price the whole snapshot with every leg forced to `lots`."""
        specs = []
        for t in self.trades:
            spec = self._to_spec(t, self.payload)
            spec["lots"] = lots
            specs.append(spec)
        # simulate_trades_batch returns a FLAT list (the (results, bad_trades)
        # tuple belongs to the private _core fn, not the PyO3 wrapper).
        return list(self.native.simulate_trades_batch(specs))

    def _paired(self):
        one, two = self._run(1), self._run(2)
        pairs = [(a, b) for a, b in zip(one, two) if not a["missing"]]
        self.assertGreater(len(pairs), 0, "snapshot produced no priced rows")
        return pairs

    def test_net_pnl_doubles_when_lots_doubles(self):
        for a, b in self._paired():
            self.assertAlmostEqual(b["net_pnl"], a["net_pnl"] * 2, places=2)

    def test_prices_do_not_scale(self):
        for a, b in self._paired():
            self.assertEqual(b["entry_price"], a["entry_price"])
            self.assertEqual(b["exit_price"], a["exit_price"])


if __name__ == "__main__":
    unittest.main()
```

`_bulk_load_for_snapshot` calls `bulk_load_options`, which **reads** only. It does
not call `build_cache`, so it does not write the shared feather — this is safe
under the no-warm-cache-for-testing rule.

- [ ] **Step 2: Run the test to verify it fails**

`algotest_native` exists only inside the `backend` container, whose `/app` root
IS the `backend` package — so the module path there is `tests.…`, not
`backend.tests.…`:

```bash
docker compose exec -T backend python -m unittest tests.test_lot_quantity_scaling.TestRustNetPnlScalesWithLots -v
```

Expected: `test_net_pnl_doubles_when_lots_doubles` FAILS — the two values are equal, not 2×. (`test_prices_do_not_scale` passes already; that is correct, it is a regression guard.)

If both tests SKIP, market data is absent or the snapshot is missing. Resolve that before proceeding — a skipped test proves nothing.

- [ ] **Step 3: Apply the change**

In `backend/native/src/simulate.rs`, replace lines 1645-1656:

```rust
    // Engine convention: Net P&L is in PREMIUM POINTS scaled by LOTS, not rupees.
    // For SELL: net = (entry - exit) * lots   (we receive entry, pay exit)
    // For BUY : net = (exit - entry) * lots
    // lot_size is NOT part of P&L — it is informational and downstream uses it
    // for the display Qty column (lots × lot_size) and Turnover only. This
    // mirrors the intraday engine (iengine/src/engine.rs:2549).
    // simulate_trades_batch_core sums these already-scaled per-leg values into
    // the trade total, so the multiplier must NOT be re-applied there.
    let is_sell = s.position.trim().eq_ignore_ascii_case("SELL");
    let lots = s.lots as f64;
    let net_pnl = if is_sell {
        round2((entry_px - exit_px) * lots)
    } else {
        round2((exit_px - entry_px) * lots)
    };
```

- [ ] **Step 4: Rebuild the native extension**

Run: `sudo ./start.sh`

Expected: build completes, containers healthy. Confirm no jobs were running first:

```bash
docker compose exec -T redis redis-cli hgetall algotest:mem_gate
docker compose exec -T redis redis-cli llen backtests
```

Expected: both empty / `0` before you rebuild.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestRustNetPnlScalesWithLots -v`

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/native/src/simulate.rs backend/tests/test_lot_quantity_scaling.py
git commit -m "feat(native): scale Net P&L by per-leg lots (points x lots)"
```

---

### Task 2: Scale Net P&L in the Python tradesheet builders

Three sites in `engine_rust.py` recompute per-leg P&L from entry/exit prices. All three are first-conversion points, so all three scale.

**Files:**
- Modify: `backend/services/engine_rust.py:1446-1450`, `:2497-2501`, `:3251-3253`
- Test: `backend/tests/test_lot_quantity_scaling.py`

**Interfaces:**
- Consumes: each row's `lots` field (already present — read at `:3258` for `qty`)
- Produces: tradesheet records whose `Net P&L`, `CE P&L`, `PE P&L`, `FUT P&L` are `points × lots`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_lot_quantity_scaling.py`:

```python
class TestTradesheetRecordsScale(unittest.TestCase):
    """priced_to_tradesheet_records must scale per-leg P&L by that leg's lots."""

    def _rows(self, lots_leg1: int, lots_leg2: int) -> list:
        return [
            {
                "trade_id": "1", "leg_id": 1, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "CE", "strike": 21500.0,
                "position": "SELL", "entry_price": 150.0, "exit_price": 90.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": lots_leg1, "lot_size": 65, "net_pnl": 60.0 * lots_leg1,
            },
            {
                "trade_id": "1", "leg_id": 2, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "PE", "strike": 21500.0,
                "position": "SELL", "entry_price": 130.0, "exit_price": 145.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": lots_leg2, "lot_size": 65, "net_pnl": -15.0 * lots_leg2,
            },
        ]

    def test_per_leg_pnl_scales_by_that_legs_lots(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")

        # CE: (150 - 90) x 2 lots = 120 ; PE: (130 - 145) x 1 lot = -15
        self.assertAlmostEqual(ce["CE P&L"], 120.0, places=4)
        self.assertAlmostEqual(pe["PE P&L"], -15.0, places=4)

    def test_qty_is_lots_times_lot_size(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")
        self.assertEqual(ce["Qty"], 130)   # 2 lots x 65
        self.assertEqual(pe["Qty"], 65)    # 1 lot  x 65

    def test_prices_stay_per_unit(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)
        ce = next(r for r in recs if r["Type"] == "CE")
        self.assertEqual(ce["Entry Price"], 150.0)
        self.assertEqual(ce["Exit Price"], 90.0)
```

Signature is `priced_to_tradesheet_records(priced, payload, lot_size)` — three positionals, verified at `backend/services/engine_rust.py:3141-3145`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestTradesheetRecordsScale -v`

Expected: `test_per_leg_pnl_scales_by_that_legs_lots` FAILS with `60.0 != 120.0`. The other two PASS (regression guards).

- [ ] **Step 3: Apply the change at site 1 (`:1446-1450`)**

Replace:

```python
            # P&L per unit — no lot_size multiplication (matches Python engine convention).
            net_pnl = round(
                (entry_price - exit_price) if position == "SELL" else (exit_price - entry_price),
                4,
            )
```

with:

```python
            # P&L = POINTS x LOTS. lot_size is NOT a factor — it feeds only the
            # display Qty column. Mirrors native/src/simulate.rs:1652.
            _lots = float(leg.get("lots") or 1)
            net_pnl = round(
                ((entry_price - exit_price) if position == "SELL" else (exit_price - entry_price))
                * _lots,
                4,
            )
```

The enclosing loop is `for leg_id, leg in enumerate(legs_src, start=1)` at `:1358`, so `leg` is the per-leg dict. Do not read `lots` from a trade-level or payload-level variable — it must be the leg's own.

- [ ] **Step 4: Apply the change at site 2 (`:2497-2501`)**

Replace:

```python
                net_pnl = round(
                    (entry_price - exit_price) if position == "SELL" else (exit_price - entry_price),
                    4,
                )
```

with:

```python
                # P&L = POINTS x LOTS (see native/src/simulate.rs:1652).
                _lots = float(leg.get("lots") or 1)
                net_pnl = round(
                    ((entry_price - exit_price) if position == "SELL" else (exit_price - entry_price))
                    * _lots,
                    4,
                )
```

The enclosing loop here is `for leg_id, leg in enumerate(legs_src, start=1)` at `:2451` — same `leg` name as site 1.

- [ ] **Step 5: Apply the change at site 3 (`:3251-3253`)**

Replace:

```python
        per_leg_pnl = round(
            (entry_px - exit_px) if position == "SELL" else (exit_px - entry_px), 4
        )
```

with:

```python
        # P&L = POINTS x LOTS (see native/src/simulate.rs:1652). Uses THIS leg's
        # lots so ratio spreads (leg 1 = 2 lots, leg 2 = 1 lot) price correctly.
        _leg_lots = float(row.get("lots") or 1)
        per_leg_pnl = round(
            ((entry_px - exit_px) if position == "SELL" else (exit_px - entry_px)) * _leg_lots, 4
        )
```

Do **not** touch `net_pnl = float(row.get("net_pnl") or 0.0)` at `:3242` — that value arrives already scaled from Rust (Task 1). Scaling it again is the `lots²` bug.

- [ ] **Step 6: Update the stale convention comments**

At `backend/services/engine_rust.py:3792` and `:6632`, replace any wording asserting P&L carries no quantity multiplication with:

```python
# P&L is POINTS x LOTS (lot_size excluded — display Qty only).
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling -v`

Expected: all tests in both classes PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_lot_quantity_scaling.py
git commit -m "feat(engine): scale tradesheet per-leg P&L by lots"
```

---

### Task 3: Scale P&L and fix `Qty` in the multi-leg engine

This engine writes `"Qty": leg.lots` (raw lots) where every other writer emits `lots × lot_size`. Because the charges recalc divides by `Qty` to derive per-unit charges, the current value inflates them by a factor of `lot_size` — 65× on NIFTY, 75–140× on MIDCPNIFTY depending on date. Fixed here alongside the scaling change, per the spec.

**Files:**
- Modify: `backend/engines/generic_multi_leg.py:347-362` (options), `:419-434` (futures)
- Test: `backend/tests/test_lot_quantity_scaling.py`

**Interfaces:**
- Consumes: `leg.lots` (attribute on the `Leg` model, `backend/strategies/strategy_types.py:140`), and the index lot size from `get_lot_size`
- Produces: leg rows with `Net P&L = points × lots` and `Qty = lots × lot_size`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_lot_quantity_scaling.py`:

Write a **behavioural** test — assert on the rows `_process_trade_legs` actually
emits, not on its source text. Patch `_get_bhav_data` so the test needs no
market data.

Open `backend/engines/generic_multi_leg.py:236` (`_get_bhav_data`) and
`:129` (`_get_all_strikes_for_expiry`) to see the exact DataFrame columns
expected, then build minimal frames matching that shape. Construct a
`StrategyDefinition` with two option legs — leg 1 at `lots=2`, leg 2 at
`lots=1` — and call `_process_trade_legs` directly.

Assert exactly these, which are what the task changes:

```python
        # leg 1: 2 lots x NIFTY lot size 65
        self.assertEqual(rows[0]["Qty"], 130)
        # leg 2: 1 lot x 65
        self.assertEqual(rows[1]["Qty"], 65)
        # P&L scales by that leg's own lots, not by lot_size
        self.assertAlmostEqual(rows[0]["Net P&L"], leg1_points * 2, places=2)
        self.assertAlmostEqual(rows[1]["Net P&L"], leg2_points * 1, places=2)
```

where `leg1_points` / `leg2_points` are the entry-minus-exit differences your
fixture frames imply. Name the class `TestMultiLegQtyConvention`.

Do **not** fall back to an `inspect.getsource` string assertion — it passes on
code that is broken in every other respect.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestMultiLegQtyConvention -v`

Expected: FAIL on the `Qty` assertion (`1 != 130` — raw lots emitted) and on the P&L assertion (unscaled). If the test SKIPs or errors on fixture setup, fix the fixture before proceeding — a skip proves nothing.

- [ ] **Step 3: Apply the change to the options branch (`:347-362`)**

Replace:

```python
            if leg.position == PositionType.BUY:
                leg_pnl = round(leg_exit_price - leg_entry_price, 2)
            else:
                leg_pnl = round(leg_entry_price - leg_exit_price, 2)

            leg_rows.append(
                {
                    "Type": leg.option_type.value,
                    "Strike": selected_strike,
                    "B/S": leg.position.value,
                    "Qty": leg.lots,
```

with:

```python
            # P&L = POINTS x LOTS (see native/src/simulate.rs:1652).
            _leg_lots = int(getattr(leg, "lots", 1) or 1)
            if leg.position == PositionType.BUY:
                leg_pnl = round((leg_exit_price - leg_entry_price) * _leg_lots, 2)
            else:
                leg_pnl = round((leg_entry_price - leg_exit_price) * _leg_lots, 2)

            leg_rows.append(
                {
                    "Type": leg.option_type.value,
                    "Strike": selected_strike,
                    "B/S": leg.position.value,
                    "Qty": _leg_lots * _lot_size,
```

- [ ] **Step 4: Apply the change to the futures branch (`:419-434`)**

Replace:

```python
            if leg.position == PositionType.BUY:
                leg_pnl = round(leg_exit_price - leg_entry_price, 2)
            else:
                leg_pnl = round(leg_entry_price - leg_exit_price, 2)

            leg_rows.append(
                {
                    "Type": "FUT",
                    "Strike": "",
                    "B/S": leg.position.value,
                    "Qty": leg.lots,
```

with:

```python
            # P&L = POINTS x LOTS (see native/src/simulate.rs:1652).
            _leg_lots = int(getattr(leg, "lots", 1) or 1)
            if leg.position == PositionType.BUY:
                leg_pnl = round((leg_exit_price - leg_entry_price) * _leg_lots, 2)
            else:
                leg_pnl = round((leg_entry_price - leg_exit_price) * _leg_lots, 2)

            leg_rows.append(
                {
                    "Type": "FUT",
                    "Strike": "",
                    "B/S": leg.position.value,
                    "Qty": _leg_lots * _lot_size,
```

- [ ] **Step 5: Resolve `_lot_size` in both branches**

Both branches now reference `_lot_size`. `generic_multi_leg.py` has no lot-size concept today (verified: zero `lot_size` occurrences), so add it.

Both leg sites live in `_process_trade_legs(strategy_def, index_name, from_date, to_date, curr_expiry, fut_expiry, entry_spot)` at `:275`, so `index_name` and `from_date` are in scope. Insert immediately after `leg_rows = []` at `:295`:

```python
    from engines.generic_algotest_engine import get_lot_size
    _lot_size = int(get_lot_size(index_name, from_date) or 1)
```

`get_lot_size(index, entry_date)` is defined at `backend/engines/generic_algotest_engine.py:235`. Import it inside the function rather than at module scope — `generic_multi_leg` does not currently import from `generic_algotest_engine`, and a module-level import risks a circular import.

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestMultiLegQtyConvention -v`

Expected: PASS

- [ ] **Step 7: Run the full existing suite for regressions**

Run: `python -m unittest discover backend/tests`

Expected: no NEW failures versus the pre-change baseline. Record the baseline before this task if you have not already:

```bash
git stash && python -m unittest discover backend/tests 2>&1 | tail -5 && git stash pop
```

- [ ] **Step 8: Commit**

```bash
git add backend/engines/generic_multi_leg.py backend/tests/test_lot_quantity_scaling.py
git commit -m "fix(multi-leg): scale P&L by lots and emit Qty as lots x lot_size"
```

---

### Task 4: Scale P&L in the legacy Python engine

`run_algotest_backtest` (`generic_algotest_engine.py:3283`) is still reachable through the legacy `/algotest` endpoint (`backend/routers/backtest.py:770`). If it is skipped, the two engines disagree at lots ≥ 2.

Eight sites. `lots` is already in scope at `:4551` (covering `:4745` and `:4970`). It is **not** in scope at `:5668` — see Step 6.

**Files:**
- Modify: `backend/engines/generic_algotest_engine.py` — `:1343`, `:1372`, `:1643`, `:2139`, `:2266`, `:4745-4747`, `:4970-4972`, `:5665-5668`
- Test: `backend/tests/test_lot_quantity_scaling.py`

**Interfaces:**
- Consumes: `leg_config['lots']` / `leg.get('lots')` / `tleg.get('lots')` depending on site
- Produces: `pnl`, `ce_pnl`, `pe_pnl`, `leg_pnl` values already scaled by lots

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_lot_quantity_scaling.py`:

```python
class TestLegacyEngineScales(unittest.TestCase):
    """_recalc_leg_pnl scales the leg's stored pnl by that leg's lots."""

    def test_recalc_leg_pnl_scales_option_leg(self):
        from engines.generic_algotest_engine import _recalc_leg_pnl

        tleg = {
            "segment": "OPTION", "option_type": "CE", "position": "SELL",
            "strike": 21500, "entry_premium": 150.0, "lots": 2,
        }
        try:
            _recalc_leg_pnl(
                tleg,
                "2024-01-04",      # leg_exit_date
                "NIFTY",           # index
                "2024-01-04",      # expiry_date
                65,                # lot_size (NIFTY)
                21500.0,           # fallback_spot
                0.0,               # slippage_pct
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Narrow on purpose: a bare `except Exception` here would swallow a
            # genuine regression in _recalc_leg_pnl and report it as a skip.
            self.skipTest(f"_recalc_leg_pnl needs market data: {exc}")

        exit_prem = tleg["exit_premium"]
        self.assertAlmostEqual(tleg["pnl"], (150.0 - exit_prem) * 2, places=2)
        self.assertAlmostEqual(tleg["ce_pnl"], tleg["pnl"], places=2)
```

Signature verified at `backend/engines/generic_algotest_engine.py:1313`:
`_recalc_leg_pnl(tleg, leg_exit_date, index, expiry_date, lot_size, fallback_spot, slippage_pct=0.0)`.

Note it already receives `lot_size` — that stays untouched and unused for P&L. The multiplier comes from `tleg['lots']`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestLegacyEngineScales -v`

Expected: FAIL — `pnl` is the unscaled points value, not 2×. If it SKIPS, fix the call signature first; a skip proves nothing.

- [ ] **Step 3: Scale both `_recalc_leg_pnl` branches (`:1343`, `:1372`)**

In the OPTION branch, replace:

```python
        # P&L in POINTS (no quantity multiplication)
        if position == 'BUY':
            tleg['pnl'] = adjusted_exit - ep
        else:  # SELL
            tleg['pnl'] = ep - adjusted_exit
```

with:

```python
        # P&L = POINTS x LOTS (see native/src/simulate.rs:1652)
        _lots = int(tleg.get('lots', 1) or 1)
        if position == 'BUY':
            tleg['pnl'] = (adjusted_exit - ep) * _lots
        else:  # SELL
            tleg['pnl'] = (ep - adjusted_exit) * _lots
```

Apply the identical transformation to the FUTURE branch at `:1372` (same four lines, same replacement).

The `ce_pnl` / `pe_pnl` assignments immediately below each branch read `tleg['pnl']` and therefore inherit the scaling. Leave them alone.

- [ ] **Step 4: Scale the lazy-leg site (`:1643`)**

Replace:

```python
    pnl = (exit_premium - entry_premium) if position == 'BUY' else (entry_premium - exit_premium)
```

with:

```python
    # P&L = POINTS x LOTS (see native/src/simulate.rs:1652)
    _lots = int(leg_config.get('lots', 1) or 1)
    pnl = ((exit_premium - entry_premium) if position == 'BUY'
           else (entry_premium - exit_premium)) * _lots
```

Read the enclosing function signature to confirm the config dict holding this leg's `lots` is named `leg_config`; if it is named otherwise, use that name.

- [ ] **Step 5: Scale the two re-entry sites (`:2139`, `:2266`)**

At `:2139` (futures re-entry) replace:

```python
            leg_pnl = (exit_price_fut - entry_price_fut) if position == 'BUY' else (entry_price_fut - exit_price_fut)
```

with:

```python
            # P&L = POINTS x LOTS (see native/src/simulate.rs:1652)
            _lots = int(leg_config.get('lots', 1) or 1)
            leg_pnl = ((exit_price_fut - entry_price_fut) if position == 'BUY'
                       else (entry_price_fut - exit_price_fut)) * _lots
```

At `:2266` (options re-entry) replace:

```python
            leg_pnl = (exit_premium - entry_premium) if position == 'BUY' else (entry_premium - exit_premium)
```

with:

```python
            # P&L = POINTS x LOTS (see native/src/simulate.rs:1652)
            _lots = int(leg_config.get('lots', 1) or 1)
            leg_pnl = ((exit_premium - entry_premium) if position == 'BUY'
                       else (entry_premium - exit_premium)) * _lots
```

- [ ] **Step 6: Scale the two main trade-build sites (`:4745`, `:4970`)**

`lots` is already a local at `:4551`, so use it directly.

At `:4745` (futures) replace:

```python
                        if position == 'BUY':
                            leg_pnl = exit_price - entry_price
                        else:  # SELL
                            leg_pnl = entry_price - exit_price
```

with:

```python
                        # P&L = POINTS x LOTS (see native/src/simulate.rs:1652)
                        if position == 'BUY':
                            leg_pnl = (exit_price - entry_price) * lots
                        else:  # SELL
                            leg_pnl = (entry_price - exit_price) * lots
```

At `:4970` (options) replace:

```python
                        # Calculate P&L in POINTS (no quantity multiplication)
                        # CE P&L = Entry - Exit for CALL SELL, Exit - Entry for CALL BUY
                        # PE P&L = Entry - Exit for PUT SELL, Exit - Entry for PUT BUY
                        if position == 'BUY':
                            leg_pnl = exit_premium - entry_premium
                        else:  # SELL
                            leg_pnl = entry_premium - exit_premium
```

with:

```python
                        # Calculate P&L = POINTS x LOTS (see native/src/simulate.rs:1652)
                        # CE P&L = Entry - Exit for CALL SELL, Exit - Entry for CALL BUY
                        # PE P&L = Entry - Exit for PUT SELL, Exit - Entry for PUT BUY
                        if position == 'BUY':
                            leg_pnl = (exit_premium - entry_premium) * lots
                        else:  # SELL
                            leg_pnl = (entry_premium - exit_premium) * lots
```

The `ce_pnl` / `pe_pnl` assignments below `:4972` read `leg_pnl` and inherit the scaling. Leave them alone.

- [ ] **Step 7: Scale ONLY the fallback at the display site (`:5665-5668`)**

This is the double-scaling trap. `leg_pnl = leg.get('pnl')` reads a value **already scaled** by Step 3 — it must not be scaled again. Only the `if leg_pnl is None` fallback recomputes from prices and needs the multiplier. Note `lots` is not yet defined here (it is read at `:5695`), so read it inline.

Replace:

```python
                    leg_pnl     = leg.get('pnl')
                    if leg_pnl is None:
                        direction = -1 if position == 'BUY' else 1
                        leg_pnl = direction * (entry_price - exit_price)
```

with:

```python
                    # NOTE: leg['pnl'] is ALREADY scaled by lots (_recalc_leg_pnl
                    # and the trade-build sites apply it). Do NOT scale it again.
                    # Only the recompute fallback below needs the multiplier.
                    leg_pnl     = leg.get('pnl')
                    if leg_pnl is None:
                        direction = -1 if position == 'BUY' else 1
                        _lots = int(leg.get('lots', 1) or 1)
                        leg_pnl = direction * (entry_price - exit_price) * _lots
```

- [ ] **Step 8: Audit the options branch below `:5674` for the same trap**

Read `backend/engines/generic_algotest_engine.py:5674-5700`. For each place it derives `leg_pnl` / `ce_pnl_val` / `pe_pnl_val`:
- reads from `leg['pnl']` / `leg['ce_pnl']` / `leg['pe_pnl']` → **leave unchanged** (already scaled)
- recomputes from `entry_premium` / `exit_premium` → **multiply by `int(leg.get('lots', 1) or 1)`**

- [ ] **Step 9: Run the test to verify it passes**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestLegacyEngineScales -v`

Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add backend/engines/generic_algotest_engine.py backend/tests/test_lot_quantity_scaling.py
git commit -m "feat(engine): scale legacy engine P&L by per-leg lots"
```

---

### Task 5: Scale the charge-adjusted P&L

`_calculate_fo_charges` divides by `Qty` to derive ₹/unit and adjusts the per-unit `Entry Price` / `Exit Price`. That stays correct. The final P&L must then be multiplied by `lots` so it lands in the same unit as the engine's.

`lots` is not directly on the row — derive it as `Qty / lot_size`.

**Files:**
- Modify: `backend/routers/backtest.py:317-320`
- Test: `backend/tests/test_lot_quantity_scaling.py`

**Interfaces:**
- Consumes: row `Qty` (= `lots × lot_size`) and the index lot size
- Produces: charge-adjusted `Net P&L` in points × lots

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_lot_quantity_scaling.py`:

```python
class TestChargeAdjustedPnlScales(unittest.TestCase):
    """With charges OFF, the recalc must reproduce points x lots."""

    def test_recalc_scales_by_lots_when_charges_disabled(self):
        from routers.backtest import _recalculate_trade_prices

        rows = [{
            "Trade": "1", "Leg": 1, "Index": "NIFTY", "Type": "CE", "B/S": "SELL",
            "Strike": 21500, "Qty": 130, "Entry Price": 150.0, "Exit Price": 90.0,
            "Net P&L": 120.0, "Entry Spot": 21500.0,
            "Entry Date": "2024-01-01", "Exit Date": "2024-01-04",
        }]
        out = _recalculate_trade_prices(rows, charges_enabled=False)
        # (150 - 90) x 2 lots = 120, NOT 60
        self.assertAlmostEqual(out[0]["Net P&L"], 120.0, places=2)
```

Signature verified at `backend/routers/backtest.py:245`:
`_recalculate_trade_prices(trades, charges_enabled=False)`. There is no `index` or `slippage_pct` parameter — slippage is already baked into each row's prices by the engine, and the index must be read off the row.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestChargeAdjustedPnlScales -v`

Expected: FAIL with `60.0 != 120.0`

- [ ] **Step 3: Apply the change (`:317-320`)**

Replace:

```python
            # ── Step 3: P&L (per-unit points) ────────────────────────────
            if position == 'BUY':
                leg_pnl = new_exit - new_entry
            else:
                leg_pnl = new_entry - new_exit
```

with:

```python
            # ── Step 3: P&L = POINTS x LOTS ──────────────────────────────
            # Charges were already folded into the PER-UNIT prices above, so
            # the points difference is charge-correct; scale it by lots to
            # match the engine (native/src/simulate.rs:1652). Qty is
            # lots x lot_size, so lots = Qty / lot_size.
            _row_lot_size = int(get_lot_size(row.get('Index') or 'NIFTY',
                                             row.get('Entry Date')) or 1) or 1
            _qty_raw = _normalize_recalc_numeric(row.get('Qty'))
            _qty = float(_qty_raw) if _qty_raw and _qty_raw > 0 else float(_row_lot_size)
            _row_lots = max(1, int(round(_qty / _row_lot_size)))
            if position == 'BUY':
                leg_pnl = (new_exit - new_entry) * _row_lots
            else:
                leg_pnl = (new_entry - new_exit) * _row_lots
```

This block is self-contained — it re-derives `_qty` via `_normalize_recalc_numeric` (defined at `:229`) rather than depending on the `qty` local at `:300-301`, which exists only inside `if charges_enabled:`. Do not hoist that local; leave the charge branch untouched.

- [ ] **Step 3b: Add the `get_lot_size` import**

`backend/routers/backtest.py:5` currently reads:

```python
from engines.generic_algotest_engine import run_algotest_backtest, _calculate_fo_charges
```

Change it to:

```python
from engines.generic_algotest_engine import run_algotest_backtest, _calculate_fo_charges, get_lot_size
```

`get_lot_size(index, entry_date)` is defined at `backend/engines/generic_algotest_engine.py:235`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestChargeAdjustedPnlScales -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/backtest.py backend/tests/test_lot_quantity_scaling.py
git commit -m "feat(charges): scale charge-adjusted P&L by lots"
```

---

### Task 6: Verification gate — 1-lot parity and scaling correctness

The blocking gate. Task 6 is not done until every check below passes on real data.

**Files:**
- Test: `backend/tests/test_lot_quantity_scaling.py`
- Use: `backend/tools/three_way_summary_parity.py`, `backend/tests/test_summary_parity_gate.py`

- [ ] **Step 1: Write the ratio-spread double-scaling test**

This is the test that catches `lots²`. With equal lots, `lots²` and `lots` are hard to tell apart from a single number; with a 2×1 spread they diverge per leg and the trade total is unambiguous.

Append to `backend/tests/test_lot_quantity_scaling.py`:

```python
class TestNoDoubleScaling(unittest.TestCase):
    """A 2x1 ratio spread distinguishes `x lots` from `x lots**2`.

    Correct  : 60 x 2  + (-15) x 1 = 105
    lots**2  : 60 x 4  + (-15) x 1 = 225
    unscaled : 60      + (-15)     =  45
    """

    def test_trade_total_uses_lots_not_lots_squared(self):
        from services.engine_rust import priced_to_tradesheet_records

        rows = [
            {
                "trade_id": "1", "leg_id": 1, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "CE", "strike": 21500.0,
                "position": "SELL", "entry_price": 150.0, "exit_price": 90.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": 2, "lot_size": 65, "net_pnl": 105.0,
            },
            {
                "trade_id": "1", "leg_id": 2, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "PE", "strike": 21500.0,
                "position": "SELL", "entry_price": 130.0, "exit_price": 145.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": 1, "lot_size": 65, "net_pnl": -15.0,
            },
        ]
        recs = priced_to_tradesheet_records(rows, {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")

        self.assertAlmostEqual(ce["CE P&L"], 120.0, places=4)   # not 240 (lots**2)
        self.assertAlmostEqual(pe["PE P&L"], -15.0, places=4)
        self.assertAlmostEqual(ce["Net P&L"], 105.0, places=4)  # trade total, first-leg row
```

- [ ] **Step 2: Run it**

Run: `python -m unittest backend.tests.test_lot_quantity_scaling.TestNoDoubleScaling -v`

Expected: PASS. A value of `240.0` means a site is scaling twice — find it before continuing.

- [ ] **Step 3: Run the whole new suite plus the existing gate**

Run:

```bash
python -m unittest backend.tests.test_lot_quantity_scaling -v
python -m unittest backend.tests.test_summary_parity_gate -v
python -m unittest discover backend/tests
```

Expected: all pass, no new failures versus the baseline recorded in Task 3 Step 7.

- [ ] **Step 4: 1-lot byte-identical parity on real data (BLOCKING)**

Run a real backtest at lots = 1 on 2024 NIFTY data and diff the exported tradesheet against the same run on `main`:

```bash
git stash
# run the backtest at lots=1, export the XLSX, save as /tmp/claude-1000/-home-aff34-Downloads-Algo-Test-Software/5684420e-a2e0-49b9-a86f-503de379eb2e/scratchpad/before.xlsx
git stash pop
# rebuild, re-run the identical payload, save as .../after.xlsx
python backend/tools/three_way_summary_parity.py
```

Expected: **zero** differences in every column. Any diff at lots = 1 is a bug — multiplying by 1 must be a no-op.

- [ ] **Step 5: 2-lot scaling on real data**

Re-run the same payload with every leg at lots = 2. Verify against the lots = 1 run:

- Same trade count, same entry dates, same exit dates, same exit reasons
- Same `Entry Price`, `Exit Price`, `Entry Spot`, `Exit Spot`
- Same `MAE`, `MFE`
- `Net P&L` exactly 2× on every row
- `% P&L` exactly 2× on every row
- `Qty` = 130 (NIFTY lot size 65)

- [ ] **Step 6: Trade-by-trade hand verification of a ratio spread**

Run a 2×1 spread on 2024 data and verify **trade by trade** that `Net P&L = 2 × leg1_points + 1 × leg2_points`. Per the standing rule, the tradesheet is the source of truth and must be checked row by row, not spot-checked.

- [ ] **Step 7: Index-agnostic check**

Run a multi-index strategy (NIFTY + MIDCPNIFTY legs) with different lots per leg. Confirm:
- Each leg scales by its own `lots`
- Each leg's `Qty` uses its own index's lot size, resolved for that leg's entry date via `get_lot_size_for_index` (`backend/services/index_metadata.py:86`). NIFTY is **65** flat. MIDCPNIFTY is date-versioned: **75** before 2024-11-20, 120 to 2025-07-01, 140 to 2026-01-01, then 120 — so a 2024 run must show MIDCPNIFTY `Qty` in multiples of 75, not 120.
- No leg picks up another index's lot size or lot count

- [ ] **Step 8: Three-way identity at lots = 2 (BLOCKING)**

Per the standing hard rule, backtest tradesheet == optimizer per-combo == optimizer master on every metric:

```bash
python backend/tools/three_way_summary_parity.py
```

Run it against a lots = 2 optimizer sweep. Expected: identical on every metric across all three outputs.

- [ ] **Step 9: Charges-on regression**

Re-run Steps 4 and 5 with charges enabled. Confirm the charge-adjusted P&L scales correctly and that the `generic_multi_leg` `Qty` fix from Task 3 did not shift 1-lot charge values for engines that were already emitting `lots × lot_size`.

- [ ] **Step 10: Commit**

```bash
git add backend/tests/test_lot_quantity_scaling.py
git commit -m "test: add lot-quantity scaling and double-scaling gate"
```

---

## Post-implementation

- [ ] Bump the engine cache-version hash if it did not pick up the changed files automatically (`backend/services/backtest_cache.py:58` lists the fingerprinted engine files). Stale Redis entries would otherwise serve pre-change tradesheets.
- [ ] Restart Celery workers so they pick up the new `.py` files — **only after** confirming no jobs are running (`algotest:mem_gate` and the queues are empty).
- [ ] Run `graphify update .` to refresh the knowledge graph.

---

## Task 7: Scale MAE/MFE by lots (added 2026-07-21 after review)

**Why:** `summary_metrics.rs:336` compounds NAV by `% P&L` (now lots-scaled);
`:362` applies `MAE` to that same NAV as `prev_cum * (1 + mae/100)`. With MAE
unscaled, Live DD / Final MAE / Max DD understate drawdown by ~1/lots. Both are
percentage multipliers on one equity curve and must be commensurate.

**Rule:** `MAE` and `MFE` scale by the leg's own `lots`. `lot_size` excluded.

**Design decision — scale at the COLUMN-WRITE sites, not inside the formulas.**
Six functions compute MAE/MFE, but all of them land in the `MAE`/`MFE` columns,
and every downstream metric (Net MAE 1/2, Final MAE, Live DD, Midcap MAE/MFE)
derives from those columns. Scaling at the writes covers the Rust path without
plumbing `lots` into `mae.rs`, and keeps the multiplier applied exactly once.

**Plumbing:** add `"lots"` to the tradesheet record in
`priced_to_tradesheet_records` (`engine_rust.py`, beside `"Qty"` at ~`:3331`).
Verified safe: `excel_builder._build_key_order` (`:301`) is an explicit
whitelist with no catch-all, so the key cannot leak into Excel output.

**Sites to scale (all reachability-verified):**

| # | Site | Reached via |
|---|---|---|
| A | `services/algotest_job.py:428-430` | live backtest job |
| B | `services/optimizer/runner.py:1281-1282` (Rust batch pairs) | `routers/optimize.py:450` |
| C | `services/optimizer/runner.py:1588-1589` (Python path) | `routers/optimize.py:436` |
| D | `services/optimizer/runner.py:_apply_futures_mae_mfe` (`:1593`) | FUT rows overwrite |
| E | `services/multi_index_feature.py:1545` | multi-index overlay |

**Must NOT scale (would double-apply):**
- `native/src/mae.rs` — stays a pure ratio; its output is scaled at site B
- `native/src/summary_metrics.rs` Net MAE 1/2, Final MAE, Live DD — sums/derivations
  of already-scaled MAE
- Midcap workbook MAE/MFE — derives from scaled values

**Also in scope:** replace the `Qty / lot_size` round-trip in
`routers/backtest.py` (~`:322`) with the now-explicit `lots` key — resolves the
reviewer's Important finding about inverting a display column.

**Gate:** lots=1 byte-identical; lots=2 → MAE/MFE exactly 2×; Live DD and Max DD
move correspondingly; three-way parity still passes.
