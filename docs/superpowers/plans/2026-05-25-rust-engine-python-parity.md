# Rust Engine ↔ Python Engine Full Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all remaining gaps between the Rust and Python backtest engines so `ENGINE_BACKEND=rust` produces identical tradesheets to Python for every supported strategy type.

**Architecture:** All changes are Python-only in `backend/services/engine_rust.py`. No Rust code changes, no `maturin` rebuild, no Docker rebuild required. Gaps: (1) FUTURES+SL/Target/TrailSL; (2) FUTURES+re-entry; (3) FUTURES+NEXT_WEEKLY mixed; (4) ReEntryIndex/Trigger/Mode output columns always empty; (5) Exit Reason for FUTURES always "EXPIRY".

**Tech Stack:** Python 3.11, FastAPI, `algotest_native` (PyO3 Rust extension), `base.get_future_price_from_db` for daily futures prices.

**Working directory for all commands:** `backend/` (i.e. `cd /home/user/Algo_Test_Software/backend` first).

**Run tests with:** `python -m unittest tests.test_engine_rust_pipeline -v`

---

## Files changed

| File | Change |
|---|---|
| `backend/services/engine_rust.py` | New `_scan_futures_sl_target` helper; remove 3 `return None` gates; inject `_reentry_*` fields; update `priced_to_tradesheet_records` |
| `backend/tests/parity/archetypes.py` | 4 new archetype payloads |
| `backend/tests/test_engine_rust_pipeline.py` | 4 new archetype names in `SLICE_4_ARCHETYPES` |
| `backend/services/algotest_job.py` | Update stale docstring only |

Snapshots are auto-generated (captured from Python engine); no manual editing.

---

## Task 1: Add `_scan_futures_sl_target` helper

**Files:**
- Modify: `backend/services/engine_rust.py` (insert before line 405, `def _build_futures_specs`)

- [ ] **Step 1: Open `engine_rust.py` and locate line 403** (the blank line before `def _build_futures_specs`). Insert the following block immediately before that function:

```python
def _scan_futures_sl_target(
    entry_date: str,
    entry_price_raw: float,
    position: str,
    leg_src: Dict[str, Any],
    sorted_td: List[str],
    scheduled_exit: str,
    index: str,
    fut_expiry: str,
    slippage: float,
) -> Tuple[str, Optional[float], str]:
    """
    Scan daily futures prices entry+1 → scheduled_exit for SL/Target/TrailSL.

    Returns (actual_exit_date, exit_price_raw_or_None, exit_reason).
    exit_price_raw is None when nothing fires — caller keeps original price.
    Mirrors check_leg_stop_loss_target logic from generic_algotest_engine.py.
    """
    from base import get_future_price_from_db

    sl = (leg_src.get("stopLoss") or {}) if isinstance(leg_src.get("stopLoss"), dict) else {}
    tp = (leg_src.get("targetProfit") or {}) if isinstance(leg_src.get("targetProfit"), dict) else {}
    trail = (leg_src.get("trailSL") or {}) if isinstance(leg_src.get("trailSL"), dict) else {}

    sl_val = _maybe_float(sl.get("value"))
    sl_type = _norm_mode(sl.get("mode"))
    tp_val = _maybe_float(tp.get("value"))
    tp_type = _norm_mode(tp.get("mode"))
    trail_trig = _maybe_float(trail.get("trigger") or trail.get("x"))
    trail_move_v = _maybe_float(trail.get("move") or trail.get("y"))
    trail_mode = _norm_mode(trail.get("mode"))
    trail_enabled = bool(trail_trig and trail_move_v and trail_trig > 0 and trail_move_v > 0)

    has_sl = sl_val is not None and sl_val > 0
    has_tp = tp_val is not None and tp_val > 0
    if not has_sl and not has_tp and not trail_enabled:
        return (scheduled_exit, None, "EXPIRY")

    trail_armed = False
    trail_best: Optional[float] = None

    def _metric(current: float, mode: str) -> float:
        """Directional P&L in pct or points, positive = favourable."""
        if mode == "pct":
            base = entry_price_raw or 1.0
            raw = (current - entry_price_raw) / base * 100.0
        else:
            raw = current - entry_price_raw
        return raw if position == "BUY" else -raw

    for day in sorted_td:
        if day <= entry_date:
            continue
        if day > scheduled_exit:
            break
        try:
            current = get_future_price_from_db(day, index, expiry=fut_expiry)
        except Exception:
            current = None
        if current is None or current <= 0:
            continue

        pnl_pct = _metric(current, "pct")
        pnl_pts = _metric(current, "points")

        if trail_enabled:
            trail_metric = pnl_pct if trail_mode == "pct" else pnl_pts
            if not trail_armed:
                if trail_metric >= trail_trig:
                    trail_armed = True
                    trail_best = trail_metric
            else:
                if trail_metric > trail_best:
                    trail_best = trail_metric
                if trail_best - trail_metric >= trail_move_v:
                    return (day, current, "TRAIL_SL")

        if has_sl:
            adverse = pnl_pct if sl_type == "pct" else pnl_pts
            if adverse <= -sl_val:
                return (day, current, "SL")

        if has_tp:
            favourable = pnl_pct if tp_type == "pct" else pnl_pts
            if favourable >= tp_val:
                return (day, current, "TARGET")

    return (scheduled_exit, None, "EXPIRY")
```

