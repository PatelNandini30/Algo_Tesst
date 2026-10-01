# Optimizer — How to Set Parameters (UI Reference)

This guide tells you exactly what to type in each field for every parameter
in the optimizer. For each one you get: what it means, the unit, what
min/max/step to enter, and what values that produces.

---

## How the min / max / step fields work

When you tick a parameter, three boxes appear:

```
min [ 30 ]   max [ 150 ]   step [ 30 ]
```

The optimizer will try every value from **min** to **max** jumping by **step**.

Formula: `number of values = (max − min) / step + 1`

**Example:**
```
min = 30,  max = 150,  step = 30
→ tries:  30 · 60 · 90 · 120 · 150     (5 values)
```

**Another example:**
```
min = 1.0,  max = 3.0,  step = 0.5
→ tries:  1.0 · 1.5 · 2.0 · 2.5 · 3.0     (5 values)
```

---

## Group 1 — Per-Leg Risk

These parameters apply **per leg** individually.
If your strategy has 2 legs you will see "Leg 1" and "Leg 2" entries for each.

---

### Stop Loss value

**What it does:** Sets the stop-loss level for the leg. When the option
premium moves against you by this amount, the leg exits.

**Unit:** % or points — depends on how your strategy's SL is configured.
Most NIFTY strategies use % of premium.

| Field | Recommended value | Notes |
|---|---|---|
| min | 20 | Don't go below 20% — too tight, trades out constantly |
| max | 100 | 100% = let it double before exiting |
| step | 20 | Gives 5 values — good starting resolution |

```
min [20]   max [100]   step [20]
→ tries:  20 · 40 · 60 · 80 · 100     (5 values)
```

**Narrower sweep (fine-tune after first run):**
```
min [40]   max [80]   step [10]
→ tries:  40 · 50 · 60 · 70 · 80     (5 values)
```

**Wider sweep (first exploration):**
```
min [20]   max [200]   step [20]
→ tries:  20 · 40 · 60 · 80 · 100 · 120 · 140 · 160 · 180 · 200     (10 values)
```

---

### Target Profit value

**What it does:** Locks in profit when the option premium falls by this
amount (for short legs). The leg exits early at target.

**Unit:** % or points.

| Field | Recommended value | Notes |
|---|---|---|
| min | 20 | 20% of premium captured |
| max | 100 | Full premium captured |
| step | 20 | Gives 5 values |

```
min [20]   max [100]   step [20]
→ tries:  20 · 40 · 60 · 80 · 100     (5 values)
```

**If testing aggressive targets:**
```
min [30]   max [90]   step [15]
→ tries:  30 · 45 · 60 · 75 · 90     (5 values)
```

---

### SL-with-Buffer value

**What it does:** A smarter stop-loss. Instead of exiting immediately when
SL is hit, it waits for a buffer zone to confirm the move before exiting.
This avoids getting stopped out by brief spikes.

**Unit:** % of premium.

| Field | Recommended value | Notes |
|---|---|---|
| min | 10 | Small SL trigger |
| max | 60 | Large SL trigger |
| step | 10 | Gives 6 values |

```
min [10]   max [60]   step [10]
→ tries:  10 · 20 · 30 · 40 · 50 · 60     (6 values)
```

> **Note:** SL-with-Buffer only takes effect if your strategy has
> SL-with-Buffer mode turned on. If the leg uses a plain stop-loss,
> changing this field will have no effect.

---

### SL-with-Buffer buffer %

**What it does:** The confirmation buffer size. After the main SL level is
hit, the price must continue by this % before the exit triggers.

**Unit:** % of premium.

| Field | Recommended value | Notes |
|---|---|---|
| min | 1 | Tiny buffer — exits almost immediately after SL |
| max | 10 | Large buffer — waits for significant follow-through |
| step | 1 | Gives 10 values — can be reduced to step=2 for fewer combos |

