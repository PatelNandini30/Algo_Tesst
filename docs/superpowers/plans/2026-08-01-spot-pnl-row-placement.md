# Spot P&L Row Placement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a trade's Spot P&L on the lowest leg row that actually exists, instead of only on `leg_id == 1`, so a trade whose leg 1 was removed by its individual filter still reports Spot P&L.

**Architecture:** One-line class of bug. `priced_to_tradesheet_records` hardcodes `leg_id == 1`, while the Rust post-process that places Net P&L computes `lowest_leg` per trade. Make Spot P&L use the same rule, then fix the two positional `"first"` aggregations that read it back.

**Tech Stack:** Python 3 / pandas, PyO3 Rust extension (read-only here), `unittest`.

## Global Constraints

- **The calculation does not change.** Spot P&L stays `exit_spot - entry_spot` of the row that carries it. Only which row carries it changes. Ruled by the project owner on 2026-08-01: when the carrying leg is itself truncated by its own filter, Spot P&L describes **that row's own window**, not the trade's.
- **Exactly one row per trade carries a value.** Every downstream sum (`metrics.py::Long Spot P&L`, the Excel column totals, WOW/MOM) depends on this. It must hold before and after.
- **No behaviour change for trades that have a leg 1** — the overwhelming majority. Those must be byte-identical.
- Tests are `unittest`, not pytest. **HARD RULE: tests must never call `build_cache`, `warm_cache`, `_prepare_market_data`, or run a real symbol backtest** — that narrows the shared NIFTY feather and breaks other runs. Synthetic dicts only.
- **optim == backtest**: the optimizer's per-combo tradesheet must match a direct backtest cell-for-cell.
- Python 4-space indent, snake_case. Inside `backend/services/*.py` the import form is `from services.X import Y`; tests use `from backend.services.X import Y`. Both are correct.
- Never `git add -A` / `git add .` — the tree carries scratch files (`backend/*.xlsx`, `backend/rd*.py`) and a root-owned modified `frontend/dist/index.html`. Never `git stash` — it fails on that file.

---

## File Structure

| File | Change |
|---|---|
| `backend/services/engine_rust.py:3857-3862` | Replace the `leg_id == 1` gate with the trade's lowest present `leg_id`. **The root cause.** |
| `backend/services/algotest_job.py:473` | `"Spot P&L": "first"` → first NON-EMPTY value. |
| `backend/services/optimizer/runner.py:1878` | Same aggregation fix, for optim==backtest. |
| `backend/tests/test_spot_pnl_placement.py` | **New.** Synthetic-dict tests. |

---

### Task 1: Place Spot P&L on the lowest present leg

**Files:**
- Modify: `backend/services/engine_rust.py:3850-3862` (inside `priced_to_tradesheet_records`, which starts at :3756)
- Create: `backend/tests/test_spot_pnl_placement.py`

**Interfaces:**
- Consumes: the `priced` list of row dicts, each with `trade_id`, `leg_id`, `entry_spot`, `exit_spot`.
- Produces: tradesheet records where exactly one row per trade has a numeric `"Spot P&L"` and the rest have `""`.

**The bug in one sentence:** the comment at `:3857-3860` says this "mirrors the Net P&L convention", but `backend/native/src/simulate.rs:1793-1803` computes `lowest_leg` per trade and assigns the total there, while this code hardcodes `1`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_spot_pnl_placement.py`. Build minimal `priced` row dicts and call `priced_to_tradesheet_records`; if that function needs more context than a bare row provides, instead extract the placement decision into a tiny helper and test that — but do NOT restructure the function beyond what the test needs.

```python
import unittest


def _row(trade_id, leg_id, entry_spot, exit_spot, **kw):
    r = {
        "trade_id": trade_id, "leg_id": leg_id,
        "entry_spot": entry_spot, "exit_spot": exit_spot,
        "option_type": "CE", "position": "SELL",
        "entry_price": 100.0, "exit_price": 90.0,
        "entry_date": "2019-11-21", "exit_date": "2019-11-28",
        "expiry": "2019-11-28", "strike": 12000.0, "lots": 1, "lot_size": 75,
        "net_pnl": 10.0,
    }
    r.update(kw)
    return r


