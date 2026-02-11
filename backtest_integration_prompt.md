# Backtest UI ↔ Backend Integration: Full Specification Prompt

## Context

This document is a complete, ready-to-use prompt you can give to an AI assistant (or developer) to rebuild your `ConfigPanel.jsx` and connect it correctly to your Python backtest engines. Read it in full before starting.

---

## What Exists Already (Do NOT Rewrite)

- **Python backend engines** (`v1_ce_fut.py`, `v2_pe_fut.py`, `v3_strike_breach.py`, `v4_strangle.py`, `v5_protected.py`, `v6_inverse_strangle.py`, `v7_premium.py`, `v8_ce_pe_fut.py`, `v8_hsl.py`, `v9_counter.py`) — all working correctly.
- **`base.py`** — provides `get_strike_data`, `load_expiry`, `load_bhavcopy`, `get_option_price`, `build_intervals`, `compute_analytics`, `build_pivot`, `round_half_up`.
- **`/api/backtest` POST endpoint** — already wired to the engines. It receives JSON params and calls the appropriate engine `run_*` function, returning `{ trades, summary, pivot }`.
- The backend is **positional only** — no intraday or BTST logic exists. Do not add entry time / exit time / intraday toggles anywhere in the UI.

---

## CRITICAL DESIGN RULE: No Hardcoded Strategy Presets

The current UI hardcodes strategy names like "CE Sell + FUT Buy (V1)". **This must be replaced.** The user must build the strategy themselves by selecting legs (CE Sell, PE Sell, PE Buy, Future Buy) and the engine is determined by what legs they pick. The UI should be flexible and reflect what the engines actually support.

---

## Part 1 — Engine Catalogue: Exact Parameters Per Engine

This is the ground truth. Every field in the JSON payload sent to `/api/backtest` must exactly match what the engine `params.get()` calls expect.

---

### V1 — CE Sell + Future Buy (`v1_ce_fut.py`)

**Entry point:** `run_v1(params)` (or `run_v1_main1` through `run_v1_main5` for different expiry windows — the router should call `run_v1` with `expiry_window` set)

