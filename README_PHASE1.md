# Phase 1: Foundation Layer - Implementation Complete

## Overview
Phase 1 establishes the foundation for the production-ready NSE Options Strategy Execution platform. This phase includes:
- Strategy abstraction layer with interface
- Data provider for database access
- FastAPI REST API
- Database migration for execution tracking
- First strategy implementation (Call Sell + Future Buy)

## Project Structure
```
.
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py              # FastAPI application
│   ├── data/
│   │   ├── __init__.py
│   │   └── provider.py          # Data access layer
│   └── strategies/
│       ├── __init__.py
│       ├── base.py              # Strategy interface
│       └── call_sell_future_buy.py  # First strategy implementation
├── migrations/
│   └── 001_add_execution_tables.sql  # Database schema
├── requirements.txt
├── run_migration.py
└── README_PHASE1.md
```

## Installation

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Database Migration
```bash
python run_migration.py
```

This will create the following tables:
- `strategy_registry` - Registered strategies
- `execution_runs` - Execution history
- `execution_results` - Execution results
- `parameter_cache` - Result caching
- `db_metadata` - Schema version tracking

## Running the API

### Start the API Server
```bash
python -m src.api.main
```

Or using uvicorn directly:
```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

API Documentation (Swagger UI): `http://localhost:8000/docs`

## API Endpoints

### 1. Health Check
```bash
curl http://localhost:8000/health
```

### 2. List Available Strategies
```bash
curl http://localhost:8000/api/v1/strategies
```

Response:
```json
[
  {
    "name": "call_sell_future_buy_weekly",
    "description": "Sell Call Option + Buy Future (Weekly Expiry to Expiry)",
    "version": "1.0.0",
    "parameter_schema": [
      {
        "name": "spot_adjustment_type",
        "type": "select",
        "required": true,
        "default": 0,
        "options": [0, 1, 2, 3],
        "description": "0=No Adjustment, 1=Rise Only, 2=Fall Only, 3=Rise or Fall"
      },
      ...
    ]
  }
]
```

### 3. Get Strategy Details
```bash
curl http://localhost:8000/api/v1/strategies/call_sell_future_buy_weekly
```

### 4. Execute Strategy
```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_name": "call_sell_future_buy_weekly",
    "parameters": {
      "spot_adjustment_type": 0,
      "spot_adjustment": 1.0,
      "call_sell_position": 0.0,
      "symbol": "NIFTY"
    },
    "user_id": "admin"
  }'
```

Response:
```json
{
  "execution_id": 1,
  "strategy_name": "call_sell_future_buy_weekly",
  "status": "completed",
  "started_at": "2026-02-09T10:30:00",
  "completed_at": "2026-02-09T10:30:15",
  "duration_ms": 15234,
  "row_count": 156,
  "error_message": null
}
```

### 5. Get Execution Results
```bash
curl http://localhost:8000/api/v1/executions/1
```

Response:
```json
{
  "execution_id": 1,
  "status": "completed",
  "data": [
    {
      "Entry Date": "2020-01-02",
      "Exit Date": "2020-01-09",
      "Entry Spot": 12100.5,
      "Exit Spot": 12250.75,
      "Call Strike": 12100,
      "Call P&L": 125.50,
      "Future P&L": 150.25,
      "Net P&L": 275.75
    },
    ...
  ],
  "metadata": {
    "strategy": "call_sell_future_buy_weekly",
    "version": "1.0.0",
    "trades_executed": 156
  },
  "row_count": 156
}
```

### 6. List Recent Executions
```bash
# All executions
curl http://localhost:8000/api/v1/executions

# Filter by status
curl http://localhost:8000/api/v1/executions?status=completed

# Limit results
curl http://localhost:8000/api/v1/executions?limit=10
```

## Strategy Parameters

### Call Sell + Future Buy Strategy

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| spot_adjustment_type | select | Yes | 0 | 0=No Adjustment, 1=Rise Only, 2=Fall Only, 3=Rise or Fall |
| spot_adjustment | float | Yes | 1.0 | Spot adjustment percentage for re-entry (0-100) |
| call_sell_position | float | Yes | 0.0 | Call strike position: 0=ATM, +ve=OTM%, -ve=ITM% (-50 to 50) |
| symbol | string | Yes | "NIFTY" | Underlying symbol |