```
min [1]   max [10]   step [1]
→ tries:  1 · 2 · 3 · 4 · 5 · 6 · 7 · 8 · 9 · 10     (10 values)
```

**Typical research sweep:**
```
min [2]   max [8]   step [2]
→ tries:  2 · 4 · 6 · 8     (4 values)
```

---

### Trail SL — Trigger

**What it does:** The trailing stop starts moving only after the option
has moved in your favour by this % (the "trigger" profit level).

**Unit:** % of initial premium.

| Field | Recommended value | Notes |
|---|---|---|
| min | 5 | Trail kicks in after 5% profit |
| max | 50 | Trail kicks in only after 50% profit |
| step | 5 | Gives 10 values — use step=10 for fewer combos |

```
min [5]   max [50]   step [5]
→ tries:  5 · 10 · 15 · 20 · 25 · 30 · 35 · 40 · 45 · 50     (10 values)
```

**Lighter sweep:**
```
min [10]   max [40]   step [10]
→ tries:  10 · 20 · 30 · 40     (4 values)
```

---

### Trail SL — Move

**What it does:** Once the trail is triggered, this is how much the SL
moves for every 1% the option moves in your favour.

**Unit:** % (the step size of the trailing SL movement).

| Field | Recommended value | Notes |
|---|---|---|
| min | 2 | SL trails slowly |
| max | 20 | SL trails aggressively |
| step | 2 | Gives 10 values — use step=4 for fewer combos |

```
min [2]   max [20]   step [2]
→ tries:  2 · 4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20     (10 values)
```

**Lighter sweep:**
```
min [5]   max [20]   step [5]
→ tries:  5 · 10 · 15 · 20     (4 values)
```

---

## Group 2 — Per-Leg Strike

---

### Strike offset % (pct_of_atm value)

**What it does:** How far from ATM (in % of spot) the strike is selected.
Positive = OTM, Negative = ITM, Zero = ATM.

**Unit:** % of spot price.

**Important:** Ticking this automatically switches the leg to `pct_of_atm`
mode. You do not need to change anything else.

| Field | Recommended value | Notes |
|---|---|---|
| min | 0.5 | Very close to ATM (0.5% OTM) |
| max | 5.0 | Deep OTM (5% away from spot) |
| step | 0.5 | Gives 10 values — half-percent steps |

```
min [0.5]   max [5.0]   step [0.5]
→ tries:  0.5 · 1.0 · 1.5 · 2.0 · 2.5 · 3.0 · 3.5 · 4.0 · 4.5 · 5.0     (10 values)
```

**Tighter sweep around typical NIFTY strangle:**
```
min [1.0]   max [4.0]   step [0.5]
→ tries:  1.0 · 1.5 · 2.0 · 2.5 · 3.0 · 3.5 · 4.0     (7 values)
```

**Testing ITM strikes (negative values):**
```
min [-3.0]   max [3.0]   step [1.0]
→ tries:  -3.0 · -2.0 · -1.0 · 0.0 · 1.0 · 2.0 · 3.0     (7 values)
→ -3.0 = 3% ITM,  0.0 = ATM,  3.0 = 3% OTM
```

---

### Strike type (enum)

**What it does:** Selects strikes by fixed labels (ATM, OTM1, OTM2, etc.)
instead of percentage distance. OTM1 = 1 strike above ATM, OTM2 = 2 strikes above, etc.

**Type:** Enum (comma-separated word choices, not min/max/step).

Default choices loaded in UI:
```
ATM, ITM1, ITM2, ITM3, OTM1, OTM2, OTM3
```

**What to type in the box:**

Testing just OTM strikes for a short strangle CE leg:
```
[ OTM1, OTM2, OTM3 ]     → tries 3 values
```

Testing ATM and near-OTM:
```
[ ATM, OTM1, OTM2 ]      → tries 3 values
```