- [ ] **Step 2: Verify the file still parses cleanly**

```bash
cd /home/user/Algo_Test_Software/backend && python -c "import services.engine_rust; print('OK')"
```
Expected: `OK`

---

## Task 2: Wire SL scanner into `_build_futures_specs` (Gap 1 + Gap 5)

**Files:**
- Modify: `backend/services/engine_rust.py` lines 478–554

- [ ] **Step 1: Remove the SL/Target/Trail early return (lines 478–482)**

Find and delete this block inside the `for leg_id, leg in enumerate(legs_src)` loop in `_build_futures_specs`:

```python
            # SL / Target / Trail on futures not yet supported → Python fallback.
            for risk_key in ("stopLoss", "targetProfit", "trailSL"):
                v = leg.get(risk_key) or {}
                if isinstance(v, dict) and _maybe_float(v.get("value")):
                    return None
```

- [ ] **Step 2: After computing `entry_price_raw, exit_price_raw, fut_expiry` (around line 512), replace the slippage block and `out.append` with the version that calls `_scan_futures_sl_target`**

The original block (lines ~518–555) is:

```python
            # Slippage — mirrors _apply_slippage in generic_algotest_engine.py
            if slippage > 0:
                _entry_fac = (1.0 - slippage / 100.0) if position == "SELL" else (1.0 + slippage / 100.0)
                _exit_fac = (1.0 + slippage / 100.0) if position == "SELL" else (1.0 - slippage / 100.0)
                entry_price = round(max(float(entry_price_raw) * _entry_fac, 0.0), 2)
                exit_price = round(max(float(exit_price_raw) * _exit_fac, 0.0), 2)
            else:
                entry_price = round(float(entry_price_raw), 2)
                exit_price = round(float(exit_price_raw), 2)

            # P&L per unit — no lot_size multiplication (matches Python engine convention).
            net_pnl = round(
                (entry_price - exit_price) if position == "SELL" else (exit_price - entry_price),
                4,
            )

            out.append({
                ...
                "exit_reason": "EXPIRY",
            })
```

Replace the whole block with:

```python
            # Save original scheduled exit BEFORE the scan — re-entry uses it as cap.
            _orig_sched_exit = fut_exit_date

            # SL / Target / TrailSL scan for FUTURES leg.
            _scan_exit_date, _scan_exit_raw, _actual_exit_reason = _scan_futures_sl_target(
                entry_date, float(entry_price_raw), position, leg, sorted_td,
                _orig_sched_exit, index, fut_expiry or "", slippage,
            )
            if _scan_exit_raw is not None:
                # SL/Target/Trail fired — truncate exit.
                fut_exit_date = _scan_exit_date
                exit_price_raw = _scan_exit_raw
                # Re-fetch exit spot for the new exit date.
                exit_spot = spot_by_date.get(fut_exit_date, exit_spot)

            # Slippage — mirrors _apply_slippage in generic_algotest_engine.py
            if slippage > 0:
                _entry_fac = (1.0 - slippage / 100.0) if position == "SELL" else (1.0 + slippage / 100.0)
                _exit_fac = (1.0 + slippage / 100.0) if position == "SELL" else (1.0 - slippage / 100.0)
                entry_price = round(max(float(entry_price_raw) * _entry_fac, 0.0), 2)
                exit_price = round(max(float(exit_price_raw) * _exit_fac, 0.0), 2)
            else:
                entry_price = round(float(entry_price_raw), 2)
                exit_price = round(float(exit_price_raw), 2)

            # P&L per unit — no lot_size multiplication (matches Python engine convention).
            net_pnl = round(
                (entry_price - exit_price) if position == "SELL" else (exit_price - entry_price),
                4,
            )

            out.append({
                "trade_id": trade_id,
                "leg_id": leg_id,
                "index": index,
                "entry_date": entry_date,
                "exit_date": fut_exit_date,
                "expiry": fut_expiry or "",
                "strike": 0.0,
                "option_type": "FUT",
                "position": position,
                "lots": lots,
                "lot_size": lot_size,
                "slippage_pct": slippage,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "raw_entry_price": round(float(entry_price_raw), 4),
                "raw_exit_price": round(float(exit_price_raw), 4),
                "net_pnl": net_pnl,
                "entry_spot": float(entry_spot),
                "exit_spot": float(exit_spot),
                "exit_reason": _actual_exit_reason,
            })
```

