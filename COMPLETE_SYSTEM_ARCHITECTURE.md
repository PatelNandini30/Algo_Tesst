# Complete System Architecture & Workflow

## 🎯 System Overview
This is an **Options Trading Backtesting Platform** - an AlgoTest clone that allows users to backtest options trading strategies using historical NSE (National Stock Exchange) data.

---

## 📊 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     USER INTERFACE                          │
│              (React Frontend - Port 3000)                   │
│  - Strategy Selection                                       │
│  - Parameter Configuration                                  │
│  - Results Visualization                                    │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP/REST API
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  BACKEND API SERVER                         │
│              (FastAPI - Port 8000)                          │
│  - Strategy Router                                          │
│  - Backtest Router                                          │
│  - Expiry Router                                            │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  STRATEGY ENGINES                           │
│  - V1-V10 Strategy Implementations                          │
│  - Generic Multi-Leg Engine                                 │
│  - Position Management                                      │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  DATA LAYER                                 │
│  - Bhavcopy Database (SQLite)                              │
│  - CSV Files (6362 files, 2000-2026)                       │
│  - Strike Data Retrieval                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 Folder Structure & Components

### 1. **ROOT DIRECTORY**
```
Algo_Test_Software/
├── backend/              # Backend API & Strategy Engines
├── frontend/             # React Frontend Application
├── cleaned_csvs/         # Historical Market Data (6362 CSV files)
├── src/                  # Additional utilities (cache, etc.)
├── bhavcopy_data.db      # SQLite Database
└── [utility scripts]     # Data processing & testing scripts
```

---

## 🔧 BACKEND (`/backend`)

### **Purpose**: Core backtesting engine and API server

### **Structure**:
```
backend/
├── main.py                    # FastAPI app entry point
├── start_server.py            # Server startup script
├── base.py                    # Core data retrieval functions
├── analytics.py               # Performance metrics calculation
├── backtest_manager.py        # Backtest orchestration
├── algotest_engine.py         # Legacy engine wrapper
├── strategy_engine.py         # Strategy execution coordinator
│
├── routers/                   # API Endpoints
│   ├── backtest.py           # POST /api/backtest, /api/algotest-backtest
│   ├── strategies.py         # GET /api/strategies, /api/data/dates
│   └── expiry.py             # GET /api/expiry
│
├── engines/                   # Strategy Implementations
│   ├── v1_ce_fut.py          # CE Sell + Future Buy
│   ├── v2_pe_fut.py          # PE Sell + Future Buy
│   ├── v3_strike_breach.py   # Strike Breach Strategy
│   ├── v4_strangle.py        # Short Strangle
│   ├── v5_protected.py       # Protected Strategies
│   ├── v6_inverse_strangle.py # Inverse Strangle
│   ├── v7_premium.py         # Premium-Based Strategy
│   ├── v8_hsl.py             # Hard Stop Loss Strategy
│   ├── v8_ce_pe_fut.py       # Hedged Bull Strategy
│   ├── v9_counter.py         # Counter-Expiry Strategy
│   ├── v10_days_before_expiry.py # Days-Based Entry/Exit
│   ├── generic_multi_leg.py  # Dynamic Multi-Leg Engine
│   └── fixengine.py          # Engine fixes & utilities
│
└── strategies/                # Strategy Type Definitions
    ├── strategy_types.py     # Enums & Data Classes
    └── generic_multi_leg_engine.py
```

### **Key Backend Files**:

#### **main.py**
- FastAPI application initialization
- CORS middleware configuration
- Router registration
- Health check endpoints

#### **base.py**
- `get_strike_data()`: Retrieves option chain data for a date
- `get_future_data()`: Gets futures data
- `get_expiry_dates()`: Calculates expiry dates
- Database query functions

#### **analytics.py**
- `calculate_summary_stats()`: Computes PnL, CAGR, drawdown
- `generate_pivot_table()`: Creates performance breakdowns
- Win rate, expectancy, recovery factor calculations

