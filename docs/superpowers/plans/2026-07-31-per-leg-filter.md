# Per-Leg Individual Filter Files — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any leg optionally carry its own uploaded filter-date CSV that subtracts that leg from trades whose entry falls outside the file's ranges, and shortens its hold when the file's window ends before the trade's exit.

**Architecture:** The strategy filter keeps sole control over *which trades exist*. A new pure module `backend/services/leg_filter.py` decides, per leg spec row, whether the leg is taken and what its exit is (`min(window end, trade exit)`). It is applied as a single post-pass over the resolved spec list just before pricing — the same shape as the existing `_apply_per_leg_slippage` / `_apply_fixed_rollover_strike` passes — so all six option spec builders are covered by one call site. Futures builders price internally and get the same helper applied inside their own loop before pricing.

**Tech Stack:** Python 3 / FastAPI / Polars+pandas, PyO3 Rust extension (`backend/native/`), React (Vite) frontend, `unittest` for tests.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-07-31-per-leg-filter-design.md`.
- A run with **no** individual filter on any leg must be **byte-identical** to current output. Every task must preserve this.
- Tests are `unittest`, not pytest: `python -m unittest discover backend/tests`.
- **HARD RULE — no market-data warming in tests.** Tests must not call `build_cache`, `warm_cache`, `_prepare_market_data`, or run a real symbol backtest; those narrow the shared NIFTY feather and break other runs. Every test in this plan uses synthetic dicts only.
- **HARD RULE — optim == backtest.** The optimizer's per-combo tradesheet for a config must equal a direct backtest of that config. Task 9 exists to prove it.
- **HARD RULE — Rust-only.** No Python engine fallback may be introduced. Unsupported combinations hard-fail with a clear message.
- Never hand-edit `frontend/dist/`. Build via the `node:22-bookworm-slim` docker command in `CLAUDE.md`.
- New exit reason string is exactly `LEG_FILTER_END`.
- Per-leg segments travel in the payload as `leg["filter_segments"] = [{"start": "...", "end": "..."}, ...]`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/leg_filter.py` | **New.** Pure segment logic: ISO normalisation, per-leg window lookup, the spec post-pass. No I/O, no engine imports. |
| `backend/tests/test_leg_filter.py` | **New.** Unit tests for the above, synthetic dicts only. |
| `backend/services/engine_rust.py` | Call the post-pass at the spec convergence point; register `LEG_FILTER_END`; stamp it after pricing; apply the mask inside the futures builders; guard spot-adj. |
| `backend/services/trade_anchor.py` | New `exit_anchor_row()` — the trade's exit is the latest leg exit, not leg-order-dependent "first". |
| `backend/services/algotest_job.py` | Use `exit_anchor_row()` for trade-level `Exit Date` / `Exit Reason`. |
| `backend/services/optimizer/rust_combo_loop.py` | Coverage gate returns `leg_filter` so the Rust batch never silently ignores a mask. |
| `frontend/src/components/StrategyBuilder.jsx` | `Individual Filter` checkbox + file input per leg card; serialise `filter_segments` into the leg payload. |

---

### Task 1: Pure leg-filter module

**Files:**
- Create: `backend/services/leg_filter.py`
- Create: `backend/tests/test_leg_filter.py`
- Modify: `backend/services/engine_rust.py` (move `_seg_iso` out of the `_load_filter_segments` closure and import it from the new module)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `seg_iso(v) -> str` — normalise a date-ish value to `YYYY-MM-DD`.
  - `normalize_segments(raw) -> list[tuple[str, str]]` — `[{"start":…,"end":…}, …]` or `[(s,e), …]` → sorted `[(iso, iso), …]`.
  - `leg_segments(leg: dict) -> list[tuple[str,str]] | None` — `None` when the leg has no individual filter.
  - `leg_window(mask, entry_date, trade_exit) -> tuple[bool, str, bool]` — `(taken, leg_exit_iso, truncated)`.
  - `apply_leg_filters(specs, legs) -> list[dict]` — the spec post-pass (added in Task 2).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_leg_filter.py`:

```python
import unittest

from backend.services.leg_filter import (
    leg_segments,
    leg_window,
    normalize_segments,
    seg_iso,
)


class TestSegIso(unittest.TestCase):
    def test_dayfirst_string_is_not_flipped(self):
        # 10-May-2019, NOT 5-Oct-2019. This is the bug _seg_iso exists to prevent.
        self.assertEqual(seg_iso("10/05/2019"), "2019-05-10")

    def test_iso_string_passes_through(self):
        self.assertEqual(seg_iso("2019-05-10"), "2019-05-10")

    def test_datetime_is_formatted_not_reparsed(self):
        from datetime import datetime
        self.assertEqual(seg_iso(datetime(2019, 5, 10)), "2019-05-10")


class TestNormalizeSegments(unittest.TestCase):
    def test_dicts_become_sorted_iso_tuples(self):
        raw = [{"start": "05-06-2025", "end": "05-07-2025"},
               {"start": "05-04-2025", "end": "05-05-2025"}]
        self.assertEqual(
            normalize_segments(raw),
            [("2025-04-05", "2025-05-05"), ("2025-06-05", "2025-07-05")],
        )

    def test_bad_rows_are_skipped_not_fatal(self):
        raw = [{"start": "05-04-2025", "end": "05-05-2025"}, {"start": "", "end": ""}]
        self.assertEqual(normalize_segments(raw), [("2025-04-05", "2025-05-05")])

    def test_inverted_range_is_dropped(self):
        self.assertEqual(normalize_segments([{"start": "05-05-2025", "end": "05-04-2025"}]), [])


class TestLegSegments(unittest.TestCase):
    def test_absent_key_is_none(self):
        self.assertIsNone(leg_segments({"leg_id": 1}))

    def test_empty_list_is_none(self):
        # An uploaded-then-cleared file must behave exactly like no file at all.
        self.assertIsNone(leg_segments({"filter_segments": []}))

    def test_present_returns_tuples(self):
        leg = {"filter_segments": [{"start": "05-04-2025", "end": "05-06-2025"}]}
        self.assertEqual(leg_segments(leg), [("2025-04-05", "2025-06-05")])