Note: `exit_spot` was computed before the `for leg_id` loop at line 447 as `exit_spot = spot_by_date.get(exit_date, 0.0)`. After the scan we update it to match the actual exit date.

- [ ] **Step 3: Verify file still parses**

```bash
cd /home/user/Algo_Test_Software/backend && python -c "import services.engine_rust; print('OK')"
```

- [ ] **Step 4: Add `futures_with_sl` archetype to `archetypes.py`**

Open `backend/tests/parity/archetypes.py` and append at the bottom:

```python
# FUTURES with per-leg SL — verifies _scan_futures_sl_target fires correctly.
# Use an aggressive (5%) SL on a short FUTURES SELL so it fires during the window.
_register(
    "futures_with_sl",
    {
        **_base(),
        "legs": [
            {
                "segment": "FUTURES",
                "option_type": "FUT",
                "position": "SELL",
                "lots": 1,
                "expiry": "CURRENT_MONTH",
                "fut_exit_mode": "ON_EXPIRY",
                "stopLoss": {"mode": "PERCENT", "value": 0.5},
            }
        ],
    },
)

# FUTURES with TrailSL.
_register(
    "futures_with_trail_sl",
    {
        **_base(),
        "legs": [
            {
                "segment": "FUTURES",
                "option_type": "FUT",
                "position": "SELL",
                "lots": 1,
                "expiry": "CURRENT_MONTH",
                "fut_exit_mode": "ON_EXPIRY",
                "trailSL": {"trigger": 0.3, "move": 0.2, "mode": "PERCENT"},
            }
        ],
    },
)
```

- [ ] **Step 5: Capture Python snapshots for the new archetypes**

```bash
cd /home/user/Algo_Test_Software/backend && python -m tests.parity.capture --only futures_with_sl,futures_with_trail_sl --force
```

Expected: Two JSON files appear in `tests/parity/snapshots/`.

- [ ] **Step 6: Add the new archetype names to `test_engine_rust_pipeline.py`**

In `backend/tests/test_engine_rust_pipeline.py`, add to `SLICE_4_ARCHETYPES` tuple:

```python
    # Gap 1: FUTURES + SL / TrailSL
    "futures_with_sl",
    "futures_with_trail_sl",
```

- [ ] **Step 7: Run parity tests — all must pass (including existing 20)**

```bash
cd /home/user/Algo_Test_Software/backend && python -m unittest tests.test_engine_rust_pipeline -v 2>&1 | tail -30
```

Expected: All tests PASS. `futures_with_sl` and `futures_with_trail_sl` must not be SKIP or FAIL.

- [ ] **Step 8: Commit**

```bash
cd /home/user/Algo_Test_Software
git add backend/services/engine_rust.py backend/tests/parity/archetypes.py backend/tests/parity/snapshots/futures_with_sl.json backend/tests/parity/snapshots/futures_with_trail_sl.json backend/tests/test_engine_rust_pipeline.py
git commit -m "feat: FUTURES SL/Target/TrailSL parity — _scan_futures_sl_target"
```

---

## Task 3: FUTURES re-entry support (Gap 2)

**Files:**
- Modify: `backend/services/engine_rust.py` (inside `_build_futures_specs`, around line 484)

- [ ] **Step 1: Remove the re-entry early return (lines 484–485)**

