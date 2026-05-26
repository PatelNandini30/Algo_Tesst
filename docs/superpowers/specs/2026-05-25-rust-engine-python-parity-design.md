# Rust Engine ↔ Python Engine Full Parity

**Date:** 2026-05-25  
**Status:** Approved  
**Constraint:** Do not break any existing logic. All 20 existing parity tests must continue to pass.

---

## Context

The Rust engine (`backend/services/engine_rust.py` + `backend/native/`) handles all options-based strategies with full parity. Three hard fallback conditions still cause the Rust path to return `None` and the Python engine to run the full strategy instead. Two output columns are always empty in Rust tradesheet output. This spec closes all five gaps.

---

## Scope

All changes are in `backend/services/engine_rust.py` only.  
**No Rust code changes. No `maturin` rebuild. No Docker rebuild.**

---

## Gap 1: FUTURES + SL / Target / TrailSL

### Current behaviour
`_build_futures_specs` returns `None` if any FUTURES leg has `stopLoss`, `targetProfit`, or `trailSL` configured (lines 478–482).

### Fix

#### New helper: `_scan_futures_sl_target`

```
_scan_futures_sl_target(
    entry_date: str,
    entry_price: float,
    position: str,          # "BUY" or "SELL"
    leg_cfg: dict,          # raw leg dict (stopLoss, targetProfit, trailSL keys)
    trading_days: List[str],
    scheduled_exit: str,    # original DTE exit date (inclusive upper bound)
    index: str,             # e.g. "NIFTY"
    expiry_str: str,        # futures contract expiry on entry_date
) -> Tuple[str, float, str]   # (actual_exit_date, actual_exit_price, exit_reason)
```

Logic (mirrors `check_leg_stop_loss_target` in Python engine):
1. Iterate `trading_days` from `entry_date + 1` to `scheduled_exit` (inclusive).
2. For each day, get current futures price via fast_lookup using the futures key format (verify exact key shape from `base.py` / `data_loader.py` during implementation — options use `(symbol, expiry, strike, type, date)`; futures use `None`/`0` for strike/type).  If key absent for a day, try the next contract expiry (rollover day).  If still absent, stop scan and use scheduled exit.
3. Compute move from entry: `pnl_pct = (current - entry) / entry * 100` (sign flipped for SELL).
4. **SL check** (type pct/points/underlying_pct/underlying_pts): if adverse move ≥ threshold → exit.
5. **Target check**: if favourable move ≥ threshold → exit.
6. **TrailSL**: track `best_pnl` seen so far; once `best_pnl ≥ trail_trigger`, armed; exit when `best_pnl - current_pnl ≥ trail_move`.
7. On trigger: call `resolve_futures_pnl_with_rollover` with `to_date=trigger_day` to get the precise exit price.  Return `(trigger_day, exit_price, "SL"|"TARGET"|"TRAIL_SL")`.
8. If nothing fires: return `(scheduled_exit, original_exit_price, "EXPIRY")`.

#### In `_build_futures_specs`
- Remove `return None` block at lines 478–482.
- After computing `(entry_price_raw, exit_price_raw, fut_expiry)` via `resolve_futures_pnl_with_rollover`, call `_scan_futures_sl_target`.
- Overwrite `exit_price_raw` and `exit_reason` from its result.

---

## Gap 2: FUTURES + Re-entry

### Current behaviour
`_build_futures_specs` returns `None` if any FUTURES leg has `reEntryOnSL` or `reEntryOnTarget` (line 484–485).

### Fix

#### In `_build_futures_specs` (after Gap 1 changes)
- Remove `return None` block at lines 484–485.
- After a trade row is finalised with its actual exit (possibly SL-triggered from Gap 1):
  - If exit_reason matches the configured re-entry trigger (SL → `reEntryOnSL`, TARGET → `reEntryOnTarget`):
    - Find next trading day after `exit_date`. If no trading day exists before the original scheduled exit, skip re-entry for this cycle.
    - Call `resolve_futures_pnl_with_rollover` for `new_entry → original_scheduled_exit` (re-entry cannot extend past the original DTE exit).
    - Apply `_scan_futures_sl_target` for the new window.
    - Append new row with same `trade_id`, incremented `reentry_index`.
    - Repeat up to `reEntryCount` times.
- Re-entry rows carry `reentry_index` (1-based), `reentry_trigger`, `reentry_mode` in the row dict.

---

## Gap 3: FUTURES + NEXT_WEEKLY mixed