Testing both sides of ATM:
```
[ ITM1, ATM, OTM1, OTM2 ]   → tries 4 values
```

> **Note:** Strike type enum uses fixed strike steps (like OTM1 = 1 strike
> above ATM in the options chain). Strike offset % uses exact percentage
> distance from spot. Pick one or the other — do not tick both for the
> same leg at the same time.

---

### Expiry window (enum)

**What it does:** Which expiry cycle the leg trades. Allows testing weekly
vs monthly without rebuilding the strategy.

**Type:** Enum.

Default choices:
```
WEEKLY, NEXT_WEEKLY, MONTHLY, NEXT_MONTHLY
```

**What to type:**

Testing weekly vs monthly:
```
[ WEEKLY, MONTHLY ]     → tries 2 values
```

Testing current and next weekly (for calendar spreads):
```
[ WEEKLY, NEXT_WEEKLY ]     → tries 2 values
```

Full sweep:
```
[ WEEKLY, NEXT_WEEKLY, MONTHLY, NEXT_MONTHLY ]     → tries 4 values
```

---

## Group 3 — Global Entry / Exit

These apply to the whole strategy (not per leg). They control *when* each
trade enters and exits relative to the expiry date.

---

### What is DTE?

**DTE = Days To Expiry** — counted in **trading days only**
(weekends and public holidays are not counted).

The engine counts backwards from the expiry date:

```
Weekly expiry: Thursday 23-Jan-2025
Trading days:  Mon 20 · Tue 21 · Wed 22 · Thu 23

DTE 0  →  23-Jan (Thu)   expiry day itself
DTE 1  →  22-Jan (Wed)   1 trading day before expiry
DTE 2  →  21-Jan (Tue)   2 trading days before expiry
DTE 3  →  20-Jan (Mon)   3 trading days before expiry
```

> **EOD price model:** The engine uses end-of-day closing prices. "Enter at
> DTE 2" means entry premium = closing price of 21-Jan. In practice this
> represents the price available the following morning at open.

---

### Entry DTE (days before expiry)

**What it does:** How many trading days before *each* expiry the trade enters.
This applies to every single trade in the backtest.

**Unit:** days (whole numbers only — step must always be 1).

**Trade examples — weekly expiry, Entry DTE = 2:**

```
Expiry 1:  23-Jan  →  Entry = 21-Jan (Tue),  Exit = 23-Jan (Thu)
Expiry 2:  30-Jan  →  Entry = 28-Jan (Tue),  Exit = 30-Jan (Thu)
Expiry 3:  06-Feb  →  Entry = 04-Feb (Tue),  Exit = 06-Feb (Thu)
```

**What each value means:**

| Entry DTE | Entry day (weekly) | Character |
|-----------|-------------------|-----------|
| 0 | Thursday (expiry day) | Same-day entry — usually skipped (needs special setup) |
| 1 | Wednesday | 1-day theta play |
| 2 | Tuesday | Standard weekly setup |
| 3 | Monday | Slightly longer hold |
| 5 | Previous Thursday | Near full-week hold |

**How to enter:**

```
min [1]   max [5]   step [1]
→ tries:  1 · 2 · 3 · 4 · 5     (5 values)
```

**Testing only T-1 and T-2 (most common for weekly):**
```
min [1]   max [2]   step [1]
→ tries:  1 · 2     (2 values)
```

**Wider exploration:**
```
min [0]   max [5]   step [1]
→ tries:  0 · 1 · 2 · 3 · 4 · 5     (6 values)
```

---

### Exit DTE (days before expiry)

**What it does:** How many trading days before expiry the trade exits.
0 = hold until expiry day. 1 = exit one day before expiry.

**Unit:** days (whole numbers only).

**Trade examples — Entry DTE = 3, Exit DTE = 1:**

```
Expiry 23-Jan:
  Entry = 20-Jan (Mon)  ← 3 days before expiry
  Exit  = 22-Jan (Wed)  ← 1 day before expiry
  Holding = 2 days (Mon close → Wed close)
```

