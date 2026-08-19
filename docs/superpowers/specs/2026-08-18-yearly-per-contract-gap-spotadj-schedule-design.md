# Per-Contract Gap + Spot-Adjustment Schedule for the Yearly Leg

**Date:** 2026-08-18
**Status:** Design — awaiting review
**Related:** `2026-07-17-yearly-expiry-1000-gap-design.md` (yearly December-contract pinning + 1000 gap)

## Problem

The yearly leg holds a long-dated **December** contract and rolls to the next December once a
year, at its **Yearly Exit T-n** (user-defined). Today, that leg has a single **strike gap**
(`strike_interval`) and a single **spot-adjustment trigger** (`spot_adjustment.pct`), both fixed
for the entire backtest — every December contract is forced to use the same numbers.

That does not fit the market. NIFTY sat ~11,000 in 2020 and ~24,000 in 2025, so a 1,000-point
gap/trigger is ~9% of spot early and ~4% late. The research team needs the yearly leg to use a
**different gap + trigger per December contract**, stepping up exactly when the contract rolls.

## Scope

- **Yearly leg(s) only.** Every other leg is untouched.
- **Opt-in.** When no schedule is supplied, behaviour is **byte-identical** to today (the leg's
  single `strike_interval` + `spot_adjustment` are used).
- Backtest first. Optimizer sweeping of the schedule is explicitly **out of scope** for v1.

## What the user configures

A per-yearly-leg table, one row per December contract, holding **two user-defined numbers**:

```
leg.yearly_contract_schedule = [
  { "contract": "2022", "strike_gap": 500,  "spot_adj_pct": 500  },
  { "contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000 },
  { "contract": "2024", "strike_gap": 1000, "spot_adj_pct": 1000 },
]
```

- `contract` = the **December expiry year** of the contract the leg holds (matches the year of
  `yearly_cycles[i].contract`, e.g. `2023-12-30` → `"2023"`).
- `strike_gap` = the strike spacing while that contract is held (drives ATM rounding **and** the
  OTM/ITM offset, which is `n × gap`).
- `spot_adj_pct` = the spot-adjustment trigger while that contract is held.
- **Direction and units are NOT per-row.** They stay the leg's existing
  `spot_adjustment.direction` / `.units` (e.g. Rise, points). The row supplies only the two
  magnitudes.

## When a row applies (the key semantic)

A row belongs to the **contract the leg is holding**, and a contract is held from the **T-n roll
that rolls _into_ it** until the next roll — i.e. roughly a year before that December, NOT the
calendar year of the December.

| Period the leg is live (Yearly Exit = T-1 month) | Contract held | Row used |
|---|---|---|
| ~Nov-2021 → ~Nov-2022 | Dec-2022 | `2022` → 500 / 500 |
| **~Nov-2022** → ~Nov-2023 | Dec-2023 | `2023` → **1000 / 1000** |
| ~Nov-2023 → ~Nov-2024 | Dec-2024 | `2024` → 1000 / 1000 |

So `Dec-2023 → 1000` takes effect at the **~Nov-2022 roll** (the moment the leg shifts onto the
Dec-2023 contract), exactly matching "from when we shift, start seeing 1000". The boundary always
tracks the leg's own **Yearly Exit T-n**; there is no separate date input. The contract the leg
holds per trade is already known from `yearly_cycles` (the pinned December), so the active row is
looked up by that contract's December year.

## What switches at the roll

Both, together, at the roll into a new contract:

1. **Strike gap** → ATM rounds to the new spacing, and the OTM/ITM offset (`n × gap`) widens/narrows
   with it. Example: BUY PE "2 OTM" on Dec-2022 @ spot 18,000 → `18,000 − 2×500 = 17,000`; on
   Dec-2023 @ spot 19,000 → `19,000 − 2×1000 = 17,000` (2,000 out instead of 1,000).
2. **Spot-adjustment trigger** → the leg re-strikes after a move of the new size (500 → 1000).

## Fallback (non-disruption)

- A December contract with **no matching row** (or years before the first row) → the leg uses its
  **normal single `strike_interval` + `spot_adjustment`**. Nothing breaks for unscheduled years.
- No `yearly_contract_schedule` on any leg → the resolver is a no-op and the pipeline behaves
  exactly as before. This is the primary safety guarantee.

## Excel reason

On the **first trade of each new contract** (the trade whose entry is the first at/after the roll
into that contract), write a reason into the existing **Strike Shift Reason** column:

```
YEARLY_ROLL → Dec-2023 (gap 1000, adj 1000)
```

- Only the **first trade** of the new contract carries it (not every trade of the contract).
- Co-exists with any existing Strike Shift Reason via the same `" + "` join already used for
  combined reasons.
- Only emitted when a schedule row actually changed the gap and/or trigger at that roll (a roll
  with unchanged numbers, or an unscheduled contract, writes nothing new).

## UI (backtest)

Under the yearly leg's config, a small editable table: `Contract (Dec-YYYY) | Strike gap |
Spot-adj`. Shown only when the leg's expiry is Yearly. Rows are optional; empty table = today's
behaviour. Direction/units are inherited from the leg's existing spot-adjustment controls and are
not repeated per row.

## Non-disruption guarantees (summary)

- Opt-in: absent schedule → identical output (the resolver returns the leg's existing gap/pct).
- Yearly-leg-only: the schedule is read only when a leg's expiry resolves to the December-pinned
  yearly contract; weekly/monthly legs never consult it.
- No change to direction/units, to the roll timing (still Yearly Exit T-n), or to any non-yearly
  leg.

## Out of scope (v1)

- Optimizer sweeping of schedule rows.
- Per-row direction/units.
- Percent↔points mixing across rows (units are the leg's single setting).
- Any leg other than the yearly leg.

## Testing approach

- **Fallback parity:** a yearly config with no schedule produces a byte-identical tradesheet to the
  current engine (guards non-disruption).
- **Boundary:** a Dec-2022→Dec-2023 roll with `2022→500`, `2023→1000` uses 500 gap/trigger on the
  last Dec-2022 trade and 1000 on the first Dec-2023 trade; the OTM offset and the adjust trigger
  both step at the roll.
- **Reason:** the first Dec-2023 trade carries `YEARLY_ROLL → Dec-2023 (gap 1000, adj 1000)` in
  Strike Shift Reason; later Dec-2023 trades do not; an unscheduled year carries nothing.
- **Off-by-one:** confirm `2023 → 1000` activates at the ~Nov-2022 roll, not Jan-2023.