### Current behaviour
`run_rust_engine_pipeline` returns `None` when `_has_futures_leg AND _has_next_leg` (line 1706–1707).

### Fix

In `run_rust_engine_pipeline`, replace `return None` with:

1. **Split legs** into `futures_legs` and `option_legs` by `segment`.
2. **Build FUTURES rows** — clone payload with only `futures_legs`, call `_build_futures_specs`. If this returns `None`, return `None` (preserves safe fallback).
3. **Build option specs** — clone payload with only `option_legs`, call `_build_next_expiry_specs`. If `None`, return `None`.
4. **Price option specs** via `algotest_native.simulate_trades_batch`.
5. **Align by period** — group both result sets by `(entry_date, exit_date)` window; assign consistent `trade_id`s.  Windows present in one set but not the other are kept (leg may not trade that cycle).
6. **Merge** — concatenate aligned rows. The merged list feeds the existing SL/re-entry orchestration below.

If any step above raises an exception, catch and return `None` (falls back to Python).

---

## Gap 4: ReEntryIndex / ReEntryTrigger / ReEntryMode columns

### Current behaviour
`priced_to_tradesheet_records` hardcodes these to `""` (lines 1515–1517).

### Fix

#### New dict: `reentry_meta_map`
Type: `Dict[Tuple[int, int, str], Tuple[int, str, str]]`  
Key: `(trade_id, leg_id, entry_date)`  
Value: `(reentry_index, reentry_trigger, reentry_mode)`

Populate this during the existing re-entry orchestration loop (around lines 2157–2400):
- Original rows: `reentry_index=0`, trigger/mode empty.
- First re-entry: `reentry_index=1`, trigger=`"SL"`/`"TARGET"`, mode=`"RE_ASAP"`/etc.

Pass `reentry_meta_map` into `priced_to_tradesheet_records` as optional kwarg (`default={}`).  Existing call sites that don't pass it get `{}` → columns stay `""` → **no behaviour change for non-re-entry strategies**.

In `priced_to_tradesheet_records`, look up each row:
```python
meta = reentry_meta_map.get((trade_id, leg_id, entry_date), (0, "", ""))
"ReEntryIndex": meta[0] if meta[0] > 0 else "",
"ReEntryTrigger": meta[1],
"ReEntryMode": meta[2],
```

Matching Python engine convention: original row `ReEntryIndex=""`, first re-entry `ReEntryIndex=1`.

---

## Gap 5: Exit Reason for FUTURES

### Current behaviour
`_build_futures_specs` hardcodes `"exit_reason": "EXPIRY"` for all FUTURES rows (line 554).

### Fix
Flow naturally from Gap 1: `_scan_futures_sl_target` returns the actual reason.  Line 554 is replaced with the value returned by `_scan_futures_sl_target`.

---

## Stale docstring

Update `algotest_job.py:290–295` to remove the outdated list of fallback conditions. Replace with the accurate short list (only 3 FUTURES edge cases, which after this work become zero).

---

## Safety rules (non-negotiable)

1. Every new code branch that calls external functions (`resolve_futures_pnl_with_rollover`, fast_lookup, etc.) is wrapped in `try/except` returning `None` so the Python fallback is preserved if data is missing.
2. No changes to the options SL/Target/re-entry path (lines ~1880–2640).
3. `priced_to_tradesheet_records` signature change is backwards-compatible (new kwarg defaults to `{}`).
4. All 20 existing parity tests must pass after this work.

---

## Verification

```bash
# Run all existing parity tests (must all pass, 0 fallbacks)
cd backend && python -m unittest tests.test_engine_rust_pipeline -v

# New archetypes to add to test_engine_rust_pipeline.py
#   futures_with_sl          — FUTURES leg + stopLoss pct
#   futures_with_trail_sl    — FUTURES leg + trailSL
#   futures_with_reentry_sl  — FUTURES leg + reEntryOnSL
#   futures_next_weekly_mix  — one FUTURES leg + one NEXT_WEEKLY option leg

# Parity check: each new archetype must produce identical P&L to Python engine
# within ±0.01 ₹ tolerance (same rule as existing tests)
```

---

## Files changed

| File | Type of change |
|---|---|
| `backend/services/engine_rust.py` | New helpers, remove 3 `return None` gates, populate re-entry metadata |
| `backend/services/algotest_job.py` | Update stale docstring only |
| `backend/tests/test_engine_rust_pipeline.py` | Add 4 new parity archetypes |
