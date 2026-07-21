# Lot-quantity scaling of Net P&L (EOD backtester)

**Date:** 2026-07-21
**Status:** Approved design, pending implementation plan

## Problem

`lots` is user-defined per leg in the frontend and flows end-to-end to the
engine, but it never multiplies anything in the reported P&L. Net P&L is stored
in per-unit premium points by a documented convention
(`backend/native/src/simulate.rs:1645`), so raising a leg from 1 lot to 2 lots
produces a byte-identical tradesheet — only the display `Qty` column changes.

The intraday product (`~/Downloads/intraday-algo`) already implements the
intended behaviour and documents it as the AlgoTest-matching rule
(`crates/iengine/src/engine.rs:2921`):

```rust
// POINTS × lots (matches tradesheet net_pnl; AlgoTest triggers on that P&L).
```

with the multiplication at `engine.rs:2549`, `:2726`, `:3048`:

```rust
let net = (match leg.side {
    PosSide::Sell => entry_px - exit_px,
    PosSide::Buy => exit_px - entry_px,
}) * leg.lots as i64;
```

## Goal

Bring the EOD backtester to the same rule: **Net P&L = points × lots, applied
per leg**, so a user-entered lot quantity scales the P&L everywhere in the
software, for every leg independently, regardless of index.

## The rule

```
leg_pnl_points = (entry_price − exit_price)  for SELL
                 (exit_price − entry_price)  for BUY
leg_pnl        = leg_pnl_points × leg.lots
trade_net_pnl  = Σ leg_pnl        (over the trade's legs)
```

`lot_size` is **not** part of this. It stays per-index and feeds only the
display `Qty = lots × lot_size` column — identical to intraday
(`engine.rs:2571`).

Per-leg means legs may carry different lot counts (a 2×1 ratio spread is
valid); each leg scales by its own `lots`.

### What changes

| Field | Scales with lots? |
|---|---|
| `Net P&L` | **Yes** |
| `CE P&L` / `PE P&L` / `FUT P&L` | **Yes** (per-leg) |
| `% P&L` (`net / entry_spot`) | **Yes** — consequence of Net P&L; matches intraday |
| `Qty` | Yes — already `lots × lot_size`, unchanged |
| NAV base-100, Max DD, CAGR, WOW/MOM, patch-wise, optimizer objectives | **Yes** — derived from Net P&L, scale automatically, no code change |

### What does not change

| Field | Why |
|---|---|
| `Entry Price`, `Exit Price`, `Raw Entry/Exit Price` | Per-unit by definition. Intraday does not scale them either. |
| `MAE` / `MFE` | Ratios (`backend/native/src/mae.rs:191`). Quantity-invariant by construction; intraday's `compute_mae_mfe` likewise never sees lots. |
| `Entry Spot` / `Exit Spot` / `Spot P&L` | Index levels, not position values. |
| Overall SL/Target threshold machinery | Deliberately out of scope — see below. |

### lots = 1 is a no-op

Multiplying by 1 changes nothing, so every existing 1-lot output stays
byte-identical. This is the primary safety property and the basis of the
parity gate.

## Explicitly out of scope: Overall SL/Target

EOD and intraday use genuinely different units in the Overall SL box, and the
difference is **not** a lot-scaling difference — it exists at 1 lot.

`backend/services/engine_rust.py:240-268` vs `crates/iengine/src/engine.rs:2925`:

| Basis | EOD | Intraday | Equivalent at 1 lot? |
|---|---|---|---|
| `total_premium_pct` | `Σ(ep × lots × lot_size) × pct` | `Σ(ep × lots) × pct` | **Yes** — the factor cancels against the P&L it is compared to |
| `underlying_pts` / `underlying_pct` | raw value | raw value | **Yes** |
| `fixed` / `max_loss` / `max_profit` | literal ₹ vs `pts × lots × lot_size` | literal vs `pts × lots` | **No** |
| `points` | `value × Σ(lots × lot_size)` | literal `value` | **No** |

Converting the bottom two rows would change existing 1-lot results for any
strategy using an amount- or points-based Overall SL, and old saved runs would
stop reproducing.

**Decision: leave the SL threshold machinery untouched.** The live comparison
at `backend/native/src/lib.rs:1920` already scales by `lots × lot_size`, so as
`lots` rises the threshold check and the reported P&L now move in the same
direction. EOD's SL box keeps meaning rupees; intraday's keeps meaning points.
The two products differ only in that one box, and that is accepted.

Two pre-existing items noted but **not** addressed here:
- `backend/native/src/lib.rs:1655-1656` — `lots`/`lot_size` extracted in
  `check_leg_stop_loss_target` and never used. Dead reads.
- The EOD/intraday unit mismatch above, should parity ever be wanted later.