Find and delete this block inside `_build_futures_specs`:

```python
            # Re-entry on futures not yet supported.
            if leg.get("reEntryOnSL") or leg.get("reEntryOnTarget"):
                return None
```

- [ ] **Step 2: After `out.append({...})` for the initial trade row, add the FUTURES re-entry loop**

The initial row is appended after the slippage block. Immediately after that `out.append(...)` call (still inside the `for leg_id, leg in enumerate(legs_src)` loop), add:

```python
            # Re-entry loop for FUTURES legs (mirrors options re-entry in run_rust_engine_pipeline).
            _re_on_sl = leg.get("reEntryOnSL") if isinstance(leg.get("reEntryOnSL"), dict) else None
            _re_on_tgt = leg.get("reEntryOnTarget") if isinstance(leg.get("reEntryOnTarget"), dict) else None
            _sl_budget = int((_re_on_sl or {}).get("count") or 0)
            _tgt_budget = int((_re_on_tgt or {}).get("count") or 0)
            _re_mode = str(
                ((_re_on_sl or _re_on_tgt or {}).get("mode") or "RE_ASAP")
            ).upper()

            if (_re_on_sl or _re_on_tgt) and (_sl_budget > 0 or _tgt_budget > 0):
                _sl_used_re = 0
                _tgt_used_re = 0
                _cur_exit = fut_exit_date           # tracks re-entry's start date (post-scan actual exit)
                _cur_reason = _actual_exit_reason   # reason that may trigger re-entry
                _sched_exit = _orig_sched_exit      # original scheduled exit cap — re-entry cannot exceed

                while True:
                    if _cur_reason in ("SL", "TRAIL_SL") and _re_on_sl and _sl_used_re < _sl_budget:
                        _sl_used_re += 1
                        _re_trigger = "SL"
                    elif _cur_reason == "TARGET" and _re_on_tgt and _tgt_used_re < _tgt_budget:
                        _tgt_used_re += 1
                        _re_trigger = "TARGET"
                    else:
                        break

                    _re_entry_date = next(
                        (d for d in sorted_td if d > _cur_exit and d < _sched_exit),
                        None,
                    )
                    if not _re_entry_date:
                        break

                    _re_idx = _sl_used_re + _tgt_used_re

                    try:
                        _re_ep_raw, _re_xp_raw, _re_expiry = resolve_futures_pnl_with_rollover(
                            entry_date=_re_entry_date,
                            exit_date=_sched_exit,
                            index=index,
                            position=position,
                            preference=fut_pref,
                        )
                    except Exception:
                        break
                    if _re_ep_raw is None:
                        break
                    if _re_xp_raw is None:
                        _re_xp_raw = _re_ep_raw

                    _re_scan_date, _re_scan_raw, _re_reason = _scan_futures_sl_target(
                        _re_entry_date, float(_re_ep_raw), position, leg, sorted_td,
                        _sched_exit, index, _re_expiry or fut_expiry or "", slippage,
                    )
                    if _re_scan_raw is not None:
                        _re_xp_raw = _re_scan_raw
                        _re_exit_date = _re_scan_date
                    else:
                        _re_exit_date = _sched_exit
                        _re_reason = "EXPIRY"

                    if slippage > 0:
                        _re_ep = round(max(float(_re_ep_raw) * _entry_fac, 0.0), 2)
                        _re_xp = round(max(float(_re_xp_raw) * _exit_fac, 0.0), 2)
                    else:
                        _re_ep = round(float(_re_ep_raw), 2)
                        _re_xp = round(float(_re_xp_raw), 2)

                    _re_pnl = round(
                        (_re_ep - _re_xp) if position == "SELL" else (_re_xp - _re_ep), 4
                    )

                    out.append({
                        "trade_id": trade_id,
                        "leg_id": leg_id,
                        "index": index,
                        "entry_date": _re_entry_date,
                        "exit_date": _re_exit_date,
                        "expiry": _re_expiry or fut_expiry or "",
                        "strike": 0.0,
                        "option_type": "FUT",
                        "position": position,
                        "lots": lots,
                        "lot_size": lot_size,
                        "slippage_pct": slippage,
                        "entry_price": _re_ep,
                        "exit_price": _re_xp,
                        "raw_entry_price": round(float(_re_ep_raw), 4),
                        "raw_exit_price": round(float(_re_xp_raw), 4),
                        "net_pnl": _re_pnl,
                        "entry_spot": float(spot_by_date.get(_re_entry_date, 0.0)),
                        "exit_spot": float(spot_by_date.get(_re_exit_date, 0.0)),
                        "exit_reason": _re_reason,
                        "_reentry_index": _re_idx,
                        "_reentry_trigger": _re_trigger,
                        "_reentry_mode": _re_mode,
                    })

                    _cur_exit = _re_exit_date
                    _cur_reason = _re_reason
```

