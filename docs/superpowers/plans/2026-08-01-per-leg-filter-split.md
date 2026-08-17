# Per-Leg Filter Mid-Cycle Split (Option C) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a filtered leg's individual-filter range boundary falls mid-trade, split that weekly block at the boundary — carry the unfiltered leg across cost-free, and let the filtered leg enter/exit exactly on its range.

**Architecture:** A spec-list rewrite at the point where `apply_leg_filters` runs today (`engine_rust.py:6166`), promoted from drop/truncate to split. Runs after strike resolution and `_apply_fixed_rollover_strike`, before pricing. Splits the unfiltered leg into cost-free segments, resolves a fresh mid-cycle entry for the filtered leg, and renumbers trades so all downstream sees a normal, longer list.

**Tech Stack:** Python 3 / pandas, PyO3 Rust extension (pricing, unchanged), `unittest`.

**Spec of record:** `docs/superpowers/specs/2026-08-01-per-leg-filter-split-design.md`. Read it first.

## Global Constraints

- **A run with NO individual filter is byte-identical to before this change.** The rewrite is a no-op when no leg carries a file.
- **P&L conservation:** the unfiltered leg's total across its split segments equals its unsplit total (the boundary mark cancels; slippage suppressed at the synthetic boundary). Every engine-level task asserts this.
- **This REPLACES the current subtract-only per-leg behaviour** (drop-at-start / truncate-at-end). Existing per-leg-filter tradesheets change by design; there is no blessed production result to preserve.
- **optim == backtest** — identical per-combo numbers, or fail-closed in the Rust batch gate until the optimizer path is covered.
- **Rust-only, no Python fallback**; unsupported combinations hard-fail with a clear message.
- **Tests never touch market data** (`build_cache`, `warm_cache`, `_prepare_market_data`, real-symbol runs are forbidden — they narrow a shared feather). Synthetic specs only. Engine-wiring assertions use source-text checks, per the standing project ruling.
- **Exit reasons:** strategy boundary → `FILTER_END`; a filtered leg's own range boundary (start-split carry row, end-split, filtered-leg exit) → `LEG_FILTER_END`; normal weekly expiry → `EXPIRY`; combined reasons join with `+`.
- Python 4-space, snake_case. Inside `backend/services/*.py`: `from services.X import Y`; tests: `from backend.services.X import Y`. Never `git add -A`/`.`; never `git stash` (root-owned `frontend/dist/index.html`). Verify each commit with `git show --stat HEAD`.
- Test runner (no host venv):
  ```
  cd /home/aff34/Downloads/Algo_Test_Software && docker compose exec -T -e PYTHONPATH=/app:/tmp/root backend bash -c \
    "mkdir -p /tmp/root && ln -sfn /app /tmp/root/backend && cd /tmp/root && python -m unittest <module> -v"
  ```
  Full suite has **112 pre-existing failures/errors** (61 F, 51 E) unrelated to this work; capture before, confirm identical after, on any task that changes engine code.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/leg_filter.py` | **New pure function** `split_windows(...)` — the boundary/sub-window math. Plus the existing `resolve_leg_window` stays for the futures inline path (which also gains split handling in Task 3). |
| `backend/services/engine_rust.py` | Replace the `apply_leg_filters` drop/truncate call site (~6166) with the split rewrite: carried-leg split (Task 2), filtered-leg mid-cycle entry (Task 3), tagging (Task 4), renumber. Absorb extra rows in cascade/NAV (Task 5). |
| `backend/services/optimizer/rust_combo_loop.py` | Coverage gate already returns `leg_filter`; confirm it still fails-closed for split (Task 6). |
| `backend/tests/test_leg_split.py` | **New.** Pure `split_windows` tests. |
| `backend/tests/test_leg_split_wiring.py` | **New.** Source-text + synthetic-spec engine tests. |

---

### Task 1: Pure window-split helper

**Files:**
- Modify: `backend/services/leg_filter.py` (add `split_windows`, export it)
- Create: `backend/tests/test_leg_split.py`

**Interfaces:**
- Consumes: `leg_segments`, `last_trading_day_on_or_before`, and a first-trading-day-on/after helper (add `first_trading_day_on_or_after` if not present — check `leg_window`'s bisect for reuse).
- Produces:
  `split_windows(entry, exit, ranges, trading_days) -> list[dict]`, each `{seg_start, seg_end, in_range: bool}`, consecutive and covering `[entry, exit]`, boundaries drawn only from `ranges` (a filtered leg's normalized `[(start,end),…]`) that fall strictly inside `(entry, exit)`. A range start snaps **forward** (first trading day ≥ start); a range end snaps **back** (last trading day ≤ end). `in_range` marks whether the filtered leg trades that sub-window.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_leg_split.py`:

