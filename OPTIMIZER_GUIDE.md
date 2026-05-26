# Optimizer Guide for Research Team

---

## What is the Optimizer? (Simple Explanation)

Imagine you have a strategy — say, sell a NIFTY strangle every week.
But you are not sure:

- Should the stop-loss be 30 points or 60 or 90?
- Should you enter 1 day before expiry or 2 days before?
- Should the CE be 2% OTM or 3% OTM?

**The Optimizer answers this automatically.**

You tell it: *"Try all these values for me."*
It runs your strategy hundreds of times — once for each combination — and
gives you a ranked table showing which combination performed best.

**One run of the optimizer gives you:**
- A master summary table (`summary.csv`) — one row per combination, 37 metrics
- Individual tradesheets for every combination
- Everything bundled in a single ZIP file to download

---

## The 3 Things You Need to Set Up

### 1. Base Payload
Your normal strategy — the one you would backtest as usual. Dates, index,
legs, everything. The optimizer will keep this fixed and only change the
parameters you specify.

### 2. Param Specs
The list of parameters you want to sweep. For example:
- "Try stop-loss from 30 to 150 in steps of 30"
- "Try entry DTE of 0, 1, or 2"

### 3. Objective
The metric you want to rank results by. For example `car_mdd_live`
(return divided by worst live drawdown — the most useful metric for live trading).

---

## How to Define Parameters

### Type 1 — Range (for numbers you want to sweep in steps)

Use this when you want to try a number from minimum to maximum in equal steps.

```json
{
  "path": "legs[0].stopLoss.value",
  "kind": "range",
  "min": 30,
  "max": 150,
  "step": 30
}
```

This tries: **30, 60, 90, 120, 150** — that is 5 values.

Another example for strike offset:
```json
{
  "path": "legs[0].strike_selection.value",
  "kind": "range",
  "min": 1.0,
  "max": 3.0,
  "step": 0.5
}
```

This tries: **1.0, 1.5, 2.0, 2.5, 3.0** — that is 5 values.

---

### Type 2 — Values (for a hand-picked list)

Use this when you want to try specific values that are not evenly spaced.

```json
{
  "path": "entry_dte",
  "kind": "values",
  "values": [0, 1, 2, 5]
}
```

This tries exactly: **0, 1, 2, 5**.

---

### Type 3 — Enum (for choosing between words/modes)

Use this when the parameter is a label or mode, not a number.

```json
{
  "path": "legs[0].strike_selection.strike_type",
  "kind": "enum",
  "values": ["ATM", "OTM1", "OTM2"]
}
```

This tries exactly: **ATM, OTM1, OTM2**.

---

## The "path" — What You Can Sweep

The `path` is the address of the field inside your strategy.

| What you want to change | Path to use |
|---|---|
| Leg 0 stop-loss (points) | `legs[0].stopLoss.value` |
| Leg 1 stop-loss (points) | `legs[1].stopLoss.value` |
| Leg 0 trailing stop | `legs[0].trailingStop.value` |
| Leg 0 strike offset % from ATM | `legs[0].strike_selection.value` |
| Leg 1 strike offset % from ATM | `legs[1].strike_selection.value` |
| Leg 0 strike type (ATM, OTM1…) | `legs[0].strike_selection.strike_type` |
| Entry DTE (days before expiry) | `entry_dte` |
| Exit DTE (days before expiry) | `exit_dte` |
| Target profit | `legs[0].targetProfit.value` |
| Spot adjustment % | `spot_adjustment_pct` |
| Spot adjustment direction | `spot_adjustment_direction` |

> **Note on spot adjustment:** If you sweep `spot_adjustment_pct` or
> `spot_adjustment_direction`, the optimizer automatically turns on
> `spot_adjustment_enabled`. You do not need to set it yourself.

> **Note on strike offset:** If you sweep `legs[N].strike_selection.value`,
> the optimizer automatically sets `type = "pct_of_atm"` for that leg.
> You do not need to set it yourself.

