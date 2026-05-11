# Stop Loss with Buffer — Design Spec

**Date:** 2026-05-11  
**Status:** Approved

---

## Context

The current per-leg stop loss exits at the EOD close price on the day SL is triggered. In gap scenarios (e.g., market opens 30% above the previous close due to overnight events), the engine reports an unrealistically clean exit at that close price. In reality, the trader would pay significantly more (for SELL legs) or receive significantly less (for BUY legs) due to gap-open market impact.

"Stop Loss with Buffer" is a new, separate leg-level toggle that models this realistically: when a gap fires the SL, the exit price is computed as `close × (1 ± buffer%)`, bounded by the day's HIGH or LOW. This gives a more conservative (pessimistic) and accurate backtest result.

---

## Scope

- New feature only — zero changes to existing SL, trailing SL, target, overall SL, or any other existing feature.
- Applies to: regular legs (all 4 SL modes) and lazy legs (Points and Percent modes).
- Works with and without filters, across all index symbols.
- Mutually exclusive with the existing Stop Loss toggle in the UI.

---

## Feature Behavior

### Trigger Condition

Identical logic to regular SL for the chosen mode:

| Mode | Trigger when |
|------|-------------|
| `pct` | `adverse_pct ≥ sl_buffer_value` |
| `points` | `adverse_premium_pts ≥ sl_buffer_value` |
| `underlying_pts` | `adverse_spot_pts ≥ sl_buffer_value` |
| `underlying_pct` | `adverse_spot_pct ≥ sl_buffer_value` |

### Gap Detection

Buffer exit price is applied **only when** the previous holding day's adverse move was **below** the SL threshold (i.e., SL was not about to fire yesterday — today's close breached it in one step).

- For `pct` / `points` modes: compare previous holding day's option close to SL threshold in premium space.
- For `underlying_pts` / `underlying_pct` modes: compare previous holding day's **spot** adverse move to the SL threshold (spot space), since these modes trigger on spot movement.
- If `idx == 0` (first holding day after entry): always treated as a gap.
- If gap is NOT detected (close gradually crossed the threshold): exit at close as normal.

### Exit Price Formula

```
# SELL leg — exits by buying back; worse price is higher
buffer_price     = current_close × (1 + buffer_pct / 100)
exit_price_raw   = min(buffer_price, day_HIGH_of_option)

# BUY leg — exits by selling; worse price is lower
buffer_price     = current_close × (1 - buffer_pct / 100)
exit_price_raw   = max(buffer_price, day_LOW_of_option)
```

Slippage is applied to `exit_price_raw` using the existing `_apply_slippage()` function, identical to regular SL.

For underlying-based modes (`underlying_pts`, `underlying_pct`): the trigger is based on spot price movement, but the buffer and exit price are still applied to the **option premium** (the actual traded instrument).

### Day HIGH/LOW Source

The `option_data` table already has `high_price` and `low_price` columns (schema lines 63–65). Two new lookup functions will be added to `backend/base.py`, reusing the existing fast-lookup cache pattern.

---

## Real Data Examples

**Setup:** SELL NIFTY 22100 CE, entry = 73, SL mode = Percent 100%, buffer = 10%, lot = 50

| Scenario | Close | Day HIGH | buffer_price | exit_price | P&L |
|----------|-------|----------|-------------|------------|-----|
| Gap (close=250, HIGH=310) | 250 | 310 | 275 | min(275,310)=**275** | -₹10,100 |
| Gap, muted day (close=250, HIGH=258) | 250 | 258 | 275 | min(275,258)=**258** | -₹9,250 |
| Massive gap (close=400, HIGH=480) | 400 | 480 | 440 | min(440,480)=**440** | -₹18,350 |
| No gap (close=148, HIGH=155) | 148 | 155 | 162.8 | min(162.8,155)=**155** | -₹4,100 |
| Regular SL (same scenario, no buffer) | 148 | — | — | **148** | -₹3,750 |

---

## Implementation Plan

### 1. `backend/base.py`

Add two new functions mirroring `get_option_premium_from_db` but returning `high_price` and `low_price`:

```python
def get_option_high_from_db(date, index, strike, option_type, expiry): ...
def get_option_low_from_db(date, index, strike, option_type, expiry): ...
```

These use the same O(1) fast-lookup cache already built for the close price.

### 2. `backend/engines/generic_algotest_engine.py`

#### `_copy_sl_tgt_to_leg()` (line ~1320)

Add parsing for the `slWithBuffer` key from the leg payload:

```python
if 'slWithBuffer' in leg_src and isinstance(leg_src['slWithBuffer'], dict):
    leg_dict['sl_buffer_value']  = leg_src['slWithBuffer'].get('value')
    leg_dict['sl_buffer_type']   = _normalize_sl_tgt_type(leg_src['slWithBuffer'].get('mode'))
    leg_dict['sl_buffer_pct']    = leg_src['slWithBuffer'].get('buffer_pct', 0)
else:
    leg_dict['sl_buffer_value']  = None
    leg_dict['sl_buffer_type']   = 'pct'
    leg_dict['sl_buffer_pct']    = 0
```

#### `check_leg_stop_loss_target()` (line ~2271)

In the per-day loop, after the existing `hit_sl` block, add a parallel `hit_sl_buffer` block:

