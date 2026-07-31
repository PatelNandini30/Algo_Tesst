# Per-Leg Individual Filter Files — Design

Date: 2026-07-31
Status: Approved, not implemented

## Problem

Today the Filter is strictly strategy-level. `_load_filter_segments()`
(`backend/services/engine_rust.py:443`) resolves one segment list, the scheduler
iterates it, and every leg of a trade inherits the same entry/exit window.

Users need to **exclude individual legs from some trades** without changing which
trades exist — e.g. a PE leg that should sit out a known adverse stretch while the
CE leg keeps trading.

## Requirement (as specified)

The strategy filter remains the **sole gate on whether a trade exists**. An
individual per-leg file is a **subtractive mask** — it can only remove a leg or
shorten its hold. It can never create a trade, widen a window, or move a leg onto
a date the strategy filter excludes.

Behaviour for a 2-leg strategy, leg 1 on the strategy filter, leg 2 with its own file:

| Trade entry date | Strategy filter | Individual file | Result |
|---|---|---|---|
| Case 1 | in | in | Both legs taken (unchanged) |
| Case 2 | in | out | **Only leg 1 taken; leg 2 absent** (new) |
| Case 3 | out | — | No trade at all (unchanged) |

**Exit rule — earliest wins.** When leg 2 is taken, its exit is
`min(individual window end, trade exit)`:

- individual window ends 05-Jun, trade exits 26-Jun → leg 2 exits **05-Jun**
- individual window ends 28-Jun, trade exits 26-Jun → leg 2 exits **26-Jun**

**Strategy filter OFF:** the backtest's from/to range plays the role of the
strategy filter (already what the engine does internally — `effective_segs =
[(from_date, to_date)]`, `engine_rust.py:2221`). Trades are taken as normal and
the individual file still masks its leg.

**Optimizer:** must honour per-leg filters and produce numbers identical to a
direct backtest — the standing optim == backtest rule.

## Why this is cheap

The foundation already exists:

- The scheduler emits **per-leg spec rows** — `{trade_id, leg_id, entry_date,
  exit_date, expiry, strike, ...}` (`engine_rust.py:2477`). Nothing forces legs of
  one trade to share a window; the builder just writes the same dates into each.
- `simulate.rs` prices **each spec over its own `entry_date..exit_date`**
  (`simulate.rs:1642`), including the MAE/MFE window (`simulate.rs:2013`). A leg
  exiting early needs no new plumbing.
- `services/trade_anchor.py` already exists because legs can have differing entry
  dates (carried YEARLY leg vs re-entering weekly leg). Divergent per-leg windows
  are the designed-for case, not a new one.
- Upload plumbing exists: `POST /upload-filter-csv` → `base.parse_filter_csv`
  (day-first `Start,End` / `Entry,Exit`) → inline segments in the payload.

## Design

### 1. UI (`frontend/src/components/StrategyBuilder.jsx`)

A checkbox `Individual Filter` on each leg card. When checked, a file input in the
same card posts to the existing `POST /upload-filter-csv`; the returned segments
are stored on the leg as `leg.filter_segments` (`[{start, end}, ...]`) and travel
in the payload with the rest of the leg.

Hidden for `segment === 'midcap100'` legs — the Midcap overlay rides base trades
and has no schedule of its own to mask.

The checkbox is available whether or not the strategy filter is on.

### 2. Engine — one chokepoint

Inside the existing per-leg loop in the fixed/DTE spec builders, where
`current_entry` and `exit_date` are already resolved and immediately before the
spec is appended (`engine_rust.py:2477`):

```
mask = leg.get("filter_segments")          # None/empty when no individual file
if mask:
    seg = segment_containing(mask, current_entry)
    if seg is None:
        -> leg is ABSENT for this trade            (case 2)
    leg_exit = min(exit_date, last_trading_day_on_or_before(seg["end"]))
    if leg_exit <= current_entry:
        -> leg is ABSENT for this trade            (degenerate window)
    spec["exit_date"]        = leg_exit
    spec["_leg_filter_end"]  = leg_exit < exit_date
```