Note: `_entry_fac` and `_exit_fac` are already defined in the slippage block just above (they exist when `slippage > 0`). The code reuses them. If `slippage == 0`, the `if slippage > 0` branch above never ran so `_entry_fac`/`_exit_fac` won't exist — but the `if slippage > 0` in the re-entry block guards them. This is safe.

- [ ] **Step 3: Verify the file parses**

```bash
cd /home/user/Algo_Test_Software/backend && python -c "import services.engine_rust; print('OK')"
```

- [ ] **Step 4: Add `futures_with_reentry_sl` archetype to `archetypes.py`**

Append to `backend/tests/parity/archetypes.py`:

```python
# FUTURES with re-entry on SL (RE_ASAP mode).
_register(
    "futures_with_reentry_sl",
    {
        **_base(),
        "legs": [
            {
                "segment": "FUTURES",
                "option_type": "FUT",
                "position": "SELL",
                "lots": 1,
                "expiry": "CURRENT_MONTH",
                "fut_exit_mode": "ON_EXPIRY",
                "stopLoss": {"mode": "PERCENT", "value": 0.5},
                "reEntryOnSL": {"mode": "RE_ASAP", "count": 1},
            }
        ],
    },
)
```

- [ ] **Step 5: Capture Python snapshot**

```bash
cd /home/user/Algo_Test_Software/backend && python -m tests.parity.capture --only futures_with_reentry_sl --force
```

- [ ] **Step 6: Add archetype name to `test_engine_rust_pipeline.py`**

Add `"futures_with_reentry_sl"` to `SLICE_4_ARCHETYPES`.

- [ ] **Step 7: Run all parity tests**

```bash
cd /home/user/Algo_Test_Software/backend && python -m unittest tests.test_engine_rust_pipeline -v 2>&1 | tail -30
```

Expected: All pass, including `futures_with_reentry_sl`.

- [ ] **Step 8: Commit**

```bash
cd /home/user/Algo_Test_Software
git add backend/services/engine_rust.py backend/tests/parity/archetypes.py backend/tests/parity/snapshots/futures_with_reentry_sl.json backend/tests/test_engine_rust_pipeline.py
git commit -m "feat: FUTURES re-entry on SL/Target parity"
```

---

## Task 4: ReEntryIndex / ReEntryTrigger / ReEntryMode columns (Gap 4)

**Files:**
- Modify: `backend/services/engine_rust.py` (re-entry loop + `priced_to_tradesheet_records`)

### Part A — Track metadata in the options re-entry loop

- [ ] **Step 1: Add `reentry_meta_map` declaration alongside `reentry_reason_map`**

In `run_rust_engine_pipeline`, find line 2145:
```python
    reentry_reason_map: Dict[Tuple[int, int, str], str] = {}  # (trade_id, leg_id, entry_date) → reason
```

Add the line immediately after it:
```python
    reentry_meta_map: Dict[Tuple[int, int, str], Tuple[int, str, str]] = {}  # → (index, trigger, mode)
```

- [ ] **Step 2: Populate `reentry_meta_map` at the RE_ASAP/RE_ASAP_REV re-entry append point**

Find line 2434 (inside the while loop, RE_ASAP branch):
```python
                reentry_reason_map[(int(leg["trade_id"]), int(leg["leg_id"]), str(current_trig))] = re_reason or "EXPIRY"
```

Immediately after that line add:
```python
                reentry_meta_map[(int(leg["trade_id"]), int(leg["leg_id"]), str(current_trig))] = (
                    sl_used + tgt_used,
                    "SL" if current_reason in _SL_REASONS else "TARGET",
                    re_mode,
                )
```

- [ ] **Step 3: Populate `reentry_meta_map` at the LAZY_LEG re-entry append point**

Find line 2358 (LAZY_LEG branch):
```python
                    reentry_reason_map[(int(leg["trade_id"]), int(lazy_spec["leg_id"]), str(current_trig))] = lazy_reason or "EXPIRY"
```

