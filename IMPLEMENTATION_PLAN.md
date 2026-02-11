# AlgoTest Clone Implementation Plan

## Project Overview

Build a fully functional web-based options backtesting platform modelled after [AlgoTest](https://algotest.in/backtest) that lets a user:
- Configure NIFTY options strategy parameters through a GUI
- Run a backtest against real historical bhavcopy data stored in `./cleaned_csvs/YYYY-MM-DD.csv` files
- Immediately see detailed performance analytics — equity curve, drawdown, monthly P&L pivot, trade-by-trade log, and key statistics

## Technology Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18 + Vite. Tailwind CSS for layout. Recharts for all charts. Lucide-React for icons. |
| **Backend API** | Python FastAPI. Uvicorn ASGI server. Pandas + NumPy (directly reuses Python strategy functions). |
| **Data Store** | Flat file system. No database. `./cleaned_csvs/`, `./expiryData/`, `./strikeData/`, `./Filter/base2.csv` served from disk. |
| **State Mgmt** | React `useState` + `useReducer` for form state. TanStack Query for API calls and caching. |
| **Auth** | None for local deployment. Optional JWT for multi-user cloud. |

## Project Structure

```
project/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── routers/
│   │   ├── backtest.py            # POST /api/backtest
│   │   ├── expiry.py              # GET /api/expiry
│   │   └── strategies.py          # GET /api/strategies
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

## Phase 1: Backend Foundation (Days 1-3)

### 1. Set up FastAPI project
- Create backend directory structure
- Install dependencies (FastAPI, uvicorn, pandas, numpy)
- Configure CORS middleware
- Set up basic routing

### 2. Implement data loading utilities
- `get_strike_data(symbol, from_date, to_date)` - reads `./strikeData/Nifty_strike_data.csv`
- `load_expiry(index, expiry_type)` - reads expiry CSVs
- `load_base2()` - reads `./Filter/base2.csv`
- `load_bhavcopy(date_str)` - reads daily CSVs with LRU cache (500 files)
- `get_option_price(bhavcopy_df, symbol, instrument, option_type, expiry, strike)` - with ±1 day tolerance

### 3. Create shared utilities
- `build_intervals(filtered_data, spot_adjustment_type, spot_adjustment)` - core re-entry engine
- `compute_analytics(trades_df)` - translates `create_summary_idx()`
- `build_pivot(trades_df, expiry_col)` - translates `getPivotTable()`

## Phase 2: Strategy Engines (Days 4-7)

### 1. Implement V1 Engine (CE Sell + Future Buy)
- Handle all expiry windows (weekly_expiry, weekly_t1, weekly_t2, monthly_expiry, monthly_t1)
- Strike calculation: `round_half_up((spot*(1+pct%))/100)*100`
- Option and future price lookups
- P&L calculations for both CE and Future legs

### 2. Implement remaining engines (V2-V9)
- V2: PE Sell + Future Buy (similar to V1 with PE instead of CE)
- V3: Strike-Breach Re-entry (roll call expiry when spot breaches strike × (1+pct_diff))
- V4: Short Strangle (CE Sell + PE Sell, no Future)
- V5: Protected Sell (CE/PE Sell + optional protective leg)
- V6: Inverse-base (operates OUTSIDE base2 ranges)
- V7: Premium-based strike selection
- V8: HSL (Hard Stop Loss) with daily monitoring
- V9: Counter-based put expiry logic

## Phase 3: Backend API (Days 8-9)

### 1. Create API endpoints
- `POST /api/backtest` - main backtesting endpoint
- `GET /api/expiry?index=NIFTY&type=weekly` - expiry date lists
- `GET /api/strategies` - supported strategies with parameters
- `GET /api/data/dates?index=NIFTY` - date range discovery

### 2. Define API schemas
- Request schema matching all strategy parameters
- Response schema with trades, summary, pivot, and logs

## Phase 4: Frontend Foundation (Days 10-12)

### 1. Set up React project
- Create Vite React project
- Install dependencies (Tailwind CSS, Recharts, Lucide React, TanStack Query)
- Configure Tailwind
- Set up basic component structure

### 2. Create layout components
- Top navigation bar (dark theme with AlgoTest-like styling)
- Strategy type tabs (Weekly & Monthly, Monthly Only, Stocks, Delta Exchange)
- Main configuration panel (two-column layout)

## Phase 5: Configuration Components (Days 13-15)

### 1. Create ConfigPanel.jsx
- Instrument settings card (index dropdown, underlying toggle)
- Legwise settings card (square off, trail SL, add leg button)
- Entry settings card (strategy type, entry/exit times, momentum)
- Adjustment card (spot adjustment types)

### 2. Create LegBuilder.jsx
- Dynamic leg management (up to 4 legs)
- Instrument type (CE/PE/FUT)
- Buy/Sell toggle
- Lots input
- Strike type and value
- Expiry selection
- Stop loss and target inputs

### 3. Strategy presets
- Implement 8 preset cards (CE Sell + FUT Buy, PE Sell + FUT Buy, Short Strangle, etc.)
- Auto-populate form fields when presets are selected

### 4. Date range picker
- From/To date selection
- "All Data" quick button
- Integration with data discovery API

## Phase 6: Results Components (Days 16-18)

### 1. Create ResultsPanel.jsx
- KPI summary cards (Total P&L, Win Rate, Total Trades, CAGR, Max Drawdown, CAR/MDD)
- Equity curve chart (strategy vs spot comparison)
- Drawdown chart (visualize worst periods)
- Monthly P&L heatmap (year × month visualization)
- Trade-by-trade log table (sortable, paginated, exportable)
- Full summary statistics table

### 2. Chart components
- EquityChart.jsx with Recharts ComposedChart
- DrawdownChart.jsx with Recharts AreaChart
- MonthlyHeatmap.jsx with CSS grid and color coding
- TradeLog.jsx with pagination and sorting

## Phase 7: Integration & Polish (Days 19-21)

### 1. API integration
- Connect frontend components to backend API
- Implement loading states and error handling
- Form validation and submission

### 2. Additional features
- Save/Load strategy configurations (localStorage)
- Import/Export .algtst files (JSON format)
- PDF export (browser print functionality)
- Responsive design for tablet devices

### 3. Testing and documentation
- Test with sample data
- Create README with setup instructions
- Document how to add new CSV data
- Document how to add new strategies

## Data File Contracts

### 1. `cleaned_csvs/YYYY-MM-DD.csv`
Critical columns:
- `Instrument`: `OPTIDX` (options) or `FUTIDX` (futures)
- `Symbol`: `NIFTY`, `BANKNIFTY`, `FINNIFTY`, etc.
- `ExpiryDate`: Option/Future expiry in `YYYY-MM-DD`
- `OptionType`: `CE` (Call), `PE` (Put), `XX` (futures)
- `StrikePrice`: Strike as float (e.g., `17500.0`)
- `Close`: End-of-day close price
- `TurnOver`: Daily turnover (filter `TurnOver > 0` for liquid strikes)
- `Date`: Trade date (same as filename)

### 2. `expiryData/NIFTY.csv` (Weekly)
- `Previous Expiry`: Previous weekly expiry `YYYY-MM-DD`
- `Current Expiry`: Current weekly expiry
- `Next Expiry`: Next weekly expiry

### 3. `expiryData/NIFTY_Monthly.csv`
Same structure as weekly but for monthly expiry dates.

### 4. `strikeData/Nifty_strike_data.csv`
- `Ticker`: Symbol name
- `Date`: Trading date
- `Close`: NIFTY spot/cash index close

### 5. `Filter/base2.csv`
- `Start`: Start of trending/directional phase
- `End`: End of that phase
- V1–V5, V7, V8, V9 use INSIDE these ranges
- V6, V8_premium use OUTSIDE (inverse) of these ranges

## Strike Calculation Reference

| Parameter | Formula |
|---|---|
| ATM (50-rounded) | `round_half_up(spot / 50) * 50` |
| ATM (100-rounded) | `round_half_up(spot / 100) * 100` |
| OTM Call at X% | `round_half_up((spot * (1 + X/100)) / 100) * 100` |
| OTM Put at X% | `round_half_up((spot * (1 - X/100)) / 100) * 100` |
| `round_half_up(x)` | `math.floor(x + 0.5)` — rounds 0.5 upward |

## API Endpoint Specifications

### POST /api/backtest
Request:
```json
{
  "strategy_version": "v1",
  "expiry_window": "weekly_expiry",
  "spot_adjustment_type": 0,
  "spot_adjustment": 1.0,
  "call_sell_position": 0.0,
  "put_sell_position": 0.0,
  "put_strike_pct_below": 1.0,
  "protection": false,
  "protection_pct": 1.0,
  "call_premium": true,
  "put_premium": true,
  "premium_multiplier": 1.0,
  "call_hsl_pct": 100,
  "max_put_spot_pct": 0.04,
  "pct_diff": 0.3,
  "from_date": "2019-01-01",
  "to_date": "2026-01-02",
  "index": "NIFTY"
}
```

Response:
```json
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
```

This implementation plan provides a roadmap to build the complete AlgoTest clone with all the required functionality, from backend data processing to frontend visualization.