class TestLegWindow(unittest.TestCase):
    MASK = [("2025-04-05", "2025-06-05")]

    def test_entry_outside_mask_drops_the_leg(self):
        taken, _, _ = leg_window(self.MASK, "2025-03-01", "2025-03-27")
        self.assertFalse(taken)

    def test_entry_inside_and_exit_inside_is_untouched(self):
        self.assertEqual(
            leg_window(self.MASK, "2025-04-10", "2025-04-24"),
            (True, "2025-04-24", False),
        )

    def test_exit_beyond_window_end_is_truncated(self):
        # Spec case 1: window ends 05-Jun, trade exits 26-Jun -> leg exits 05-Jun.
        self.assertEqual(
            leg_window(self.MASK, "2025-06-02", "2025-06-26"),
            (True, "2025-06-05", True),
        )

    def test_window_end_beyond_trade_exit_keeps_trade_exit(self):
        # Spec case 2: window ends 28-Jun, trade exits 26-Jun -> leg exits 26-Jun.
        mask = [("2025-04-05", "2025-06-28")]
        self.assertEqual(
            leg_window(mask, "2025-06-02", "2025-06-26"),
            (True, "2025-06-26", False),
        )

    def test_degenerate_window_drops_the_leg(self):
        # Entry lands ON the window's last day: truncated exit <= entry, so the
        # leg would have a zero/negative hold. Drop it instead of emitting it.
        self.assertEqual(leg_window(self.MASK, "2025-06-05", "2025-06-26")[0], False)

    def test_entry_on_window_boundaries_is_inclusive(self):
        self.assertTrue(leg_window(self.MASK, "2025-04-05", "2025-04-24")[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.leg_filter'`

- [ ] **Step 3: Write the module**

Create `backend/services/leg_filter.py`:

```python
"""
services/leg_filter.py

Per-leg individual filter files.

The STRATEGY filter decides which trades exist. An individual per-leg file is a
purely SUBTRACTIVE mask on top of that: it can drop a leg from a trade, or end
that leg's hold early, and nothing else. It can never create a trade, widen a
window, or move a leg onto a date the strategy filter excludes.

See docs/superpowers/specs/2026-07-31-per-leg-filter-design.md.
"""

from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "seg_iso",
    "normalize_segments",
    "leg_segments",
    "leg_window",
    "apply_leg_filters",
    "LEG_FILTER_END",
]

LEG_FILTER_END = "LEG_FILTER_END"


def seg_iso(v: Any) -> str:
    """Normalize a segment boundary to ISO YYYY-MM-DD.

    Moved verbatim out of engine_rust._load_filter_segments so the strategy
    filter and the per-leg filter parse dates through ONE implementation.

    A datetime / date / Timestamp is ALREADY unambiguous — format it directly
    and NEVER reparse. str(datetime) is "2019-05-10 00:00:00", whose " 00:00:00"
    defeats the year-first strptime formats below, after which dayfirst=True
    FLIPS every date with day<=12 & month<=12 (10-May -> 05-Oct), inverting
    segments.
    """
    import pandas as pd

    if not isinstance(v, str) and hasattr(v, "strftime"):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    text = str(v).strip()
    for _fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y%m%d"):
        try:
            return pd.to_datetime(text, format=_fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return pd.to_datetime(text, dayfirst=True).strftime("%Y-%m-%d")


def normalize_segments(raw: Any) -> List[Tuple[str, str]]:
    """[{'start':…,'end':…}, …] or [(s, e), …] -> sorted [(iso, iso), …].

    Malformed rows are skipped rather than raising: a filter file is user input
    and one bad line must not abort a backtest. Inverted ranges (end < start)
    are dropped for the same reason get_filter_segments drops them (base.py).
    """
    segs: List[Tuple[str, str]] = []
    for s in raw or []:
        try:
            if isinstance(s, dict):
                start, end = s["start"], s["end"]
            else:
                start, end = s[0], s[1]
            a, b = seg_iso(start), seg_iso(end)
        except Exception:
            continue
        if b >= a:
            segs.append((a, b))
    segs.sort()
    return segs


def leg_segments(leg: Dict[str, Any]) -> Optional[List[Tuple[str, str]]]:
    """The leg's own mask, or None when it has no individual filter.

    An EMPTY list means "uploaded then cleared" and must behave exactly like no
    file at all — returning [] here would mask the leg out of every trade.
    """
    if not isinstance(leg, dict):
        return None
    segs = normalize_segments(leg.get("filter_segments"))
    return segs or None


def leg_window(
    mask: Sequence[Tuple[str, str]],
    entry_date: str,
    trade_exit: str,
) -> Tuple[bool, str, bool]:
    """Decide this leg's fate for one trade.

    Returns (taken, leg_exit, truncated):
      * taken=False    -> the leg is ABSENT from this trade (entry outside every
                          window, or the window leaves it no holding period).
      * leg_exit       -> min(window end, trade exit) — earliest wins.
      * truncated=True -> leg_exit came from the window, not the trade: the row
                          is tagged LEG_FILTER_END instead of its natural reason.
    """
    entry = seg_iso(entry_date)
    exit_ = seg_iso(trade_exit)

    # Rightmost window whose start <= entry; it contains entry iff entry <= its end.
    starts = [s for s, _ in mask]
    idx = bisect.bisect_right(starts, entry) - 1
    if idx < 0:
        return (False, exit_, False)
    seg_start, seg_end = mask[idx]
    if entry > seg_end:
        return (False, exit_, False)

    if seg_end < exit_:
        if seg_end <= entry:
            # Zero or negative holding period — emit nothing rather than a
            # degenerate row that would divide by a zero-length window downstream.
            return (False, exit_, False)
        return (True, seg_end, True)
    return (True, exit_, False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter -v`
Expected: PASS (all 15 tests)

- [ ] **Step 5: Point the strategy filter at the shared `seg_iso`**

In `backend/services/engine_rust.py`, inside `_load_filter_segments` (around line 486), delete the nested `def _seg_iso(v) -> str:` and its body, and replace the usage. The function keeps its existing name locally so the two `_seg_iso(...)` call sites below it are untouched:

```python
    from services.leg_filter import seg_iso as _seg_iso
```

Place that import immediately after the existing `import pandas as pd` line inside the function.

- [ ] **Step 6: Verify the strategy filter is unchanged**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest discover backend/tests -v 2>&1 | tail -20`
Expected: PASS — same pass/fail set as before the change. If any test was already failing on this branch, note it and confirm it fails identically.

- [ ] **Step 7: Commit**

```bash
git add backend/services/leg_filter.py backend/tests/test_leg_filter.py backend/services/engine_rust.py
git commit -m "feat(leg-filter): pure per-leg filter-segment helpers

seg_iso moves out of _load_filter_segments so the strategy filter and
the new per-leg filter share one date parser.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Spec post-pass

**Files:**
- Modify: `backend/services/leg_filter.py`
- Modify: `backend/tests/test_leg_filter.py`

**Interfaces:**
- Consumes: `leg_segments`, `leg_window`, `LEG_FILTER_END` (Task 1).
- Produces: `apply_leg_filters(specs: list[dict], legs: list[dict]) -> list[dict]`. Input spec rows carry `trade_id`, `leg_id` (1-based), `entry_date`, `exit_date`. Output rows may have `exit_date` shortened and gain `_leg_filter_end: True`; masked-out rows are removed; trades left with no rows disappear entirely.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_leg_filter.py`:

```python
from backend.services.leg_filter import apply_leg_filters


def _spec(trade_id, leg_id, entry, exit_):
    return {"trade_id": trade_id, "leg_id": leg_id,
            "entry_date": entry, "exit_date": exit_}


class TestApplyLegFilters(unittest.TestCase):
    # Leg 1 has no file; leg 2 is masked to April only.
    LEGS = [
        {"option_type": "CE"},
        {"option_type": "PE",
         "filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
    ]

    def test_no_leg_has_a_file_returns_input_unchanged(self):
        specs = [_spec(1, 1, "2025-03-05", "2025-03-27"),
                 _spec(1, 2, "2025-03-05", "2025-03-27")]
        legs = [{"option_type": "CE"}, {"option_type": "PE"}]
        self.assertEqual(apply_leg_filters(specs, legs), specs)

    def test_case_1_both_in_window_keeps_both_legs(self):
        specs = [_spec(1, 1, "2025-04-07", "2025-04-17"),
                 _spec(1, 2, "2025-04-07", "2025-04-17")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(len(out), 2)
        self.assertNotIn("_leg_filter_end", out[1])

    def test_case_2_masked_leg_is_absent_trade_survives(self):
        specs = [_spec(1, 1, "2025-03-05", "2025-03-27"),
                 _spec(1, 2, "2025-03-05", "2025-03-27")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual([(r["trade_id"], r["leg_id"]) for r in out], [(1, 1)])

    def test_truncated_leg_exits_early_and_is_tagged(self):
        specs = [_spec(2, 1, "2025-04-15", "2025-04-29"),
                 _spec(2, 2, "2025-04-15", "2025-04-29")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(out[0]["exit_date"], "2025-04-29")   # leg 1 untouched
        self.assertEqual(out[1]["exit_date"], "2025-04-20")   # leg 2 clamped
        self.assertTrue(out[1]["_leg_filter_end"])

    def test_trade_with_every_leg_masked_out_disappears(self):
        legs = [
            {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
            {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
        ]
        specs = [_spec(9, 1, "2025-03-05", "2025-03-27"),
                 _spec(9, 2, "2025-03-05", "2025-03-27")]
        self.assertEqual(apply_leg_filters(specs, legs), [])

    def test_leg_id_beyond_legs_list_is_left_alone(self):
        # Re-entry / synthetic rows can carry a leg_id with no config behind it.
        # They must pass through untouched, never be silently dropped.
        specs = [_spec(3, 7, "2025-03-05", "2025-03-27")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(out, specs)

    def test_other_spec_keys_are_preserved(self):
        specs = [dict(_spec(1, 2, "2025-04-07", "2025-04-30"), strike=23000.0)]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(out[0]["strike"], 23000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter.TestApplyLegFilters -v`
Expected: FAIL with `ImportError: cannot import name 'apply_leg_filters'`

- [ ] **Step 3: Implement**

Append to `backend/services/leg_filter.py`:

```python
def apply_leg_filters(
    specs: List[Dict[str, Any]],
    legs: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply every leg's individual filter to a resolved spec list, IN ORDER.

    Runs as a post-pass rather than inside each spec builder because there are
    six builders (_build_fixed_entry_specs, _build_next_expiry_specs, the two
    futures ones and the two mixed ones) and they all converge on one spec list.
    Post-processing also leaves the STRIKE epochs alone: a masked-out leg still
    anchored its Fixed/pinned epoch when the schedule was built, which is what
    we want — a mask must not silently re-strike the legs that remain.

    Returns a NEW list; `specs` is not mutated.
    """
    masks: Dict[int, List[Tuple[str, str]]] = {}
    for i, leg in enumerate(legs or []):
        m = leg_segments(leg)
        if m:
            masks[i + 1] = m
    if not masks:
        return specs  # nothing configured — identical object, zero cost

    kept: List[Dict[str, Any]] = []
    for s in specs:
        try:
            leg_id = int(s.get("leg_id") or 1)
        except (TypeError, ValueError):
            kept.append(s)
            continue
        mask = masks.get(leg_id)
        if mask is None:
            kept.append(s)
            continue
        taken, leg_exit, truncated = leg_window(
            mask, str(s.get("entry_date") or ""), str(s.get("exit_date") or "")
        )
        if not taken:
            continue
        row = dict(s)
        if truncated:
            row["exit_date"] = leg_exit
            row["_leg_filter_end"] = True
        kept.append(row)

    # A trade every one of whose legs was masked out must vanish completely
    # rather than survive as an empty trade id.
    live = {s.get("trade_id") for s in kept}
    return [s for s in kept if s.get("trade_id") in live]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add backend/services/leg_filter.py backend/tests/test_leg_filter.py
git commit -m "feat(leg-filter): apply_leg_filters spec post-pass

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the post-pass into the options path

**Files:**
- Modify: `backend/services/engine_rust.py` (after the `_apply_fixed_rollover_strike` block, ~line 5006)

**Interfaces:**
- Consumes: `apply_leg_filters` (Task 2).
- Produces: spec rows reaching `simulate_trades_batch` already masked; truncated rows carry `_leg_filter_end: True`.

**Why here:** this point is *after* `_apply_fixed_rollover_strike` (so strike epochs are computed from the unmasked schedule, as designed), *before* `_seg_clamped_keys` is built (so dropped rows can't leave stale FILTER_END markers), and *before* the `return_specs_only` early return at ~line 5033 (so the multi-index FUSED path is covered too).

- [ ] **Step 1: Add the call**

In `backend/services/engine_rust.py`, immediately after the `specs = _apply_fixed_rollover_strike(specs, payload, original_segments)` block and before the comment `# Step 2: price entries + scheduled exits.`, insert:

```python
    # ── Per-leg individual filter files ─────────────────────────────────────
    # A leg may carry its own uploaded date file. It is purely SUBTRACTIVE: the
    # strategy filter above already decided which trades exist; this only drops
    # a leg from a trade or ends its hold early (earliest of window-end and
    # trade-exit wins). No-op — same list object — when no leg has a file, so
    # every existing strategy is byte-identical.
    # See docs/superpowers/specs/2026-07-31-per-leg-filter-design.md.
    specs = apply_leg_filters(specs, payload.get("legs") or [])
    if not specs:
        return []
```

- [ ] **Step 2: Add the import**

At the top of `backend/services/engine_rust.py`, alongside the other `from services...` imports, add:

```python
from services.leg_filter import LEG_FILTER_END, apply_leg_filters
```

If the file's other service imports are function-local rather than module-level, match that style instead and import inside the enclosing function.

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_leg_filter_wiring.py` — this asserts the call site exists and is correctly ordered, without running a backtest (no market data, per the global constraint):

```python
import ast
import os
import unittest

_ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services", "engine_rust.py",
)


def _source():
    with open(_ENGINE, "r", encoding="utf-8") as fh:
        return fh.read()


class TestLegFilterWiring(unittest.TestCase):
    """The mask must be applied, and applied in the right place.

    Grep-based order checks are deliberate: the only alternative is a full
    engine run, which needs market data and would narrow the shared feather.
    """

    def test_apply_leg_filters_is_called(self):
        self.assertIn("apply_leg_filters(specs, payload.get(\"legs\")", _source())

    def test_runs_after_fixed_rollover_strike_and_before_pricing(self):
        src = _source()
        i_fixed = src.index("_apply_fixed_rollover_strike(specs, payload")
        i_mask = src.index("apply_leg_filters(specs, payload.get(\"legs\")")
        i_price = src.index("algotest_native.simulate_trades_batch(specs)")
        self.assertLess(i_fixed, i_mask, "mask must not disturb strike epochs")
        self.assertLess(i_mask, i_price, "mask must be applied before pricing")

    def test_runs_before_the_return_specs_only_early_exit(self):
        src = _source()
        i_mask = src.index("apply_leg_filters(specs, payload.get(\"legs\")")
        i_ret = src.index("if return_specs_only:")
        self.assertLess(i_mask, i_ret, "multi-index FUSED path must be masked too")

    def test_engine_module_parses(self):
        ast.parse(_source())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter_wiring -v`
Expected: PASS (if a step-1/2 edit was missed, the specific assertion names which one)

- [ ] **Step 5: Confirm nothing else broke**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest discover backend/tests 2>&1 | tail -5`
Expected: same pass/fail set as before this task.

- [ ] **Step 6: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_leg_filter_wiring.py
git commit -m "feat(leg-filter): apply per-leg mask at the spec convergence point

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: LEG_FILTER_END exit reason

**Files:**
- Modify: `backend/services/engine_rust.py` (`_FILTER_END_SKIP_REASONS` ~line 536; pre-pricing key capture ~line 5015; post-tagging after `_apply_filter_end_last_per_patch(final_priced, …)` ~line 8731)
- Modify: `backend/tests/test_leg_filter_wiring.py`

**Interfaces:**
- Consumes: `_leg_filter_end` spec key (Task 2), `LEG_FILTER_END` constant (Task 1).
- Produces: priced rows whose exit reason is `LEG_FILTER_END`.

**Why two places:** `simulate_trades_batch` rebuilds rows from the fields Rust knows and **drops Python-only spec keys** — the same reason `_cadence_expiry` is captured before pricing and re-attached after (`engine_rust.py:5040-5060`). Follow that established pattern exactly.

- [ ] **Step 1: Register the reason as un-overridable**

In `backend/services/engine_rust.py`, extend `_FILTER_END_SKIP_REASONS` (~line 536):

```python
_FILTER_END_SKIP_REASONS: frozenset = frozenset({
    "STOP_LOSS", "SL_WITH_BUFFER", "SL_WITH_BUFFER_GAP",
    "STOP_LOSS_BUFFER", "STOP_LOSS_BUFFER_GAP",
    # A leg truncated by its OWN filter file. _apply_filter_end_last_per_patch
    # tags the last trade of each STRATEGY patch; without this it would strip
    # this per-leg tag from every trade that is not that patch's last one.
    "LEG_FILTER_END",
})
```

- [ ] **Step 2: Capture the keys before pricing**

Next to the existing `_seg_clamped_keys` construction (~line 5015), add:

```python
    # Keyed by (trade_id, leg_id): a per-leg truncation is a property of ONE leg
    # row, unlike _seg_clamped which describes the whole trade.
    _leg_filter_end_keys: set = {
        (int(s.get("trade_id") or 0), int(s.get("leg_id") or 1))
        for s in specs if s.get("_leg_filter_end")
    }
```

- [ ] **Step 3: Stamp it after the strategy-patch tagger**

Immediately after the existing `_apply_filter_end_last_per_patch(final_priced, original_segments, _clamp_reason)` call (~line 8731), add:

```python
    # Per-leg filter truncation. Runs AFTER the strategy-patch tagger so it wins
    # on the rows it owns, and joins any co-occurring reason with "+" to match
    # the combined-exit-reason convention used elsewhere in this module.
    if _leg_filter_end_keys:
        for _row in final_priced:
            _k = (int(_row.get("trade_id") or 0), int(_row.get("leg_id") or 1))
            if _k not in _leg_filter_end_keys:
                continue
            _cur = str(_row.get("exit_reason") or "").strip()
            if not _cur or _cur == "EXPIRY":
                _row["exit_reason"] = LEG_FILTER_END
            elif LEG_FILTER_END not in _cur:
                _row["exit_reason"] = _cur + "+" + LEG_FILTER_END
```

- [ ] **Step 4: Write the failing test**

Append to `backend/tests/test_leg_filter_wiring.py`:

```python
class TestLegFilterEndReason(unittest.TestCase):
    def test_reason_is_protected_from_the_patch_tagger(self):
        self.assertIn("\"LEG_FILTER_END\",", _source())
        src = _source()
        i_set = src.index("_FILTER_END_SKIP_REASONS")
        i_reason = src.index("\"LEG_FILTER_END\",")
        self.assertLess(i_reason - i_set, 500,
                        "LEG_FILTER_END must be inside _FILTER_END_SKIP_REASONS")

    def test_keys_are_captured_before_pricing(self):
        src = _source()
        self.assertLess(src.index("_leg_filter_end_keys: set = {"),
                        src.index("algotest_native.simulate_trades_batch(specs)"))

    def test_stamped_after_the_patch_tagger(self):
        src = _source()
        self.assertLess(
            src.index("_apply_filter_end_last_per_patch(final_priced"),
            src.index("if _leg_filter_end_keys:"),
        )
```

- [ ] **Step 5: Run the tests**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter_wiring -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_leg_filter_wiring.py
git commit -m "feat(leg-filter): LEG_FILTER_END exit reason for truncated legs

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Trade-level Exit Date must not depend on leg order

**Files:**
- Modify: `backend/services/trade_anchor.py`
- Modify: `backend/services/algotest_job.py:502-513`
- Create: `backend/tests/test_exit_anchor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure pandas/dict logic).
- Produces: `trade_anchor.exit_anchor_row(rows) -> dict | None` — the row with the LATEST `Exit Date`, ties broken by the LOWEST `Leg`.

**Why:** `algotest_job.py:504,511` aggregate `Exit Date` and `Exit Reason` with `"first"`, which takes the anchor leg (latest *entry*). A leg truncated by its own filter has an *earlier exit* than the trade, and if it is also the anchor, the trade would report the truncated exit as the whole trade's exit — corrupting Exit Date, the pivot, WOW/MOM bucketing and the Patch-wise sheet. This is the same defect class `trade_anchor.py` was written to kill.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_exit_anchor.py`:

```python
import unittest

from backend.services.trade_anchor import exit_anchor_row


class TestExitAnchorRow(unittest.TestCase):
    def test_picks_the_latest_exit(self):
        rows = [
            {"Leg": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ]
        self.assertEqual(exit_anchor_row(rows)["Exit Reason"], "EXPIRY")

    def test_leg_order_does_not_change_the_answer(self):
        rows = [
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
            {"Leg": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
        ]
        self.assertEqual(exit_anchor_row(rows)["Exit Date"], "2025-06-26")

    def test_ties_break_on_lowest_leg(self):
        rows = [
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "B"},
            {"Leg": 1, "Exit Date": "2025-06-26", "Exit Reason": "A"},
        ]
        self.assertEqual(exit_anchor_row(rows)["Exit Reason"], "A")

    def test_legs_exiting_together_is_unchanged_behaviour(self):
        rows = [
            {"Leg": 1, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ]
        self.assertEqual(exit_anchor_row(rows), rows[0])

    def test_empty_is_none(self):
        self.assertIsNone(exit_anchor_row([]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_exit_anchor -v`
Expected: FAIL with `ImportError: cannot import name 'exit_anchor_row'`

- [ ] **Step 3: Implement in `trade_anchor.py`**

Add to `backend/services/trade_anchor.py` (and add `"exit_anchor_row"` to `__all__`):

```python
def exit_anchor_row(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The row that describes when this TRADE ended.

    The entry anchor (anchor_row) is the LATEST entry; the exit anchor is the
    LATEST exit, ties broken by the LOWEST Leg number for the same reason — to
    make the pick total without introducing a leg-order dependency.

    Legs that exit together (nearly every strategy) all share one Exit Date, so
    this reduces to Leg 1 and such runs are byte-identical to the old "first".
    It differs only when a leg ends early — a per-leg filter truncation
    (LEG_FILTER_END) — where the trade's exit is the surviving legs', not the
    truncated one's.
    """
    best: Optional[Dict[str, Any]] = None
    for r in rows or []:
        if best is None:
            best = r
            continue
        if (str(r.get("Exit Date")), -int(r.get("Leg") or 0)) > (
            str(best.get("Exit Date")), -int(best.get("Leg") or 0)
        ):
            best = r
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_exit_anchor -v`
Expected: PASS

- [ ] **Step 5: Use it in the aggregation**

In `backend/services/algotest_job.py`, change the two `"Exit Date"` / `"Exit Reason"` entries at lines 504 and 511 from `"first"` to `"last"`, and extend the `_anchor_sorted` ordering so the LAST row of each trade is the exit anchor. Replace the aggregation block:

```python
    # "first" takes the ENTRY anchor (see _anchor_sorted). Exit Date/Reason need
    # the EXIT anchor instead — the LATEST exit — because a leg truncated by its
    # own filter file (LEG_FILTER_END) ends before the trade does, and reporting
    # its date as the trade's exit would corrupt the pivot, WOW/MOM bucketing and
    # the Patch-wise sheet. Legs that exit together make the two anchors the same
    # row, so existing strategies aggregate exactly as before.
    # See services/trade_anchor.py::exit_anchor_row.
    _sorted = _anchor_sorted(trades_df)
    _exit_anchor = (
        _sorted.sort_values(["Trade", "Exit Date", "Leg"], ascending=[True, True, False])
        .groupby("Trade", as_index=False)
        .agg({"Exit Date": "last", "Exit Reason": "last"})
    )
    aggregated = _sorted.groupby("Trade", as_index=False).agg({
        "Entry Date": "first",
        "Entry Spot": "first",
        "Exit Spot": "first",
        "Spot P&L": "first",
        "CE P&L": "sum",
        "PE P&L": "sum",
        "FUT P&L": "sum",
    })
    aggregated = aggregated.merge(_exit_anchor, on="Trade", how="left")
```

Note `Exit Date` was already coerced to datetime at line 483-485, so `sort_values` orders it chronologically, not lexically. Keep the resulting column order identical to before by reindexing if any downstream consumer depends on it:

```python
    aggregated = aggregated[[
        "Trade", "Entry Date", "Exit Date", "Entry Spot", "Exit Spot",
        "Spot P&L", "CE P&L", "PE P&L", "FUT P&L", "Exit Reason",
    ]]
```

- [ ] **Step 6: Verify unchanged for ordinary strategies**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest discover backend/tests 2>&1 | tail -5`
Expected: same pass/fail set as before this task. Any newly failing test here is a real regression — legs that exit together must aggregate identically.

- [ ] **Step 7: Commit**

```bash
git add backend/services/trade_anchor.py backend/services/algotest_job.py backend/tests/test_exit_anchor.py
git commit -m "fix(trade-anchor): trade Exit Date/Reason from the latest leg exit

A leg truncated by its own filter file exits before the trade does;
'first' would report the truncated date as the trade's exit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Futures builders

**Files:**
- Modify: `backend/services/engine_rust.py` — `_build_futures_specs` (~line 1351, the pricing loop at ~1502) and `_build_fixed_entry_futures_specs` (~line 2531)

**Interfaces:**
- Consumes: `leg_segments`, `leg_window` (Task 1).
- Produces: futures rows masked identically to option rows.

**Why not the Task 3 post-pass:** the futures paths return **already-priced** rows and skip `simulate_trades_batch` entirely (`engine_rust.py:4718`, `4711`). Truncating an exit after pricing would leave the P&L computed over the wrong window. The mask must therefore run inside those builders, before `_fut_price` / `_resolve_futures_pnl_native` is called.

- [ ] **Step 1: Mask inside `_build_futures_specs`**

In the per-leg loop, immediately before the first `entry_price_raw = _fut_price(...)` call (~line 1502), insert:

```python
                # Per-leg individual filter (same rule as the options path, but
                # applied BEFORE pricing because futures rows are priced here and
                # never pass through apply_leg_filters).
                _lf_mask = leg_segments(leg)
                if _lf_mask is not None:
                    _lf_taken, _lf_exit, _lf_trunc = leg_window(
                        _lf_mask, entry_date, fut_exit_date
                    )
                    if not _lf_taken:
                        continue
                    if _lf_trunc:
                        fut_exit_date = _last_trading_day_on_or_before(_lf_exit, sorted_td) or _lf_exit
                        if fut_exit_date <= entry_date:
                            continue
                        _leg_filter_end_row = True
                    else:
                        _leg_filter_end_row = False
                else:
                    _leg_filter_end_row = False
```

Then, where the row dict for this leg is appended, add `"_leg_filter_end": _leg_filter_end_row` and set its exit reason to `LEG_FILTER_END` when true (futures rows carry their reason directly; follow the row's existing `exit_reason` key).

- [ ] **Step 2: Mask inside `_build_fixed_entry_futures_specs`**

Apply the identical block in that function's per-leg loop, before its pricing call, using its own entry/exit variable names.

- [ ] **Step 3: Write the failing test**

Append to `backend/tests/test_leg_filter_wiring.py`:

```python
class TestFuturesPathMasked(unittest.TestCase):
    """Futures rows are priced inside their builders and never reach
    apply_leg_filters, so the mask must appear in both futures builders."""

    def _slice(self, src, fn_name):
        start = src.index("def %s(" % fn_name)
        nxt = src.index("\ndef ", start + 1)
        return src[start:nxt]

    def test_build_futures_specs_applies_the_mask(self):
        body = self._slice(_source(), "_build_futures_specs")
        self.assertIn("leg_segments(leg)", body)
        self.assertIn("leg_window(", body)

    def test_fixed_entry_futures_specs_applies_the_mask(self):
        body = self._slice(_source(), "_build_fixed_entry_futures_specs")
        self.assertIn("leg_segments(leg)", body)
        self.assertIn("leg_window(", body)

    def test_mask_precedes_pricing_in_build_futures_specs(self):
        body = self._slice(_source(), "_build_futures_specs")
        self.assertLess(body.index("leg_window("), body.index("_fut_price(index, entry_date"))
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter_wiring -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_leg_filter_wiring.py
git commit -m "feat(leg-filter): mask futures legs before they are priced

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Spot-adjustment guard

**Files:**
- Modify: `backend/services/engine_rust.py` (spot-adjustment cascade, ~lines 7549-7601 and the re-entry synthesis around 8427)

**Interfaces:**
- Consumes: `_leg_filter_end_keys` (Task 4).
- Produces: no spot-adj exit or re-entry generated for a leg past its own filter end.

**Why:** the cascade scans a trade's window for a spot breach and truncates/re-enters. A leg that already ended at its own `LEG_FILTER_END` must not be given a later spot-adj exit date (that would resurrect it past its window), and a breach check must tolerate the leg being absent from the trade entirely.

- [ ] **Step 1: Skip truncated legs in the cascade**

Where the cascade assigns `reason = _sa_clamp_reason` and a spot-adj exit date to a row (~line 7598), add a guard before the assignment:

```python
            # A leg already ended by its OWN filter file cannot be re-exited or
            # re-entered later by the spot-adjustment cascade — that would place
            # it outside the window its file allows.
            if (int(row.get("trade_id") or 0), int(row.get("leg_id") or 1)) in _leg_filter_end_keys:
                continue
```

Use the row-variable name in scope at that site.

- [ ] **Step 2: Tolerate a missing leg in the breach check**

Where the cascade reads a trade's legs to evaluate a breach, replace any indexed access (`legs[0]`, `rows[1]`, etc.) with a lookup that skips absent legs. If the breach evaluation already iterates the trade's rows, no change is needed — confirm by reading the block and record the finding in the commit message.

- [ ] **Step 3: Write the failing test**

Append to `backend/tests/test_leg_filter_wiring.py`:

```python
class TestSpotAdjGuard(unittest.TestCase):
    def test_cascade_skips_legs_ended_by_their_own_filter(self):
        src = _source()
        self.assertIn("in _leg_filter_end_keys:", src)
        # The guard must live in the spot-adj cascade, i.e. after the reason
        # assignment site, not only in the tagging block from Task 4.
        self.assertGreater(
            src.rindex("in _leg_filter_end_keys:"),
            src.index("_sa_clamp_reason = spot_adj_reasons.get"),
        )
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_leg_filter_wiring -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_leg_filter_wiring.py
git commit -m "fix(leg-filter): spot-adj cascade must not revive a filter-ended leg

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Optimizer coverage gate

**Files:**
- Modify: `backend/services/optimizer/rust_combo_loop.py:191`
- Modify: `backend/tests/test_rust_combo_whitelist.py`

**Interfaces:**
- Consumes: the `filter_segments` leg key (Task 2).
- Produces: `rust_batch_unsupported(payload)` returns `"leg_filter"` when any leg carries a mask.

**Why:** the existing gate returns `"filter"` only for payload-level `filter_config` / `filter_segments`. A leg-level mask with the strategy filter OFF slips straight through, and the Rust batch would price the leg over its full window — wrong numbers with no error. The gate is FAIL-CLOSED by design; this keeps it that way.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_rust_combo_whitelist.py`, inside the excluded-features test class:

```python
    def test_per_leg_filter_is_unsupported(self):
        leg = {"option_type": "CE", "position": "SELL",
               "filter_segments": [{"start": "2025-04-05", "end": "2025-06-05"}]}
        self.assertEqual(rust_batch_unsupported({"legs": [leg]}), "leg_filter")

    def test_empty_per_leg_filter_is_still_supported(self):
        # Uploaded-then-cleared must not push the combo off the Rust batch.
        leg = {"option_type": "CE", "position": "SELL", "filter_segments": []}
        self.assertIsNone(rust_batch_unsupported({"legs": [leg]}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_rust_combo_whitelist -v`
Expected: FAIL — `None != 'leg_filter'`

- [ ] **Step 3: Implement**

In `backend/services/optimizer/rust_combo_loop.py`, immediately after the payload-level filter check at line 191-192, add:

```python
        # Per-leg individual filter files are applied by the Python spec
        # post-pass (services/leg_filter.py); the Rust batch has no notion of
        # them and would price the masked leg over its full window.
        for _leg in (p.get("legs") or []):
            if isinstance(_leg, dict) and _truthy(_leg.get("filter_segments")):
                return "leg_filter"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/aff34/Downloads/Algo_Test_Software && python -m unittest backend.tests.test_rust_combo_whitelist -v`
Expected: PASS

- [ ] **Step 5: Verify the mask survives combo expansion**

`param_expander` deep-copies the base payload and sets swept paths, so unknown leg keys should survive. Prove it rather than assume it — add to `backend/tests/test_rust_combo_whitelist.py`:

```python
    def test_param_expander_preserves_per_leg_filter_segments(self):
        from backend.services.optimizer.param_expander import apply_combo_for_optim
        base = {"legs": [{"option_type": "CE", "position": "SELL",
                          "filter_segments": [{"start": "2025-04-05",
                                               "end": "2025-06-05"}]}]}
        out = apply_combo_for_optim(base, {"legs[0].stopLoss.value": 30})
        self.assertEqual(len(out["legs"][0]["filter_segments"]), 1)
```

If `apply_combo_for_optim` has a different name or signature, read `backend/services/optimizer/param_expander.py` and adjust the call — the assertion is what matters. If the key is NOT preserved, fix `param_expander` to carry it and keep this test.

- [ ] **Step 6: Commit**

```bash
git add backend/services/optimizer/rust_combo_loop.py backend/tests/test_rust_combo_whitelist.py
git commit -m "fix(optimizer): fail closed on per-leg filter in the Rust batch gate

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: UI — Individual Filter per leg card

**Files:**
- Modify: `frontend/src/components/StrategyBuilder.jsx` (leg card render ~line 3466; leg payload serialisation ~line 2220)

**Interfaces:**
- Consumes: existing `POST /api/upload-filter-csv` (returns `{success, segments: [{start_date, end_date}, …]}`) and the existing `updateLeg(id, field, value)` helper at line 1859.
- Produces: `leg.filter_segments` in the backtest payload.

- [ ] **Step 1: Add the upload handler**

Near the other leg helpers in `StrategyBuilder.jsx`, add:

```jsx
  const handleLegFilterUpload = async (legId, event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    updateLeg(legId, 'filter_uploading', true);
    updateLeg(legId, 'filter_error', '');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/upload-filter-csv', { method: 'POST', body: formData });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data = await res.json();
      if (!data.success) throw new Error(data.message || 'Failed to parse CSV');
      const segs = (data.segments || []).map(s => ({
        start: s.start_date || s.start,
        end: s.end_date || s.end,
      }));
      if (!segs.length) throw new Error('No valid date ranges found');
      updateLeg(legId, 'filter_segments', segs);
      updateLeg(legId, 'filter_file_name', file.name);
    } catch (err) {
      updateLeg(legId, 'filter_error', err.message || 'Upload failed');
      updateLeg(legId, 'filter_segments', []);
    } finally {
      updateLeg(legId, 'filter_uploading', false);
      event.target.value = '';
    }
  };
```

- [ ] **Step 2: Add the checkbox + file input to the leg card**

In the leg card body (following the `Buffer position` block's markup conventions at line 3466), and **only when `leg.segment !== 'midcap100'`** — the Midcap overlay rides base trades and has no schedule to mask:

```jsx
{leg.segment !== 'midcap100' && (
  <div className="space-y-1">
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={!!leg.individual_filter}
        onChange={e => {
          updateLeg(leg.id, 'individual_filter', e.target.checked);
          if (!e.target.checked) {
            updateLeg(leg.id, 'filter_segments', []);
            updateLeg(leg.id, 'filter_file_name', '');
            updateLeg(leg.id, 'filter_error', '');
          }
        }}
        className="accent-blue-600"
      />
      <span className="text-xs font-semibold text-muted uppercase tracking-wide">
        Individual filter
      </span>
    </label>
    {leg.individual_filter && (
      <div className="space-y-1 pl-6">
        <input
          type="file"
          accept=".csv"
          onChange={e => handleLegFilterUpload(leg.id, e)}
          className="text-[11px]"
        />
        {leg.filter_uploading && <p className="text-[11px] text-muted">Uploading…</p>}
        {leg.filter_error && <p className="text-[11px] text-red-600">{leg.filter_error}</p>}
        {!!(leg.filter_segments || []).length && (
          <p className="text-[11px] text-muted">
            {leg.filter_file_name} — {leg.filter_segments.length} range(s).
            This leg trades only on dates inside them; it exits at whichever
            comes first, its own range end or the trade's exit.
          </p>
        )}
      </div>
    )}
  </div>
)}
```

- [ ] **Step 3: Serialise into the leg payload**

In the leg payload builder (~line 2220, beside the `if (segmentType === 'futures')` block), add — outside any segment-specific branch so it applies to option and futures legs alike:

```jsx
      if (l.individual_filter && (l.filter_segments || []).length) {
        leg.filter_segments = l.filter_segments;
      }
```

- [ ] **Step 4: Build the frontend**

The `dist` is root-owned and this network re-signs TLS, so build in a container (from `CLAUDE.md`):

```bash
cd /home/aff34/Downloads/Algo_Test_Software && docker run --rm -e NODE_TLS_REJECT_UNAUTHORIZED=0 -v "$PWD/frontend":/src \
  node:22-bookworm-slim sh -c 'npm config set strict-ssl false; \
    mkdir -p /build && cp -r /src/. /build/ && cd /build && rm -rf node_modules dist && \
    npm install --no-audit --no-fund && npm run build && \
    rm -rf /src/dist && cp -r /build/dist /src/dist'
```

Expected: build completes, `frontend/dist/` regenerated. Never hand-edit `dist`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/StrategyBuilder.jsx frontend/dist
git commit -m "feat(ui): per-leg Individual Filter checkbox and CSV upload

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: End-to-end parity verification

**Files:**
- Create: `docs/superpowers/plans/2026-07-31-per-leg-filter-verification.md` (the recorded evidence)

**Interfaces:**
- Consumes: everything above.
- Produces: signed-off evidence that the feature is correct and that nothing else moved.

**This task runs real backtests and is done by a human at the UI or via the API — not in `unittest`**, because the suite must never touch market data (global constraint).

- [ ] **Step 1: Deploy**

```bash
cd /home/aff34/Downloads/Algo_Test_Software && sudo ./start.sh
```

Before deploying, confirm no jobs are running (queue, `mem_gate` and active-optims all empty) — restarting workers mid-job strands a `mem_gate` reservation for ~40 minutes.

- [ ] **Step 2: Regression run — no individual filter**

Run any existing 2-leg strategy with a strategy filter, download the tradesheet, and diff it against the same run captured before this branch. Expected: **byte-identical**.

Record: strategy config, date range, and the diff result.

- [ ] **Step 3: Case 1 — both legs in window**

2-leg strategy, strategy filter ON, leg 2 given a file whose ranges cover the whole strategy filter. Expected: tradesheet identical to Step 2's run.

- [ ] **Step 4: Case 2 — leg absent**

Leg 2 given a file that excludes a stretch the strategy filter includes. Verify in the tradesheet, trade by trade:

- trades whose entry falls in the excluded stretch have **only the leg 1 row**;
- their trade-level Net P&L equals leg 1's P&L alone;
- their trade-level Exit Date is leg 1's exit;
- trades outside that stretch are unchanged versus Step 3.

- [ ] **Step 5: Truncation**

Give leg 2 a file whose range **ends mid-trade**. Verify:

- leg 2's Exit Date is the range end (or the last trading day on or before it);
- leg 2's Exit Reason is `LEG_FILTER_END`;
- leg 1's Exit Date and Reason are unchanged;
- the trade-level Exit Date is **leg 1's** (the later one), not leg 2's;
- leg 2's MAE/MFE are computed over the shortened window.

- [ ] **Step 6: Case 3 — outside the strategy filter**

Give leg 2 a file with a range the strategy filter excludes entirely. Expected: **no trades at all** in that period — the individual file must not create trades.

- [ ] **Step 7: Optimizer parity**

Run an optimization whose base config is the Step 5 strategy. Verify the per-combo tradesheet for the matching combo is **identical** to the Step 5 direct backtest, and that the master summary metrics agree. Confirm the run did not silently take the Rust batch path (Task 8's gate should report `leg_filter`).

- [ ] **Step 8: Record the evidence and commit**

Write each step's config, expectation and observed result into `docs/superpowers/plans/2026-07-31-per-leg-filter-verification.md`, then:

```bash
git add docs/superpowers/plans/2026-07-31-per-leg-filter-verification.md
git commit -m "docs: per-leg filter end-to-end verification evidence

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Case 1 — both in window, unchanged | 2 (unit), 10 §3 |
| Case 2 — leg absent, trade survives | 2, 3, 10 §4 |
| Case 3 — outside strategy filter, no trade | untouched by design; 10 §6 proves it |
| Exit rule, earliest wins | 1 (`leg_window`), 2, 10 §5 |
| Strategy filter OFF → range acts as the filter | inherent (`effective_segs = [(from,to)]`); the mask is independent of it |
| UI checkbox + upload per leg card | 9 |
| Midcap legs excluded | 9 §2 |
| `_trade_resolved` — masked-out ≠ unresolvable | 2 (post-pass runs after resolution, so the two can't be confused) |
| All legs masked → trade disappears | 2 |
| Trade Exit Date/Reason not leg-order dependent | 5 |
| `LEG_FILTER_END` distinct reason, protected | 4 |
| Spot-adj must not revive a filter-ended leg | 7 |
| Optimizer parity / coverage gate | 8, 10 §7 |
| `param_expander` preserves the key | 8 §5 |
| MAE/MFE over the truncated window | free — `simulate.rs:2013` windows on the spec's own exit; 10 §5 proves it |
| Carry-aware slippage on return | free — slippage keys on strike/expiry change vs the leg's own previous row |
| No-file runs byte-identical | 1 §6, 3 §5, 5 §6, 10 §2 |

**Placeholder scan:** none — every step names exact files, exact anchors, and contains the code or command to run. Task 6 §2 and Task 7 §2 require reading the surrounding block to pick variable names; both state exactly what to look for and what to record.

**Type consistency:** `seg_iso`, `normalize_segments`, `leg_segments`, `leg_window`, `apply_leg_filters`, `LEG_FILTER_END`, `exit_anchor_row` are spelled identically in every task that uses them. `leg_window` returns `(taken, leg_exit, truncated)` in Tasks 1, 2 and 6 alike. The spec key is `filter_segments` and the marker key `_leg_filter_end` throughout.

**Known deviation from the spec, deliberate:** the spec described masking at spec *emission* inside the builders. This plan masks as a *post-pass* over the converged spec list instead — one call site rather than six, and it leaves the Fixed/pinned strike epochs computed from the unmasked schedule (a mask must not silently re-strike the surviving legs). Behaviour is identical; futures builders still mask inline because they price internally.