Immediately after that line add:
```python
                    reentry_meta_map[(int(leg["trade_id"]), int(lazy_spec["leg_id"]), str(current_trig))] = (
                        sl_used + tgt_used,
                        "SL" if current_reason in _SL_REASONS else "TARGET",
                        re_mode,
                    )
```

### Part B — Inject metadata into `final_priced` rows

- [ ] **Step 4: After the `exit_reason` injection block (around line 2883), inject re-entry metadata**

Find this block (around line 2877–2883):
```python
    for row in final_priced:
        key = (int(row.get("trade_id") or 0), int(row.get("leg_id") or 1), str(row.get("entry_date") or ""))
        row["exit_reason"] = (
            reentry_reason_map.get(key)
            or adjusted_reason_by_date.get(key)
            or "EXPIRY"
        )
```

After that `for` loop, add:
```python
    # Inject re-entry metadata so priced_to_tradesheet_records can populate
    # ReEntryIndex / ReEntryTrigger / ReEntryMode columns.
    for row in final_priced:
        key = (int(row.get("trade_id") or 0), int(row.get("leg_id") or 1), str(row.get("entry_date") or ""))
        meta = reentry_meta_map.get(key)
        if meta:
            row["_reentry_index"] = meta[0]
            row["_reentry_trigger"] = meta[1]
            row["_reentry_mode"] = meta[2]
```

### Part C — Read metadata in `priced_to_tradesheet_records`

- [ ] **Step 5: Replace the three hardcoded empty-string lines (1515–1517)**

Find:
```python
            "ReEntryIndex": "",
            "ReEntryTrigger": "",
            "ReEntryMode": "",
```

Replace with:
```python
            "ReEntryIndex": row.get("_reentry_index") or "",
            "ReEntryTrigger": str(row.get("_reentry_trigger") or ""),
            "ReEntryMode": str(row.get("_reentry_mode") or ""),
```

- [ ] **Step 6: Run all existing parity tests — no regressions allowed**

```bash
cd /home/user/Algo_Test_Software/backend && python -m unittest tests.test_engine_rust_pipeline -v 2>&1 | tail -30
```

Expected: All existing tests still pass. (Snapshots were captured without checking ReEntry columns, so no snapshot change needed.)

- [ ] **Step 7: Commit**

```bash
cd /home/user/Algo_Test_Software
git add backend/services/engine_rust.py
git commit -m "feat: populate ReEntryIndex/Trigger/Mode columns in Rust engine output"
```

---

## Task 5: FUTURES + NEXT_WEEKLY mixed strategies (Gap 3)

**Files:**
- Modify: `backend/services/engine_rust.py` (new `_build_mixed_futures_next_weekly` helper + `run_rust_engine_pipeline`)

- [ ] **Step 1: Add `_build_mixed_futures_next_weekly` helper**

Insert this function immediately before `def run_rust_engine_pipeline` (line 1637):