1. Check trigger condition using `sl_buffer_value` / `sl_buffer_type` (same formulas as `hit_sl`).
2. If triggered, perform gap detection: compare previous holding day's adverse move to threshold.
3. If gap confirmed, fetch `day_HIGH` (SELL) or `day_LOW` (BUY) from the new base.py functions.
4. Compute `buffer_price` and `exit_price_raw`.
5. Store `exit_price_override` in `leg_results[li]` alongside `triggered`, `exit_date`, `exit_reason`.

Exit reason stored as `'STOP_LOSS_BUFFER'` for tracesheet identification.

#### Exact call-site touch points for `exit_price_override`

There are 4 call sites for `check_leg_stop_loss_target` in `generic_algotest_engine.py`. Each has a block that re-fetches the exit premium via `get_option_premium_from_db` when triggered. Add an override check at each:

| Location | Call variable | Re-fetch line | Description |
|----------|--------------|---------------|-------------|
| Line 4327 | `per_leg_results` | Line 4350 | Main backtest SL check |
| Line 1605 | `lazy_check` | Line 1622 | Lazy leg SL check |
| Line 2020 | `re_check` | ~Line 2080 | Re-entry leg SL check (first) |
| Line 2136 | `re_check` | Line 2169 | Re-entry leg SL check (second) |

At each re-fetch, replace:
```python
new_exit_premium = get_option_premium_from_db(date=actual_exit_date..., ...)
```
with:
```python
if leg_result.get('exit_price_override') is not None:
    new_exit_premium = leg_result['exit_price_override']
else:
    new_exit_premium = get_option_premium_from_db(date=actual_exit_date..., ...)
```

`exit_price_override` stores the raw buffer exit price **before slippage**, so `_apply_slippage()` still runs normally after the override.

Note: `generic_multi_leg.py` does NOT call `check_leg_stop_loss_target` — no changes needed there.

### 3. `frontend/src/components/StrategyBuilder.jsx`

#### New leg state fields

```js
sl_buffer_enabled: false,
sl_buffer_mode: 'POINTS',
sl_buffer_value: null,
sl_buffer_pct: null,
```

#### Regular leg UI (after existing SL row, line ~3199)

New row with:
- Toggle labeled "SL with Buffer"
- When enabled: mode dropdown (POINTS / UNDERLYING_POINTS / PERCENT / UNDERLYING_PERCENT) + value input + "Buffer%" label + buffer_pct input
- Mutual exclusion: enabling this toggle auto-disables `stop_loss_enabled` (and vice versa)

#### Lazy leg UI (after existing SL row, line ~3409)

Same structure but mode dropdown limited to POINTS / PERCENT.

#### Payload serialization (line ~1695)

```js
if (l.sl_buffer_enabled && l.sl_buffer_value > 0 && l.sl_buffer_pct > 0) {
  leg.slWithBuffer = {
    mode: l.sl_buffer_mode,
    value: l.sl_buffer_value,
    buffer_pct: l.sl_buffer_pct,
  };
}
```

---

## Payload Contract

Frontend sends (per leg):
```json
{
  "slWithBuffer": {
    "mode": "POINTS" | "PERCENT" | "UNDERLYING_POINTS" | "UNDERLYING_PERCENT",
    "value": 100,
    "buffer_pct": 10
  }
}
```

Internal leg dict (after `_copy_sl_tgt_to_leg`):
```python
{
  "sl_buffer_value": 100.0,
  "sl_buffer_type": "pct",      # normalized via _normalize_sl_tgt_type
  "sl_buffer_pct": 10.0,
}
```

---

## Files Changed

| File | Nature of change |
|------|-----------------|
| `backend/base.py` | +2 new lookup functions (`get_option_high_from_db`, `get_option_low_from_db`) |
| `backend/engines/generic_algotest_engine.py` | +parse `slWithBuffer` in `_copy_sl_tgt_to_leg`; +buffer eval block in `check_leg_stop_loss_target`; +consume `exit_price_override` in tradesheet builder |
| `frontend/src/components/StrategyBuilder.jsx` | +new state fields; +new SL-with-Buffer row (regular + lazy legs); +mutual exclusion logic; +payload serialization |

## Files NOT Changed

`strategy_types.py`, `_normalize_sl_tgt_type`, `check_overall_stop_loss_target`, trailing SL logic, target logic, `generic_multi_leg.py` (confirmed: does not call `check_leg_stop_loss_target`), filters, Docker config, migrations.

---

## Verification

1. **Unit test**: Create a leg with SL-with-Buffer, verify `_copy_sl_tgt_to_leg` populates the three new fields correctly.
2. **Engine test**: Backtest a strategy where SL fires on a gap day; confirm exit price = `min(close×1.buffer, HIGH)` not just close.
3. **No-gap test**: Backtest where close gradually crosses SL threshold; confirm buffer is NOT applied (exit = close).
4. **BUY leg test**: Confirm `max(buffer_price, day_LOW)` formula for a long option SL.
5. **Existing SL regression**: Run existing SL backtests; confirm zero change in results.
6. **Frontend**: Enable SL → verify SL-with-Buffer toggle is disabled. Enable SL-with-Buffer → verify SL is disabled.
