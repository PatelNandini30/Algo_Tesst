# AlgoTest Clone — Full-Stack Backtesting Platform
## Complete Build Prompt

> **Based on:** `analyse_bhavcopy_02-01-2026.py` | NSE NIFTY Options Strategies | 20,186 lines of Python logic

---

## Table of Contents

1. [Product Vision & Overview](#1-product-vision--overview)
2. [Frontend UI — Screen-by-Screen Specification](#2-frontend-ui--screen-by-screen-specification)
3. [Results Panel — Charts & Analytics](#3-results-panel--charts--analytics)
4. [Backend API — Endpoints & Schema](#4-backend-api--endpoints--schema)
5. [Backend Engine Logic — Python Translation](#5-backend-engine-logic--python-translation)
6. [Frontend React Component Specifications](#6-frontend-react-component-specifications)
7. [Data File Contracts](#7-data-file-contracts)
8. [Copy-Paste Master Prompt](#8-copy-paste-master-prompt)
9. [Implementation Checklist](#9-implementation-checklist)

---

## 1. Product Vision & Overview

Build a **fully functional web-based options backtesting platform** modelled after [AlgoTest](https://algotest.in/backtest) that lets a user:

- Configure NIFTY options strategy parameters through a GUI
- Run a backtest against **real historical bhavcopy data** stored in `./cleaned_csvs/YYYY-MM-DD.csv` files
- Immediately see detailed performance analytics — equity curve, drawdown, monthly P&L pivot, trade-by-trade log, and key statistics

> **Zero mock data.** Every result is computed in real-time from actual NSE bhavcopy CSV files — exactly as the Python file does, replicated as an API backend.

---

### 1.1 Technology Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18 + Vite. Tailwind CSS for layout. Recharts for all charts. Lucide-React for icons. |
| **Backend API** | Python FastAPI. Uvicorn ASGI server. Pandas + NumPy (directly reuses Python strategy functions). |
| **Data Store** | Flat file system. No database. `./cleaned_csvs/`, `./expiryData/`, `./strikeData/`, `./Filter/base2.csv` served from disk. |
| **State Mgmt** | React `useState` + `useReducer` for form state. TanStack Query for API calls and caching. |
| **Auth** | None for local deployment. Optional JWT for multi-user cloud. |

---

### 1.2 Adjustment Logic (Core Concept — Used Across ALL Strategies)

The `spot_adjustment_type` and `spot_adjustment` parameters control when a position is **re-entered mid-period**:

| `spot_adjustment_type` | Behavior |
|---|---|
| `0` | No adjustment — hold from entry to expiry |
| `1` | Re-enter when spot **RISES** by X% from entry price |
| `2` | Re-enter when spot **FALLS** by X% from entry price |
| `3` | Re-enter when spot moves **EITHER** up or down by X% |

When triggered: close current position, open new one at current spot. All sub-intervals are processed as independent trade legs.

---

## 2. Frontend UI — Screen-by-Screen Specification

### 2.1 Top Navigation Bar

Replicate AlgoTest's dark header exactly:

- **Left:** Logo `AlgoTest` with lightning bolt icon (blue `#1A56DB`)
- **Nav tabs:** `Backtest` (active, blue underline) | `Algo Trade` | `Signals` | `RA Algos` | `ClickTrade` | `Webinars`
- **Right:** `Pricing` link | `Broker Setup` button | User avatar circle | `Credits: 0` badge
- **Colors:** Background `#111827` | Text white | Active tab bottom border `#1A56DB`

---

### 2.2 Strategy Type Tabs

Four horizontal tabs below the nav bar:

| Tab | Instruments | Notes |
|---|---|---|
| **Weekly & Monthly Expiries** | NIFTY \| SENSEX | Default active, blue underline |
| **Monthly Only Expiry** | MIDCPNIFTY \| BANKNIFTY \| FINNIFTY \| BANKEX | |
| **Stocks — Cash / F&O** | ALL NIFTY 500 STOCKS | |
| **Delta Exchange** | BTCUSD \| ETHUSD | `New` badge + Algo Trading info icon |

Tab click filters which Index options appear in Instrument Settings.

---

### 2.3 Main Configuration Panel (Two-Column Layout)

#### LEFT COLUMN — Instrument Settings Card

| UI Element | Options / Specification |
|---|---|
| **Index Dropdown** | NIFTY (default) \| BANKNIFTY \| FINNIFTY \| MIDCPNIFTY \| SENSEX |
| **Underlying From** | Toggle: `[Cash]` `[Futures]` — Cash = spot from `Nifty_strike_data.csv`, Futures = FUTIDX close |
| **Expiry Type** | Auto-derived from tab. Weekly tab: Weekly or Monthly radio. Monthly tab: Monthly only. |

#### LEFT COLUMN — Legwise Settings Card

| UI Element | Options / Specification |
|---|---|
| **Square Off** | Toggle: `[Partial]` `[Complete]` |
| **Trail SL to Breakeven** | Checkbox with `All Legs` / `SL Legs` sub-toggle |
| **Add Leg Button** | `+` button appends new option leg row. Max 4 legs. |

#### EACH LEG ROW — Fields (left to right)

| Field | Options / Logic |
|---|---|
| **Instrument Type** | `CE` (Call) \| `PE` (Put) \| `FUT` (Future) |
| **Buy / Sell** | Toggle: `BUY` \| `SELL` — Sell = premium collected, affects P&L sign |
| **Lots** | Number input (default 1) |
| **Strike Type** | `ATM` \| `ATM+50` \| `ATM+100` \| `ATM-50` \| `ATM-100` \| `OTM%` \| `ITM%` \| `Spot%` \| `Premium-Based` |
| **Strike Value** | Numeric offset — e.g. if `OTM%` selected, enter `1` for 1% OTM → maps to `call_sell_position` or `put_sell_position` |
| **Expiry** | `Current Weekly` \| `Next Weekly` \| `Current Monthly` \| `Next Monthly` \| `Month+2` |
| **Stop Loss** | Number + type dropdown: `Points` \| `%` \| `Premium%` — maps to HSL parameters |
| **Target** | Number + type dropdown. `0` = no target. |

> **Preset: V8 Hedged Bull** — pre-fills 3 legs: CE SELL + PE BUY + FUT BUY automatically.

---

### 2.4 RIGHT COLUMN — Entry Settings Card

| UI Element | Specification / Python Mapping |
|---|---|
| **Strategy Type** | Tabs: `[Intraday]` `[BTST]` `[Positional]` — Positional = mode used by all Python functions |
| **Entry Time** | Time picker, default `09:35` |
| **Exit Time** | Time picker, default `15:15` |
| **No Re-entry After** | Toggle + time picker — when OFF, full re-entry allowed |
| **Overall Momentum** | Toggle → reveals: dropdown `[Points (Pts)]` with ↑↓ arrows + value input |

---

### 2.5 RIGHT COLUMN — Adjustment / Re-Entry Card

Critical mapping between GUI and Python parameters:

| GUI Label | Python Parameter |
|---|---|
| No Adjustment (toggle OFF) | `spot_adjustment_type = 0` |
| Spot Rises By X% | `spot_adjustment_type = 1`, `spot_adjustment = X` |
| Spot Falls By X% | `spot_adjustment_type = 2`, `spot_adjustment = X` |
| Spot Rises or Falls By X% | `spot_adjustment_type = 3`, `spot_adjustment = X` |
| Points Rise By N pts | `adjustment_type = 1`, `adjustment_points = N` (main_V2/V3) |
| Points Fall By N pts | `adjustment_type = 2`, `adjustment_points = N` |
| Points Either By N pts | `adjustment_type = 3`, `adjustment_points = N` |

---

### 2.6 Strategy Preset Selector

Horizontal scrollable row of preset cards. Click to auto-fill all form fields:

| Preset Name | Pre-filled Configuration |
|---|---|
| **CE Sell + FUT Buy (V1)** | 1 CE SELL at `call_sell_position%`, 1 FUT BUY. Weekly Expiry-to-Expiry. |
| **PE Sell + FUT Buy (V2)** | 1 PE SELL at `put_sell_position%`, 1 FUT BUY. Weekly Expiry-to-Expiry. |
| **Short Strangle (V4)** | 1 CE SELL OTM + 1 PE SELL OTM. No Future. Weekly Expiry. |
| **Hedged Bull (V8)** | 1 CE SELL + 1 PE BUY (`put_strike_pct_below%` below CE) + 1 FUT BUY. |
| **Protected CE Sell (V5 Call)** | 1 CE SELL + 1 CE BUY at `protection_pct%` higher (bear spread). |
| **Protected PE Sell (V5 Put)** | 1 PE SELL + 1 PE BUY at `protection_pct%` lower (bull spread). |
| **Premium-Based Strangle (V7)** | CE+PE SELL at strikes where price ≤ ATM_premium × multiplier. |
| **Counter-Expiry (V9)** | CE SELL + PE BUY with dynamic put expiry based on week-of-month counter. |

---

### 2.7 Date Range Picker

- **From Date:** default = earliest date in `base2.csv`
- **To Date:** default = last available CSV date
- **`All Data`** quick button sets full range
- Drives date filtering applied to all strategy functions before backtest execution

---

### 2.8 Bottom Action Bar

| Button | Action |
|---|---|
| **Save Strategy** | Saves form state to `localStorage` as named strategy |
| **Start Backtest** | `POST /api/backtest` — shows loading spinner, renders Results Panel on response |
| **Export .algtst** | Downloads current config as JSON |
| **Import .algtst** | File picker to load previously exported config JSON |
| **PDF** | Browser `window.print()` on Results Panel |

---

## 3. Results Panel — Charts & Analytics

After `Start Backtest` completes, configuration panel slides up and Results Panel renders below.

---

### 3.1 KPI Summary Cards (6 Cards)

| Card | Python Source | Format |
|---|---|---|
| **Total P&L** | `df['Net P&L'].sum()` | Points, 2 decimal |
| **Win Rate** | `W%` from `create_summary_idx()` | `%` |
| **Total Trades** | `Count` from `create_summary_idx()` | Integer |
| **CAGR** | `CAGR(Options)` from `create_summary_idx()` | `%` |
| **Max Drawdown** | `DD` (%) from `create_summary_idx()` | `%`, red |
| **CAR/MDD** | `round(CAGR / abs(Max_DD_%), 2)` | Ratio |

---

### 3.2 Equity Curve Chart

Recharts `ComposedChart`:

- **X-axis:** `exit_date`
- **Y-axis:** Cumulative P&L (`df['Cumulative']`)
- **Line 1:** Strategy equity — blue `#1A56DB` with 20% opacity area fill
- **Line 2:** NIFTY spot equivalent — grey `#9CA3AF`
- **Reference line** at `y = 0` (grey dashed)
- **Tooltip:** Date | Strategy P&L | Spot P&L | Net P&L for that trade
- Responsive container, 100% width, 300px height

---

### 3.3 Drawdown Chart

Recharts `AreaChart` below equity curve:

- **X-axis:** `exit_date` (synced with equity chart)
- **Y-axis:** `%DD` column (negative values)
- **Fill:** Red `#EF4444`, area below 0 line
- Shows worst drawdown period visually

---

### 3.4 Monthly P&L Heatmap

Visual grid from `getPivotTable()` output:

- **Rows:** Years (2015 → 2026)
- **Columns:** Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec, Grand Total
- **Cell value:** Net P&L for that month/year
- **Color coding:**
  - Green: `hsl(142, 70%, 90% - (value/max)*40%)` for positive
  - Red: `hsl(0, 70%, 90% - (abs(value)/maxAbs)*40%)` for negative
  - White for zero / no trades
- **Last row:** Grand Total (column sums)
- **Last column:** Year Total (row sums)
- Click on cell → filters Trade Log to that month

---

### 3.5 Trade-by-Trade Log Table

Paginated (50 rows/page), sortable, CSV-exportable:

| Column | Source Field |
|---|---|
| Entry Date | `df['Entry Date']` |
| Exit Date | `df['Exit Date']` |
| Entry Spot | `df['Entry Spot']` |
| Exit Spot | `df['Exit Spot']` |
| Spot P&L | `df['Spot P&L']` |
| Call Strike | `df['Call Strike']` |
| Call Entry Px | `df['Call EntryPrice']` |
| Call Exit Px | `df['Call ExitPrice']` (or Hypothetical at expiry) |
| Call P&L | `df['Call P&L']` |
| Put Strike | `df['Put Strike']` (if PE leg) |
| Put P&L | `df['Put P&L']` (if PE leg) |
| Future P&L | `df['Future P&L']` (if FUT leg) |
| Net P&L | `df['Net P&L']` — **green** if positive, **red** if negative |
| Cumulative | `df['Cumulative']` |
| %DD | `df['%DD']` — **red** if < 0 |

---

### 3.6 Full Summary Statistics Table

Renders all fields from `create_summary_idx()`:

`Count` | `Sum` | `Avg` | `W%` | `Avg(W)` | `L%` | `Avg(L)` | `Expectancy` | `CAGR(Options)` | `DD` | `Spot Change` | `ROI vs Spot` | `CAGR(Spot)` | `DD(Points)` | `CAR/MDD` | `Recovery Factor`

---

## 4. Backend API — Endpoints & Schema

### 4.1 `POST /api/backtest` — Request

```json
{
  "strategy_version": "v1",
  // Options: "v1" | "v2" | "v3" | "v4" | "v5_put" | "v5_call"
  //          "v6" | "v7" | "v8_prem" | "v8_ce_pe_fut" | "v9"

  "expiry_window": "weekly_expiry",
  // Options: "weekly_expiry" | "weekly_t1" | "weekly_t2"
  //          "monthly_expiry" | "monthly_t1"

  "spot_adjustment_type": 0,      // 0=none, 1=rise%, 2=fall%, 3=both
  "spot_adjustment": 1.0,         // % threshold (float)

  "call_sell_position": 0.0,      // % offset for CE strike from spot
  "put_sell_position": 0.0,       // % offset for PE strike from spot
  "put_strike_pct_below": 1.0,    // V8: PE strike % below CE strike

  "protection": false,            // V5: enable protective hedge
  "protection_pct": 1.0,          // V5: % OTM for protective leg

  "call_premium": true,           // V7: use ATM call premium for target
  "put_premium": true,            // V7: use ATM put premium for target
  "premium_multiplier": 1.0,      // V7: ATM premium scale factor
  "call_sell": true,
  "put_sell": true,

  "call_hsl_pct": 100,            // HSL: stop loss as % of entry premium
  "put_hsl_pct": 100,

  "max_put_spot_pct": 0.04,       // V9: max put strike % below spot

  "pct_diff": 0.3,                // V3: % above strike that triggers roll

  "from_date": "2019-01-01",
  "to_date": "2026-01-02",
  "index": "NIFTY"
}
```

---

### 4.2 `POST /api/backtest` — Response

```json
{
  "status": "success",
  "meta": {
    "strategy": "CE Sell + Future Buy",
    "index": "NIFTY",
    "total_trades": 312,
    "date_range": "2019-02-01 to 2026-01-02"
  },
  "trades": [
    {
      "entry_date": "2019-02-08",
      "exit_date": "2019-02-14",
      "entry_spot": 10934.35,
      "exit_spot": 10791.65,
      "spot_pnl": -142.70,
      "call_expiry": "2019-02-14",
      "call_strike": 10900,
      "call_entry_price": 87.5,
      "call_exit_price": 0.0,
      "call_entry_turnover": 12450000,
      "call_exit_turnover": 8320000,
      "call_pnl": 87.5,
      "future_expiry": "2019-03-28",
      "future_entry_price": 10952.0,
      "future_exit_price": 10810.5,
      "future_pnl": -141.5,
      "net_pnl": -54.0,
      "cumulative": 10880.35,
      "peak": 10934.35,
      "dd": -54.0,
      "pct_dd": -0.49
    }
  ],
  "summary": {
    "total_pnl": 4823.5,
    "count": 312,
    "win_pct": 64.42,
    "avg_win": 132.5,
    "avg_loss": -89.3,
    "expectancy": 1.12,
    "cagr_options": 12.4,
    "cagr_spot": 10.2,
    "max_dd_pct": -8.35,
    "max_dd_pts": -912.0,
    "car_mdd": 1.49,
    "recovery_factor": 5.29,
    "roi_vs_spot": 118.4
  },
  "pivot": {
    "headers": ["Year","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Grand Total"],
    "rows": [
      [2019, null, 121.5, -43.0, 87.3, 55.0, 210.5, -30.0, 115.0, 88.5, -22.0, 195.0, 44.0, 821.8],
      [2020, 55.0, -210.5, -312.0, 180.5, 95.0, 88.5, 122.0, 67.5, -44.0, 210.0, 88.5, 112.0, 452.5]
    ]
  },
  "log": [
    {
      "symbol": "NIFTY",
      "reason": "Call Entry Data missing for Strike 10900 with Expiry 2020-03-26",
      "call_expiry": "2020-03-26",
      "from": "2020-03-23",
      "to": "2020-03-26"
    }
  ]
}
```

---

### 4.3 `GET /api/expiry`

```
GET /api/expiry?index=NIFTY&type=weekly
```

Returns list of all expiry dates from the appropriate CSV. Used to populate expiry dropdowns.

```json
{
  "index": "NIFTY",
  "type": "weekly",
  "expiries": ["2015-01-01", "2015-01-08", "..."]
}
```

---

### 4.4 `GET /api/strategies`

Returns all supported strategy versions with display name, parameter list, and defaults — used to build preset cards.

---

### 4.5 `GET /api/data/dates`

```
GET /api/data/dates?index=NIFTY
```

Returns min/max available dates computed from `cleaned_csvs` filenames.

```json
{ "min_date": "2015-01-05", "max_date": "2026-01-02" }
```

---

## 5. Backend Engine Logic — Python Translation

### 5.1 Project File Structure

```
project/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── routers/
│   │   ├── backtest.py            # POST /api/backtest
│   │   ├── expiry.py              # GET /api/expiry
│   │   └── strategies.py         # GET /api/strategies
│   ├── engines/
│   │   ├── base.py                # Shared: data loaders, interval engine, analytics
│   │   ├── v1_ce_fut.py           # main1, main2, main3, main4
│   │   ├── v2_pe_fut.py           # main1_V2, main2_V2, main3_V2, main4_V2
│   │   ├── v3_strike_breach.py    # main1_V3 to main4_V3
│   │   ├── v4_strangle.py         # main1_V4, main2_V4
│   │   ├── v5_protected.py        # main1/2_V5_Put, main1/2_V5_Call
│   │   ├── v6_inverse_strangle.py # main1_V6, main2_V6
│   │   ├── v7_premium.py          # main1_V7, main2_V7
│   │   ├── v8_hsl.py              # V7_With_HSL, V4_With_HSL variants
│   │   ├── v8_ce_pe_fut.py        # main1_V8 to main4_V8 (CE+PE+FUT)
│   │   └── v9_counter.py          # main1_V9 to main4_V9
│   └── analytics.py               # create_summary_idx, getPivotTable
├── data/
│   ├── cleaned_csvs/              # YYYY-MM-DD.csv — one per trading day
│   ├── expiryData/                # NIFTY.csv, NIFTY_Monthly.csv
│   ├── strikeData/                # Nifty_strike_data.csv
│   └── Filter/base2.csv
└── frontend/
    ├── src/
    │   ├── App.jsx
    │   ├── components/
    │   │   ├── ConfigPanel.jsx
    │   │   ├── LegBuilder.jsx
    │   │   ├── ResultsPanel.jsx
    │   │   ├── EquityChart.jsx
    │   │   ├── DrawdownChart.jsx
    │   │   ├── MonthlyHeatmap.jsx
    │   │   └── TradeLog.jsx
    │   └── api/backtest.js
    └── package.json
```

---

### 5.2 `base.py` — Shared Functions

#### `get_strike_data(symbol, from_date, to_date)`
- Read `./strikeData/Nifty_strike_data.csv`
- Filter by symbol, parse `Date`, filter to date range
- Return DataFrame: `Date`, `Close`

#### `load_expiry(index, expiry_type)`
- Read `./expiryData/{index}.csv` (weekly) or `./expiryData/{index}_Monthly.csv`
- Parse `Previous Expiry`, `Current Expiry`, `Next Expiry`
- Return sorted DataFrame

#### `load_base2()`
- Read `./Filter/base2.csv`, parse `Start` and `End`
- Return sorted DataFrame

#### `load_bhavcopy(date_str)` ← **LRU cache 500 files**
- Read `./cleaned_csvs/{date_str}.csv`
- Parse `Date` and `ExpiryDate` columns
- Return DataFrame: `Instrument`, `Symbol`, `ExpiryDate`, `OptionType`, `StrikePrice`, `Close`, `TurnOver`

#### `get_option_price(bhavcopy_df, symbol, instrument, option_type, expiry, strike)`
- Filter for matching row
- **Allow ±1 day tolerance on expiry date**
- Return `(close_price, turnover)` or `(None, None)`

#### `build_intervals(filtered_data, spot_adjustment_type, spot_adjustment)`

```python
# Core re-entry engine — identical logic to all Python strategy functions
def build_intervals(filtered_data, spot_adjustment_type, spot_adjustment):
    if spot_adjustment_type == 0:
        return [(filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'])]
    
    entry_price = None
    reentry_dates = []
    
    for i, row in filtered_data.iterrows():
        if entry_price is None:
            entry_price = row['Close']
            continue
        
        roc = 100 * (row['Close'] - entry_price) / entry_price
        
        triggered = (
            (spot_adjustment_type == 1 and roc >= spot_adjustment) or
            (spot_adjustment_type == 2 and roc <= -spot_adjustment) or
            (spot_adjustment_type == 3 and abs(roc) >= spot_adjustment)
        )
        
        if triggered:
            reentry_dates.append(row['Date'])
            entry_price = row['Close']
    
    if not reentry_dates:
        return [(filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'])]
    
    intervals = []
    start = filtered_data.iloc[0]['Date']
    for d in reentry_dates:
        intervals.append((start, d))
        start = d
    if start != filtered_data.iloc[-1]['Date']:
        intervals.append((start, filtered_data.iloc[-1]['Date']))
    return intervals
```

#### `compute_analytics(trades_df)` — Translation of `create_summary_idx()`

```python
def compute_analytics(df):
    # Add Cumulative, Peak, DD, %DD columns
    df['Cumulative'] = df.iloc[0]['Entry Spot'] + df['Net P&L'].cumsum()
    df['Peak'] = df['Cumulative'].cummax()
    df['DD'] = np.where(df['Peak'] > df['Cumulative'], df['Cumulative'] - df['Peak'], 0)
    df['%DD'] = np.where(df['DD'] == 0, 0, round(100 * (df['DD'] / df['Peak']), 2))
    
    entry_spot = df.iloc[0]['Entry Spot']
    n_years = (df['Exit Date'].max() - df['Entry Date'].min()).days / 365.25
    total_pnl = df['Net P&L'].sum()
    count = len(df)
    
    wins = df[df['Net P&L'] > 0]
    losses = df[df['Net P&L'] < 0]
    win_pct = round(len(wins) / count * 100, 2)
    avg_win = round(wins['Net P&L'].mean(), 2)
    avg_loss = round(losses['Net P&L'].mean(), 2)
    
    cagr = round(100 * (((total_pnl + entry_spot) / entry_spot) ** (1/n_years) - 1), 2)
    max_dd_pct = df['%DD'].min()
    car_mdd = round(cagr / abs(max_dd_pct), 2)
    recovery_factor = round(total_pnl / abs(df['DD'].min()), 2)
    
    return df, {
        "total_pnl": round(total_pnl, 2),
        "count": count,
        "win_pct": win_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": round(((avg_win/abs(avg_loss)) * win_pct - (100-win_pct)) / 100, 2),
        "cagr_options": cagr,
        "max_dd_pct": max_dd_pct,
        "max_dd_pts": round(df['DD'].min(), 2),
        "car_mdd": car_mdd,
        "recovery_factor": recovery_factor
    }
```

#### `build_pivot(trades_df, expiry_col)` — Translation of `getPivotTable()`

```python
def build_pivot(df, expiry_col):
    df = df.copy()
    df['Month'] = pd.to_datetime(df[expiry_col]).dt.strftime('%b')
    df['Year'] = pd.to_datetime(df[expiry_col]).dt.year
    
    pivot = df.pivot_table(values='Net P&L', index='Year', columns='Month', aggfunc='sum')
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot.columns]
    pivot = pivot[month_order]
    pivot['Grand Total'] = pivot[month_order].sum(axis=1).round(2)
    
    headers = ['Year'] + month_order + ['Grand Total']
    rows = [[str(year)] + [round(pivot.loc[year, m], 2) if m in pivot.columns and not pd.isna(pivot.loc[year, m]) else None for m in month_order + ['Grand Total']]
            for year in pivot.index]
    return {"headers": headers, "rows": rows}
```

---

### 5.3 V1 Engine — CE Sell + Future Buy

```python
# run_v1(params) — handles all expiry windows

def run_v1(params):
    spot_df = get_strike_data("NIFTY", params.from_date, params.to_date)
    weekly_exp = load_expiry("NIFTY", "weekly")
    monthly_exp = load_expiry("NIFTY", "monthly")
    base2 = load_base2()
    
    # Filter spot to base2 ranges
    mask = pd.Series(False, index=spot_df.index)
    for _, row in base2.iterrows():
        mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    spot_df = spot_df[mask]
    
    trades = []
    
    for _, exp_row in weekly_exp.iterrows():
        prev_exp = exp_row['Previous Expiry']
        curr_exp = exp_row['Current Expiry']
        
        # Get future expiry = nearest monthly >= curr weekly
        fut_exp = monthly_exp[monthly_exp['Current Expiry'] >= curr_exp].iloc[0]['Current Expiry']
        
        # Filter spot to window
        window = spot_df[(spot_df['Date'] >= prev_exp) & (spot_df['Date'] <= curr_exp)]
        if len(window) < 2:
            continue
        
        # Build re-entry intervals
        intervals = build_intervals(window, params.spot_adjustment_type, params.spot_adjustment)
        
        for from_date, to_date in intervals:
            if from_date == to_date:
                continue
            
            entry_spot = window[window['Date'] == from_date].iloc[0]['Close']
            exit_spot_row = window[window['Date'] == to_date]
            exit_spot = exit_spot_row.iloc[0]['Close'] if not exit_spot_row.empty else None
            
            # Calculate strike: round((spot*(1+pct%))/100)*100
            call_strike = round_half_up((entry_spot * (1 + params.call_sell_position/100)) / 100) * 100
            
            # Load bhavcopy CSVs
            bhav_entry = load_bhavcopy(from_date.strftime('%Y-%m-%d'))
            bhav_exit  = load_bhavcopy(to_date.strftime('%Y-%m-%d'))
            
            # Get CE price
            call_entry_px, call_entry_tv = get_option_price(bhav_entry, "NIFTY", "OPTIDX", "CE", curr_exp, call_strike)
            call_exit_px,  call_exit_tv  = get_option_price(bhav_exit,  "NIFTY", "OPTIDX", "CE", curr_exp, call_strike)
            if call_entry_px is None or call_exit_px is None:
                log_error("NIFTY", f"Call data missing for strike {call_strike}", curr_exp, from_date, to_date)
                continue
            
            # Get Future price
            fut_entry = bhav_entry[(bhav_entry['Instrument']=="FUTIDX") & (bhav_entry['Symbol']=="NIFTY") &
                                   (bhav_entry['ExpiryDate'].dt.month == fut_exp.month) &
                                   (bhav_entry['ExpiryDate'].dt.year == fut_exp.year)]
            fut_exit  = bhav_exit[(bhav_exit['Instrument']=="FUTIDX") & (bhav_exit['Symbol']=="NIFTY") &
                                  (bhav_exit['ExpiryDate'].dt.month == fut_exp.month) &
                                  (bhav_exit['ExpiryDate'].dt.year == fut_exp.year)]
            if fut_entry.empty or fut_exit.empty:
                continue
            
            call_pnl = round(call_entry_px - call_exit_px, 2)    # SELL: entry - exit
            fut_pnl  = round(fut_exit.iloc[0]['Close'] - fut_entry.iloc[0]['Close'], 2)  # BUY: exit - entry
            net_pnl  = round(call_pnl + fut_pnl, 2)
            
            trades.append({
                "entry_date": from_date, "exit_date": to_date,
                "entry_spot": entry_spot, "exit_spot": exit_spot,
                "call_expiry": curr_exp, "call_strike": call_strike,
                "call_entry_price": call_entry_px, "call_entry_turnover": call_entry_tv,
                "call_exit_price": call_exit_px, "call_exit_turnover": call_exit_tv,
                "call_pnl": call_pnl,
                "future_expiry": fut_exp,
                "future_entry_price": fut_entry.iloc[0]['Close'],
                "future_exit_price": fut_exit.iloc[0]['Close'],
                "future_pnl": fut_pnl,
                "net_pnl": net_pnl
            })
    
    df = pd.DataFrame(trades).drop_duplicates(subset=['entry_date','exit_date'])
    df, summary = compute_analytics(df)
    pivot = build_pivot(df, 'call_expiry')
    return df, summary, pivot
```

---

### 5.4 V4 Engine — Short Strangle (CE Sell + PE Sell)

```python
# Key difference from V1: two sell legs, no Future
# Strike filtering: TurnOver > 0, StrikePrice % 100 == 0, sort to find closest

call_entry_data = bhav_entry[
    (bhav_entry['Instrument'] == "OPTIDX") &
    (bhav_entry['Symbol'] == "NIFTY") &
    (bhav_entry['OptionType'] == "CE") &
    (bhav_entry['ExpiryDate'] == curr_exp) &  # ±1 day tolerance
    (bhav_entry['StrikePrice'] >= call_strike) &
    (bhav_entry['TurnOver'] > 0) &
    (bhav_entry['StrikePrice'] % 100 == 0)
].sort_values('StrikePrice', ascending=True)

# P&L: SELL means positive when price drops
call_pnl = round(call_entry_px - call_exit_px, 2)
put_pnl  = round(put_entry_px  - put_exit_px,  2)
net_pnl  = round(call_pnl + put_pnl, 2)
```

---

### 5.5 V5 Engine — Protected Sell (CE with Hedge)

```python
# Additional protective leg when protection=True
protective_strike = round_half_up((call_strike * (1 + params.protection_pct/100)) / 50) * 50

protective_entry_px, _ = get_option_price(bhav_entry, "NIFTY", "OPTIDX", "CE", curr_exp, protective_strike)
protective_exit_px,  _ = get_option_price(bhav_exit,  "NIFTY", "OPTIDX", "CE", curr_exp, protective_strike)

# Hypothetical exit at expiry: max(0, exit_spot - strike)
if to_date == curr_exp:
    hypothetical_call_exit = max(0, exit_spot - call_strike) if exit_spot >= call_strike else 0
    hypothetical_prot_exit = max(0, exit_spot - protective_strike) if exit_spot >= protective_strike else 0
    call_pnl = round(call_entry_px - hypothetical_call_exit, 2)         # SELL
    protective_pnl = round(hypothetical_prot_exit - protective_entry_px, 2)  # BUY
else:
    call_pnl = round(call_entry_px - call_exit_px, 2)
    protective_pnl = round(protective_exit_px - protective_entry_px, 2)

net_pnl = round(call_pnl + protective_pnl, 2)
```

---

### 5.6 V7 Engine — Premium-Based Strike Selection

```python
# On entry day: find ATM strike, get ATM premium, compute target, walk OTM to find strike
atm_strike = round_half_up(entry_spot / 50) * 50

# Fetch ATM Call and Put prices
atm_call_px, _ = get_option_price(bhav_entry, "NIFTY", "OPTIDX", "CE", curr_exp, atm_strike)
atm_put_px,  _ = get_option_price(bhav_entry, "NIFTY", "OPTIDX", "PE", curr_exp, atm_strike)

# Compute target premium
if params.call_premium and params.put_premium:
    target = (atm_call_px + atm_put_px) * params.premium_multiplier
elif params.call_premium:
    target = atm_call_px * params.premium_multiplier
else:
    target = atm_put_px * params.premium_multiplier

# Walk OTM Call strikes upward to find first <= target
all_strikes = bhav_entry[
    (bhav_entry['Instrument']=="OPTIDX") & (bhav_entry['Symbol']=="NIFTY") &
    (bhav_entry['OptionType']=="CE") & (bhav_entry['ExpiryDate']==curr_exp) &
    (bhav_entry['StrikePrice'] >= atm_strike) & (bhav_entry['TurnOver'] > 0)
].sort_values('StrikePrice')

call_strike = None
for _, row in all_strikes.iterrows():
    if row['Close'] <= target:
        call_strike = row['StrikePrice']
        break
```

---

### 5.7 V8 HSL Engine — Hard Stop Loss

```python
# Monitor each day between entry and expiry for stop loss breach
call_hsl_threshold = call_entry_px * (params.call_hsl_pct / 100)
call_stopped = False
call_stop_price = None

for curr_date in trading_dates_in_window:
    bhav_today = load_bhavcopy(curr_date.strftime('%Y-%m-%d'))
    curr_call_px, _ = get_option_price(bhav_today, "NIFTY", "OPTIDX", "CE", curr_exp, call_strike)
    
    if not call_stopped and curr_call_px is not None:
        if curr_call_px > call_hsl_threshold:
            call_stopped = True
            call_stop_price = curr_call_px
            call_stop_date  = curr_date
            break  # Position closed at stop

call_exit_px = call_stop_price if call_stopped else call_exit_px_at_period_end
call_pnl = round(call_entry_px - call_exit_px, 2)
```

---

### 5.8 V9 Engine — Counter-Based Put Expiry

```python
# Assign counter = week number within month for each weekly expiry
weekly_exp['MonthYear'] = weekly_exp['Current Expiry'].dt.strftime('%Y-%m')
weekly_exp['Counter'] = weekly_exp.groupby('MonthYear').cumcount() + 1

# Dynamic put expiry based on counter
counter = exp_row['Counter']
if counter > 2:
    # 3rd or 4th week → use NEXT month's monthly expiry
    next_month = curr_exp.month + 1 if curr_exp.month < 12 else 1
    next_year  = curr_exp.year if curr_exp.month < 12 else curr_exp.year + 1
    put_expiry = monthly_exp[monthly_exp['Current Expiry'] >= pd.Timestamp(next_year, next_month, 1)].iloc[0]['Current Expiry']
else:
    # 1st or 2nd week → use current month's monthly expiry
    put_expiry = monthly_exp[monthly_exp['Current Expiry'] >= curr_exp].iloc[0]['Current Expiry']

# PE strike capped at max_put_spot_pct below spot
put_strike = min(
    round_half_up((call_strike * (1 - params.put_strike_pct_below/100)) / 50) * 50,
    round_half_up((entry_spot * (1 - params.max_put_spot_pct)) / 50) * 50
)
```

---

## 6. Frontend React Component Specifications

### 6.1 `ConfigPanel.jsx`

```jsx
// State: strategyConfig object matching full API request schema
// Behaviors:
//   - Preset click → populate all form fields
//   - 'Add Leg' → append to legs[] array (max 4)
//   - Adjustment type toggle → show/hide sub-fields
//   - Date range picker → update from_date/to_date
//   - 'Start Backtest' → validate → POST /api/backtest → set loading → on success setResults()
//   - 'Save Strategy' → localStorage.setItem('strategies', JSON.stringify([...existing, current]))
//   - 'Export .algtst' → downloadJSON(strategyConfig)
//   - 'Import .algtst' → parse JSON → setStrategyConfig(parsed)
```

---

### 6.2 `LegBuilder.jsx`

```jsx
// Props: legs[], onLegsChange(newLegs)
// Renders one row per leg with:
//   <InstrumentSelect>   CE | PE | FUT
//   <BuySellToggle>      BUY | SELL
//   <LotsInput>          number, min=1
//   <StrikeTypeSelect>   ATM | OTM% | ITM% | Spot% | Premium-Based
//   <StrikeValueInput>   number, shown only when type != ATM
//   <ExpirySelect>       Current Weekly | Next Weekly | Current Monthly | Next Monthly
//   <SLInput>            number + type dropdown
//   <TargetInput>        number + type dropdown
//   <DeleteButton>       removes leg from array
// Drag-to-reorder: react-beautiful-dnd DragDropContext
```

---

### 6.3 `EquityChart.jsx`

```jsx
import { ComposedChart, Area, Line, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer } from 'recharts';

// Props: trades[] from API response
// Data transform: trades.map(t => ({ date: t.exit_date, equity: t.cumulative, spot: computeSpotCumulative(t) }))

<ResponsiveContainer width="100%" height={300}>
  <ComposedChart data={chartData}>
    <defs>
      <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="5%"  stopColor="#1A56DB" stopOpacity={0.3}/>
        <stop offset="95%" stopColor="#1A56DB" stopOpacity={0.0}/>
      </linearGradient>
    </defs>
    <XAxis dataKey="date" />
    <YAxis />
    <Tooltip content={<CustomTooltip />} />
    <ReferenceLine y={0} stroke="#9CA3AF" strokeDasharray="3 3" />
    <Area type="monotone" dataKey="equity" stroke="#1A56DB" fill="url(#equityGrad)" strokeWidth={2} />
    <Line type="monotone" dataKey="spot" stroke="#9CA3AF" dot={false} strokeWidth={1} />
  </ComposedChart>
</ResponsiveContainer>
```

---

### 6.4 `MonthlyHeatmap.jsx`

```jsx
// Props: pivot { headers[], rows[] }
// Color calculation:
const getColor = (value, maxPos, maxNeg) => {
  if (!value) return '#FFFFFF';
  if (value > 0) {
    const intensity = Math.min(value / maxPos, 1);
    const lightness = 90 - intensity * 40;
    return `hsl(142, 70%, ${lightness}%)`;
  } else {
    const intensity = Math.min(Math.abs(value) / maxNeg, 1);
    const lightness = 90 - intensity * 40;
    return `hsl(0, 70%, ${lightness}%)`;
  }
};
// Grid: CSS grid with columns = headers.length
// Click cell → call onFilterTrades({ year, month })
```

---

### 6.5 `TradeLog.jsx`

```jsx
// Props: trades[], filter { month?, year? }
// Features:
//   - Client-side pagination (50 rows/page)
//   - Sort by any column (click header cycles asc/desc/none)
//   - Filter: apply month/year from heatmap click, or manual date range
//   - CSV export: build CSV string from filtered trades, trigger download
//   - Row coloring: net_pnl > 0 → light green bg, net_pnl < 0 → light red bg
//   - %DD column: red text if < 0
```

---

## 7. Data File Contracts

### 7.1 `cleaned_csvs/YYYY-MM-DD.csv`

One file per trading day. **Critical columns:**

| Column | Values / Notes |
|---|---|
| `Instrument` | `OPTIDX` (options) or `FUTIDX` (futures) |
| `Symbol` | `NIFTY` \| `BANKNIFTY` \| `FINNIFTY` etc. |
| `ExpiryDate` | Option/Future expiry in `YYYY-MM-DD` |
| `OptionType` | `CE` (Call) \| `PE` (Put) \| `XX` (futures) |
| `StrikePrice` | Strike as float — e.g. `17500.0` |
| `Close` | End-of-day close price of the option/future |
| `TurnOver` | Daily turnover — filter `TurnOver > 0` for liquid strikes |
| `Date` | Trade date (same as filename) |

---

### 7.2 `expiryData/NIFTY.csv` (Weekly)

| Column | Description |
|---|---|
| `Previous Expiry` | Previous weekly expiry `YYYY-MM-DD` |
| `Current Expiry` | Current weekly expiry |
| `Next Expiry` | Next weekly expiry |

---

### 7.3 `expiryData/NIFTY_Monthly.csv`

Same structure as weekly but for monthly expiry dates. Used for Future expiry and Put expiry selection in V1/V8/V9.

---

### 7.4 `strikeData/Nifty_strike_data.csv`

| Column | Description |
|---|---|
| `Ticker` | Symbol name (`NIFTY`, `BANKNIFTY` etc.) |
| `Date` | Trading date |
| `Close` | NIFTY spot/cash index close |

---

### 7.5 `Filter/base2.csv`

| Column | Description |
|---|---|
| `Start` | Start of a trending/directional phase |
| `End` | End of that phase |

> **V1–V5, V7, V8, V9** use INSIDE these ranges. **V6, V8_premium** use OUTSIDE (inverse) of these ranges.

---

### 7.6 Strike Calculation Reference

| Parameter | Formula |
|---|---|
| ATM (50-rounded) | `round_half_up(spot / 50) * 50` |
| ATM (100-rounded) | `round_half_up(spot / 100) * 100` |
| OTM Call at X% | `round_half_up((spot * (1 + X/100)) / 100) * 100` |
| OTM Put at X% | `round_half_up((spot * (1 - X/100)) / 100) * 100` |
| `round_half_up(x)` | `math.floor(x + 0.5)` — rounds 0.5 upward |

---

## 8. Copy-Paste Master Prompt

Use this prompt directly with Claude, GPT-4o, or Gemini to build the full application:

---

```
Build a full-stack NIFTY options backtesting platform (AlgoTest clone).
The UI must look and function exactly like AlgoTest (algotest.in/backtest).
The backend must run real backtests from NSE bhavcopy CSV files with zero mock data.

═══════════════════════════════════════════════════════
TECHNOLOGY STACK
═══════════════════════════════════════════════════════
Frontend : React 18 + Vite + Tailwind CSS + Recharts + Lucide React
Backend  : Python FastAPI + Pandas + NumPy + Uvicorn
Data     : Flat CSV files — no database needed
State    : useState/useReducer + TanStack Query

═══════════════════════════════════════════════════════
FRONTEND PAGES
═══════════════════════════════════════════════════════

1. BACKTEST CONFIG PAGE (main screen)
   ─────────────────────────────────
   Top Nav Bar:
   - Dark bg #111827, logo + nav tabs (Backtest active), Credits badge
   
   Strategy Type Tabs:
   - Weekly & Monthly Expiries (NIFTY | SENSEX) ← default active
   - Monthly Only Expiry (MIDCPNIFTY | BANKNIFTY | FINNIFTY | BANKEX)
   - Stocks – Cash / F&O
   - Delta Exchange (BTCUSD | ETHUSD)
   
   Instrument Settings Card:
   - Index dropdown: NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY | SENSEX
   - Underlying From toggle: [Cash] [Futures]
   
   Legwise Settings Card:
   - Square Off toggle: [Partial] [Complete]
   - Trail SL to Breakeven checkbox: All Legs | SL Legs
   - Add Leg (+) button — max 4 legs
   
   Each Leg Row (horizontal):
   - Instrument: CE | PE | FUT
   - Direction: BUY | SELL toggle
   - Lots: number input
   - Strike Type: ATM | OTM% | ITM% | Spot% | Premium-Based
   - Strike Value: number (% offset from spot)
   - Expiry: Current Weekly | Next Weekly | Current Monthly | Next Monthly | Month+2
   - Stop Loss: number + type (Points | % | Premium%)
   - Target: number + type
   
   Entry Settings Card (right column):
   - Strategy Type: [Intraday] [BTST] [Positional] — default Positional
   - Entry Time: 09:35 | Exit Time: 15:15
   - No Re-entry After: toggle + time
   - Overall Momentum: toggle → [Points/% dropdown] + value
   
   Adjustment Card (right column):
   - Radio group: No Adjustment | Rise By X% | Fall By X% | Rise or Fall By X%
   - X input: number field
   - Maps to: spot_adjustment_type (0/1/2/3) + spot_adjustment (X)
   
   Strategy Presets (scrollable row of cards):
   - CE Sell + FUT Buy | PE Sell + FUT Buy | Short Strangle
   - Hedged Bull (CE+PE+FUT) | Protected CE Sell | Protected PE Sell
   - Premium-Based Strangle | Counter-Expiry V9
   
   Date Range Picker: From / To date inputs + "All Data" button
   
   Bottom Bar: [Save Strategy] [Start Backtest ▶] [Export .algtst] [Import .algtst] [PDF]

2. RESULTS PANEL (rendered below config after backtest)
   ───────────────────────────────────────────────────
   6 KPI Cards: Total P&L | Win Rate | Total Trades | CAGR | Max Drawdown | CAR/MDD
   
   Equity Curve Chart (Recharts ComposedChart):
   - X: exit_date | Y: cumulative P&L
   - Blue line + gradient fill = strategy | Grey line = NIFTY spot equivalent
   - Grey dashed reference line at y=0
   - Custom tooltip: Date | Strategy P&L | Spot P&L | Trade Net P&L
   
   Drawdown Chart (Recharts AreaChart):
   - X: exit_date | Y: %DD (negative)
   - Red fill area below zero
   
   Monthly P&L Heatmap (CSS Grid):
   - Rows = Years, Columns = Jan–Dec + Grand Total
   - Green intensity ∝ profit magnitude | Red intensity ∝ loss magnitude
   - Click cell → filter trade log to that month
   
   Trade-by-Trade Log Table:
   - Columns: Entry Date | Exit Date | Entry Spot | Exit Spot | Spot P&L |
              Call Strike | Call Entry Px | Call Exit Px | Call P&L |
              Put Strike | Put P&L | Future P&L | Net P&L | Cumulative | %DD
   - Pagination: 50 rows/page | Sort by any column | CSV Export button
   - Net P&L colored green/red | %DD colored red if negative
   
   Summary Stats Table:
   - Count | Sum | Avg | W% | Avg(W) | L% | Avg(L) | Expectancy |
     CAGR(Options) | DD | Spot Change | ROI vs Spot | CAGR(Spot) |
     DD(Points) | CAR/MDD | Recovery Factor

═══════════════════════════════════════════════════════
BACKEND API ENDPOINTS
═══════════════════════════════════════════════════════

POST /api/backtest
  Request body (JSON):
  {
    "strategy_version": "v1",      // v1|v2|v3|v4|v5_put|v5_call|v6|v7|v8_prem|v8_ce_pe_fut|v9
    "expiry_window": "weekly_expiry", // weekly_expiry|weekly_t1|weekly_t2|monthly_expiry|monthly_t1
    "spot_adjustment_type": 0,     // 0=none, 1=rise, 2=fall, 3=both
    "spot_adjustment": 1.0,        // % threshold
    "call_sell_position": 0.0,     // % above spot for CE strike
    "put_sell_position": 0.0,      // % below spot for PE strike
    "put_strike_pct_below": 1.0,   // V8: PE % below CE strike
    "protection": false,           // V5: enable protective leg
    "protection_pct": 1.0,         // V5: protective leg offset %
    "call_premium": true,          // V7: use call ATM for target
    "put_premium": true,           // V7: use put ATM for target
    "premium_multiplier": 1.0,     // V7: ATM premium scale factor
    "call_sell": true,
    "put_sell": true,
    "call_hsl_pct": 100,           // HSL: stop = entry_price * (hsl_pct/100)
    "put_hsl_pct": 100,
    "max_put_spot_pct": 0.04,      // V9: put strike floor
    "pct_diff": 0.3,               // V3: strike breach threshold %
    "from_date": "2019-01-01",
    "to_date": "2026-01-02",
    "index": "NIFTY"
  }
  
  Response (JSON):
  {
    "status": "success",
    "meta": { "strategy": "...", "total_trades": 312, "date_range": "..." },
    "trades": [ { entry/exit dates, spots, leg prices, P&Ls, cumulative, DD } ],
    "summary": { total_pnl, count, win_pct, avg_win, avg_loss, expectancy,
                 cagr_options, cagr_spot, max_dd_pct, max_dd_pts, car_mdd,
                 recovery_factor, roi_vs_spot },
    "pivot": { "headers": [...], "rows": [[year, jan, feb, ...], ...] },
    "log": [ { symbol, reason, from, to } ]
  }

GET /api/expiry?index=NIFTY&type=weekly   → list of expiry dates
GET /api/strategies                        → list of presets with defaults
GET /api/data/dates?index=NIFTY           → { min_date, max_date }

═══════════════════════════════════════════════════════
BACKEND COMPUTATION — CORE ALGORITHM
═══════════════════════════════════════════════════════

DATA LOADING:
- load_bhavcopy(date_str): read ./cleaned_csvs/{date_str}.csv with LRU cache (500 files)
- get_strike_data(): read ./strikeData/Nifty_strike_data.csv filtered by symbol + date range
- load_expiry(index, type): read ./expiryData/{index}.csv or {index}_Monthly.csv
- load_base2(): read ./Filter/base2.csv (Start, End date ranges)

SHARED INTERVAL ENGINE (all strategies):
def build_intervals(spot_window_df, adjustment_type, adjustment_value):
  if adjustment_type == 0:
    return [(first_date, last_date)]
  
  Track entry_price per row. Compute roc = 100*(close - entry_price)/entry_price
  Trigger conditions:
    type=1: roc >= adjustment_value  (spot rose)
    type=2: roc <= -adjustment_value (spot fell)
    type=3: abs(roc) >= adjustment_value (either)
  On trigger: record reentry_date, reset entry_price
  Return list of (from, to) date tuples between triggers

STRIKE CALCULATION:
  call_strike = round_half_up((spot*(1+call_sell_position/100))/100)*100
  put_strike  = round_half_up((spot*(1+put_sell_position/100))/100)*100
  round_half_up(x) = math.floor(x+0.5)  ← NOT Python default rounding

OPTION PRICE LOOKUP:
  Filter bhavcopy for: Instrument=OPTIDX, Symbol=NIFTY, OptionType=CE/PE
  Expiry match: exact OR ±1 day tolerance
  Strike match: exact StrikePrice
  Return (Close, TurnOver) or (None, None) if not found

FUTURE PRICE LOOKUP:
  Filter bhavcopy for: Instrument=FUTIDX, Symbol=NIFTY
  Match by: ExpiryDate.month == fut_expiry.month AND ExpiryDate.year == fut_expiry.year

P&L CALCULATION:
  CE/PE SELL: P&L = entry_price - exit_price  (profit when price decays)
  CE/PE BUY:  P&L = exit_price - entry_price  (profit when price rises)
  FUT BUY:    P&L = exit_price - entry_price

HYPOTHETICAL EXIT AT EXPIRY (for summary recalculation):
  If exit_date == expiry_date:
    CE hypothetical = max(0, exit_spot - call_strike)
    PE hypothetical = max(0, put_strike - exit_spot)

ANALYTICS (compute after all trades collected):
  df['Cumulative'] = entry_spot_0 + cumsum(net_pnl)
  df['Peak'] = cummax(Cumulative)
  df['DD'] = where(Peak > Cumulative, Cumulative - Peak, 0)
  df['%DD'] = where(DD==0, 0, round(100*(DD/Peak), 2))
  
  CAGR = 100 * (((total_pnl + entry_spot) / entry_spot)^(1/n_years) - 1)
  CAR/MDD = CAGR / abs(max %DD)
  Recovery Factor = total_pnl / abs(max DD points)
  Expectancy = ((avg_win/abs(avg_loss) * win_pct) - loss_pct) / 100

═══════════════════════════════════════════════════════
DATA FILE STRUCTURE
═══════════════════════════════════════════════════════

./cleaned_csvs/YYYY-MM-DD.csv
  Columns: Instrument, Symbol, ExpiryDate, OptionType, StrikePrice, Close, TurnOver, Date
  Instrument values: OPTIDX (options) | FUTIDX (futures)
  OptionType values: CE | PE | XX (futures)
  Filter for liquid: TurnOver > 0, StrikePrice % 100 == 0

./expiryData/NIFTY.csv
  Columns: Previous Expiry, Current Expiry, Next Expiry  (format: YYYY-MM-DD)

./expiryData/NIFTY_Monthly.csv
  Same structure — monthly expiry dates only

./strikeData/Nifty_strike_data.csv
  Columns: Ticker, Date, Close  (daily NIFTY spot close)

./Filter/base2.csv
  Columns: Start, End  (date ranges of trending market phases)
  V1/V2/V3/V4/V5/V7/V8/V9 → operate INSIDE these ranges
  V6/V8_premium            → operate OUTSIDE (inverse) these ranges

═══════════════════════════════════════════════════════
DESIGN SPECIFICATIONS
═══════════════════════════════════════════════════════
Colors:
  Nav bg:       #111827
  Blue accent:  #1A56DB
  Card bg:      #FFFFFF
  Page bg:      #F9FAFB
  Active tab:   bottom border #1A56DB
  Profit green: #10B981
  Loss red:     #EF4444
  Neutral grey: #9CA3AF

Typography: system-ui / -apple-system (match AlgoTest)
Charts: Recharts — responsive, custom tooltips, synced X-axes
Heatmap: Green = hsl(142,70%,90%-40%*intensity) | Red = hsl(0,70%,90%-40%*intensity)
Layout: Two-column config (left=legs, right=settings) | Full-width results below
Responsive: Desktop 1280px+ primary | Tablet 768px+ supported

═══════════════════════════════════════════════════════
DELIVERABLES
═══════════════════════════════════════════════════════
1. /backend/ — FastAPI app with all endpoints + all strategy engines
2. /frontend/ — React app with all components + API integration
3. README.md — setup instructions, how to add new CSV data, how to add strategies
4. requirements.txt (Python) + package.json (Node)
5. Sample .algtst preset files for all 8 strategy presets
```

---

## 9. Implementation Checklist

### Phase 1 — Backend Foundation
- [ ] FastAPI project setup with CORS enabled
- [ ] `get_strike_data()` — load and filter daily spot CSV
- [ ] `load_expiry()` — weekly and monthly expiry CSVs
- [ ] `load_base2()` — trending phase date ranges
- [ ] `load_bhavcopy()` — with LRU cache (functools.lru_cache, 500 files)
- [ ] `get_option_price()` — with ±1 day expiry tolerance
- [ ] `build_intervals()` — core re-entry engine
- [ ] `compute_analytics()` — cumulative, DD, summary stats
- [ ] `build_pivot()` — year × month P&L pivot
- [ ] `GET /api/expiry` endpoint
- [ ] `GET /api/data/dates` endpoint

### Phase 2 — Strategy Engines
- [ ] **V1:** CE Sell + Future Buy (weekly_expiry, weekly_t1, weekly_t2, monthly_expiry, monthly_t1)
- [ ] **V2:** PE Sell + Future Buy (all windows)
- [ ] **V3:** Strike-Breach Re-entry (roll call expiry when spot breaches strike × (1+pct_diff))
- [ ] **V4:** Short Strangle — CE Sell + PE Sell (weekly only)
- [ ] **V5_Call:** CE Sell + Optional Protective CE Buy
- [ ] **V5_Put:** PE Sell + Optional Protective PE Buy
- [ ] **V6:** Inverse-base Short Strangle (operates OUTSIDE base2 ranges)
- [ ] **V7:** Premium-based OTM strike selection (walk strikes until price ≤ target)
- [ ] **V8_HSL:** V7 + Hard Stop Loss (daily price monitoring)
- [ ] **V8_CE_PE_FUT:** CE Sell + PE Buy + Future Buy (all 4 windows)
- [ ] **V9:** Counter-based Put expiry (week-of-month logic)

### Phase 3 — Frontend Components
- [ ] Top nav bar (dark, tabs, credits badge)
- [ ] Strategy type tab row (4 tabs)
- [ ] `ConfigPanel.jsx` — full form state management
- [ ] `LegBuilder.jsx` — add/remove/reorder legs
- [ ] 8 strategy preset cards (scrollable row)
- [ ] Adjustment type selector (radio group → API params)
- [ ] Date range picker
- [ ] `POST /api/backtest` integration + loading spinner
- [ ] `ResultsPanel.jsx` — layout shell
- [ ] `EquityChart.jsx` — strategy vs spot lines, gradient fill
- [ ] `DrawdownChart.jsx` — red area chart, synced X-axis
- [ ] `MonthlyHeatmap.jsx` — green/red CSS grid, click to filter
- [ ] `TradeLog.jsx` — paginated, sortable, CSV export
- [ ] KPI summary cards (6 cards)
- [ ] Full summary stats table

### Phase 4 — Polish & Export
- [ ] Save/Load strategy (localStorage)
- [ ] Export/Import `.algtst` JSON
- [ ] PDF export (window.print)
- [ ] Error states (API error, missing data, no trades)
- [ ] Empty states (no results yet)
- [ ] Loading skeleton screens
- [ ] Responsive layout (tablet 768px+)
- [ ] README.md with setup + data instructions

---

## Strategy Version Quick Reference

| Version | Legs | Filter | Key Parameter |
|---|---|---|---|
| V1 | CE SELL + FUT BUY | base2 inside | `call_sell_position` |
| V2 | PE SELL + FUT BUY | base2 inside | `put_sell_position` |
| V3 | CE SELL + FUT BUY | base2 inside | `pct_diff` (strike breach) |
| V4 | CE SELL + PE SELL | base2 inside | `call_sell_position`, `put_sell_position` |
| V5_Call | CE SELL + (CE BUY) | base2 inside | `protection`, `protection_pct` |
| V5_Put | PE SELL + (PE BUY) | base2 inside | `protection`, `protection_pct` |
| V6 | CE SELL + PE SELL | base2 **outside** | Same as V4 |
| V7 | CE/PE SELL | base2 inside | `premium_multiplier`, `call_premium`, `put_premium` |
| V8_HSL | CE/PE SELL + stop | base2 inside/outside | `call_hsl_pct`, `put_hsl_pct` |
| V8 | CE SELL + PE BUY + FUT BUY | base2 inside | `call_sell_position`, `put_strike_pct_below` |
| V9 | CE SELL + PE BUY + FUT BUY | base2 inside | `max_put_spot_pct`, counter logic |

---

*Generated from `analyse_bhavcopy_02-01-2026.py` — 20,186 lines | 80+ functions | 11 strategy versions*