---

## How Many Combinations Will Run?

When you sweep more than one parameter, the optimizer tries **every
combination of every value** — this is called a Cartesian product.

**Example:**
- Stop-loss: 30, 60, 90 → 3 values
- Entry DTE: 0, 1, 2 → 3 values
- Strike offset: 1%, 2%, 3% → 3 values

**Total = 3 × 3 × 3 = 27 combinations**

> **Tip:** Use the Preview endpoint first to check the count before submitting.

---

## Sampling Methods — How Many to Actually Run

Sometimes the full grid is too large. Three modes control how it runs.

---

### Exhaustive — Run Everything

```json
{ "method": "exhaustive" }
```

Runs every single combination. Best when you have fewer than a few hundred.
Hard limit: 100,000 combinations.

**Use when:** Grid is small (under 200–300 combinations).

---

### Random — Pick a Sample

```json
{ "method": "random", "sample_n": 100, "seed": 42 }
```

Randomly picks `sample_n` combinations from the full grid. The `seed` makes
it reproducible — same seed means same combinations every time. Share the seed
with a colleague and they get the same results.

**Use when:** Grid is large (500+) and you want a quick read.

---

### Smart — Let the Algorithm Explore

```json
{ "method": "smart", "algorithm": "cma-es", "sample_n": 200 }
```

Uses an evolutionary algorithm. It starts by trying random combinations,
learns which direction is improving, and focuses the remaining runs in that
direction. Much more efficient than random when parameters are continuous.

Algorithms available:
- `cma-es` — best for continuous number ranges (default)
- `pso` — particle swarm, good for multi-peak spaces
- `ga` — genetic / differential evolution