#### **routers/backtest.py**
- Main backtest endpoint
- Request validation
- Strategy function mapping
- Leg combination validation
- Response formatting

#### **engines/v1_ce_fut.py** (Example Strategy)
```python
# Strategy: Sell Call Option + Buy Future
# Entry: X days before expiry
# Exit: On expiry or adjustment trigger
# Legs: 
#   - Sell CE (Call Option)
#   - Buy FUT (Future)
```

---

## 🎨 FRONTEND (`/frontend`)

### **Purpose**: User interface for strategy configuration and results visualization

### **Structure**:
```
frontend/
├── src/
│   ├── App.jsx                      # Main app component
│   ├── main.jsx                     # React entry point
│   │
│   └── components/
│       ├── StrategyBuilder.jsx      # Main backtest interface ✅ ACTIVE
│       ├── AlgoTestBacktest.jsx     # Alternative UI (dynamic)
│       ├── ResultsPanel.jsx         # Results display
│       ├── ConfigPanel.jsx          # Strategy configuration
│       ├── LegBuilder.jsx           # Leg configuration
│       ├── InstrumentSettings.jsx   # Index/underlying settings
│       ├── EntryExitSettings.jsx    # Entry/exit configuration
│       │
│       ├── analytics/               # Charts & metrics
│       ├── strategy/                # Strategy-specific components
│       └── ui/                      # Reusable UI components
│
├── vite.config.js                   # Vite configuration + proxy
├── package.json                     # Dependencies
└── tailwind.config.js               # Styling configuration
```

### **Key Frontend Components**:

#### **StrategyBuilder.jsx** (Currently Active)
```javascript
// Features:
// - Fetches available strategies from /api/strategies
// - Displays strategy list with descriptions
// - Parameter configuration UI
// - Date range & index selection
// - Calls /api/backtest endpoint
// - Shows ResultsPanel on completion
```

#### **ResultsPanel.jsx**
```javascript
// Displays:
// - Trade-by-trade breakdown
// - Summary statistics (PnL, CAGR, Drawdown)
// - Performance charts
// - Pivot tables (monthly/yearly breakdown)
// - Export functionality
```

---

## 💾 DATA LAYER

### **1. Bhavcopy Database (`bhavcopy_data.db`)**
SQLite database containing:
- **Table: `bhavcopy`**
  - Columns: date, symbol, expiry, strike, option_type, open, high, low, close, volume, oi
  - Indexed for fast queries
  - Contains options and futures data

### **2. CSV Files (`/cleaned_csvs`)**
- 6,362 CSV files (one per trading day)
- Date range: 2000-06-12 to 2026-01-02
- Format: `YYYY-MM-DD.csv`
- Contains: NIFTY, BANKNIFTY, SENSEX options & futures data

### **3. Data Builder (`bhavcopy_db_builder.py`)**
```python
# Purpose: Build SQLite database from CSV files
# Process:
# 1. Scan cleaned_csvs directory
# 2. Parse each CSV file
# 3. Insert into bhavcopy table
# 4. Create indexes for performance
```

---

## 🔄 Complete Workflow

### **User Journey**:

