# Phase 1 Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│  (curl, Postman, Python requests, Web Browser, etc.)            │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP/JSON
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                         API LAYER                                │
│                    FastAPI Application                           │
│                    (src/api/main.py)                            │
│                                                                  │
│  Endpoints:                                                      │
│  • GET  /health                                                  │
│  • GET  /api/v1/strategies                                      │
│  • GET  /api/v1/strategies/{name}                               │
│  • POST /api/v1/execute                                         │
│  • GET  /api/v1/executions/{id}                                 │
│  • GET  /api/v1/executions                                      │
└────────────────┬────────────────────────┬───────────────────────┘
                 │                        │
                 │ Strategy Registry      │ Execution Tracking
                 ▼                        ▼
┌────────────────────────────┐  ┌────────────────────────────────┐
│   STRATEGY LAYER           │  │   DATABASE LAYER               │
│                            │  │   (SQLite)                     │
│  StrategyInterface         │  │                                │
│  (src/strategies/base.py)  │  │  New Tables:                   │
│                            │  │  • strategy_registry           │
│  Implementations:          │  │  • execution_runs              │
│  • CallSellFutureBuy       │  │  • execution_results           │
│    (call_sell_future_buy.py)  │  • parameter_cache             │
│                            │  │  • db_metadata                 │
│  Methods:                  │  │                                │
│  • get_name()              │  │  Existing Tables:              │
│  • get_description()       │  │  • cleaned_csvs (26GB)         │
│  • get_parameter_schema()  │  │  • expiry_data                 │
│  • validate_parameters()   │  │  • strike_data                 │
│  • execute()               │  │  • filter_data                 │
└────────────┬───────────────┘  └────────────────────────────────┘
             │                              ▲
             │ Data Access                  │
             ▼                              │
┌─────────────────────────────────────────┐│
│   DATA PROVIDER LAYER                   ││
│   (src/data/provider.py)                ││
│                                          ││
│  Methods:                                ││
│  • get_cleaned_data()                   ││
│  • get_data_for_date()                  ││
│  • get_strike_data()                    ││
│  • get_expiry_data()                    ││
│  • get_filter_data()                    ││
│  • get_trading_dates()                  ││
└─────────────────────────────────────────┘│
             │                              │
             └──────────────────────────────┘
```

## Request Flow

### 1. Execute Strategy Request

```
Client
  │
  │ POST /api/v1/execute
  │ {
  │   "strategy_name": "call_sell_future_buy_weekly",
  │   "parameters": {...}
  │ }
  ▼
FastAPI (main.py)
  │
  ├─► Validate strategy exists
  │
  ├─► Get/Register strategy in DB
  │   └─► INSERT INTO strategy_registry
  │
  ├─► Create execution record
  │   └─► INSERT INTO execution_runs (status='running')
  │
  ├─► Get strategy instance
  │   └─► STRATEGIES["call_sell_future_buy_weekly"]
  │
  ├─► Validate parameters
  │   └─► strategy.validate_parameters(params)
  │
  ├─► Execute strategy
  │   │
  │   └─► CallSellFutureBuyStrategy.execute()
  │       │
  │       ├─► Load data via DataProvider
  │       │   ├─► get_strike_data("NIFTY")
  │       │   ├─► get_expiry_data("NIFTY", "weekly")
  │       │   ├─► get_expiry_data("NIFTY", "monthly")
  │       │   └─► get_filter_data("base2")
  │       │
  │       ├─► Process each expiry period
  │       │   ├─► Calculate intervals
  │       │   └─► Process each trade
  │       │       ├─► Get bhavcopy data
  │       │       ├─► Calculate strikes
  │       │       ├─► Get entry/exit prices
  │       │       └─► Calculate P&L
  │       │
  │       └─► Return StrategyResult
  │           ├─► data: DataFrame
  │           ├─► metadata: Dict
  │           ├─► execution_time_ms
  │           └─► row_count
  │
  ├─► Save results
  │   └─► INSERT INTO execution_results
  │
  ├─► Update execution record
  │   └─► UPDATE execution_runs (status='completed')
  │
  └─► Return response
      {
        "execution_id": 1,
        "status": "completed",
        "duration_ms": 15234,
        "row_count": 156
      }
```

### 2. Get Results Request

```
Client
  │
  │ GET /api/v1/executions/1
  ▼
FastAPI (main.py)
  │
  ├─► Query execution info
  │   └─► SELECT FROM execution_runs WHERE id=1
  │
  ├─► Query result data
  │   └─► SELECT FROM execution_results WHERE execution_id=1
  │
  └─► Return response
      {
        "execution_id": 1,
        "status": "completed",
        "data": [...],
        "metadata": {...},
        "row_count": 156
      }
```

## Data Flow

### Strategy Execution Data Flow

```
┌──────────────────┐
│  Strike Data     │
│  (strikeData/    │
│   NIFTY.csv)     │
└────────┬─────────┘
         │
         ├─► DataProvider.get_strike_data()
         │
         ▼
┌──────────────────┐      ┌──────────────────┐
│  Expiry Data     │      │  Filter Data     │
│  (expiryData/    │      │  (Filter/        │
│   NIFTY.csv)     │      │   base2.csv)     │
└────────┬─────────┘      └────────┬─────────┘
         │                         │
         ├─► DataProvider.get_expiry_data()
         │                         │
         │                         ├─► DataProvider.get_filter_data()
         │                         │
         ▼                         ▼