**Use when:** Grid is large, parameters are numbers, and you believe there is
a smooth optimal region (e.g. "somewhere between SL=50 and SL=120 there is a
peak").

---

## Ranking Objectives — Which Metric to Sort By

After the optimizer runs, it sorts all combinations by the objective you chose.

| Name to use in request | What it means | Why use it |
|---|---|---|
| `total_pnl` | Total ₹ P&L | Biggest absolute profit |
| `avg_profit_per_trade` | Average ₹ per trade | Consistency of each trade |
| `win_pct` | % winning trades | How often does it win |
| `expectancy` | Expected ₹ per trade (risk-adjusted average) | Quality of each trade |
| `cagr_options` | Compounded annual growth rate | Growth rate year on year |
| `car_mdd` | CAGR ÷ Max Booked Drawdown | Return per unit of booked pain |
| `car_mdd_live` | CAGR ÷ Max Live Drawdown | **Best for live trading** — return per unit of worst intra-trade dip |
| `max_dd_pct` | Worst drawdown % (less negative = better) | Least painful strategy |
| `actual_live_dd_max` | Deepest live dip across all trades | How bad did it feel at worst |
| `recovery_factor` | P&L ÷ Max Drawdown | Speed of recovery |
| `profit_factor` | Gross wins ÷ Gross losses | How much do winners outweigh losers |
| `reward_to_risk` | Avg win ÷ Avg loss | Quality ratio |
| `roi_vs_spot` | P&L ÷ Index move | Did strategy beat just holding spot |

> **Recommended default: `car_mdd_live`**
> It penalises strategies that have large intra-day swings even if they
> eventually recover — which is what matters in real trading.

---

## What You Get — Output Files

### summary.csv

One row per combination. ~37 columns. This is the master comparison table.
Example (sorted by `car_mdd_live`):

| combo_id | combo_label | total_pnl | cagr_options | car_mdd_live | win_pct | actual_live_dd_max |
|---|---|---|---|---|---|---|
| 7 | CE_3%_OTM_Sell_PE_2%_OTM_Sell_NoAdjustment_Weekly_T-1_To_T-1 | 162000 | 20.8 | 2.11 | 67.4 | -8.2 |
| 4 | CE_2.5%_OTM_Sell_PE_2%_OTM_Sell_NoAdjustment_Weekly_T-1_To_T-1 | 141000 | 18.1 | 1.94 | 69.1 | -7.9 |
| 12 | CE_3.5%_OTM_Sell_PE_2%_OTM_Sell_NoAdjustment_Weekly_T-1_To_T-1 | 178000 | 22.9 | 1.73 | 64.2 | -11.3 |
| 1 | CE_1%_OTM_Sell_PE_2%_OTM_Sell_NoAdjustment_Weekly_T-1_To_T-1 | 118000 | 15.2 | 1.61 | 72.0 | -7.1 |

### Per-combination tradesheet CSVs

Each combination gets its own tradesheet — identical format to a normal
backtest export. Named after the combination's label:

```
CE_3%_OTM_Sell_PE_2%_OTM_Sell_NoAdjustment_Weekly_Expiry_T-1_To_T-1.csv
```

Columns include: `Trade, Entry Date, Exit Date, Entry Spot, Exit Spot,
Net P&L, % P&L, CE P&L, PE P&L, MAE, MFE, Lowest NAV During Trade,
Cumulative, Peak, DD, %DD`

### ZIP download

After the job completes, one click downloads everything:

```
optimize_{job_id}_tradesheets.zip
├── summary.csv
└── tradesheets/
    ├── CE_3%_OTM_Sell_..._T-1_To_T-1.csv
    ├── CE_2.5%_OTM_Sell_..._T-1_To_T-1.csv
    └── ...
```

---

## How Combinations Are Named (Combo Labels)

Every combination is automatically given a human-readable name.
The format is:

```
CE_{call_strike}_{buy/sell}_PE_{put_strike}_{buy/sell}_{spot_adj}_{expiry}_Expiry_{entry}_To_{exit}
```

**Reading the label:**

| Part of label | What it means |
|---|---|
| `CE_3%_OTM_Sell` | Call leg: 3% out-of-the-money, selling |
| `PE_2%_OTM_Sell` | Put leg: 2% out-of-the-money, selling |
| `PE_0.5%_ITM_Sell` | Put leg: 0.5% in-the-money, selling |
| `PE_ATM_Sell` | Put leg: at-the-money, selling |
| `NoAdjustment` | No spot condition filter |
| `RiseBy3%` | Only trade if spot has risen 3% |
| `FallsBy2%` | Only trade if spot has fallen 2% |
| `RisesOrFallsBy5%` | Trade if spot moves either direction by 5% |
| `Weekly_Expiry` | Weekly expiry cycle |
| `Monthly_Expiry` | Monthly expiry cycle |
| `T-1_To_T-1` | Enter 1 day before expiry, exit 1 day before expiry |
| `T-0_To_T-0` | Enter and exit on expiry day |
| `T-2_To_T-1` | Enter 2 days before, exit 1 day before |

---

## All Summary Columns Explained

### Basic identity

| Column | Meaning |
|---|---|
| `combo_id` | Combination number (1, 2, 3…) |
| `combo_label` | The human-readable name described above |

### P&L and trade counts

| Column | Meaning |
|---|---|
| `total_pnl` | Total profit or loss in ₹ across all trades |
| `avg_profit_per_trade` | Average ₹ earned per trade |
| `total_trades` | How many trades were executed |
| `win_pct` | Percentage of trades that were profitable |
| `loss_pct` | Percentage of trades that were losses |
| `expectancy` | Expected ₹ per trade (win% × avg_win − loss% × avg_loss) |
| `profit_factor` | Total profit ÷ total loss (above 1.5 is good) |
| `reward_to_risk` | Average winning trade ÷ average losing trade |

### Returns

| Column | Meaning |
|---|---|
| `cagr_options` | Annualised compounded return on options NAV (starting from 100) |
| `cagr_spot` | Annualised return of the underlying index in the same period |
| `spot_change` | How many points the index moved from start to end |
| `roi_vs_spot` | Options P&L ÷ index move — did strategy beat holding spot |

### Drawdown (booked)

| Column | Meaning |
|---|---|
| `max_dd` | Biggest peak-to-trough loss in ₹ (booked trades only) |
| `max_dd_pct` | Biggest peak-to-trough loss as % of NAV |
| `recovery_factor` | Total P&L ÷ max drawdown — how many times did it recover |
| `car_mdd` | CAGR ÷ max booked drawdown |

### Live (intra-trade) drawdown — the real pain numbers

| Column | Meaning |
|---|---|
| `actual_live_dd_max` | Worst single point the NAV dropped to during any open trade — this is what you actually see live |
| `actual_live_dd_avg` | Average intra-trade dip per trade |
| `car_mdd_live` | CAGR ÷ worst live drawdown — **the key risk-adjusted metric** |
| `outlier_dd_1` | Worst live DD if you drop the single biggest outlier trade |
| `outlier_dd_2` | Worst live DD if you drop the 2 biggest outlier trades |
| `outlier_dd_3` | Worst live DD if you drop the 3 biggest outlier trades |

> **Why outlier DD matters:** Sometimes one black-swan trade dominates the DD
> number. These columns show you: "if that one event never happened, how bad
> would the DD have been?" — useful for stress-testing the strategy's baseline
> quality.

### Per-leg breakdown

| Column | Meaning |
|---|---|
| `ce_pnl_total` | Total ₹ earned/lost from the CE (call) leg alone |
| `ce_pnl_pct` | CE leg P&L as % of initial spot |
| `pe_pnl_total` | Total ₹ earned/lost from the PE (put) leg alone |
| `pe_pnl_pct` | PE leg P&L as % of initial spot |
| `long_spot_pnl` | Total ₹ from spot hedge leg (if used) |
| `long_spot_pnl_pct` | Spot leg P&L as % of initial spot |

### Per-leg without outliers

| Column | Meaning |
|---|---|
| `ce_pnl_pct_no_outlier_1` | CE P&L % after removing the 1 best CE trade |
| `ce_pnl_pct_no_outlier_2` | CE P&L % after removing the 2 best CE trades |
| `ce_pnl_pct_no_outlier_3` | CE P&L % after removing the 3 best CE trades |
| `pe_pnl_pct_no_outlier_1` | PE P&L % after removing the 1 best PE trade |
| `pe_pnl_pct_no_outlier_2` | PE P&L % after removing the 2 best PE trades |
| `pe_pnl_pct_no_outlier_3` | PE P&L % after removing the 3 best PE trades |

> **Why no-outlier columns?** A few extremely lucky low-premium expiries can
> inflate the leg average. These columns show the "honest average" after
> excluding the flukes.

---

## End-to-End Examples

---

### Example 1 — Single parameter: sweep stop-loss only

**Question:** What stop-loss gives the best CAR/MDD Live on a NIFTY strangle?

**Setup:**
```json
{
  "base_payload": { ...your normal NIFTY strangle config... },
  "param_specs": [
    {
      "path": "legs[0].stopLoss.value",
      "kind": "range",
      "min": 30,
      "max": 150,
      "step": 30
    }
  ],
  "method": "exhaustive",
  "objective": "car_mdd_live"
}
```

**Combinations tried:** 30, 60, 90, 120, 150 → **5 runs**

**Result summary.csv (sorted by car_mdd_live):**

| combo_id | combo_label | total_pnl | cagr_options | car_mdd_live | actual_live_dd_max |
|---|---|---|---|---|---|
| 3 | CE_3%_OTM_Sell_...SL90... | 142500 | 18.4 | 1.82 | -7.2 |
| 2 | CE_3%_OTM_Sell_...SL60... | 118000 | 15.1 | 1.64 | -8.5 |
| 4 | CE_3%_OTM_Sell_...SL120... | 156000 | 20.1 | 1.51 | -12.1 |
| 1 | CE_3%_OTM_Sell_...SL30... | 98000 | 12.6 | 1.43 | -7.9 |
| 5 | CE_3%_OTM_Sell_...SL150... | 168000 | 21.8 | 1.31 | -15.3 |

**Reading the results:** SL=90 wins on CAR/MDD Live. SL=150 earns the most
money but has a much deeper live drawdown. This is the classic insight the
optimizer surfaces — more profit is not always better.

---

### Example 2 — Two parameters: strike offset + entry DTE

**Question:** Which combination of CE strike and entry DTE gives the best results?

**Setup:**
```json
{
  "base_payload": { ...NIFTY strangle... },
  "param_specs": [
    {
      "path": "legs[0].strike_selection.value",
      "kind": "range",
      "min": 1.0,
      "max": 4.0,
      "step": 1.0
    },
    {
      "path": "entry_dte",
      "kind": "values",
      "values": [0, 1, 2]
    }
  ],
  "method": "exhaustive",
  "objective": "car_mdd_live"
}
```

**Combinations tried:** 4 strikes × 3 DTEs = **12 runs**

Note: The optimizer automatically switches `legs[0].strike_selection.type` to
`pct_of_atm` for every combo — you do not need to set this.

**Result (top 4 rows from summary.csv):**

| combo_id | combo_label | cagr_options | car_mdd_live | ce_pnl_pct | pe_pnl_pct | win_pct |
|---|---|---|---|---|---|---|
| 8 | CE_3%_OTM_Sell_PE_2%_OTM_Sell_..._T-1_To_T-1 | 21.1 | 2.04 | 11.2 | 9.8 | 67.4 |
| 5 | CE_2%_OTM_Sell_PE_2%_OTM_Sell_..._T-1_To_T-1 | 17.8 | 1.91 | 8.6 | 9.1 | 69.1 |
| 11 | CE_4%_OTM_Sell_PE_2%_OTM_Sell_..._T-1_To_T-1 | 22.9 | 1.73 | 14.1 | 9.8 | 64.2 |
| 2 | CE_1%_OTM_Sell_PE_2%_OTM_Sell_..._T-1_To_T-1 | 15.6 | 1.62 | 6.3 | 9.1 | 72.0 |

**ZIP will contain:**
```
summary.csv
tradesheets/CE_1%_OTM_Sell_PE_2%_OTM_Sell_..._T-0_To_T-0.csv
tradesheets/CE_1%_OTM_Sell_PE_2%_OTM_Sell_..._T-1_To_T-1.csv
tradesheets/CE_1%_OTM_Sell_PE_2%_OTM_Sell_..._T-2_To_T-2.csv
tradesheets/CE_2%_OTM_Sell_PE_2%_OTM_Sell_..._T-0_To_T-0.csv
... (12 files total)
```

---

### Example 3 — Spot adjustment filter sweep

**Question:** Does requiring a 2%, 3%, or 4% rise/fall in spot before entering
improve performance?

**Setup:**
```json
{
  "base_payload": { ...NIFTY strangle... },
  "param_specs": [
    {
      "path": "spot_adjustment_pct",
      "kind": "values",
      "values": [2.0, 3.0, 4.0]
    },
    {
      "path": "spot_adjustment_direction",
      "kind": "enum",
      "values": ["rise", "fall", "both"]
    }
  ],
  "method": "exhaustive",
  "objective": "car_mdd_live"
}
```

**Combinations:** 3 pct × 3 directions = **9 runs**

Note: `spot_adjustment_enabled` is automatically set to `true` for every combo.

**Example output combo labels:**
```
CE_3%_OTM_Sell_PE_2%_OTM_Sell_RiseBy2%_Weekly_Expiry_T-1_To_T-1
CE_3%_OTM_Sell_PE_2%_OTM_Sell_RiseBy3%_Weekly_Expiry_T-1_To_T-1
CE_3%_OTM_Sell_PE_2%_OTM_Sell_FallsBy2%_Weekly_Expiry_T-1_To_T-1
CE_3%_OTM_Sell_PE_2%_OTM_Sell_RisesOrFallsBy3%_Weekly_Expiry_T-1_To_T-1
```

---

### Example 4 — Large grid with random sampling

**Question:** Sweep 3 parameters at once but the grid is too large to run fully.

**Full grid size:** 5 SL values × 5 trailing SL values × 6 strike offsets = **150 combinations**

**You only want to run 50** to get a quick read:

```json
{
  "base_payload": { ...your strategy... },
  "param_specs": [
    {"path": "legs[0].stopLoss.value",         "kind": "range", "min": 30, "max": 150, "step": 30},
    {"path": "legs[1].stopLoss.value",         "kind": "range", "min": 30, "max": 150, "step": 30},
    {"path": "legs[0].strike_selection.value", "kind": "range", "min": 1.0, "max": 3.5, "step": 0.5}
  ],
  "method": "random",
  "sample_n": 50,
  "seed": 42,
  "objective": "car_mdd_live"
}
```

50 randomly chosen combinations out of 150 are run. `seed: 42` makes the
choice reproducible — run it again with the same seed and you get the same 50.

---

## API Calls — Quick Reference

### Step 1 — Check how many combinations before running

```
POST /api/optimize/preview
```
```json
{
  "base_payload": { ... },
  "param_specs": [ ... ],
  "method": "exhaustive"
}
```
Returns:
```json
{
  "grid_size": 12,
  "planned_runs": 12,
  "estimated_seconds": 3
}
```

---

### Step 2 — Submit the job

```
POST /api/optimize/jobs
```
```json
{
  "base_payload": { ... },
  "param_specs": [ ... ],
  "method": "exhaustive",
  "objective": "car_mdd_live"
}
```
Returns:
```json
{
  "status": "queued",
  "job_id": "abc123-...",
  "total_combos": 12
}
```

---

### Step 3 — Check progress

```
GET /api/optimize/jobs/{job_id}
```
Returns:
```json
{
  "status": "running",
  "meta": { "total": 12, "done": 7, "eta_seconds": 4 }
}
```
Status goes: `queued` → `running` → `success` or `failed`

---

### Step 4 — Download the ZIP when done

```
GET /api/optimize/jobs/{job_id}/tradesheets.zip
```

Downloads `optimize_{job_id}_tradesheets.zip` containing `summary.csv` and
all individual tradesheet CSVs.

---

### Other useful calls

```
GET /api/optimize/objectives
→ lists all valid objective names you can use
```

```
GET /api/optimize/jobs/{job_id}/results?sort_by=car_mdd_live&order=desc&limit=50
→ paginated result table (JSON) — useful for the UI
```

```
GET /api/optimize/jobs/{job_id}/combo/{combo_id}/tradesheet
→ download one specific combination's tradesheet CSV
```

```
DELETE /api/optimize/jobs/{job_id}
→ cancel and delete all results for this job
```

---

## Common Mistakes to Avoid

| Mistake | What happens | Fix |
|---|---|---|
| Very large exhaustive grid (1000+ combos) | Job takes hours | Use `random` with `sample_n: 200` first |
| Forgetting `sample_n` with `random` method | Request rejected | Add `"sample_n": 200` (or any number) |
| Setting wrong `path` for a field | That parameter has no effect — results look identical | Check the path table above |
| Not checking preview first | Job runs 50,000 combos when you expected 50 | Always call `/preview` first |
| Downloading ZIP before job finishes | 400 error — job not complete | Wait for `status: success` |

---

## Limits

| Setting | Default | Meaning |
|---|---|---|
| Max combinations (exhaustive) | 100,000 | Safety cap to prevent infinite jobs |
| Results kept in memory | 24 hours | After 24 hours the job results are gone |
| Estimated time per combination | ~250 ms | Rough guide on 2-year NIFTY at P=1 |

---

*Covers optimizer as shipped. For backend questions contact the dev team.*