```
1. USER OPENS BROWSER
   └─> http://localhost:3000
   
2. FRONTEND LOADS
   └─> StrategyBuilder.jsx renders
   └─> Calls GET /api/strategies
   
3. BACKEND RESPONDS
   └─> Returns list of 9 strategies with parameters
   
4. USER SELECTS STRATEGY
   └─> Example: "CE Sell + Future Buy (V1)"
   └─> Frontend displays strategy parameters
   
5. USER CONFIGURES PARAMETERS
   ├─> Index: NIFTY
   ├─> Date Range: 2024-01-01 to 2024-12-31
   ├─> Expiry Window: Weekly
   ├─> Call Sell Position: 0% (ATM)
   ├─> Spot Adjustment: None
   └─> Other strategy-specific params
   
6. USER CLICKS "RUN BACKTEST"
   └─> Frontend sends POST /api/backtest
   └─> Payload includes:
       {
         strategy: "v1_ce_fut",
         index: "NIFTY",
         date_from: "2024-01-01",
         date_to: "2024-12-31",
         call_sell: true,
         future_buy: true,
         ...parameters
       }
   
7. BACKEND PROCESSES REQUEST
   ├─> Validates request (leg combinations, dates)
   ├─> Maps strategy to engine function
   ├─> Calls run_v1_main1(params)
   
8. STRATEGY ENGINE EXECUTES
   ├─> Loop through each trading day
   ├─> For each expiry cycle:
   │   ├─> Entry Logic:
   │   │   ├─> Get spot price
   │   │   ├─> Calculate strike (ATM + offset)
   │   │   ├─> Get option premium from database
   │   │   ├─> Get future price
   │   │   └─> Record entry prices
   │   │
   │   ├─> Daily Monitoring:
   │   │   ├─> Check spot adjustment triggers
   │   │   ├─> Check stop loss conditions
   │   │   └─> Update position values
   │   │
   │   └─> Exit Logic:
   │   │   ├─> On expiry or trigger
   │   │   ├─> Get exit prices
   │   │   ├─> Calculate PnL per leg
   │   │   └─> Calculate net PnL
   │   
   └─> Collect all trades in DataFrame
   
9. ANALYTICS CALCULATION
   ├─> Calculate summary statistics:
   │   ├─> Total PnL
   │   ├─> Win Rate
   │   ├─> CAGR (Options vs Spot)
   │   ├─> Maximum Drawdown
   │   ├─> Expectancy
   │   └─> Recovery Factor
   │
   └─> Generate pivot tables:
       ├─> Monthly breakdown
       └─> Yearly breakdown
   
10. BACKEND RETURNS RESPONSE
    └─> JSON with:
        ├─> status: "success"
        ├─> meta: {strategy, total_trades, date_range}
        ├─> trades: [{entry_date, exit_date, pnl, ...}]
        ├─> summary: {total_pnl, cagr, max_dd, ...}
        └─> pivot: {headers, rows}
   
11. FRONTEND DISPLAYS RESULTS
    └─> ResultsPanel.jsx renders:
        ├─> Trade table with all positions
        ├─> Summary cards (PnL, CAGR, Drawdown)
        ├─> Equity curve chart
        ├─> Monthly/Yearly pivot tables
        └─> Export buttons (CSV download)
```

---

## 🎯 Strategy Engine Logic (Detailed)

### **Example: V1 CE Sell + Future Buy**

```python
def run_v1_main1(params):
    """
    Strategy: Sell Call Option + Buy Future
    Entry: Weekly expiry basis
    Exit: On expiry
    """
    
    # 1. INITIALIZATION
    index = params['index']  # NIFTY
    from_date = params['from_date']
    to_date = params['to_date']
    call_sell_position = params['call_sell_position']  # % OTM
    
    trades = []
    position = None
    
    # 2. LOOP THROUGH DATES
    for current_date in date_range(from_date, to_date):
        
        # 3. CHECK IF EXPIRY DAY
        if is_expiry(current_date):
            
            # 4. EXIT EXISTING POSITION
            if position:
                exit_data = get_strike_data(current_date, index)
                position['exit_date'] = current_date
                position['exit_spot'] = exit_data['spot']
                position['call_exit_price'] = get_option_price(...)
                position['future_exit_price'] = get_future_price(...)
                
                # Calculate PnL
                position['call_pnl'] = (entry - exit) * lot_size  # Sell
                position['future_pnl'] = (exit - entry) * lot_size  # Buy
                position['net_pnl'] = call_pnl + future_pnl
                
                trades.append(position)
                position = None
        
        # 5. CHECK ENTRY CONDITIONS
        if is_entry_day(current_date) and not position:
            
            # 6. CREATE NEW POSITION
            entry_data = get_strike_data(current_date, index)
            spot = entry_data['spot']
            
            # Calculate strike
            call_strike = calculate_strike(spot, call_sell_position)
            
            # Get prices
            call_premium = get_option_price(current_date, call_strike, 'CE')
            future_price = get_future_price(current_date)
            
            position = {
                'entry_date': current_date,
                'entry_spot': spot,
                'call_strike': call_strike,
                'call_entry_price': call_premium,
                'future_entry_price': future_price,
                'call_expiry': get_next_expiry(current_date)
            }
        
        # 7. DAILY MONITORING (if position exists)
        if position:
            # Check spot adjustment triggers
            current_spot = get_spot_price(current_date)
            if spot_adjustment_triggered(current_spot, position['entry_spot']):
                # Exit position early
                # ... (similar to expiry exit)
    
    # 8. CONVERT TO DATAFRAME
    df = pd.DataFrame(trades)
    
    # 9. CALCULATE ANALYTICS
    summary = calculate_summary_stats(df)
    pivot = generate_pivot_table(df)
    
    return df, summary, pivot
```