class TestSpotPnlPlacement(unittest.TestCase):
    """A trade-level quantity must land on the lowest leg that EXISTS.

    Leg 1 can be absent: an individual per-leg filter file removes that leg
    from the trade (see docs/superpowers/specs/2026-07-31-per-leg-filter-design.md).
    """

    def _spot_by_leg(self, rows):
        from backend.services.engine_rust import priced_to_tradesheet_records
        out = priced_to_tradesheet_records(rows)
        return {(r["Trade"], r["Leg"]): r["Spot P&L"] for r in out}

    def test_normal_trade_still_reports_on_leg_1(self):
        got = self._spot_by_leg([
            _row(1, 1, 11968.40, 12151.15),
            _row(1, 2, 11968.40, 12151.15),
        ])
        self.assertEqual(got[(1, 1)], 182.75)
        self.assertEqual(got[(1, 2)], "")

    def test_missing_leg_1_reports_on_leg_2(self):
        got = self._spot_by_leg([
            _row(2, 2, 12151.15, 12018.40),
        ])
        self.assertEqual(got[(2, 2)], -132.75)

    def test_missing_legs_1_and_2_reports_on_leg_3(self):
        got = self._spot_by_leg([
            _row(3, 3, 12018.40, 11971.80),
            _row(3, 4, 12018.40, 11971.80),
        ])
        self.assertEqual(got[(3, 3)], -46.60)
        self.assertEqual(got[(3, 4)], "")

    def test_exactly_one_row_per_trade_carries_a_value(self):
        """The invariant every downstream SUM depends on."""
        rows = [
            _row(1, 1, 100.0, 110.0), _row(1, 2, 100.0, 110.0),
            _row(2, 2, 110.0, 105.0), _row(2, 3, 110.0, 105.0),
            _row(3, 3, 105.0, 120.0),
        ]
        from backend.services.engine_rust import priced_to_tradesheet_records
        out = priced_to_tradesheet_records(rows)
        for tid in (1, 2, 3):
            carried = [r for r in out
                       if r["Trade"] == tid and r["Spot P&L"] != ""]
            self.assertEqual(len(carried), 1, f"trade {tid}")

    def test_per_leg_ordering_does_not_matter(self):
        """Rows may arrive in any order; the lowest leg still wins."""
        got = self._spot_by_leg([
            _row(4, 3, 100.0, 120.0),
            _row(4, 2, 100.0, 120.0),
        ])
        self.assertEqual(got[(4, 2)], 20.0)
        self.assertEqual(got[(4, 3)], "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

```
cd /home/aff34/Downloads/Algo_Test_Software && docker compose exec -T -e PYTHONPATH=/app:/tmp/root backend bash -c \
  "mkdir -p /tmp/root && ln -sfn /app /tmp/root/backend && cd /tmp/root && \
   python -m unittest backend.tests.test_spot_pnl_placement -v"
```
Expected: `test_missing_leg_1_reports_on_leg_2`, `test_missing_legs_1_and_2_reports_on_leg_3`, `test_exactly_one_row_per_trade_carries_a_value` and `test_per_leg_ordering_does_not_matter` FAIL (blank where a number is expected). `test_normal_trade_still_reports_on_leg_1` PASSES already.

- [ ] **Step 3: Implement**

In `priced_to_tradesheet_records`, before the `for row in priced:` loop at `:3852`, add:

```python
    # Spot P&L is a trade-level quantity and rides ONE row per trade. That row
    # is the trade's LOWEST PRESENT leg — not literally leg 1, because a leg
    # can be absent: an individual per-leg filter file removes it from the
    # trade. This mirrors native/src/simulate.rs:1793-1803, which places the
    # Net P&L total on `lowest_leg` computed the same way. Before this, the
    # gate was `leg_id == 1`, so a trade whose leg 1 was filtered out reported
    # a BLANK Spot P&L.
    _lowest_leg_by_trade: Dict[Any, int] = {}
    for _r in priced:
        _tid = _r.get("trade_id")
        _lid = int(_r.get("leg_id") or 1)
        if _tid not in _lowest_leg_by_trade or _lid < _lowest_leg_by_trade[_tid]:
            _lowest_leg_by_trade[_tid] = _lid
```

Then replace `:3862`:

```python
        spot_pnl = (
            round(exit_spot - entry_spot, 2)
            if _leg_id_val == _lowest_leg_by_trade.get(row.get("trade_id"), 1)
            else ""
        )
```

Leave the calculation itself alone — it stays this row's `exit_spot - entry_spot`, per the owner's ruling.

- [ ] **Step 4: Run the tests, all pass**

Same command as Step 2. Expected: all 5 PASS.

- [ ] **Step 5: Full suite, failing set unchanged**

```
python -m unittest discover backend/tests
```
The suite has **112 pre-existing failures/errors** (61 failures, 51 errors) on this branch, unrelated to this work. Capture the set BEFORE editing and confirm it is IDENTICAL after. Any newly failing test is a real regression.

- [ ] **Step 6: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_spot_pnl_placement.py
git commit -m "fix(tradesheet): Spot P&L rides the lowest PRESENT leg, not leg 1

A per-leg filter file can remove leg 1 from a trade, and the leg_id == 1
gate then left Spot P&L blank for the whole trade. Mirrors the Net P&L
placement in simulate.rs, which already uses the lowest present leg.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Fix the two positional `"first"` aggregations

**Files:**
- Modify: `backend/services/algotest_job.py:473`
- Modify: `backend/services/optimizer/runner.py:1878`
- Modify: `backend/tests/test_spot_pnl_placement.py`

**Interfaces:**
- Consumes: Task 1's output (one non-empty Spot P&L per trade, on an arbitrary leg).
- Produces: the aggregated trade-level Spot P&L, independent of row order.

**Why this is needed even after Task 1.** `algotest_job.py:473` aggregates `"Spot P&L": "first"` over `_anchor_sorted(trades_df)`, which sorts by **latest Entry Date**, not lowest leg. So the first row of a trade is not necessarily the row carrying the value.

**This is also a pre-existing bug, unrelated to per-leg filters.** In a carried-YEARLY strategy the yearly leg 1 holds an older entry date while the weekly leg 2 re-enters each cycle, so `_anchor_sorted` puts **leg 2 first** — and leg 2's Spot P&L is `""`. Those strategies already report a blank Spot P&L today. Fixing the aggregation repairs that at the same time.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_spot_pnl_placement.py`:

```python
import pandas as pd


class TestSpotPnlAggregation(unittest.TestCase):
    """The aggregate must find the carrying row wherever it sorts."""

    def _agg(self, df):
        from backend.services.algotest_job import _anchor_sorted
        # Mirror the real aggregation for just this column.
        return (_anchor_sorted(df)
                .groupby("Trade", as_index=False)
                .agg({"Spot P&L": _spot_first_non_empty}))

    def test_carried_yearly_leg_order_still_finds_the_value(self):
        """Leg 2 enters LATER, so _anchor_sorted puts it first; leg 1 carries
        the value. Positional "first" returns "" here — the pre-existing bug."""
        df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": pd.Timestamp("2019-01-01"),
             "Spot P&L": 182.75},
            {"Trade": 1, "Leg": 2, "Entry Date": pd.Timestamp("2019-11-21"),
             "Spot P&L": ""},
        ])
        self.assertEqual(self._agg(df)["Spot P&L"].iloc[0], 182.75)

    def test_value_on_leg_2_when_leg_1_is_absent(self):
        df = pd.DataFrame([
            {"Trade": 2, "Leg": 2, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": -132.75},
            {"Trade": 2, "Leg": 3, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": ""},
        ])
        self.assertEqual(self._agg(df)["Spot P&L"].iloc[0], -132.75)

    def test_all_blank_stays_blank(self):
        df = pd.DataFrame([
            {"Trade": 3, "Leg": 1, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": ""},
        ])
        self.assertIn(self._agg(df)["Spot P&L"].iloc[0], ("", None))
```

Import `_spot_first_non_empty` from wherever you define it in Step 2, and adjust the test's import accordingly.

- [ ] **Step 2: Run it and watch it fail**

Expected: `test_carried_yearly_leg_order_still_finds_the_value` FAILS (returns `""`), plus an ImportError until the helper exists.

- [ ] **Step 3: Implement**

Define one shared helper — put it in `backend/services/trade_anchor.py` beside the other trade-level placement rules, and add it to `__all__`, so both callers import the same function rather than duplicating it:

```python
def spot_first_non_empty(series) -> Any:
    """Aggregate a trade-level column that rides ONE leg row.

    Positional "first" is wrong here: `anchor_sorted` orders by LATEST entry
    date, so the first row of a trade is not necessarily the row carrying the
    value. A carried-YEARLY leg holds an older entry date than the weekly leg
    that re-enters each cycle, so the weekly leg sorts first and its blank
    would win. Returns "" when no row carries a value.
    """
    for v in series:
        if v != "" and v is not None and not (isinstance(v, float) and v != v):
            return v
    return ""
```

Then use it at `algotest_job.py:473` and `optimizer/runner.py:1878` in place of `"first"` for the `"Spot P&L"` key only. Leave every other key's `"first"` alone.

- [ ] **Step 4: Run the tests**

Both new classes pass, plus `backend.tests.test_exit_anchor` and `backend.tests.test_leg_order_parity` (they cover the same aggregation area).

- [ ] **Step 5: Full suite, failing set unchanged**

As Task 1 Step 5.

- [ ] **Step 6: Commit**

```bash
git add backend/services/trade_anchor.py backend/services/algotest_job.py \
        backend/services/optimizer/runner.py backend/tests/test_spot_pnl_placement.py
git commit -m "fix(tradesheet): aggregate Spot P&L by first non-empty, not position

anchor_sorted orders by latest entry date, so the first row of a trade
need not be the row carrying Spot P&L. Also repairs a pre-existing blank
Spot P&L on carried-YEARLY strategies.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Audit every other consumer for a leg-1 assumption

**Files:**
- Read-only audit; modify only what the audit proves wrong.
- Modify: `backend/tests/test_spot_pnl_placement.py` (record the audit as a test where practical)

**Why this task exists.** The per-leg-filter branch's reviews found hidden leg-1 / positional assumptions twice — the optimizer's own `Exit Date` aggregation, and a futures ordering test that silently covered only one of two branches. Assume nothing here; prove each site.

Audit at minimum, and state the verdict for each with file:line evidence:

- [ ] **Step 1: `backend/services/optimizer/metrics.py:59,72,447`** — `Long Spot P&L` sums the column. A sum is placement-independent **provided exactly one row per trade carries a value**. Confirm the invariant holds and that nothing filters to `Leg == 1` first.
- [ ] **Step 2: `backend/services/optimizer/excel_builder.py:707-712`** — derives `Spot P&L %` from the row's own `Spot P&L` and `Entry Spot`. Confirm it is genuinely row-derived (then it follows automatically) and that its comment, which says "written only on Leg 1 rows", is updated to match the new rule.
- [ ] **Step 3: `excel_builder.py:1335,1365,1730-1738` and `:2149`** — column totals and the summary's Spot P&L row. Confirm they sum rather than pick leg 1.
- [ ] **Step 4: `engine_rust.py:4274-4275` and `:4480-4483`** — both sort rows by `(trade, leg)` with comments asserting "leg-1 carries Spot P&L". With the new rule the lowest present leg sorts first anyway, so the ordering still works — but the comments are now wrong and must be corrected, because a future reader will otherwise re-introduce the `== 1` gate.
- [ ] **Step 5: WOW/MOM builders and `backend/services/optimizer/wow_mom.py`** — grep for `Spot P&L` and confirm sum-based handling.
- [ ] **Step 6: Frontend** — grep `frontend/src` for `Spot P&L` and any `Leg === 1` / `legs[0]` gate around it (`ResultsPanel.jsx`, `OptimizationResults.jsx`, `utils/optimSummaryExport.js`).
- [ ] **Step 7: `backend/engines/generic_multi_leg.py:749`** — the Python engine also writes `Spot P&L`. Determine whether that path is still reachable under the Rust-only rule. If it is dead, leave it and say so; if it is live, apply the same fix.
- [ ] **Step 8: Commit** any corrections plus the updated comments.

```bash
git add -- <only the files the audit proved wrong>
git commit -m "fix(tradesheet): correct stale leg-1 comments and any leg-1 gates found by audit

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Verify against the reported case

**This runs a real backtest and is done by a human at the UI — not in `unittest`**, per the hard rule that the suite must never touch market data.

- [ ] **Step 1:** Re-run the exact strategy that produced the reported tradesheet — 2 legs, leg 1 carrying an individual filter file that excludes 28-Nov-2019 through 09-Jan-2020, NIFTY, weekly.
- [ ] **Step 2:** Confirm trades 17-22 (leg 2 only) now show Spot P&L and Spot P&L %: e.g. trade 17 = `12,018.40 - 12,151.15 = -132.75`, and `-132.75 / 12,151.15 = -1.09%`.
- [ ] **Step 3:** Confirm trades 16 and 23 (both legs present) are UNCHANGED — Spot P&L still on the leg 1 row, same values (182.75 / 1.53% and 139.60 / 1.14%).
- [ ] **Step 4:** Confirm the Spot P&L column TOTAL and the summary's `Long Spot P&L` changed by exactly the sum of the previously-blank trades, and nothing else moved.
- [ ] **Step 5:** Run the same config through the optimizer and confirm the per-combo tradesheet matches the direct backtest cell-for-cell on Spot P&L and Spot P&L %.
- [ ] **Step 6:** Run a control strategy with NO individual filter and confirm its tradesheet is byte-identical to a pre-change run.

---

## Self-Review

**Spec coverage:** the reported symptom is Task 1; the aggregation that would still have blanked it in some orderings is Task 2; hidden assumptions are Task 3; behavioural proof is Task 4.

**Placeholder scan:** none. Task 1 Step 3 contains the exact code; Task 2 Step 3 contains the helper; Task 3 is an audit whose steps each name a concrete file:line and the verdict required.

**Type consistency:** `spot_first_non_empty` is spelled identically in Task 2's helper, its two call sites and the test. `_lowest_leg_by_trade` is local to `priced_to_tradesheet_records`.

**Scope check:** four tasks, one of them human-run. Small enough for a single plan.

**Known limitation, ruled by the owner on 2026-08-01:** when the carrying leg is itself truncated by its own filter, Spot P&L describes that row's own (shorter) window rather than the trade's. This is deliberate — the calculation was to stay exactly as it is. If a trade's leg 1 is truncated while leg 2 runs to expiry, the displayed Spot P&L will measure the shorter span.

## Post-implementation corrections (final review, 2026-08-01)

**The futures re-entry duplicate-`leg_id` guard changes no output.** During Task 1 it was believed that
`_build_futures_specs` emitting its re-entry row (`engine_rust.py:1733-1734`) with the same `trade_id` AND
`leg_id` as the primary row (`:1623-1624`) had been double-counting Spot P&L in column totals. It had not.
Every consumer re-derives the column after aggregating, using a per-trade `parent_seen`/`seen` set that
writes the value on one row and `None` on the rest — `algotest_job.py:559-581`,
`optimizer/runner.py:1959-1985`, and the four `multi_index_feature.py` sites — and the aggregation itself is
first-semantics, never a sum. The `_spot_assigned` guard is therefore **defensive only**. No total changed.

**Known second writer, latent.** `multi_index_feature.py:292-294` (`_price_futures_group`) builds its own
rows and assigns `r["Spot P&L"] = spot_pl if k == 0 else 0.0` — positional first leg rather than lowest
present leg, and `0.0` rather than `""` for blanks. Because `0.0` is genuinely non-empty,
`spot_first_non_empty` degenerates to plain `first` on a fused trade whose futures group leads the concat.
Not reachable through a per-leg filter today (that path reads only the global `_load_filter_segments`), so it
is a latent gap. Fix it if that path ever gains per-leg filter support.

**Dtype nuance.** For an all-blank float64/NaN column the helper returns `""` where `"first"` returned `NaN`,
so the cell renders blank instead of `nan`. Benign, arguably better, but it is a real cell-content change.