┌─────────────────────────────────────────────┐
│         Strategy Execution Logic             │
│      (CallSellFutureBuyStrategy)            │
│                                              │
│  1. Filter dates by base2 ranges            │
│  2. For each weekly expiry:                 │
│     - Calculate intervals                   │
│     - For each interval:                    │
│       • Get entry/exit spot prices          │
│       • Calculate strikes                   │
│       • Get bhavcopy data                   │
│       • Calculate P&L                       │
└────────┬────────────────────────────────────┘
         │
         ├─► DataProvider.get_data_for_date()
         │
         ▼
┌──────────────────┐
│  Cleaned CSVs    │
│  (cleaned_csvs/  │
│   YYYY-MM-DD.csv)│
└────────┬─────────┘
         │
         │ Query by Date, Symbol, Strike, Expiry
         │
         ▼
┌─────────────────────────────────────────────┐
│         SQLite Database                      │
│         (bhavcopy_data.db)                  │
│                                              │
│  Table: cleaned_csvs                         │
│  • Date, Symbol, Instrument                  │
│  • StrikePrice, OptionType                   │
│  • Open, High, Low, Close                    │
│  • ExpiryDate, TurnOver, OpenInterest       │
└─────────────────────────────────────────────┘
```

## Component Responsibilities

### API Layer (src/api/main.py)
- **Responsibilities:**
  - HTTP request handling
  - Request validation (Pydantic models)
  - Strategy registration
  - Execution orchestration
  - Result storage
  - Response formatting
- **Dependencies:**
  - FastAPI framework
  - Strategy implementations
  - Database connection

### Strategy Layer (src/strategies/)
- **Responsibilities:**
  - Strategy logic implementation
  - Parameter validation
  - Data processing
  - P&L calculation
  - Result generation
- **Dependencies:**
  - Data Provider
  - Pandas for data manipulation

### Data Provider Layer (src/data/provider.py)
- **Responsibilities:**
  - Database abstraction
  - Data loading from files
  - Date parsing
  - Connection management
- **Dependencies:**
  - SQLite database
  - CSV files (strikeData, expiryData, Filter)

### Database Layer
- **Responsibilities:**
  - Data persistence
  - Execution tracking
  - Result storage
  - Query optimization
- **Tables:**
  - **strategy_registry**: Strategy metadata
  - **execution_runs**: Execution history
  - **execution_results**: Result data
  - **parameter_cache**: Result caching
  - **cleaned_csvs**: Market data (existing)

## Technology Stack

```
┌─────────────────────────────────────────────┐
│              Application Layer               │
│                                              │
│  • Python 3.8+                               │
│  • FastAPI 0.109.0                           │
│  • Pydantic 2.5.3                            │
│  • Uvicorn 0.27.0 (ASGI server)              │
└─────────────────────────────────────────────┘
                     │
┌─────────────────────────────────────────────┐
│            Data Processing Layer             │
│                                              │
│  • Pandas 2.1.4                              │
│  • NumPy 1.26.3                              │
│  • Python-dateutil 2.8.2                     │
└─────────────────────────────────────────────┘
                     │
┌─────────────────────────────────────────────┐
│              Database Layer                  │
│                                              │
│  • SQLite 3.x                                │
│  • SQLAlchemy 2.0.25 (future use)           │
└─────────────────────────────────────────────┘
```

## Deployment Architecture (Phase 1)

```
┌─────────────────────────────────────────────────────────┐
│                    Single Server                         │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │         Uvicorn (ASGI Server)                  │    │
│  │         Port: 8000                             │    │
│  │                                                 │    │
│  │  ┌──────────────────────────────────────┐     │    │
│  │  │   FastAPI Application                │     │    │
│  │  │   (src/api/main.py)                  │     │    │
│  │  └──────────────────────────────────────┘     │    │
│  └────────────────────────────────────────────────┘    │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │   SQLite Database                              │    │
│  │   (bhavcopy_data.db - 26GB)                   │    │
│  └────────────────────────────────────────────────┘    │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │   Data Files                                   │    │
│  │   • cleaned_csvs/ (6362 files)                │    │
│  │   • strikeData/                               │    │
│  │   • expiryData/                               │    │
│  │   • Filter/                                   │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## Security Considerations (Phase 1)

```
┌─────────────────────────────────────────────┐
│              Security Layers                 │
│                                              │
│  1. Network Level                            │
│     • Localhost only (127.0.0.1)             │
│     • No external access                     │
│                                              │
│  2. Application Level                        │
│     • Parameter validation                   │
│     • SQL injection prevention (parameterized)│
│     • Input sanitization                     │
│                                              │
│  3. Data Level                               │
│     • Read-only access to cleaned_csvs       │
│     • Isolated execution tables              │
│                                              │
│  Future (Phase 3):                           │
│     • JWT authentication                     │
│     • Role-based access control              │
│     • API rate limiting                      │
└─────────────────────────────────────────────┘
```

## Performance Characteristics

### Execution Time
- **Strategy Execution**: 10-20 seconds (data processing)
- **API Overhead**: <100ms (request handling)
- **Database Write**: <50ms (result storage)
- **Total**: ~10-20 seconds per execution

### Resource Usage
- **Memory**: ~500MB (Pandas DataFrames)
- **CPU**: Single core (synchronous execution)
- **Disk I/O**: Read-heavy (CSV files + SQLite)
- **Database**: 26GB (existing) + <10MB (new tables)

### Scalability (Phase 1)
- **Concurrent Users**: 1-2 (synchronous execution)
- **Requests/Second**: <1 (long-running strategies)
- **Database Connections**: 1 per request
- **Bottleneck**: Synchronous strategy execution

**Note:** Phase 2 will add async execution and caching to improve scalability.