---

## 🔌 API Endpoints

### **1. GET /api/strategies**
```json
Response:
{
  "strategies": [
    {
      "name": "CE Sell + Future Buy (V1)",
      "version": "v1_ce_fut",
      "description": "Sell Call Option and Buy Future",
      "parameters": {
        "call_sell_position": "Percentage OTM for call strike",
        "spot_adjustment_type": "Type of spot adjustment",
        ...
      },
      "defaults": {
        "call_sell_position": 0.0,
        "call_sell": true,
        "future_buy": true,
        ...
      }
    },
    ...
  ]
}
```

### **2. POST /api/backtest**
```json
Request:
{
  "strategy": "v1_ce_fut",
  "index": "NIFTY",
  "date_from": "2024-01-01",
  "date_to": "2024-12-31",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 0.0,
  "call_sell": true,
  "put_sell": false,
  "future_buy": true,
  "spot_adjustment_type": "None",
  "spot_adjustment": 1.0
}

Response:
{
  "status": "success",
  "meta": {
    "strategy": "CE Sell + Future Buy",
    "total_trades": 52,
    "date_range": "2024-01-01 to 2024-12-31"
  },
  "trades": [
    {
      "entry_date": "2024-01-03",
      "exit_date": "2024-01-10",
      "entry_spot": 21500,
      "exit_spot": 21650,
      "call_strike": 21500,
      "call_entry_price": 150,
      "call_exit_price": 50,
      "call_pnl": 5000,
      "future_entry_price": 21500,
      "future_exit_price": 21650,
      "future_pnl": 7500,
      "net_pnl": 12500,
      "cumulative": 12500
    },
    ...
  ],
  "summary": {
    "total_pnl": 125000,
    "count": 52,
    "win_pct": 65.4,
    "cagr_options": 18.5,
    "max_dd_pct": -12.3,
    ...
  },
  "pivot": {
    "headers": ["Month", "Trades", "PnL", "Win%"],
    "rows": [
      ["2024-01", 4, 25000, 75.0],
      ...
    ]
  }
}
```

### **3. GET /api/data/dates**
```json
Response:
{
  "min_date": "2000-06-12",
  "max_date": "2026-01-02"
}
```

### **4. GET /api/expiry**
```json
Request: ?index=NIFTY&type=weekly

Response:
{
  "index": "NIFTY",
  "type": "weekly",
  "expiries": [
    "2024-01-04",
    "2024-01-11",
    "2024-01-18",
    ...
  ]
}
```

---

## 🚀 How to Run the System

### **1. Start Backend**
```bash
cd backend
python start_server.py
# Server runs on http://localhost:8000
# API docs at http://localhost:8000/docs
```

### **2. Start Frontend**
```bash
cd frontend
npm run dev
# Frontend runs on http://localhost:3000
# Proxies /api requests to backend
```

### **3. Access Application**
```
Open browser: http://localhost:3000
```

---

## 📊 Available Strategies