**Common Entry / Exit DTE combinations and their trade windows:**

| Entry DTE | Exit DTE | Entry day | Exit day | Holding |
|-----------|----------|-----------|----------|---------|
| 2 | 0 | Tuesday | Thursday (expiry) | 2 days — most common weekly setup |
| 1 | 0 | Wednesday | Thursday | 1 day |
| 3 | 1 | Monday | Wednesday | 2 days — exit before expiry |
| 3 | 0 | Monday | Thursday | 3 days |
| 2 | 1 | Tuesday | Wednesday | 1 day |

> **Warning — invalid pairs:** If Exit DTE ≥ Entry DTE (e.g. entry=1, exit=2),
> the exit date falls *before* the entry date. The engine detects this and
> **skips those trades — they produce zero P&L.** When sweeping both together,
> roughly half the combinations will be invalid. You can spot them in the
> results by `total_trades = 0`. It is safer to fix Exit DTE = 0 in the
> strategy and only sweep Entry DTE.

**How to enter:**

```
min [0]   max [2]   step [1]
→ tries:  0 · 1 · 2     (3 values)
```

**Testing only exit-on-expiry vs exit-1-day-before:**
```
min [0]   max [1]   step [1]
→ tries:  0 · 1     (2 values)
```

> **Sweeping both Entry and Exit DTE together:**
> Entry min=1, max=3, step=1 (3 values) × Exit min=0, max=1, step=1 (2 values)
> = 6 combinations. Of those, 3 will be valid (exit < entry) and 3 invalid
> (exit ≥ entry, zero trades).

---

### Min days to entry

**What it does:** A filter applied *only to the very first trade* when the
strategy starts. It checks how many trading days remain to the first expiry
from the strategy start date. If fewer days remain than this threshold,
the first cycle is skipped and the strategy starts from the next expiry.

**Unit:** days (whole numbers only).

**Why this matters:** A strategy starting on Wednesday with weekly expiry
on Thursday has only 1 trading day of runway. With Entry DTE = 2, there
is not enough room for a clean entry. `min_days_to_entry` lets you skip
that first stub trade.

**Trade example — Strategy start: Wednesday 22-Jan-2025:**

Weekly expiries: 23-Jan, 30-Jan, 06-Feb. Entry DTE = 2.

```
Trading days from 22-Jan to first expiry 23-Jan = 2 days (Wed + Thu)
```

**With min_days_to_entry = 0 (no filter):**
```
2 ≥ 0 → first trade proceeds
Trade 1: Entry = 22-Jan (forced to seg_start), Exit = 23-Jan  ← stub, 1 day only
Trade 2: Entry = 28-Jan, Exit = 30-Jan
Trade 3: Entry = 04-Feb, Exit = 06-Feb
```

**With min_days_to_entry = 3:**
```
2 < 3 → skip 23-Jan cycle
Trade 1: Entry = 28-Jan, Exit = 30-Jan  ← clean full trade
Trade 2: Entry = 04-Feb, Exit = 06-Feb
```

**What each value means:**

| min_days_to_entry | Effect on first trade |
|-------------------|-----------------------|
| 0 | No restriction — always start from the first available expiry |
| 1 | Skip only if strategy starts on the expiry day itself |
| 2 | Skip if fewer than 2 trading days remain to first expiry |
| 3 | Skip if fewer than 3 days remain — use with Entry DTE = 2 |
| 4 | Skip unless at least 4 days remain |

> **Rule of thumb:** Set `min_days_to_entry` equal to your `entry_dte`. If you
> enter 2 days before expiry, use `min_days_to_entry = 2` so the first trade
> always has the full intended holding window.

**How to enter:**

```
min [0]   max [4]   step [1]
→ tries:  0 · 1 · 2 · 3 · 4     (5 values)
```

---

### Sweeping all three together