```python
def _build_mixed_futures_next_weekly(
    payload: Dict[str, Any],
    expiry_dates: List[str],
    trading_days: List[str],
    lot_size: int,
    spot_by_date: Dict[str, float],
    segments: Optional[List[Tuple[str, str]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Handle strategies that mix FUTURES legs with NEXT_WEEKLY/NEXT_MONTHLY option legs.

    Builds each leg type independently then merges rows by aligning on entry_date.
    Returns None on any failure so the caller falls back to Python.
    """
    try:
        import algotest_native  # type: ignore
    except ImportError:
        return None

    legs_src = payload.get("legs") or []

    # FUTURES legs: _build_futures_specs already skips non-FUTURES by segment check.
    # Pass the full payload — it will only process FUTURES legs.
    fut_rows = _build_futures_specs(
        payload, expiry_dates, trading_days, spot_by_date, lot_size, segments
    )
    if fut_rows is None:
        return None  # FUTURES rejected (unsupported config)

    # Option legs only.
    opt_legs = [
        l for l in legs_src
        if isinstance(l, dict)
        and str(l.get("segment") or "OPTION").upper() not in ("FUTURE", "FUTURES")
    ]
    if not opt_legs:
        return fut_rows  # No option legs — just return FUTURES rows.

    # Build option specs using the NEXT_WEEKLY path.
    _ext_expiry_dates = _fetch_one_extra_expiry(expiry_dates, payload)
    opt_payload = {**payload, "legs": opt_legs}
    opt_specs = _build_next_expiry_specs(
        opt_payload, _ext_expiry_dates, trading_days, spot_by_date, int(lot_size)
    )
    if opt_specs is None:
        return None

    # Price option specs.
    if not algotest_native.is_loaded():
        return None
    priced_opts = list(algotest_native.simulate_trades_batch(opt_specs)) if opt_specs else []

    # Remap option leg_ids to match their original position in legs_src.
    # _build_next_expiry_specs enumerates opt_legs (1-based); original indices differ.
    _opt_leg_id_remap: Dict[int, int] = {}
    for new_idx, leg in enumerate(opt_legs, start=1):
        for orig_idx, orig_leg in enumerate(legs_src, start=1):
            if leg is orig_leg:
                _opt_leg_id_remap[new_idx] = orig_idx
                break
    for row in priced_opts:
        if row.get("leg_id") in _opt_leg_id_remap:
            row["leg_id"] = _opt_leg_id_remap[row["leg_id"]]

    # Group both sets by entry_date and merge — assign consistent trade_ids.
    from collections import defaultdict
    fut_by_entry: Dict[str, List[Dict]] = defaultdict(list)
    for row in fut_rows:
        fut_by_entry[row["entry_date"]].append(row)

    opt_by_entry: Dict[str, List[Dict]] = defaultdict(list)
    for row in priced_opts:
        opt_by_entry[row["entry_date"]].append(row)

    all_entries = sorted(set(list(fut_by_entry.keys()) + list(opt_by_entry.keys())))
    combined: List[Dict[str, Any]] = []
    next_tid = 1
    for ed in all_entries:
        period_tid = next_tid
        next_tid += 1
        for row in fut_by_entry.get(ed, []):
            combined.append({**row, "trade_id": period_tid})
        for row in opt_by_entry.get(ed, []):
            combined.append({**row, "trade_id": period_tid})

    return combined if combined else None
```

- [ ] **Step 2: Replace the `return None` at lines 1706–1707 in `run_rust_engine_pipeline`**

Find:
```python
        if _has_next_leg:
            return None
```
(this is inside the `if _has_futures_leg:` block)

Replace with:
```python
        if _has_next_leg:
            # Mixed FUTURES + NEXT_WEEKLY: build each type separately, merge by period.
            try:
                _mixed = _build_mixed_futures_next_weekly(
                    payload, expiry_dates, trading_days, lot_size, spot_by_date, segments,
                )
            except Exception as _exc:
                logger.warning("[ENGINE_RUST] mixed FUTURES+NEXT_WEEKLY failed: %s", _exc)
                _mixed = None
            return _mixed  # None → caller falls back to Python engine
```

- [ ] **Step 3: Verify file parses**

```bash
cd /home/user/Algo_Test_Software/backend && python -c "import services.engine_rust; print('OK')"
```

- [ ] **Step 4: Add `futures_next_weekly_mix` archetype to `archetypes.py`**

Append to `backend/tests/parity/archetypes.py`:

```python
# Mixed FUTURES + NEXT_WEEKLY option leg.
# Leg 1 is a monthly FUTURES SELL; Leg 2 is a NEXT_WEEKLY CE SELL (next expiry).
_register(
    "futures_next_weekly_mix",
    {
        **_base(),
        "legs": [
            {
                "segment": "FUTURES",
                "option_type": "FUT",
                "position": "SELL",
                "lots": 1,
                "expiry": "CURRENT_MONTH",
                "fut_exit_mode": "ON_EXPIRY",
            },
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "NEXT_WEEKLY",
                "strike_interval": 50,
                "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
            },
        ],
    },
)
```

- [ ] **Step 5: Capture Python snapshot**

```bash
cd /home/user/Algo_Test_Software/backend && python -m tests.parity.capture --only futures_next_weekly_mix --force
```

- [ ] **Step 6: Add archetype name to `test_engine_rust_pipeline.py`**

Add `"futures_next_weekly_mix"` to `SLICE_4_ARCHETYPES`.

- [ ] **Step 7: Run all parity tests**

```bash
cd /home/user/Algo_Test_Software/backend && python -m unittest tests.test_engine_rust_pipeline -v 2>&1 | tail -30
```

