# Complete System Files and Functions Documentation

## Table of Contents
1. [Core Base Functions](#core-base-functions)
2. [Database Management](#database-management)
3. [Strategy Engines](#strategy-engines)
4. [API Routers](#api-routers)
5. [Analytics Functions](#analytics-functions)
6. [Filter System](#filter-system)

---

## Core Base Functions

### File: `backend/base.py`
**Purpose**: Core utility functions for data loading, strike calculations, and analytics

#### Data Loading Functions

1. **`get_strike_data(symbol, from_date, to_date)`**
   - Loads historical spot price data from strikeData directory
   - Handles multiple date formats automatically
   - Returns DataFrame with Date and Close columns
   - Used by all strategy engines to get spot prices

2. **`load_expiry(index, expiry_type)`**
   - Loads expiry dates (weekly or monthly) from expiryData directory
   - Parses Previous, Current, and Next Expiry columns
   - Returns sorted DataFrame by Current Expiry
   - Critical for determining option contract expiries

3. **`load_base2()`**
   - Loads base2 filter data from Filter/base2.csv
   - Contains Start and End date ranges for filtering trading periods
   - Used to exclude certain date ranges from backtests
   - Returns sorted DataFrame by Start date

4. **`load_bhavcopy(date_str)`**
   - Loads daily market data (bhavcopy) for a specific date
   - Uses LRU cache (500 files) for performance
   - Returns instrument data: Symbol, Strike, Close, TurnOver, etc.
   - Core function for getting option/future prices

#### Strike Calculation Functions

5. **`round_half_up(x)`**
   - Rounds half values up (0.5 → 1, 1.5 → 2)
   - Used in strike price calculations

6. **`round_to_50(value)`**
   - Rounds to nearest 50 (for NIFTY strikes)
   - Example: 24,375 → 24,400

7. **`get_atm_strike(spot, available_strikes)`**
   - Finds At-The-Money strike closest to spot price
   - Returns nearest available strike

8. **`get_otm_strike(spot, available_strikes, otm_distance, option_type)`**
   - Calculates Out-of-The-Money strike
   - For CE: strikes above spot
   - For PE: strikes below spot

9. **`get_itm_strike(spot, available_strikes, itm_distance, option_type)`**
   - Calculates In-The-Money strike
   - For CE: strikes below spot
   - For PE: strikes above spot

10. **`calculate_strike_from_selection(spot_price, strike_interval, selection, option_type)`**
    - AlgoTest-style strike selection (ATM, ITM1, ITM2, OTM1, OTM2, etc.)
    - Handles NIFTY (50 interval) and BANKNIFTY (100 interval)
    - Example: Spot=24,350, Selection=OTM2, Type=CE → Strike=24,500

11. **`get_strike_interval(index)`**
    - Returns strike interval for index
    - NIFTY: 50, BANKNIFTY: 100, FINNIFTY: 50, etc.

#### Price Retrieval Functions

12. **`get_option_price(bhavcopy_df, symbol, instrument, option_type, expiry, strike)`**
    - Extracts option price from bhavcopy DataFrame
    - Handles exact match and ±1 day tolerance
    - Returns (Close price, TurnOver)

13. **`get_option_premium_from_db(date, index, strike, option_type, expiry, db_path)`**
    - Gets option premium directly from database
    - Used for database-based backtests
    - Returns Close price or None

14. **`get_future_price_from_db(date, index, expiry, db_path)`**
    - Gets future price from database
    - Returns Close price or None

15. **`get_spot_price_from_db(date, index, db_path)`**
    - Gets spot price from database
    - Returns Close price

#### Spot Adjustment Functions

16. **`apply_spot_adjustment(spot, mode, value)`**
    - Applies spot adjustment based on mode:
      - Mode 0: Unadjusted
      - Mode 1: Spot rises by X%
      - Mode 2: Spot falls by X%
      - Mode 3: Volatility assumption
      - Mode 4: Custom shift
    - Used for strike selection with adjusted spot

17. **`calculate_strike_offset(spot, offset_type, offset_value)`**
    - Calculates strike with offset from spot
    - Supports 'percent' or 'points' offset

#### Re-entry Engine

18. **`build_intervals(filtered_data, spot_adjustment_type, spot_adjustment)`**
    - Core re-entry logic used by all strategies
    - Creates trading intervals based on spot movement triggers
    - Returns list of (start_date, end_date) tuples
    - Handles 3 modes:
      - Mode 1: Re-enter when spot rises by X%
      - Mode 2: Re-enter when spot falls by X%
      - Mode 3: Re-enter when spot moves by X% (either direction)

#### Analytics Functions

19. **`compute_analytics(df)`**
    - Calculates comprehensive backtest statistics
    - Adds Cumulative, Peak, DD (Drawdown), %DD columns
    - Returns (DataFrame, summary_dict) with:
      - Total P&L, Win%, Avg Win/Loss
      - CAGR, Max Drawdown, CAR/MDD
      - Expectancy, Recovery Factor

20. **`build_pivot(df, expiry_col)`**
    - Creates monthly P&L pivot table
    - Rows: Years, Columns: Months
    - Returns dict with headers and rows

#### Date/Expiry Functions

21. **`calculate_trading_days_before_expiry(expiry_date, days_before, trading_calendar_df)`**
    - Calculates entry date by counting back trading days from expiry
    - Critical for DTE (Days To Expiry) strategies
    - Example: Expiry=14-Jan, DTE=2 → Entry=10-Jan (skips weekends)

22. **`get_trading_calendar(from_date, to_date, db_path)`**
    - Gets all trading dates from database
    - Returns DataFrame with date column

23. **`get_expiry_dates(symbol, expiry_type, from_date, to_date)`**
    - Gets expiry dates for symbol and type
    - Supports date filtering

24. **`get_custom_expiry_dates(symbol, expiry_day_of_week, from_date, to_date)`**
    - Gets custom expiry dates based on day of week
    - 0=Monday, 1=Tuesday, etc.

25. **`get_next_expiry_date(start_date, expiry_day_of_week)`**
    - Finds next expiry date from start date

26. **`get_monthly_expiry_date(year, month, expiry_day_of_week)`**
    - Gets last occurrence of specified day in month

27. **`calculate_intrinsic_value(spot, strike, option_type)`**
    - Calculates option intrinsic value at expiry
    - CE: max(0, Spot - Strike)
    - PE: max(0, Strike - Spot)

---

## Database Management

### File: `bhavcopy_db_builder.py`
**Purpose**: Builds and manages SQLite database from CSV files

#### Classes

1. **`IngestionStats`**
   - Tracks ingestion statistics
   - Properties: files_processed, rows_inserted, rows_skipped, errors
   - Method: `duration()` - calculates processing time

2. **`BhavcopyDatabaseBuilder`**
   - Main database builder class
   - Manages database creation, data ingestion, and indexing

#### Key Methods

1. **`create_database(force=False)`**
   - Creates database schema with tables:
     - `bhavcopy`: Main market data
     - `expiry_data`: Expiry dates
     - `strike_data`: Spot prices
     - `filter_data`: Base2 filter ranges
     - `file_metadata`: Tracks processed files
   - Creates indices for fast queries
   - If force=True, drops existing database

2. **`normalize_csv_data(df)`**
   - Normalizes CSV data to standard format
   - Handles date parsing, column mapping
   - Converts data types

3. **`validate_business_key(df)`**
   - Validates unique business keys
   - Business key: (date, symbol, expiry, strike, option_type)
   - Returns (valid_df, duplicate_records)

4. **`insert_cleaned_data(df)`**
   - Inserts data into bhavcopy table
   - Uses UPSERT logic (INSERT OR REPLACE)
   - Returns (inserted_count, skipped_count)

5. **`ingest_csv_file(csv_path)`**
   - Ingests single CSV file
   - Checks if already processed using file hash
   - Returns ingestion result dict

6. **`ingest_directory(csv_directory, pattern='*.csv')`**
   - Ingests all CSV files in directory
   - Processes files in parallel batches
   - Returns list of results

7. **`build_expiry_data()`**
   - Builds expiry_data table from expiryData/*.csv
   - Handles weekly and monthly expiries

8. **`build_strike_data()`**
   - Builds strike_data table from strikeData/*.csv
   - Stores spot price history

9. **`build_filter_data()`**
   - Builds filter_data table from Filter/base2.csv
   - Stores date range filters

10. **`get_database_stats()`**
    - Returns database statistics
    - Row counts, date ranges, file counts

11. **`print_database_stats()`**
    - Prints formatted database statistics

---

## Strategy Engines

### File: `backend/algotest_engine.py`
**Purpose**: Main strategy execution engine

#### Functions

1. **`run_backtest(strategy, params)`**
   - Executes backtest for specified strategy
   - Routes to appropriate strategy engine (v1-v10)
   - Returns (DataFrame, summary_dict, pivot_dict)

2. **`format_response(df, summary, pivot)`**
   - Formats backtest results for API response
   - Converts DataFrames to JSON-serializable format

3. **`validate_params(params)`**
   - Validates strategy parameters
   - Checks required fields, data types
   - Returns validated params dict

4. **`get_available_strategies()`**
   - Returns list of available strategies
   - Each strategy has: name, version, description, parameters

### Strategy Engine Files

Located in `backend/engines/`:

1. **`v1_ce_fut.py`** - Call Sell + Future Buy
2. **`v2_pe_fut.py`** - Put Sell + Future Sell
3. **`v3_strike_breach.py`** - Strike Breach Strategy
4. **`v4_strangle.py`** - Strangle Strategy
5. **`v5_protected.py`** - Protected Strategy
6. **`v6_inverse_strangle.py`** - Inverse Strangle
7. **`v7_premium.py`** - Premium-based Strategy
8. **`v8_ce_pe_fut.py`** - CE + PE + Future
9. **`v9_counter.py`** - Counter Strategy
10. **`v10_days_before_expiry.py`** - DTE-based Strategy
11. **`generic_multi_leg.py`** - Generic Multi-Leg Engine
12. **`generic_algotest_engine.py`** - Generic AlgoTest Engine

Each engine implements:
- `run_vX()` function - Main execution logic
- Base2 filter application
- Re-entry logic
- P&L calculation
- Trade recording

---

## API Routers

### File: `backend/routers/backtest.py`
**Purpose**: FastAPI endpoints for backtest execution

#### Models

1. **`BacktestRequest`**
   - Strategy parameters
   - Index, date range, capital
   - Leg configurations

2. **`TradeRecord`**
   - Single trade details
   - Entry/exit dates, strikes, prices, P&L

3. **`SummaryStats`**
   - Backtest summary statistics
   - Total P&L, win rate, CAGR, drawdown

4. **`PivotData`**
   - Monthly P&L pivot table
   - Headers and rows

5. **`BacktestResponse`**
   - Complete backtest response
   - Status, meta, trades, summary, pivot

#### Endpoints

1. **`POST /backtest`**
   - Runs backtest with specified parameters
   - Returns BacktestResponse

2. **`POST /algotest/backtest`**
   - AlgoTest-compatible backtest endpoint
   - Same as /backtest but with AlgoTest parameter mapping

3. **`POST /dynamic/backtest`**
   - Dynamic multi-leg strategy backtest
   - Accepts custom leg configurations

4. **`GET /export/trades/{strategy_id}`**
   - Exports trades as CSV

5. **`GET /export/summary/{strategy_id}`**
   - Exports summary as CSV

6. **`POST /export/trades`**
   - Exports trades from request body

7. **`POST /export/summary`**
   - Exports summary from request body

### File: `backend/routers/strategies.py`
**Purpose**: Strategy listing endpoint

#### Endpoints

1. **`GET /strategies`**
   - Returns list of available strategies
   - Each strategy has: name, version, description

### File: `backend/routers/expiry.py`
**Purpose**: Expiry date endpoints

#### Endpoints

1. **`GET /expiry/{index}/{type}`**
   - Returns expiry dates for index and type
   - Type: weekly or monthly

---

## Analytics Functions

### File: `backend/analytics.py`
**Purpose**: Analytics and reporting functions

#### Functions

1. **`create_summary_idx(df)`**
   - Creates comprehensive summary statistics
   - Calculates:
     - Total P&L, Count, Average
     - Win%, Avg Win, Loss%, Avg Loss
     - Expectancy
     - CAGR (Options and Spot)
     - Max Drawdown (% and Points)
     - CAR/MDD (Calmar Ratio)
     - Recovery Factor
     - ROI vs Spot
   - Returns summary dict

2. **`getPivotTable(df, expiry_col)`**
   - Creates monthly P&L pivot table
   - Rows: Years
   - Columns: Jan, Feb, ..., Dec, Grand Total
   - Returns pivot DataFrame

3. **`generate_trade_sheet(df)`**
   - Generates detailed trade sheet
   - Includes:
     - Trade Date, Strategy Name
     - Leg-specific information
     - P&L breakdown
     - Strike and premium details
     - Running equity
   - Returns trade sheet DataFrame

4. **`generate_summary_report(df)`**
   - Generates summary report DataFrame
   - Two columns: Metric and Value
   - Includes all key performance metrics
   - Returns summary DataFrame

---

## Filter System

### Base2 Filter

**File**: `Filter/base2.csv`
**Purpose**: Defines date ranges to include/exclude in backtests

#### Structure
- Columns: Start, End
- Each row defines a date range
- Strategies filter spot data to only include dates within these ranges

#### Usage in Engines

All strategy engines apply base2 filter:

```python
base2 = load_base2()
mask = pd.Series(False, index=spot_df.index)
for _, row in base2.iterrows():
    mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
spot_df = spot_df[mask].reset_index(drop=True)
```

#### Inverse Base2

Some strategies (v6) support `inverse_base2`:
- Inverts the filter logic
- Includes dates OUTSIDE base2 ranges
- Used for counter-trend strategies

### Filter Data Table

**Database Table**: `filter_data`
**Built by**: `bhavcopy_db_builder.py::build_filter_data()`

Stores base2 filter ranges in database for query-based filtering.

---

## File Organization Summary

### Backend Structure
```
backend/
├── base.py                 # Core utility functions
├── algotest_engine.py      # Main strategy executor
├── analytics.py            # Analytics functions
├── backtest_manager.py     # Backtest management
├── strategy_engine.py      # Strategy engine interface
├── engines/
│   ├── v1_ce_fut.py       # Strategy engines
│   ├── v2_pe_fut.py
│   ├── ...
│   ├── generic_multi_leg.py
│   └── generic_algotest_engine.py
├── routers/
│   ├── backtest.py        # Backtest API endpoints
│   ├── strategies.py      # Strategy listing
│   └── expiry.py          # Expiry endpoints
└── strategies/
    ├── generic_multi_leg_engine.py
    └── strategy_types.py   # Pydantic models
```

### Data Structure
```
Project Root/
├── cleaned_csvs/          # Daily bhavcopy CSVs (6362 files)
├── expiryData/            # Expiry date CSVs
├── strikeData/            # Spot price CSVs
├── Filter/
│   └── base2.csv         # Date range filter
└── bhavcopy_data.db      # SQLite database
```

### Database Schema
```
bhavcopy_data.db
├── bhavcopy              # Main market data
├── expiry_data           # Expiry dates
├── strike_data           # Spot prices
├── filter_data           # Base2 ranges
└── file_metadata         # Processed file tracking
```

---

## Key Workflows

### 1. Database Building
```
bhavcopy_db_builder.py
├── create_database()
├── ingest_directory('cleaned_csvs')
├── build_expiry_data()
├── build_strike_data()
└── build_filter_data()
```

### 2. Backtest Execution
```
API Request → backtest.py
├── validate_params()
├── algotest_engine.run_backtest()
│   ├── Load data (base.py functions)
│   ├── Apply base2 filter
│   ├── Execute strategy engine
│   └── Calculate analytics
└── format_response()
```

### 3. Strategy Engine Flow
```
Strategy Engine (e.g., v1_ce_fut.py)
├── Load spot data (get_strike_data)
├── Load expiry data (load_expiry)
├── Apply base2 filter (load_base2)
├── Build intervals (build_intervals)
├── For each interval:
│   ├── Calculate strikes
│   ├── Get entry prices
│   ├── Get exit prices
│   ├── Calculate P&L
│   └── Record trade
├── Compute analytics
└── Build pivot table
```

---

## Function Call Hierarchy

### Most Used Functions (by dependency)

1. **`load_bhavcopy()`** - Called by all price retrieval functions
2. **`get_strike_data()`** - Called by all strategy engines
3. **`load_expiry()`** - Called by all strategy engines
4. **`load_base2()`** - Called by all strategy engines
5. **`build_intervals()`** - Called by all strategy engines
6. **`compute_analytics()`** - Called after every backtest
7. **`build_pivot()`** - Called after every backtest

### Critical Path Functions

For a backtest to execute successfully, these functions must work:
1. Data loading: `get_strike_data()`, `load_expiry()`, `load_base2()`
2. Filtering: base2 filter logic
3. Strike calculation: `calculate_strike_from_selection()` or ATM/OTM/ITM functions
4. Price retrieval: `get_option_price()` or `get_option_premium_from_db()`
5. Re-entry: `build_intervals()`
6. Analytics: `compute_analytics()`, `build_pivot()`

---

## Performance Considerations

### Caching
- `load_bhavcopy()` uses LRU cache (500 files)
- Reduces disk I/O for repeated date access

### Database Indexing
- Indices on: (date, symbol, expiry, strike, option_type)
- Enables fast option price lookups

### Batch Processing
- `ingest_directory()` processes files in batches
- Drops indices during ingestion, rebuilds after

---

## Error Handling

### Common Errors

1. **FileNotFoundError**
   - Missing CSV files
   - Missing database
   - Check file paths and existence

2. **ValueError**
   - Invalid parameters
   - Invalid date formats
   - Check parameter validation

3. **KeyError**
   - Missing DataFrame columns
   - Check column name normalization

4. **Database Errors**
   - Connection issues
   - Query failures
   - Check database integrity

---

## Testing Files

- `test_api.py` - API endpoint tests
- `test_dynamic_backtest.py` - Dynamic strategy tests
- `test_date_parsing.py` - Date parsing tests
- `diagnose_strikes.py` - Strike calculation diagnostics
- `Diagnose_backtest.py` - Backtest diagnostics

---

## Configuration Files

- `.gitignore` - Git ignore rules
- `backend/start_server.py` - Server startup script
- `kill_and_restart.bat` - Server restart script
- `push_to_github.bat` - Git push script

---

## Documentation Files

- `COMPLETE_SYSTEM_ARCHITECTURE.md` - System architecture
- `BACKEND_QUICKSTART.md` - Backend quickstart guide
- `BACKEND_STATUS.md` - Backend status
- `BACKTEST_MANAGER_GUIDE.md` - Backtest manager guide
- `ALGOTEST_ALIGNMENT.md` - AlgoTest alignment notes
- `CRITICAL_FIX_APPLIED.md` - Critical fixes log
- `PERFORMANCE_OPTIMIZATIONS.md` - Performance notes

---

*This documentation covers all major files and functions in the system. For specific implementation details, refer to the source code.*