```
Entry DTE:       min=1, max=3, step=1   →  3 values
Exit DTE:        min=0, max=1, step=1   →  2 values
Min days entry:  min=0, max=3, step=1   →  4 values
Total = 3 × 2 × 4 = 24 combinations
```

| Combo | Entry DTE | Exit DTE | Min days | Trade window | First-trade filter |
|-------|-----------|----------|----------|--------------|--------------------|
| A | 1 | 0 | 0 | Wed → Thu | None |
| B | 2 | 0 | 0 | Tue → Thu | None |
| C | 2 | 0 | 2 | Tue → Thu | Skip if <2 days at start |
| D | 3 | 1 | 3 | Mon → Wed | Skip if <3 days at start |
| E | 2 | 1 | 2 | Tue → Wed | Skip if <2 days at start |

Combos where Exit DTE ≥ Entry DTE (e.g. entry=1, exit=1) will show
`total_trades = 0` in results — they are automatically skipped by the engine.

---

## Group 4 — Global Risk

These apply to the whole portfolio of legs together.

---

### Overall SL value

**What it does:** A portfolio-level stop-loss. When the combined P&L of all
legs hits this level as a loss, the entire trade exits — even if individual
leg SLs have not triggered.

**Unit:** % of initial spot (or points — depends on your strategy config).

| Field | Recommended value | Notes |
|---|---|---|
| min | 1 | 1% of spot as overall SL |
| max | 10 | 10% of spot |
| step | 1 | Gives 10 values |

```
min [1]   max [10]   step [1]
→ tries:  1 · 2 · 3 · 4 · 5 · 6 · 7 · 8 · 9 · 10     (10 values)
```

**Lighter sweep:**
```
min [2]   max [8]   step [2]
→ tries:  2 · 4 · 6 · 8     (4 values)
```

---

### Overall Target value

**What it does:** A portfolio-level profit target. When combined P&L
reaches this level, the entire trade exits and locks in profits.

**Unit:** % of initial spot (or points).

| Field | Recommended value | Notes |
|---|---|---|
| min | 1 | Lock in at 1% |
| max | 20 | Lock in at 20% |
| step | 1 | Gives 20 values — reduce step to 2 or 5 for fewer combos |

```
min [2]   max [10]   step [2]
→ tries:  2 · 4 · 6 · 8 · 10     (5 values)
```

---

### Slippage %

**What it does:** A simulated transaction cost applied to every entry and
exit. Tests how robust the strategy is to different levels of market impact.

**Unit:** % of premium (per leg, per trade).

| Field | Recommended value | Notes |
|---|---|---|
| min | 0 | No slippage (best case) |
| max | 0.5 | High slippage scenario |
| step | 0.05 | Gives 11 values — use 0.1 for 6 values |

```
min [0]   max [0.5]   step [0.1]
→ tries:  0.0 · 0.1 · 0.2 · 0.3 · 0.4 · 0.5     (6 values)
```

**Quick test of slippage sensitivity:**
```
min [0]   max [0.3]   step [0.1]
→ tries:  0.0 · 0.1 · 0.2 · 0.3     (4 values)
```

---

## Group 5 — Global Spot Adjustment

**These two parameters are meant to be swept together.**
When you sweep `Spot Adjustment %`, the optimizer automatically turns on
the spot-filter — you do not need to enable it manually.

---

### Spot Adjustment %

**What it does:** The minimum spot move (in %) required before the strategy
enters a trade. For example, 3% means the index must have already moved
3% before you enter.

**Unit:** % of spot.

| Field | Recommended value | Notes |
|---|---|---|
| min | 0.5 | Enter after 0.5% spot move |
| max | 5.0 | Enter only after 5% spot move |
| step | 0.5 | Gives 10 values |

```
min [0.5]   max [5.0]   step [0.5]
→ tries:  0.5 · 1.0 · 1.5 · 2.0 · 2.5 · 3.0 · 3.5 · 4.0 · 4.5 · 5.0     (10 values)
```

