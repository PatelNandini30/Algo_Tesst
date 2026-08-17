# Per-Leg Filter — Mid-Cycle Split (Option C) — Design

Date: 2026-08-01
Status: Approved, not implemented

## Problem

The per-leg individual filter today is **subtract-only**, evaluated once at the
trade's entry date:

- Range **start** inside a live trade → the leg is simply **absent** for that
  trade and rejoins at the next scheduled roll.
- Range **end** inside a live trade → the leg's exit is **truncated**
  (`LEG_FILTER_END`) while the other legs run the full trade.

The user wants the filtered leg to trade **exactly its own range**, even when a
range boundary falls mid-cycle. Concretely (real case, Trade 25 of the yearly
BUY + weekly SELL strategy): Leg 2's range opens **06-Jan-2020**, which lands
inside T25's window (02-Jan → 09-Jan). Today Leg 2 sits out T25 entirely and
only joins at T26 (09-Jan). The user wants Leg 2 to **enter on 06-Jan**.

## The chosen behaviour — Option C (mid-cycle split)

A filtered leg's range boundary (start OR end) that falls **strictly inside a
live trade's window** **splits that weekly block in two at the boundary date.**
The unfiltered leg is carried straight across the cut; the filtered leg begins
(at a range start) or ends (at a range end) exactly on the boundary.

**Worked example — entry split, Trade 25** (Leg 1 = 12200 PE SELL, entry
02-Jan @ 23.35, exit 09-Jan @ 1.00; Leg 2 range = `[06-Jan → 04-Feb]`):

```
Today (Leg 2 absent):
  T25  L1  02-Jan → 09-Jan   PE 12200 SELL   23.35 → 1.00   = +22.35   EXPIRY

Under C (split at 06-Jan):
  T25  L1  02-Jan → 06-Jan   PE 12200 SELL   23.35 → P₆     LEG_FILTER_END
  T26  L1  06-Jan → 09-Jan   PE 12200 SELL   P₆   → 1.00    EXPIRY        (carried)
  T26  L2  06-Jan → 09-Jan   PE 12000 BUY(Dec-20)  B₆ → B₉   EXPIRY        (fresh entry)
  (old T26 rolls on as T27, everything renumbers)
```

**Worked example — exit split, Trade 29** (Leg 1 = 12000 PE SELL 30-Jan @
132.10 → 06-Feb @ 0.10; Leg 2 = 12000 PE BUY Dec-20 30-Jan @ 459.75 → 422.50;
range end = 04-Feb):

```
Under C (split at 04-Feb):
  T29  L1  30-Jan → 04-Feb   PE 12000 SELL   132.10 → Q₄    LEG_FILTER_END
  T29  L2  30-Jan → 04-Feb   PE 12000 BUY    459.75 → C₄    LEG_FILTER_END   (leg 2 stops at range end)
  T30  L1  04-Feb → 06-Feb   PE 12000 SELL   Q₄   → 0.10    EXPIRY           (leg 1 alone)
```

### Locked rules (owner decisions, 2026-08-01)

1. **Split triggers** on a filtered leg's range **start** and range **end** when
   the boundary (snapped to the last trading day on/before the range end, or the
   first trading day on/after the range start) falls strictly inside a live
   trade's `(entry, exit)` window.
2. **Carried leg is cost-free.** The unfiltered leg keeps its strike and
   contract across the cut; the split-date mark is both the close of segment 1
   and the open of segment 2 — **no slippage, no re-strike, no re-entry cost.**
   Its P&L is only *partitioned*; the mark cancels and the total is unchanged.
3. **Filtered leg** enters fresh at a range start (its own ATM strike/price
   resolved at the boundary date, on the same contract it would trade at the
   next roll) and exits at a range end.
4. **Each split segment is its own trade row.** Trades renumber; NAV /
   Cumulative compounds over the extra step (owner confirmed).
5. **Exit reasons:** strategy-patch boundary → `FILTER_END`; a filtered leg's
   own range boundary (start-split carry row, end-split, filtered-leg exit) →
   `LEG_FILTER_END`; a normal weekly expiry → `EXPIRY`. Combined reasons join
   with `+`, as elsewhere.

### Supersedes

Option C **replaces** the current subtract-only behaviour of the per-leg filter
(drop-at-start / truncate-at-end). That behaviour shipped on `feat/per-leg-filter`
but was never blessed by an end-to-end verification, so there is no production
result to preserve. Existing per-leg-filter tradesheets **will change** — the
filtered leg now trades its exact range instead of joining/leaving at roll
boundaries. This is the intended correction, not a regression.

(If a toggle between "subtract-only" and "split" is ever wanted, it can be added
later. Not in scope now — the owner wants split to be the behaviour.)

## Architecture

The transformation is a **spec-list rewrite**, applied where the current
`apply_leg_filters` post-pass runs (`engine_rust.py:6166`), but promoted from
"drop/truncate" to "split". It must run:

- **after** the spec builders resolve every leg's schedule and strike, and
  after `_apply_fixed_rollover_strike` (so strike epochs come from the unmasked
  schedule — same ordering constraint the current mask already respects);
- **before** pricing (`simulate_trades_batch`) and before the spot-adjustment
  cascade / NAV, so everything downstream sees a normal, longer spec list with
  sequential trade ids.

It needs three things the current post-pass does not receive and must be given:
`spot_by_date`, the trading-day list (already passed), and a **strike resolver**
for the filtered leg's mid-cycle entry (`_compute_strike_for_leg_python`, the
same one the builders use).

### The core transform (per trade)

For each trade with window `[E, X]` built from the unfiltered legs:

1. Collect boundary dates `B` = every filtered leg's range starts/ends that fall
   strictly inside `(E, X)`, snapped to trading days, deduped and sorted.
2. If `B` is empty → the trade is unchanged **except** the existing
   whole-window masking: a filtered leg whose `[E, X]` lies entirely outside all
   its ranges is dropped (today's absent case); entirely inside → present as now.
3. If `B` is non-empty → split `[E, X]` into consecutive sub-windows
   `[E, b₁], [b₁, b₂], …, [bₖ, X]`. Each sub-window becomes its **own trade**
   (new sequential id). For each sub-window and each leg:
   - **Unfiltered leg:** present in every sub-window, carried — same strike, the
     boundary mark is a cost-free carry (segment-1 exit / segment-2 entry both
     suppress slippage via the existing carry-slippage guard).
   - **Filtered leg:** present iff the sub-window lies inside one of its ranges.
     A fresh strike is resolved at the sub-window's start date; it exits at the
     sub-window's end.

Presence for a filtered leg thus reduces to "is this sub-window inside a range",
and the split boundaries come **only** from the filtered legs' ranges.

### Pricing / carry

- Splitting the unfiltered leg into two specs `(E→b₁, strike)` and
  `(b₁→X, strike)` makes `simulate_trades_batch` price each naturally; the `b₁`
  mark is both the seg-1 exit price and the seg-2 entry price. **Slippage must be
  suppressed at the synthetic `b₁` boundary** (seg-1 exit and seg-2 entry are a
  carry, not real fills) so the partition is exactly cost-free — reuse
  `_apply_carry_slippage_guard`.
- The filtered leg's mid-cycle entry is priced from the boundary date's data
  like any other entry.

## Constraints (project hard rules)

- **A run with no individual filter is byte-identical to before this change.**
  The transform is a no-op when no leg carries a file.
- **P&L conservation:** the unfiltered leg's total across its split segments
  equals its unsplit total (the boundary mark cancels). Assert this.
- **optim == backtest:** the split must produce identical per-combo numbers in
  the optimizer. Either implement it on the optimizer path too, or fail-closed
  in the Rust batch coverage gate until it is (mirrors how the current per-leg
  filter is gated).
- **Rust-only, no Python fallback**; unsupported combinations hard-fail.
- **Tests never touch market data** (no `build_cache` / `warm_cache` /
  `_prepare_market_data` / real-symbol runs) — synthetic specs only.
- **Spot P&L** already rides the lowest present leg per trade — a split trade
  whose only leg is the carried one still reports correctly.

## Downstream that must absorb the extra rows

- **NAV / Cumulative / DD** — extra trades, one NAV step each (owner confirmed).
- **MAE / MFE / Spot P&L** — recomputed over each split window (same machinery
  used for truncated legs today).
- **Spot-adjustment cascade / re-entry** — must treat a split boundary correctly
  and must not resurrect a leg past its range (the existing `_leg_was_truncated`
  guard, keyed renumber-proof, is the model).
- **WOW/MOM and Patch-wise** — bucket the extra rows correctly; a split segment
  belongs to the same strategy patch as its parent trade.
- **Trade numbering** — renumber sequentially at the split point so every
  downstream consumer keyed on `trade_id` sees a normal list.

## Testing

Pure-function first: given `[E, X]`, a filtered leg's ranges and a trading-day
list, the transform yields the right sub-windows and per-leg presence — cover
entry-split, exit-split, both-in-one-trade, boundary on a non-trading day
(snap), boundary on E or X exactly (no split), and whole-window-outside (drop).

Engine-level (synthetic specs, no market data): P&L conservation on the carried
leg; exit-reason tags; sequential renumbering; byte-identical output when no
filter is present.

End-to-end (manual, human-run — the suite must not touch market data): reproduce
Trade 25 and confirm Leg 2 opens 06-Jan; Trade 29 and confirm Leg 2 exits 04-Feb
with Leg 1 split; a control run with no filter byte-identical to a pre-change
build; the optimizer per-combo tradesheet equal to the direct backtest.

## Out of scope

- A toggle between subtract-only and split (split is THE behaviour now).
- Any change to the strategy-level filter or to legs without an individual file.
- Splitting on anything other than a filtered leg's own range boundaries.