Expected: All pass, including `futures_next_weekly_mix`. If `futures_next_weekly_mix` is skipped (snapshot exists but Rust returns None), debug `_build_mixed_futures_next_weekly`.

- [ ] **Step 8: Commit**

```bash
cd /home/user/Algo_Test_Software
git add backend/services/engine_rust.py backend/tests/parity/archetypes.py backend/tests/parity/snapshots/futures_next_weekly_mix.json backend/tests/test_engine_rust_pipeline.py
git commit -m "feat: FUTURES + NEXT_WEEKLY mixed strategy parity"
```

---

## Task 6: Update stale docstring + final verification

**Files:**
- Modify: `backend/services/algotest_job.py` (lines 290–295 only)

- [ ] **Step 1: Update the stale docstring in `algotest_job.py`**

Find (around line 290):
```python
    Known gaps vs Python (set ENGINE_BACKEND=python if these matter for you):
      * Exit Reason is always 'Expiry' regardless of SL/Target/Spot-Adj firing
      * ReEntryIndex/Trigger/Mode tags missing (re-entry rows still produced)
      * Buffer strike / futures / lazy legs / rollover / no_rollover /
        filter_entry_mode='fixed'|'min_days' → orchestrator returns None →
        falls back to Python.
```

Replace with:
```python
    Known gaps vs Python:
      * None for typical options strategies — Rust is now fully parity with Python.
      * FUTURES + SL/Target/re-entry is supported. FUTURES + NEXT_WEEKLY mixed
        is supported. The only remaining Python-only paths are rare edge cases
        where data is genuinely missing (strike unresolvable, missing spot data).
```

- [ ] **Step 2: Run the full parity test suite one final time**

```bash
cd /home/user/Algo_Test_Software/backend && python -m unittest tests.test_engine_rust_pipeline -v
```

Expected output (all 24+ tests):
```
test_pipeline__filter_entry_fixed ... ok
test_pipeline__futures_next_weekly_mix ... ok
test_pipeline__futures_with_reentry_sl ... ok
test_pipeline__futures_with_sl ... ok
test_pipeline__futures_with_trail_sl ... ok
test_pipeline__reentry_re_asap_rev ... ok
test_pipeline__rollover_fixed_strike ... ok
test_pipeline__single_leg_ce_atm_sell ... ok
... (all remaining) ...
OK
```

- [ ] **Step 3: Final commit**

```bash
cd /home/user/Algo_Test_Software
git add backend/services/algotest_job.py
git commit -m "chore: update stale docstring — Rust engine now fully parity with Python"
```

---

## Troubleshooting

**`futures_with_sl` Rust result doesn't match Python snapshot (price diff > 0.01):**
The issue is likely slippage direction. In `_scan_futures_sl_target`, the function returns `exit_price_raw` (raw price without slippage). The caller in `_build_futures_specs` applies slippage via `_exit_fac`. Verify `_exit_fac` is defined when slippage > 0 (it comes from the slippage block above the `out.append`).

**`futures_with_reentry_sl` produces 0 re-entries:**
Check that `_actual_exit_reason` is set to `"SL"` (not `"EXPIRY"`) for the initial trade. If the SL threshold is too loose, the SL never fires and no re-entry is triggered. Try reducing `stopLoss.value` in the archetype payload.

**`futures_next_weekly_mix` Rust returns None:**
Enable debug logging: `import logging; logging.basicConfig(level=logging.DEBUG)` before the test. Check whether `_build_futures_specs` returns None (FUTURES path rejected) or `_build_next_expiry_specs` returns None (options path rejected).

**`_entry_fac` / `_exit_fac` NameError in re-entry loop:**
These variables are only defined in the `if slippage > 0:` block. The re-entry loop guards them with its own `if slippage > 0:` check, which is safe. If you see this error, check that the re-entry code block is inside the `for leg_id, leg in enumerate(legs_src)` loop (correct indentation).

**Any existing parity test starts FAILING after these changes:**
The changes to `_build_futures_specs` only affect FUTURES legs. The existing failing test is likely exercising a FUTURES archetype. Check: does `single_leg_futures_monthly` still pass? If not, the `_scan_futures_sl_target` call is modifying the exit even when there's no SL configured. Verify the early-return guard:
```python
if not has_sl and not has_tp and not trail_enabled:
    return (scheduled_exit, None, "EXPIRY")
```