**Typical NIFTY research sweep:**
```
min [1.0]   max [4.0]   step [0.5]
→ tries:  1.0 · 1.5 · 2.0 · 2.5 · 3.0 · 3.5 · 4.0     (7 values)
```

**Quick wide sweep:**
```
min [1.0]   max [5.0]   step [1.0]
→ tries:  1.0 · 2.0 · 3.0 · 4.0 · 5.0     (5 values)
```

---

### Spot Adjustment direction (enum)

**What it does:** Whether the required spot move must be upward, downward,
or either direction.

**Type:** Enum (comma-separated).

| Choice | Meaning |
|---|---|
| `rise` | Enter only if spot has risen by the required % |
| `fall` | Enter only if spot has fallen by the required % |
| `both` | Enter if spot has moved either up or down by the required % |

**What to type:**

Testing all three directions:
```
[ rise, fall, both ]     → tries 3 values
```

Testing only directional filters:
```
[ rise, fall ]     → tries 2 values
```

Testing neutral (non-directional) filter only:
```
[ both ]     → tries 1 value
```

> **Always combine with Spot Adjustment %.**
> Ticking direction alone (without %) still works but every combo will
> use the base payload's existing % value.

---

## Group 6 — Global Buffer Strike

---

### Buffer strike value

**What it does:** An additional safety offset added to the selected strike.
Useful for strategies where you want the strike to be some distance away
from the current price (buffer from ATM or from another reference point).

**Unit:** % or points (depends on strategy config).

| Field | Recommended value | Notes |
|---|---|---|
| min | 0.5 | Small buffer |
| max | 5.0 | Large buffer |
| step | 0.5 | Gives 10 values |

```
min [0.5]   max [5.0]   step [0.5]
→ tries:  0.5 · 1.0 · 1.5 · 2.0 · 2.5 · 3.0 · 3.5 · 4.0 · 4.5 · 5.0     (10 values)
```

---

## How Many Combinations Will Run

When you tick multiple parameters, the total is multiplied together.

**Example — Stop Loss × Entry DTE:**
```
Stop Loss:   min=20, max=100, step=20  →  5 values
Entry DTE:   min=0,  max=2,   step=1   →  3 values
Total = 5 × 3 = 15 combinations
```

**Example — Strike Offset × Spot Adjustment % × Direction:**
```
Strike offset:   min=1.0, max=4.0, step=0.5  →  7 values
Spot Adj %:      min=1.0, max=4.0, step=1.0  →  4 values
Direction:       rise, fall, both             →  3 values
Total = 7 × 4 × 3 = 84 combinations
```

**Tip:** Always check the **Plan** section on the right panel before
launching. It shows the total combinations and estimated runtime.

---

## Quick Reference — Default Settings in the UI

These are the values pre-filled when you first tick each parameter.
You can change them to anything you need.

