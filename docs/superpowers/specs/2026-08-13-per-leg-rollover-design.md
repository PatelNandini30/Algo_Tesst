# Per-Leg Rollover — Design Spec

**Date:** 2026-08-13
**Branch:** feat/per-leg-filter
**Status:** approved design → implementation

## 1. Goal

Let each leg roll on **its own expiry type + its own T-n**, independently of the
other legs. Today a single shared **Roll Cadence** + single global `entry_dte`/
`exit_dte` drive every leg together; the only per-leg independence that exists is
the YEARLY-pinned case (a yearly leg holds its December contract while the shared
weekly cadence rolls the trade underneath it).

Per-leg rollover is the **N-cadence generalization** of that pinned mechanism.

## 2. Scope

- **In:** No-Adjustment and Adjustment (spot-adj) variants; any leg mix (weekly /
  monthly / yearly); per-leg T-n; futures legs; multi-index; single-index mixed
  expiry.
- **Out (unchanged, later):** none of the existing calc logic changes. The
  Adjustment variant reuses the existing spot-adjustment boundary machinery — it
  is not re-implemented here.

## 3. Hard constraints

1. **Opt-in, additive.** New `per_leg_rollover` flag. OFF ⇒ engine byte-identical
   to today (the entire new path is behind the flag).
2. **Rust-only.** New scheduler lives in the Rust core; Python mirror updated only
   to preserve parity. No Python fallback for the hot path.
3. **Parity.** backtest per-combo == optim per-combo == optim master.
4. **No calc-logic change.** Strike selection, pricing, MAE/MFE, slippage,
   WOW/MOM, Live-DD all reuse existing helpers unchanged.

## 4. Mechanics (confirmed with user, tradewise)

### 4.1 Boundaries = union
For each leg, generate its own `(contract, entry, exit)` segments from its own
expiry list + its own T-n. **Trade boundaries = sorted union of every leg's exit
dates.** Each boundary starts a new trade row.

### 4.2 Split + carry
At each row `[start, next_boundary)`:
- A leg whose **own** boundary == `start` **rolls**: new contract; strike re-pick
  only if its existing Fresh/Fixed reset rule (`opens_new_epoch`) fires.
- Every other leg **carries** its current contract + strike verbatim.

This is exactly the current pinned-yearly behavior (`epoch_strike` carry +
`validate_or_shift_strike` re-validation), generalized so **any** leg can be the
one that carries.

### 4.3 Carried-leg MTM
A carried leg is marked-to-market per sub-segment: entry px = its held contract
price at row start, exit px at row end. Same as the yearly leg appearing in every
weekly row today.

### 4.4 Reset rules (unchanged `opens_new_epoch`)
- FIXED (any leg): new cycle only.
- FRESH weekly/monthly: every own-entry.
- FRESH yearly: first own-entry of each calendar month.

### 4.5 Exit reason
Row exit reason names the triggering leg(s); multiple same-day triggers join with
`+` (existing combined-reason join). Spot-adj breach co-occurring with a scheduled
roll ⇒ `SPOT_ADJ + SCHEDULED_EXIT`.

### 4.6 Adjustment variant
Spot-adjustment is just **another boundary source** merged into the same union.
On re-entry each leg re-picks by its own rule (Fresh ⇒ new ATM at breach spot;
Fixed ⇒ keeps pinned strike). A carried leg's adjusted strike **propagates** to
its subsequent carried rows until its next own-roll. Other legs' rollover
schedule is untouched.

## 5. Worked example

See tradesheet tables in the conversation (Feb–Mar 2026, L1 SELL CE WEEKLY T-1
Fresh + L2 SELL PE MONTHLY T-7 Fresh + L3 BUY CE YEARLY Dec-26 Fixed):
- Rows end on the union of weekly T-1 (Wed) and monthly T-7 (17th).
- Weekly leg carries its contract across the monthly boundary, then rolls on its
  own T-1.
- Fixed yearly never re-picks; on spot-adj it splits the row but keeps its strike.

## 6. Implementation plan (phased, each phase non-breaking)

### Phase 1 — schema + gate (safe, no behavior change)
- Payload: `per_leg_rollover: bool`. Per-leg `entry_dte` / `exit_dte` (fall back
  to global when absent). Per-leg `rollover_cadence` already implied by the leg's
  own `expiry` type.
- `engine_rust.py`: read the flag; when OFF, existing path verbatim.
- Frontend `StrategyBuilder.jsx`: toggle + per-leg T-n inputs (shown only when ON).

### Phase 2 — Rust union scheduler (behind flag)
- New `build_rollover_schedule_per_leg(legs_expiries, legs_tn, trading_days,
  cycles_per_leg)` → rows `(trade_id, start, end, Vec<per-leg (contract, is_own_boundary)>)`.
- `resolve_trade_specs_core`: when `per_leg_rollover`, drive rows off the union;
  per leg, `is_own_boundary` ⇒ roll (existing epoch logic), else carry (existing
  `epoch_strike` + `validate_or_shift_strike`).
- Reuse `opens_new_epoch`, `compute_strike_for_leg`, `validate_or_shift_strike`
  untouched.

### Phase 3 — Python mirror parity
- Mirror the union scheduler in `engine_rust.py`'s Python resolve path so the
  parity harness (`backend/tools/parity_harness.py`) passes.

### Phase 4 — Adjustment merge
- Fold spot-adj boundaries into the union (reuse existing spot-adj detection).

### Phase 5 — verify + deploy
- Unit tests: union boundaries, carry-across-foreign-boundary, Fresh/Fixed reset,
  OFF==today parity (golden).
- `python -m unittest discover backend/tests`.
- Rust rebuild (base image) + worker restart via `sudo ./start.sh` when workers
  idle.

## 7. Files touched

- `backend/native/src/simulate.rs` — new scheduler + gated branch (Phase 2).
- `backend/services/engine_rust.py` — flag plumbing + Python mirror (1, 3, 4).
- `frontend/src/components/StrategyBuilder.jsx` — toggle + per-leg T-n (1).
- `backend/tests/test_per_leg_rollover.py` — new (5).

## 8. Parity guarantee

Toggle OFF ⇒ no code path changes ⇒ bit-identical to today. Every existing test
and golden stays green. New behavior only when the user turns it on.