## Sites to change

### A. Rust — authoritative engine

- `backend/native/src/simulate.rs:1651-1656` — multiply `net_pnl` by `s.lots`.
  Field exists at `:193`, parsed at `:392`, `:1053`, `:1600`.
- Verify the trade-total post-process that writes the trade sum into the parent
  row's `net_pnl` aggregates already-scaled per-leg values (no double-scaling).
- Update the convention comment at `:1645-1650`.

### B. Python tradesheet builders — `backend/services/engine_rust.py` (live path)

- `:1446-1450` — `net_pnl` × lots
- `:2497-2501` — `net_pnl` × lots
- `:3251-3253` — `per_leg_pnl` × lots (feeds `CE P&L` / `PE P&L` / `FUT P&L`)

Update the "no lot_size multiplication" comments at `:1446`, `:3792`, `:6632`.

### C. Python reference engine — `backend/engines/generic_algotest_engine.py`

Still reachable through the legacy `/algotest` endpoint
(`backend/routers/backtest.py:770` → `run_algotest_backtest` at `:3283`), so it
must move in lockstep or the two paths diverge.

`_recalc_leg_pnl` at `:1343`, `:1372`; plus `:1643`, `:2139`, `:2266`,
`:4745/4747`, `:4970/4972`, `:5668`.

### D. `backend/engines/generic_multi_leg.py`

- `:349-352` (options) and `:419-422` (futures) — × `leg.lots`
- `:357`, `:429` — writes `"Qty": leg.lots` (raw lots) where every other writer
  emits `lots × lot_size`. Fix to `leg.lots * lot_size`: the charges recalc
  divides by `Qty` to derive per-unit charges, so the current value inflates
  them by a factor of `lot_size` — 65× on NIFTY, 75–140× on MIDCPNIFTY
  depending on date — for any run from this engine. Lot sizes come from
  `get_lot_size_for_index` (`backend/services/index_metadata.py:86`):
  **NIFTY = 65 flat**, **MIDCPNIFTY = 75** before 2024-11-20 then 120/140/120.

### E. Charges — `backend/routers/backtest.py:292-320`

`_calculate_fo_charges` divides by `Qty` to get ₹/unit and adjusts the
per-unit `Entry Price` / `Exit Price`. That stays correct. The final
`leg_pnl = new_entry − new_exit` at `:318-320` must then be multiplied by
`lots` so the charge-adjusted P&L lands in the same unit as the engine's.

### F. No change required

`analytics.rs`, `summary_metrics.rs`, `optimizer.rs`, `optim_metrics.rs`,
`xlsx_writer.rs`, `services/optimizer/*`, WOW/MOM and patch-wise builders all
read Net P&L and contain zero `lots` references. They scale automatically.

## Verification

Per the standing rule that optimizer output must equal the backtest tradesheet
on every metric, and that tradesheets are verified trade-by-trade.

1. **lots = 1 parity gate (blocking).** Full byte-identical output vs current
   `main` for a representative strategy set. Reuse
   `backend/tools/three_way_summary_parity.py` and
   `backend/tests/test_summary_parity_gate.py`.
2. **lots = 2 scaling.** Same trades, same entry/exit dates, same entry/exit
   prices, same MAE/MFE; `Net P&L` exactly 2× the 1-lot run, `% P&L` exactly 2×.
3. **Mixed per-leg lots.** Leg 1 = 2 lots, leg 2 = 1 lot — hand-verified
   trade-by-trade on 2024 data that `Net P&L = 2×leg1_pts + 1×leg2_pts`.
4. **Index-agnostic.** Multi-index run (NIFTY + MIDCPNIFTY legs) with differing
   lots per leg; confirm each leg uses its own `lots` and its own index's
   `lot_size` for `Qty` only.
5. **Three-way identity at lots = 2.** Backtest tradesheet == optimizer
   per-combo == optimizer master, every metric.
6. **Charges on.** Confirm the charge-adjusted P&L scales correctly and the
   `generic_multi_leg` `Qty` fix does not shift 1-lot charge values for engines
   that were already emitting `lots × lot_size`.

## Risks

- **Double-scaling.** The trade total is assembled from per-leg values in more
  than one place. If both the leg and the aggregate multiply, P&L becomes
  `lots²`. Test 2 catches this only if lots ≠ 1 — hence test 3 with unequal
  lots, where `lots²` and `lots` differ per leg.
- **Cached results.** Redis caches by request hash; `lots` is part of the
  payload so keys differ, but the engine cache-version hash
  (`backend/services/backtest_cache.py:58`) must pick up the changed engine
  files, and Celery workers must be restarted before results are trusted.
- **Legacy `/algotest` path.** If section C is skipped, the two engines
  disagree at lots ≥ 2.