| Strategy | Version | Description | Legs |
|----------|---------|-------------|------|
| CE Sell + Future Buy | v1_ce_fut | Sell Call + Buy Future | CE Sell, FUT Buy |
| PE Sell + Future Buy | v2_pe_fut | Sell Put + Buy Future | PE Sell, FUT Buy |
| Strike Breach | v3_strike_breach | Breach-based entry | CE Sell, FUT Buy |
| Short Strangle | v4_strangle | Sell Call + Sell Put | CE Sell, PE Sell |
| Protected CE Sell | v5_call | Sell Call + Buy Call | CE Sell, CE Buy |
| Protected PE Sell | v5_put | Sell Put + Buy Put | PE Sell, PE Buy |
| Inverse Strangle | v6_inverse_strangle | Buy Call + Buy Put | CE Buy, PE Buy |
| Premium-Based | v7_premium | Premium target based | CE Sell, PE Sell |
| Hedged Bull | v8_ce_pe_fut | CE Sell + PE Buy + FUT | CE Sell, PE Buy, FUT |
| Counter-Expiry | v9_counter | Dynamic put expiry | CE Sell, PE Buy, FUT |
| Days Before Expiry | v10_days_before_expiry | Flexible entry/exit | Configurable |

---

## 🔑 Key Concepts

### **Expiry Windows**
- `weekly_expiry`: Current week expiry
- `weekly_t1`: Next week expiry
- `weekly_t2`: Week after next
- `monthly_expiry`: Current month expiry
- `monthly_t1`: Next month expiry

### **Strike Selection**
- `ATM`: At The Money (closest to spot)
- `OTM`: Out of The Money (above spot for CE, below for PE)
- `ITM`: In The Money (below spot for CE, above for PE)
- Percentage offset: e.g., 1% OTM = spot * 1.01

### **Spot Adjustment**
- `None`: No adjustment
- `Rises`: Exit if spot rises by X%
- `Falls`: Exit if spot falls by X%
- `RisesOrFalls`: Exit if spot moves X% in either direction

### **PnL Calculation**
```
Call Sell PnL = (Entry Premium - Exit Premium) × Lot Size
Put Sell PnL = (Entry Premium - Exit Premium) × Lot Size
Future Buy PnL = (Exit Price - Entry Price) × Lot Size
Net PnL = Sum of all leg PnLs
```

---

## 🛠️ Utility Scripts

- `bhavcopy_db_builder.py`: Build database from CSVs
- `analyse_bhavcopy_02-01-2026.py`: Analyze specific date data
- `check_files.py`: Verify CSV file integrity
- `diagnose_strikes.py`: Debug strike selection
- `test_api_phase2.py`: API endpoint testing
- `Diagnose_backtest.py`: Debug backtest execution

---

## 📈 Performance Metrics

### **Summary Statistics**
- **Total PnL**: Net profit/loss across all trades
- **Win %**: Percentage of profitable trades
- **Avg Win/Loss**: Average profit per winning/losing trade
- **Expectancy**: Expected value per trade
- **CAGR**: Compound Annual Growth Rate
- **Max Drawdown**: Largest peak-to-trough decline
- **Recovery Factor**: Total PnL / Max Drawdown
- **CAR/MDD**: CAGR / Max Drawdown ratio

### **Trade Metrics**
- Entry/Exit dates and prices
- Strike prices and expiries
- Individual leg PnLs
- Cumulative PnL
- Drawdown at each trade

---

## 🎯 System Capabilities

✅ **Supported**:
- Multiple strategy types (10 engines)
- Historical backtesting (2000-2026)
- Multi-leg strategies (up to 4 legs)
- Dynamic strike selection
- Spot adjustment triggers
- Performance analytics
- Export to CSV
- Pivot table analysis

❌ **Not Supported**:
- Live trading
- Real-time data
- Intraday strategies (limited)
- Order slippage simulation
- Transaction costs
- Multiple indices simultaneously

---

This is a comprehensive options backtesting platform designed to test and validate trading strategies using 26 years of historical NSE data.