## Testing the Implementation

### Test 1: Basic Execution (No Adjustment, ATM)
```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_name": "call_sell_future_buy_weekly",
    "parameters": {
      "spot_adjustment_type": 0,
      "spot_adjustment": 1.0,
      "call_sell_position": 0.0,
      "symbol": "NIFTY"
    }
  }'
```

### Test 2: With Spot Adjustment (Rise Only, 2%)
```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_name": "call_sell_future_buy_weekly",
    "parameters": {
      "spot_adjustment_type": 1,
      "spot_adjustment": 2.0,
      "call_sell_position": 0.0,
      "symbol": "NIFTY"
    }
  }'
```

### Test 3: OTM Call (2% OTM)
```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_name": "call_sell_future_buy_weekly",
    "parameters": {
      "spot_adjustment_type": 0,
      "spot_adjustment": 1.0,
      "call_sell_position": 2.0,
      "symbol": "NIFTY"
    }
  }'
```

## Architecture

### Strategy Interface
All strategies implement the `StrategyInterface` abstract base class:
- `get_name()` - Strategy identifier
- `get_description()` - Human-readable description
- `get_version()` - Version string
- `get_parameter_schema()` - Parameter definitions
- `validate_parameters()` - Parameter validation
- `execute()` - Strategy execution logic

### Data Provider
The `DataProvider` class abstracts database access:
- `get_cleaned_data()` - Query bhavcopy data
- `get_data_for_date()` - Get data for specific date
- `get_strike_data()` - Load spot price data
- `get_expiry_data()` - Load expiry dates
- `get_filter_data()` - Load filter data
- `get_trading_dates()` - Get trading date list

### Execution Flow
1. Client sends POST request to `/api/v1/execute`
2. API validates strategy name and parameters
3. Strategy is registered in `strategy_registry` (if not already)
4. Execution record created in `execution_runs` table
5. Strategy executes with parameters
6. Results saved to `execution_results` table
7. Execution record updated with status and duration
8. Response returned to client

## Database Schema

### strategy_registry
- `id` - Primary key
- `name` - Unique strategy name
- `description` - Strategy description
- `version` - Version string
- `parameter_schema` - JSON parameter schema
- `created_at`, `updated_at` - Timestamps

### execution_runs
- `id` - Primary key
- `strategy_id` - Foreign key to strategy_registry
- `parameters_hash` - MD5 hash for caching
- `parameters_json` - Full parameters
- `status` - pending/running/completed/failed
- `started_at`, `completed_at` - Timestamps
- `duration_ms` - Execution time
- `user_id` - User identifier
- `error_message` - Error details if failed

### execution_results
- `id` - Primary key
- `execution_id` - Foreign key to execution_runs
- `result_data` - JSON result data
- `row_count` - Number of result rows
- `metadata` - Additional metadata
- `created_at` - Timestamp

### parameter_cache
- `id` - Primary key
- `strategy_id` - Foreign key to strategy_registry
- `parameters_hash` - MD5 hash
- `result_data` - Cached result
- `row_count` - Number of rows
- `execution_time_ms` - Execution time
- `created_at`, `last_accessed` - Timestamps
- `access_count` - Cache hit counter

## Next Steps (Phase 2)

Phase 2 will add:
1. Result caching layer
2. Convert 2-3 more strategies
3. Background job processing
4. Enhanced error handling
5. Performance monitoring

## Troubleshooting

### Migration Fails
```bash
# Check if database exists
ls -lh bhavcopy_data.db

# Verify database is not corrupted
sqlite3 bhavcopy_data.db "PRAGMA integrity_check;"
```

### API Won't Start
```bash
# Check if port 8000 is available
netstat -an | findstr 8000

# Check Python path
python -c "import sys; print(sys.path)"

# Verify dependencies
pip list | findstr fastapi
```

### Strategy Execution Fails
- Check that all required data files exist:
  - `./strikeData/NIFTY.csv`
  - `./expiryData/NIFTY.csv`
  - `./expiryData/NIFTY_Monthly.csv`
  - `./Filter/base2.csv`
  - `./cleaned_csvs/*.csv` files

## Support
For issues or questions, check the execution logs in the `execution_runs` table:
```sql
SELECT * FROM execution_runs WHERE status = 'failed' ORDER BY started_at DESC LIMIT 10;
```