**Required params:**
```json
{
  "strategy": "v1_ce_fut",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**Trade sheet columns returned:**
`entry_date, exit_date, entry_spot, exit_spot, spot_pnl, call_expiry, call_strike, call_entry_price, call_entry_turnover, call_exit_price, call_exit_turnover, call_pnl, future_expiry, future_entry_price, future_exit_price, future_pnl, net_pnl, cumulative, %dd`

**`call_sell_position`**: % offset from spot for call strike. `0.0` = ATM, `1.0` = 1% OTM above spot, `-1.0` = 1% ITM. Formula used: `round_half_up((spot * (1 + pct/100)) / 50) * 50`

---

### V2 — PE Sell + Future Buy (`v2_pe_fut.py`)

**Required params:**
```json
{
  "strategy": "v2_pe_fut",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "expiry_window": "weekly_expiry",
  "put_sell_position": -1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**⚠️ BUG NOTE in v2:** The current strike formula uses `/100)*100` instead of `/50)*50`. This is the bug `fixengine.py` fixes. After running `fixengine.py --fix`, the formula becomes `round_half_up((spot * (1 + pct/100)) / 50) * 50` — same as V1.

**Trade sheet columns:** same as V1 but with `put_` prefix instead of `call_`.

---

### V3 — Strike Breach Re-entry (`v3_strike_breach.py`)

**Required params:**
```json
{
  "strategy": "v3_strike_breach",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 1.0,
  "pct_diff": 0.3,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**`pct_diff`**: The % threshold above/below the call strike that triggers a breach event. Default 0.3.

**Extra trade sheet columns vs V1:** `breach_occurred` (bool), `breach_date` (date or null), `breach_spot` (float or null)

---

### V4 — Short Strangle (`v4_strangle.py`)

**Required params:**
```json
{
  "strategy": "v4_strangle",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "call_sell_position": 1.0,
  "put_sell_position": -1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**No `expiry_window` param** — V4 only uses weekly expiry internally.

**Trade sheet columns:** `entry_date, exit_date, entry_spot, exit_spot, spot_pnl, call_expiry, call_strike, call_entry_price, call_entry_turnover, call_exit_price, call_exit_turnover, call_pnl, put_expiry, put_strike, put_entry_price, put_entry_turnover, put_exit_price, put_exit_turnover, put_pnl, net_pnl, cumulative, %dd`

---

### V5 — Protected CE Sell or PE Sell (`v5_protected.py`)

**For CE leg (`run_v5_call`):**
```json
{
  "strategy": "v5_call",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 1.0,
  "protection": true,
  "protection_pct": 1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**For PE leg (`run_v5_put`):**
```json
{
  "strategy": "v5_put",
  ...same fields but use "put_sell_position" instead of "call_sell_position"...
}
```

**`protection`**: bool — if `true`, a protective buy is added at `call_strike * (1 + protection_pct/100)`.
**`protection_pct`**: % above (for call) or below (for put) the sold strike to place the protective buy.

**Trade sheet columns for v5_call:** includes `protective_strike, protective_entry_price, protective_exit_price, protective_pnl` in addition to the V1 columns (without future columns).

---

### V6 — Inverse Strangle (Outside Base Ranges) (`v6_inverse_strangle.py`)

**Required params:** identical to V4 (CE + PE sell), but this engine trades **outside** base2 date ranges instead of inside.

```json
{
  "strategy": "v6_inverse_strangle",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "call_sell_position": 1.0,
  "put_sell_position": -1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**Trade sheet columns:** same as V4.

---

### V7 — Premium-Based Strike Selection (`v7_premium.py`)

This engine is different: it finds strikes by targeting a premium level, not a % offset.

**Required params:**
```json
{
  "strategy": "v7_premium",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "call_sell": true,
  "put_sell": true,
  "call_premium": true,
  "put_premium": true,
  "premium_multiplier": 1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**`call_premium` / `put_premium`**: bool — whether to include ATM call or put in the target premium calculation.
**`premium_multiplier`**: multiplier on the ATM straddle/call/put to set the target premium.
**`call_sell` / `put_sell`**: which legs to sell.

Strike selection logic: ATM price is computed, target = (ATM call + ATM put) × multiplier (depending on which are enabled), then the engine walks OTM to find the strike whose premium is closest to target.

**Trade sheet columns:** same as V4 (CE + PE sell columns) when both are sold.

---

### V8-CE-PE-FUT — Hedged Bull (`v8_ce_pe_fut.py`)

```json
{
  "strategy": "v8_ce_pe_fut",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 1.0,
  "put_strike_pct_below": 1.0,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**`put_strike_pct_below`**: % below the call strike to place the protective PE buy. Formula: `round_half_up((call_strike * (1 - pct/100)) / 50) * 50`.

**Trade sheet columns:** `entry_date, exit_date, entry_spot, exit_spot, spot_pnl, call_expiry, call_strike, call_entry_price, ..., call_pnl, put_expiry, put_strike, put_entry_price, ..., put_pnl, future_expiry, future_entry_price, future_exit_price, future_pnl, net_pnl, cumulative, %dd`

---

### V8-HSL — Hard Stop Loss Variant (`v8_hsl.py`)

```json
{
  "strategy": "v8_hsl",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 1.0,
  "call_hsl_pct": 100,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**`call_hsl_pct`**: Stop loss as % of entry premium. E.g., `100` means stop when option price reaches 2× entry (100% above). Formula: `stop_price = entry_price × (hsl_pct / 100)`.

**Extra trade sheet columns:** `actual_exit_date, call_stopped, call_stop_date, call_stop_price, call_actual_exit`

---

### V9 — Counter-Based Put Expiry (`v9_counter.py`)

```json
{
  "strategy": "v9_counter",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2026-01-01",
  "call_sell_position": 1.0,
  "put_strike_pct_below": 1.0,
  "max_put_spot_pct": 0.04,
  "spot_adjustment_type": 0,
  "spot_adjustment": 0.0
}
```

**`max_put_spot_pct`**: Maximum allowed % below spot for the PE buy strike (as a decimal: 0.04 = 4%).
**Logic**: Week 1–2 of month → PE expiry = current month monthly. Week 3–4 → PE expiry = next month monthly.

**Trade sheet columns:** same as V8-CE-PE-FUT plus `counter` (week-of-month number).

---

## Part 2 — Expiry Window Options

All engines that support `expiry_window` accept exactly these string values:

| Value | Description |
|---|---|
| `weekly_expiry` | Trade from previous weekly expiry to current weekly expiry |
| `weekly_t1` | Trade from current weekly to next weekly (T+1) |
| `weekly_t2` | Trade from current weekly to 2 weeks forward (T+2) |
| `monthly_expiry` | Trade from previous monthly expiry to current monthly |
| `monthly_t1` | Trade from current monthly to next monthly |

**Engines that do NOT have `expiry_window`:** V4, V6, V7, V9. For these, do not send or show this field.

---

## Part 3 — Re-Entry / Spot Adjustment

All engines use `spot_adjustment_type` and `spot_adjustment`. These map to the `build_intervals()` call.

| `spot_adjustment_type` | Meaning |
|---|---|
| `0` | No adjustment — one trade per expiry window |
| `1` | Re-entry when spot rises by X% |
| `2` | Re-entry when spot falls by X% |
| `3` | Re-entry when spot rises OR falls by X% |

`spot_adjustment` is the X% value (float). It is only relevant when `spot_adjustment_type` is 1, 2, or 3.

**Send as integers, not strings.** The engine uses `params.get("spot_adjustment_type", 0)` which expects an int.

---

## Part 4 — Summary Statistics Returned by Backend

The `/api/backtest` response has this shape:
```json
{
  "trades": [...],
  "summary": {
    "index": "NIFTY",
    "total_pnl": 8889.70,
    "count": 291,
    "win_pct": 73.20,
    "cagr": 9.16,
    "max_dd": -10.03,
    "car_mdd": 0.91
  },
  "pivot": {...}
}
```

The `trades` array contains exactly the columns documented per-engine above — no extra columns. If the UI tries to display a column that does not exist for a given strategy, it must handle it gracefully (hide or show "N/A").

---

## Part 5 — Index Options

The following indices are supported. Send the exact string:

| Value | Strike Interval | Expiry Types |
|---|---|---|
| `NIFTY` | 50 | Weekly + Monthly |
| `SENSEX` | 100 | Weekly + Monthly |
| `BANKNIFTY` | 100 | Monthly only |
| `FINNIFTY` | 50 | Monthly only |
| `MIDCPNIFTY` | 25 | Monthly only |

If the user selects an index with monthly-only expiry, hide all weekly expiry window options and only show `monthly_expiry` and `monthly_t1`.

---

## Part 6 — UI Rebuild Specification

### Design Principles
1. **Simple, clean, no clutter.** Match the aesthetic in the screenshots: white cards, a left config column, a right settings column.
2. **No Intraday / BTST / Entry Time / Exit Time** — these are positional strategies only. Remove all time pickers and strategy type toggles.
3. **Dynamic, not hardcoded.** The user builds the strategy by selecting legs. The engine is inferred from the leg combination.
4. **Graceful degradation.** If the user selects a combination that does not have a matching engine, show a clear message: "This leg combination is not available. Available combinations: [list]."
5. **Column-exact trade sheet.** The results table must show only the columns returned for the selected strategy. No phantom columns.

### Strategy-to-Engine Mapping (frontend logic)

```javascript
function inferEngine(legs, params) {
  const { ce_sell, pe_sell, pe_buy, fut_buy, premium_mode, breach_mode, hsl_mode } = legs;

  if (hsl_mode && ce_sell && fut_buy)       return "v8_hsl";
  if (breach_mode && ce_sell)                return "v3_strike_breach";
  if (premium_mode && (ce_sell || pe_sell))  return "v7_premium";
  if (ce_sell && pe_buy && fut_buy)         return "v8_ce_pe_fut";
  if (ce_sell && pe_sell && !fut_buy)       return params.inverse ? "v6_inverse_strangle" : "v4_strangle";
  if (ce_sell && fut_buy && !pe_sell)       return "v1_ce_fut";
  if (pe_sell && fut_buy && !ce_sell)       return "v2_pe_fut";
  if (ce_sell && !fut_buy && !pe_sell && params.protection)  return "v5_call";
  if (pe_sell && !fut_buy && !ce_sell && params.protection)  return "v5_put";
  if (ce_sell && pe_sell && pe_buy && fut_buy) return "v9_counter";

  return null; // unsupported combination
}
```

### Config Panel Sections to Build

#### Section 1: Instrument Settings
- **Index** — dropdown: NIFTY, SENSEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY
- **Expiry Window** — radio or dropdown, dynamically filtered based on index (see Part 5)

#### Section 2: Leg Builder
Build this as a dynamic list. Each leg has:
- **Leg Type** — CE Sell / PE Sell / PE Buy / Future Buy (toggle buttons, not checkboxes)
- **Strike %** — numeric input (only shown for option legs, not Future). Label: "% from spot". Positive = OTM for CE, Negative = OTM for PE.

Show only the legs that are compatible with the selected engine inference. If an unsupported combination is assembled, show the warning inline (do not hide the Submit button — let the user proceed and let the backend reject it, but also show the warning).

**For V7 (Premium mode):** instead of Strike %, show:
- Include ATM Call premium? (checkbox)
- Include ATM Put premium? (checkbox)
- Premium multiplier (number input)

**For V5 (Protection mode):** show an additional "Add Protection" toggle that reveals:
- Protection % input

**For V8-HSL:** show "Hard Stop Loss %" input (default 100).

**For V3 (Breach mode):** show "Breach % (pct_diff)" input (default 0.3).

#### Section 3: Re-Entry Settings
Radio group:
- No Adjustment
- Spot Rises By X% → shows X% input
- Spot Falls By X% → shows X% input
- Spot Rises or Falls By X% → shows X% input

Map to: `spot_adjustment_type: 0/1/2/3` and `spot_adjustment: float`

#### Section 4: Date Range
- From Date (date input)
- To Date (date input)
- "All Data" button → sets from_date to "2019-01-01" and to_date to "2026-01-01" (or whatever the full data range is)

---

## Part 7 — Payload Construction

Before calling `/api/backtest`, construct the payload as follows:

```javascript
function buildPayload(legs, uiState) {
  const engine = inferEngine(legs, uiState);
  if (!engine) {
    showError("Unsupported strategy combination. Please review leg selection.");
    return null;
  }

  const base = {
    strategy: engine,
    index: uiState.index,
    from_date: uiState.from_date,
    to_date: uiState.to_date,
    spot_adjustment_type: parseInt(uiState.spot_adjustment_type),
    spot_adjustment: parseFloat(uiState.spot_adjustment),
  };

  // Add expiry_window only for engines that support it
  const expiry_engines = ["v1_ce_fut","v2_pe_fut","v3_strike_breach","v5_call","v5_put","v8_ce_pe_fut","v8_hsl"];
  if (expiry_engines.includes(engine)) {
    base.expiry_window = uiState.expiry_window;
  }

  // Engine-specific params
  switch(engine) {
    case "v1_ce_fut":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      break;
    case "v2_pe_fut":
      base.put_sell_position = parseFloat(uiState.put_sell_position);
      break;
    case "v3_strike_breach":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      base.pct_diff = parseFloat(uiState.pct_diff ?? 0.3);
      break;
    case "v4_strangle":
    case "v6_inverse_strangle":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      base.put_sell_position = parseFloat(uiState.put_sell_position);
      break;
    case "v5_call":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      base.protection = uiState.protection ?? false;
      base.protection_pct = parseFloat(uiState.protection_pct ?? 1.0);
      break;
    case "v5_put":
      base.put_sell_position = parseFloat(uiState.put_sell_position);
      base.protection = uiState.protection ?? false;
      base.protection_pct = parseFloat(uiState.protection_pct ?? 1.0);
      break;
    case "v7_premium":
      base.call_sell = uiState.call_sell ?? true;
      base.put_sell = uiState.put_sell ?? true;
      base.call_premium = uiState.call_premium ?? true;
      base.put_premium = uiState.put_premium ?? true;
      base.premium_multiplier = parseFloat(uiState.premium_multiplier ?? 1.0);
      break;
    case "v8_ce_pe_fut":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      base.put_strike_pct_below = parseFloat(uiState.put_strike_pct_below ?? 1.0);
      break;
    case "v8_hsl":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      base.call_hsl_pct = parseFloat(uiState.call_hsl_pct ?? 100);
      break;
    case "v9_counter":
      base.call_sell_position = parseFloat(uiState.call_sell_position);
      base.put_strike_pct_below = parseFloat(uiState.put_strike_pct_below ?? 1.0);
      base.max_put_spot_pct = parseFloat(uiState.max_put_spot_pct ?? 0.04);
      break;
  }

  return base;
}
```

---

## Part 8 — Results Display

### Summary Cards (always show these 6)
1. **Total P&L** — `summary.total_pnl`
2. **Win Rate** — `summary.win_pct`%
3. **Total Trades** — `summary.count`
4. **CAGR** — `summary.cagr`%
5. **Max Drawdown** — `summary.max_dd`%
6. **CAR/MDD** — `summary.car_mdd`

### Charts
- **Equity Curve** — cumulative column from trades, plotted as a line chart
- **Drawdown Chart** — `%dd` column from trades, plotted as a filled area chart
- **Monthly P&L Heatmap** — from pivot table; rows = year, columns = month (Jan–Dec) + Grand Total

### Trade Log Table
Show only columns that exist in the returned trades array. Column visibility rules:

| Column | Show when |
|---|---|
| `future_entry_price`, `future_exit_price`, `future_pnl`, `future_expiry` | engine has `fut_buy` leg |
| `put_entry_price`, `put_exit_price`, `put_pnl`, `put_expiry`, `put_strike` | engine has PE leg |
| `call_entry_price`, `call_exit_price`, `call_pnl`, `call_expiry`, `call_strike` | engine has CE leg |
| `breach_occurred`, `breach_date`, `breach_spot` | V3 only |
| `actual_exit_date`, `call_stopped`, `call_stop_date`, `call_stop_price` | V8-HSL only |
| `protective_strike`, `protective_entry_price`, `protective_exit_price`, `protective_pnl` | V5 only |
| `counter` | V9 only |

Numbers should be formatted to 2 decimal places. Negative P&L in red, positive in green.

---

## Part 9 — Error Handling

1. **Unsupported leg combination** — inline warning in the Leg Builder section before submit. Do not block the submit button, but show the message clearly.
2. **Backend returns empty trades** — show: "No trades generated for this configuration. Check your date range and parameter settings."
3. **Backend returns an error** — show the error message from `response.body` in a toast or inline alert.
4. **Missing bhavcopy data** — the backend already prints warnings for missing data days. The frontend just needs to handle an unexpectedly low trade count gracefully.

---

## Part 10 — What to Remove from Current ConfigPanel.jsx

Remove all of the following (they are not used by any backend engine):

- Entry Time / Exit Time inputs
- No Re-entry After checkbox+time
- "Intraday / BTST / Positional" toggle (we are positional only)
- "Overall Momentum" toggle
- "Delay Restart" toggle  
- "Strategy Type" tabs (Intraday / BTST / Positional)
- Hardcoded strategy preset buttons ("CE Sell + FUT Buy (V1)" etc.)
- The top nav tabs: Algo Trade, Signals, RA Algos, ClickTrade, Webinars (keep only Backtest active)
- PrinterIcon / PDF button (unless PDF export is actually wired up)
- LegBuilder as a separate component — fold all leg logic into ConfigPanel

---

## Part 11 — Strike Rounding Reference

After running `fixengine.py --fix`, all engines use this correct formula:

```python
# NIFTY (interval=50)
strike = round_half_up((spot * (1 + pct/100)) / 50) * 50

# BANKNIFTY / SENSEX (interval=100)
strike = round_half_up((spot * (1 + pct/100)) / 100) * 100

# MIDCPNIFTY (interval=25)
strike = round_half_up((spot * (1 + pct/100)) / 25) * 25
```

The UI does not need to compute strikes itself — this is only shown here so you can display informational labels like "Approx Strike: 23,500" if desired. The authoritative calculation is always done server-side.

---

## Part 12 — Full Checklist Before Going Live

- [ ] Run `fixengine.py --fix` on all engine files to fix the `/100)*100` bug
- [ ] Confirm `/api/backtest` router correctly dispatches to the right engine based on `strategy` key
- [ ] Confirm the router passes `expiry_window` into the engine params
- [ ] Confirm the router returns `{ trades, summary, pivot }` in the shape described in Part 4
- [ ] Test V1 trade count and net_pnl matches the standalone script output for the same date range and params
- [ ] Test V4 with a strangle: verify both call_pnl and put_pnl are present in every row
- [ ] Test V8-HSL with `call_hsl_pct=100`: verify `call_stopped`, `actual_exit_date` are in trade rows
- [ ] Test V7 with `premium_multiplier=0.5`: verify strike is farther OTM than with multiplier=1.0
- [ ] Confirm monthly P&L heatmap rows match the screenshots (2019 to 2025)
- [ ] Confirm no extra columns appear in the trade sheet beyond what the engine returns

---

## Summary

The key principle is: **the backend is the source of truth**. The frontend collects the minimal inputs needed, constructs a clean JSON payload with the exact field names the engines expect, and displays exactly what the backend returns — no transformations, no extra columns, no hardcoded strategy names.

The engines are positional and date-driven. There are no time-based inputs. The only inputs are: index, expiry window, leg selection (which determines the engine), strike % or premium params for each leg, re-entry type, and date range.