```python
import unittest
from backend.services.leg_filter import split_windows

# A dense trading-day list around the real cases (Jan–Feb 2020 weekly-ish).
TD = [
    "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08",
    "2020-01-09", "2020-01-30", "2020-02-03", "2020-02-04", "2020-02-05",
    "2020-02-06",
]


class TestSplitWindows(unittest.TestCase):
    def test_no_ranges_single_window_out(self):
        out = split_windows("2020-01-02", "2020-01-09", [], TD)
        self.assertEqual(out, [{"seg_start": "2020-01-02",
                                "seg_end": "2020-01-09", "in_range": False}])

    def test_entry_split_range_starts_midtrade(self):
        # Range [06-Jan → 04-Feb]; trade 02→09-Jan. Split at 06-Jan.
        out = split_windows("2020-01-02", "2020-01-09",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [
            {"seg_start": "2020-01-02", "seg_end": "2020-01-06", "in_range": False},
            {"seg_start": "2020-01-06", "seg_end": "2020-01-09", "in_range": True},
        ])

    def test_exit_split_range_ends_midtrade(self):
        # Range ends 04-Feb; trade 30-Jan→06-Feb. Split at 04-Feb.
        out = split_windows("2020-01-30", "2020-02-06",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [
            {"seg_start": "2020-01-30", "seg_end": "2020-02-04", "in_range": True},
            {"seg_start": "2020-02-04", "seg_end": "2020-02-06", "in_range": False},
        ])

    def test_boundary_on_entry_no_split(self):
        out = split_windows("2020-01-06", "2020-01-09",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [{"seg_start": "2020-01-06",
                                "seg_end": "2020-01-09", "in_range": True}])

    def test_whole_window_outside_all_ranges(self):
        out = split_windows("2020-01-02", "2020-01-03",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [{"seg_start": "2020-01-02",
                                "seg_end": "2020-01-03", "in_range": False}])

    def test_two_boundaries_in_one_trade(self):
        # A short range fully inside the trade -> three sub-windows: out/in/out.
        out = split_windows("2020-01-02", "2020-01-09",
                            [("2020-01-06", "2020-01-08")], TD)
        self.assertEqual([w["in_range"] for w in out], [False, True, False])
        self.assertEqual([w["seg_start"] for w in out],
                         ["2020-01-02", "2020-01-06", "2020-01-08"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it, watch it fail** (`ImportError: split_windows`).

- [ ] **Step 3: Implement `split_windows`** in `leg_filter.py` and add to `__all__`. Snap each range start forward and each range end back; keep only snapped boundaries strictly between `entry` and `exit`; build consecutive `[cut_i, cut_{i+1}]` windows; a window is `in_range` iff its `seg_start` falls inside any (snapped) range. Reuse the existing bisect helpers; do not add a market-data dependency.

- [ ] **Step 4: Run the tests, all pass.**

- [ ] **Step 5: Commit** (`leg_filter.py`, `test_leg_split.py`).

---

### Task 2: Carried (unfiltered) leg split in the engine

**Files:**
- Modify: `backend/services/engine_rust.py` — the `apply_leg_filters` call site (~6166) and a new split routine near it; reuse `_apply_carry_slippage_guard` (`:3986`).
- Modify: `backend/tests/test_leg_split_wiring.py` (create)

**Interfaces:**
- Consumes: `split_windows` (Task 1), `spot_by_date` and `trading_days` (in scope at the call site), the resolved spec list.
- Produces: a spec list where every trade touched by a filtered leg's boundary is split into per-sub-window trades, the **unfiltered** leg duplicated across segments with the same strike, slippage suppressed at synthetic boundaries, and **trade ids renumbered sequentially**. The filtered leg's placement is Task 3 — here it is still dropped/truncated as today, so this task is landable and testable on its own.

**Scope note:** this task does the block-splitting and renumbering machinery and proves P&L conservation on the carried leg. It deliberately leaves the filtered-leg mid-cycle *entry* to Task 3 to keep each diff reviewable.

- [ ] **Step 1: Investigate and record** in the report: the exact shape of a spec dict at line 6166 (fields), how `trade_id` is assigned, how `_apply_carry_slippage_guard` decides a boundary is a carry (so the synthetic split boundary qualifies), and every downstream reader of `trade_id` between 6166 and pricing. Cite line numbers. Do not edit yet.

- [ ] **Step 2: Write the failing test** — `backend/tests/test_leg_split_wiring.py`, synthetic specs only. Build a 1-trade, 2-leg spec list (unfiltered leg + a filtered leg whose range starts mid-window), run the new split routine, and assert: the unfiltered leg becomes two specs at the boundary with the same strike; trade ids are sequential; and (once priced by a stub or by asserting the entry/exit dates) the two carried segments abut at the boundary date. Assert no slippage field is added on the synthetic boundary rows.

- [ ] **Step 3: Implement** the split routine and swap it in for the drop/truncate branch of `apply_leg_filters` at the carried-leg level. Renumber `trade_id` sequentially across the whole list after splitting. Suppress carry slippage at synthetic boundaries via the existing guard. Keep behaviour identical when no leg carries a filter (early return).

- [ ] **Step 4: Run** the wiring tests + full suite; confirm the pre-existing failing set is identical.

- [ ] **Step 5: Commit.**

---

### Task 3: Filtered-leg mid-cycle entry

**Files:**
- Modify: `backend/services/engine_rust.py` (the split routine from Task 2; the strike resolver is `_compute_strike_for_leg_python`).
- Modify: `backend/services/leg_filter.py` futures inline path (`_apply_leg_filter_mask` / `resolve_leg_window`) so futures legs split too, matching options.
- Modify: `backend/tests/test_leg_split_wiring.py`

**Interfaces:**
- Consumes: `split_windows` `in_range` flags, `_compute_strike_for_leg_python`, `spot_by_date`, the filtered leg's contract/expiry as the builders resolved it.
- Produces: for each sub-window where a filtered leg is `in_range`, a fresh filtered-leg spec — strike resolved at the sub-window start date on the same contract it would trade at the roll — entering at the sub-window start, exiting at the sub-window end.

- [ ] **Step 1: Investigate and record** how the builders resolve the filtered leg's contract at a normal entry (which expiry, which strike-selection call, what `out_info`), so the mid-cycle entry uses the identical resolution at the boundary date. Cite lines.

- [ ] **Step 2: Write the failing test** — synthetic: filtered leg present only in the middle sub-window; assert exactly one filtered-leg spec is emitted, dated to that sub-window, with a strike resolved from that window's start spot (feed a synthetic `spot_by_date`). Assert the filtered leg is absent from out-of-range sub-windows. Add the exit-split case (filtered leg exits at the range end).

- [ ] **Step 3: Implement** the mid-cycle entry. Guard: if the strike is unresolvable at the boundary (illiquid), drop just that filtered-leg segment and keep the carried leg — mirror the builders' existing unresolvable-strike drop, don't abort.

- [ ] **Step 4: Run** wiring tests + full suite; pre-existing set identical.

- [ ] **Step 5: Commit.**

---

### Task 4: Exit-reason tagging on split rows

**Files:**
- Modify: `backend/services/engine_rust.py` (the split routine + the existing `_leg_filter_end_keys` tagging, ~6205 / ~8736).
- Modify: `backend/tests/test_leg_split_wiring.py`

**Interfaces:**
- Consumes: the split segments and their boundary provenance (which cut came from a filtered leg's range vs the trade's natural expiry).
- Produces: the segment row that ends **at a filtered-leg range boundary** carries `LEG_FILTER_END`; a segment ending at the trade's natural expiry carries `EXPIRY`; a strategy-patch boundary stays `FILTER_END`; combined reasons join with `+`.

- [ ] **Step 1: Write the failing test** — synthetic: entry-split trade → the carried leg's segment-1 row (ending at the range start) is tagged `LEG_FILTER_END`, segment-2 (ending at expiry) `EXPIRY`. Exit-split → the filtered leg's row and the carried segment-1 row both `LEG_FILTER_END`. Confirm a no-filter run has none.

- [ ] **Step 2: Implement.** Reuse the renumber-proof key model already in place (`_leg_filter_end_keys` keyed by `(expiry, leg_id) -> {boundary dates}`). Extend it so a carried-leg segment whose exit equals a filtered-leg boundary is tagged. Do not disturb the `_leg_was_truncated` safety guard semantics from the prior feature.

- [ ] **Step 3: Run** tests + full suite; pre-existing set identical.

- [ ] **Step 4: Commit.**

---

### Task 5: Downstream absorption + P&L conservation

**Files:**
- Modify: `backend/services/engine_rust.py` (spot-adj cascade guard), and verify (mostly no-change) `algotest_job.py`, WOW/MOM and patch-wise builders.
- Modify: `backend/tests/test_leg_split_wiring.py`

**Interfaces:** none new — this task proves the extra rows flow correctly through analytics.

- [ ] **Step 1: P&L conservation test** (synthetic, priced via the real pricing only if it can be done without market data — otherwise assert on constructed priced rows): the carried leg's summed P&L across its split segments equals the unsplit total; exactly one Spot P&L per split trade (lowest present leg); MAE/MFE computed over each split window.
- [ ] **Step 2: Spot-adj guard** — extend/verify the `_leg_was_truncated` / cascade guards so a spot-adjustment re-entry cannot resurrect a filtered leg past its range across a split boundary. Add a source-text test that the guard covers the split path.
- [ ] **Step 3: WOW/MOM + Patch-wise** — confirm a split segment buckets into the same strategy patch as its parent trade (the patch is decided by the strategy filter, unchanged by the split). Add an assertion or record the trace with evidence if no code change is needed.
- [ ] **Step 4: Run** full suite; pre-existing set identical.
- [ ] **Step 5: Commit.**

---

### Task 6: Optimizer parity / coverage gate

**Files:**
- Modify/verify: `backend/services/optimizer/rust_combo_loop.py` (`rust_batch_unsupported`), `backend/services/optimizer/runner.py`.
- Modify: `backend/tests/test_rust_combo_whitelist.py`

**Interfaces:** the split must produce identical numbers in a per-combo optimizer run, or be fail-closed.

- [ ] **Step 1:** Confirm `rust_batch_unsupported` still returns `leg_filter` for any leg carrying `filter_segments` (the split is even less Rust-batch-representable than the mask). Add a test that a leg with a split-triggering range is routed to the Python path / hard-fails, never silently to the Rust batch.
- [ ] **Step 2:** Confirm the Python optimizer path invokes the same engine split (it calls the same `run_rust_engine_pipeline`), so per-combo == backtest by construction. Record the call-path evidence.
- [ ] **Step 3: Commit.**

---

### Task 7: End-to-end verification (human-run, not unittest)

Real backtests — the suite must never touch market data.

- [ ] Deploy (confirm no jobs in flight first, per the mem-gate rule): `sudo ./start.sh`.
- [ ] **Trade 25 (entry split):** Leg 2 opens **06-Jan-2020**, exits 09-Jan; Leg 1 shows two rows 02→06 (`LEG_FILTER_END`) and 06→09 (`EXPIRY`); Leg 1's two-segment P&L sums to the pre-split +22.35.
- [ ] **Trade 29 (exit split):** Leg 2 exits **04-Feb**; Leg 1 splits 30-Jan→04-Feb / 04-Feb→06-Feb; Leg 1 total unchanged.
- [ ] **No-filter control:** byte-identical to a pre-change build.
- [ ] **Optimizer:** per-combo tradesheet equals the direct backtest cell-for-cell on the split trades.
- [ ] Record all results in `docs/superpowers/plans/2026-08-01-per-leg-filter-split-verification.md`; commit.

---

## Self-Review

**Spec coverage:** split trigger (T1), carried-leg cost-free split (T2), filtered-leg mid-cycle entry (T3), tags (T4), downstream + conservation (T5), optim (T6), proof (T7). All spec rules mapped.

**Placeholder scan:** Task 1 has full code; Tasks 2–6 are investigation-then-implement with concrete anchors and named tests, deliberately not fabricating engine code the implementer must derive from the live file (the flow from spec-build → pricing → cascade is too intertwined to hand-write blind, and a wrong hand-written diff in a money path is worse than an honest "investigate first"). Each such task starts with a recorded investigation step and lands a synthetic-data or source-text test.

**Type consistency:** `split_windows(entry, exit, ranges, trading_days) -> list[{seg_start, seg_end, in_range}]` used identically across tasks. `LEG_FILTER_END` / `FILTER_END` spelled per the locked convention.

**Risk note:** Task 2 (renumbering + carry split) is the highest-risk change — it alters the spec list every backtest builds. Its no-filter no-op path is the single most important thing to keep byte-identical; every subsequent task re-checks it.