`_load_filter_segments()` and the segment loop are **untouched** — the strategy
filter remains the sole trade generator. Segment boundaries are normalised to ISO
by the same `_seg_iso` day-first rules used for strategy filters.

The rollover same-day chain continues to advance on the **trade's** `exit_date`,
never a leg's truncated one. Runs with no individual file are bit-identical to
today.

### 3. Downstream corrections

Each of these is a real defect if skipped:

1. **`_trade_resolved` must distinguish masked-out from unresolvable.** Today any
   failed leg drops the whole trade (`engine_rust.py:2470`). "Masked out" is a
   legitimate absence and must not drop the trade; "strike unresolvable" must
   still drop it. Two distinct paths.
2. **All legs masked out → no trade emitted**, and `trade_id` is not consumed —
   mirrors the existing dropped-trade rule.
3. **Trade-level `Exit Date` / `Exit Reason` aggregation.**
   `services/algotest_job.py:504,511` aggregate with `"first"`, which is leg-order
   dependent. A truncated leg placed first would report the wrong trade exit. Trade
   exit becomes the **latest** leg exit — same class of fix as, and consistent
   with, `services/trade_anchor.py`.
4. **Spot-adjustment cascade** must not fire on a leg after its own filter end, and
   must tolerate a missing leg when breach-checking.

### 4. Exit reason

A leg truncated by its own file is tagged **`LEG_FILTER_END`**, distinct from
`FILTER_END`. `_tag_filter_end` (`engine_rust.py:547`) anchors on the last trade
of each *strategy* patch; reusing the same string would let the two collide
invisibly. The new tag is added to `_FILTER_END_SKIP_REASONS` so the strategy-patch
tagger never overwrites it.

### 5. Optimizer

`leg.filter_segments` rides the leg through `base_payload`, so per-combo backtests
inherit it. Two things must be verified rather than assumed:

- `services/optimizer/param_expander.py` must not strip unknown leg keys.
- The Rust batch loop's `rust_batch_unsupported` coverage gate must flag per-leg
  filters until they are ported, otherwise mode-1 silently ignores the mask and
  emits wrong numbers.

Combo labels and filenames are unchanged — the mask is not a swept parameter.

## Correct by construction

- **Per-leg MAE/MFE** over the truncated window — `simulate.rs:2013` already
  windows on the spec's own entry/exit. (This is the exact bug that had to be fixed
  by hand in the intraday engine.)
- **Carry-aware slippage** — a leg that goes absent and returns is a strike/expiry
  change versus its own previous row, so it is correctly re-slipped.
- **Net P&L** — recomputed from per-leg CE/PE/FUT columns by `trade_anchor.py`, so
  a trade with fewer legs sums correctly.

## Testing

One runnable check: a 2-leg strategy with leg 2 masked so it is excluded from
trade A and truncated in trade B. Assert:

- leg 2 has no row in trade A;
- leg 2's exit in trade B `== min(window end, trade exit)`, reason `LEG_FILTER_END`;
- trade-level Exit Date for trade B is still the **full** trade exit;
- Net P&L for trade A equals leg 1 alone;
- a run with no individual file is byte-identical to the current output.

Per the repo's hard rule, the per-combo optimizer tradesheet for the same config
must match the direct backtest exactly.

## Decisions taken

- Midcap overlay legs cannot carry an individual filter.
- `LEG_FILTER_END` is a distinct exit reason.
- All legs masked out → the trade is skipped entirely.
- The individual file is an upload only; no named/DB per-leg filters.

## Out of scope

- Per-leg filters that *add* trades or shift entries onto other dates.
- Per-leg selection of a named filter from `filter_date_sets`.
- Any change to strategy-level filter behaviour.