| Parameter | Default min | Default max | Default step | Values produced |
|---|---|---|---|---|
| Stop Loss value | 10 | 100 | 10 | 10, 20, 30 … 100 (10 values) |
| Target Profit value | 20 | 200 | 20 | 20, 40, 60 … 200 (10 values) |
| SL-with-Buffer value | 10 | 60 | 10 | 10, 20, 30, 40, 50, 60 (6 values) |
| SL-with-Buffer buffer % | 1 | 10 | 1 | 1, 2, 3 … 10 (10 values) |
| Trail SL — Trigger | 5 | 50 | 5 | 5, 10, 15 … 50 (10 values) |
| Trail SL — Move | 1 | 20 | 2 | 1, 3, 5 … 19 (10 values) |
| Strike offset % | -5.0 | 5.0 | 0.5 | -5, -4.5, -4 … 5 (21 values) |
| Strike type | ATM, ITM1, ITM2, ITM3, OTM1, OTM2, OTM3 | — | — | 7 values |
| Expiry window | WEEKLY, NEXT_WEEKLY, MONTHLY, NEXT_MONTHLY | — | — | 4 values |
| Entry DTE | 0 | 10 | 1 | 0, 1, 2 … 10 (11 values) |
| Exit DTE | 0 | 10 | 1 | 0, 1, 2 … 10 (11 values) |
| Min days to entry | 0 | 5 | 1 | 0, 1, 2, 3, 4, 5 (6 values) |
| Overall SL value | 1 | 10 | 1 | 1, 2, 3 … 10 (10 values) |
| Overall Target value | 1 | 20 | 1 | 1, 2, 3 … 20 (20 values) |
| Slippage % | 0 | 0.5 | 0.05 | 0, 0.05, 0.1 … 0.5 (11 values) |
| Spot Adjustment % | 0.5 | 5.0 | 0.5 | 0.5, 1.0, 1.5 … 5.0 (10 values) |
| Spot Adjustment direction | rise, fall, both | — | — | 3 values |
| Buffer strike value | 0.5 | 5.0 | 0.5 | 0.5, 1.0, 1.5 … 5.0 (10 values) |

> **Warning:** The default Strike offset range is -5 to 5 with step 0.5
> which gives **21 values**. If you combine that with any other parameter,
> the grid grows very quickly. Narrow it to 0.5 to 3.5 (step 0.5) = 7 values
> for a more manageable sweep.

---

## Practical Recipes for Common Research Questions

---

### "What is the best stop-loss for our strangle?"

Tick only **Stop Loss value (Leg 1)** and **Stop Loss value (Leg 2)**:
```
Leg 1 SL:  min=20,  max=100,  step=20   →  5 values
Leg 2 SL:  min=20,  max=100,  step=20   →  5 values
Total = 5 × 5 = 25 combinations
Method: Exhaustive
```

---

### "Should we enter T-0 or T-1 or T-2?"

Tick only **Entry DTE**:
```
Entry DTE:  min=0,  max=2,  step=1   →  3 values
Total = 3 combinations
Method: Exhaustive
```

---

### "What % OTM is best for the CE leg?"

Tick only **Strike offset % (Leg 1)**:
```
Strike offset:  min=1.0,  max=4.0,  step=0.5   →  7 values
Total = 7 combinations
Method: Exhaustive
```

---

### "Does a spot-filter help, and at what %?"

Tick **Spot Adjustment %** and **Spot Adjustment direction** together:
```
Spot Adj %:   min=1.0,  max=4.0,  step=1.0   →  4 values
Direction:    rise, fall, both                →  3 values
Total = 4 × 3 = 12 combinations
Method: Exhaustive
```

---

### "Find the best combination of strike + DTE + spot-filter"

Three parameters together:
```
Strike offset %:   min=1.0,  max=3.5,  step=0.5   →  6 values
Entry DTE:         min=0,    max=2,    step=1      →  3 values
Spot Adj %:        min=1.0,  max=3.0,  step=1.0   →  3 values
Total = 6 × 3 × 3 = 54 combinations
Method: Exhaustive
```

---

### "Explore a huge space quickly"

Many parameters, too many to run fully:
```
SL value (Leg 1):   min=20,  max=100,  step=20   →  5 values
SL value (Leg 2):   min=20,  max=100,  step=20   →  5 values
Strike % (Leg 1):   min=1.0, max=4.0,  step=0.5  →  7 values
Entry DTE:          min=0,   max=2,    step=1     →  3 values
Spot Adj %:         min=1.0, max=3.0,  step=1.0  →  3 values
Full grid = 5 × 5 × 7 × 3 × 3 = 1,575 combinations — too many to run fully
→ Switch to Method: Random,  Sample N: 200
```

---

*All default values are pre-filled in the UI when you tick a parameter.
You only need to change the ones that do not suit your research question.*
