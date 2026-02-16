# API Endpoints and Callers Mapping

## Overview
This document maps all API endpoints to where they are called from (frontend, test files, etc.)

---

## Backend API Endpoints

### Main Server: `backend/main.py`
**Base URL**: `http://localhost:8000`

#### Root Endpoints

1. **`GET /`**
   - **Handler**: `backend/main.py::read_root()`
   - **Purpose**: Root endpoint, returns API info
   - **Called From**:
     - `test_complete_api.py`
     - `validate_system.py`

2. **`GET /health`**
   - **Handler**: `backend/main.py::health_check()`
   - **Purpose**: Health check endpoint
   - **Called From**:
     - `verify_integration.py`
     - `validate_system.py`
     - `validate_alignment.py`
     - `test_integration.py`
     - `test_dynamic_endpoint.py`
     - `test_backend.py`
     - `test_api_phase2.py`
     - `test_complete_api.py`

---

### Backtest Router: `backend/routers/backtest.py`
**Prefix**: `/api`

#### Backtest Endpoints

3. **`POST /api/backtest`**
   - **Handler**: `backend/routers/backtest.py::backtest()`
   - **Purpose**: Main backtest execution endpoint
   - **Request Model**: `BacktestRequest`
   - **Response Model**: `BacktestResponse`
   - **Called From**:
     - **Frontend**:
       - `frontend/src/components/ConfigPanel.jsx` (line 399)
       - `frontend/src/components/StrategyBuilder.jsx` (line 90)
     - **Test Files**:
       - `verify_integration.py`
       - `test_integration.py`
       - `test_frontend_payload.py`
       - `test_backend.py`
       - `test_api_call.py`
       - `test_complete_api.py`
       - `test_new_ui_api.py`
       - `validate_alignment.py`
       - `test_server.py`

4. **`POST /api/algotest-backtest`**
   - **Handler**: `backend/routers/backtest.py::algotest_backtest()`
   - **Purpose**: AlgoTest-compatible backtest endpoint (alias for /api/backtest)
   - **Request Model**: `BacktestRequest`
   - **Response Model**: `BacktestResponse`
   - **Called From**: None directly (alternative endpoint)

5. **`POST /api/dynamic-backtest`**
   - **Handler**: `backend/routers/backtest.py::dynamic_backtest()`
   - **Purpose**: Dynamic multi-leg strategy backtest
   - **Request**: JSON dict with strategy definition
   - **Called From**:
     - **Frontend**:
       - `frontend/src/components/AlgoTestBacktest.jsx` (line 118)
       - `frontend/src/components/AlgoTestBacktest_Complete.jsx` (line 72)
       - `frontend/src/components/AlgoTestStyleBuilder.jsx` (line 113)
       - `frontend/src/components/ConfigPanel.jsx` (line 399)
     - **Test Files**:
       - `test_dynamic_endpoint.py`
       - `test_dynamic_simple.py`
       - `test_dynamic_backtest.py`
       - `test_backtest_response.py`

6. **`POST /api/algotest`**
   - **Handler**: `backend/routers/backtest.py::run_algotest_backtest_endpoint()`
   - **Purpose**: AlgoTest-style backtest execution
   - **Called From**: Test files (alternative endpoint)

#### Export Endpoints

7. **`GET /api/export/trades`**
   - **Handler**: `backend/routers/backtest.py::export_trades()`
   - **Purpose**: Export trades as CSV
   - **Query Params**: `strategy_id`
   - **Called From**: Frontend (potential)

8. **`GET /api/export/summary`**
   - **Handler**: `backend/routers/backtest.py::export_summary()`
   - **Purpose**: Export summary as CSV
   - **Query Params**: `strategy_id`
   - **Called From**: Frontend (potential)

9. **`POST /api/export/trades`**
   - **Handler**: `backend/routers/backtest.py::export_trades_post()`
   - **Purpose**: Export trades from request body
   - **Called From**: Frontend (potential)

10. **`POST /api/export/summary`**
    - **Handler**: `backend/routers/backtest.py::export_summary_post()`
    - **Purpose**: Export summary from request body
    - **Called From**: Frontend (potential)

---

### Strategies Router: `backend/routers/strategies.py`
**Prefix**: `/api`

11. **`GET /api/strategies`**
    - **Handler**: `backend/routers/strategies.py::get_strategies()`
    - **Purpose**: List all available strategies
    - **Response Model**: `StrategiesResponse`
    - **Called From**:
      - **Frontend**:
        - `frontend/src/components/StrategyBuilder.jsx` (line 28)
      - **Test Files**:
        - `test_complete_api.py`
        - `test_new_ui_api.py`
        - `backend/simple_server.py` (duplicate endpoint)

12. **`GET /api/data/dates`**
    - **Handler**: `backend/routers/strategies.py::get_date_range()`
    - **Purpose**: Get available date range for an index
    - **Query Params**: `index` (default: NIFTY)
    - **Called From**:
      - `test_complete_api.py`

---

### Expiry Router: `backend/routers/expiry.py`
**Prefix**: `/api`

13. **`GET /api/expiry`**
    - **Handler**: `backend/routers/expiry.py::get_expiry_dates()`
    - **Purpose**: Get expiry dates for index and type
    - **Query Params**: `index`, `type` (weekly/monthly)
    - **Response Model**: `ExpiryResponse`
    - **Called From**:
      - `test_complete_api.py`
      - `backend/simple_server.py` (has `/api/expiry/dates` variant)

---

## Alternative/Test Servers

### Simple Server: `backend/simple_server.py`
**Base URL**: `http://localhost:8000` (when running this server)

14. **`GET /`**
    - Returns API info

15. **`GET /health`**
    - Health check

16. **`GET /api/strategies`**
    - List strategies

17. **`POST /api/backtest`**
    - Simplified backtest endpoint

18. **`GET /api/expiry/dates`**
    - Get expiry dates

19. **`GET /api/validate`**
    - System validation endpoint

---

### Backtest Manager: `backend/backtest_manager.py`
**Base URL**: `http://localhost:5000` (Flask server)

20. **`GET /api/health`**
    - Health check

21. **`POST /api/backtest/run`**
    - Trigger backtest execution

22. **`GET /api/backtest/status/<run_id>`**
    - Get backtest status

23. **`GET /api/backtest/logs/<run_id>`**
    - Get execution logs

24. **`POST /api/backtest/validate/<run_id>`**
    - Validate results

25. **`GET /api/backtest/results/<run_id>`**
    - List result files

26. **`GET /api/backtest/results/<run_id>/<filename>`**
    - Download result file

27. **`GET /api/backtest/list`**
    - List all backtest runs

28. **`POST /api/backtest/cancel/<run_id>`**
    - Cancel running backtest

---

### SaaS API Server: `src/api/main.py`
**Base URL**: `http://localhost:8000` (when running this server)

29. **`GET /health`**
    - Health check

30. **`GET /api/v1/strategies`**
    - List strategies
    - **Called From**: `test_api_phase2.py`

31. **`GET /api/v1/strategies/{strategy_name}`**
    - Get strategy details

32. **`POST /api/v1/execute`**
    - Execute strategy with caching
    - **Called From**: `test_api_phase2.py`

33. **`GET /api/v1/executions/{execution_id}`**
    - Get execution result

34. **`GET /api/v1/executions`**
    - List recent executions

35. **`GET /api/v1/jobs/{job_id}`**
    - Get async job status
    - **Called From**: `test_api_phase2.py`

36. **`GET /api/v1/metrics`**
    - Get performance metrics
    - **Called From**: `test_api_phase2.py`

37. **`GET /api/v1/metrics/{strategy_name}`**
    - Get strategy-specific metrics

38. **`POST /api/v1/cache/invalidate`**
    - Invalidate cache entries

39. **`GET /api/v1/cache/stats`**
    - Get cache statistics
    - **Called From**: `test_api_phase2.py`

40. **`GET /api/v1/cache/health`**
    - Check cache health
    - **Called From**: `test_api_phase2.py`

---

## Frontend API Calls Summary

### Frontend Components Making API Calls

#### 1. `frontend/src/components/ConfigPanel.jsx`
```javascript
// Line 399
const endpoint = strategyMode === 'dynamic' ? '/api/dynamic-backtest' : '/api/backtest';
const response = await fetch(endpoint, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload)
});
```
**Calls**:
- `POST /api/backtest` (standard mode)
- `POST /api/dynamic-backtest` (dynamic mode)

#### 2. `frontend/src/components/StrategyBuilder.jsx`
```javascript
// Line 28 - Fetch strategies
const response = await fetch('/api/strategies');

// Line 90 - Run backtest
const response = await fetch('/api/backtest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(backtestConfig)
});
```
**Calls**:
- `GET /api/strategies`
- `POST /api/backtest`

#### 3. `frontend/src/components/AlgoTestBacktest.jsx`
```javascript
// Line 118
const response = await fetch('/api/dynamic-backtest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload)
});
```
**Calls**:
- `POST /api/dynamic-backtest`

#### 4. `frontend/src/components/AlgoTestBacktest_Complete.jsx`
```javascript
// Line 72
const response = await fetch('http://localhost:8000/api/dynamic-backtest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(config)
});
```
**Calls**:
- `POST /api/dynamic-backtest` (with full URL)

#### 5. `frontend/src/components/AlgoTestStyleBuilder.jsx`
```javascript
// Line 113
const response = await fetch('/api/dynamic-backtest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload)
});
```
**Calls**:
- `POST /api/dynamic-backtest`

---

## Test Files Making API Calls

### Python Test Files

1. **`verify_integration.py`**
   - `GET /health`
   - `POST /api/backtest`

2. **`validate_system.py`**
   - `GET /health`
   - `GET /` (root)
   - Various endpoints for validation

3. **`validate_alignment.py`**
   - `GET /health`
   - `POST /api/backtest`

4. **`test_integration.py`**
   - `GET /health`
   - `POST /api/backtest`

5. **`test_complete_api.py`**
   - `GET /health`
   - `GET /`
   - `GET /api/strategies`
   - `GET /api/data/dates`
   - `GET /api/expiry`
   - `POST /api/backtest`

6. **`test_new_ui_api.py`**
   - `GET /health`
   - `GET /api/strategies`
   - `POST /api/backtest`

7. **`test_dynamic_endpoint.py`**
   - `GET /health`
   - `POST /api/dynamic-backtest`

8. **`test_dynamic_simple.py`**
   - `POST /api/dynamic-backtest`

9. **`test_dynamic_backtest.py`**
   - `POST /api/dynamic-backtest`

10. **`test_backtest_response.py`**
    - `POST /api/dynamic-backtest`

11. **`test_backend.py`**
    - `GET /health`
    - `POST /api/backtest`

12. **`test_api_call.py`**
    - `POST /api/backtest`

13. **`test_frontend_payload.py`**
    - `POST /api/backtest`

14. **`test_api_phase2.py`** (SaaS API tests)
    - `GET /health`
    - `GET /api/v1/strategies`
    - `GET /api/v1/cache/health`
    - `POST /api/v1/execute`
    - `GET /api/v1/jobs/{job_id}`
    - `GET /api/v1/metrics`
    - `GET /api/v1/cache/stats`

---

## API Call Flow Diagram

```
Frontend (React)
├── ConfigPanel.jsx
│   ├── POST /api/backtest
│   └── POST /api/dynamic-backtest
├── StrategyBuilder.jsx
│   ├── GET /api/strategies
│   └── POST /api/backtest
├── AlgoTestBacktest.jsx
│   └── POST /api/dynamic-backtest
├── AlgoTestBacktest_Complete.jsx
│   └── POST /api/dynamic-backtest
└── AlgoTestStyleBuilder.jsx
    └── POST /api/dynamic-backtest

Backend (FastAPI)
├── main.py
│   ├── GET /
│   └── GET /health
├── routers/backtest.py
│   ├── POST /api/backtest
│   ├── POST /api/algotest-backtest
│   ├── POST /api/dynamic-backtest
│   ├── POST /api/algotest
│   ├── GET /api/export/trades
│   ├── GET /api/export/summary
│   ├── POST /api/export/trades
│   └── POST /api/export/summary
├── routers/strategies.py
│   ├── GET /api/strategies
│   └── GET /api/data/dates
└── routers/expiry.py
    └── GET /api/expiry

Test Files (Python)
├── verify_integration.py → GET /health, POST /api/backtest
├── validate_system.py → GET /health, GET /
├── test_integration.py → GET /health, POST /api/backtest
├── test_complete_api.py → Multiple endpoints
├── test_dynamic_backtest.py → POST /api/dynamic-backtest
└── test_api_phase2.py → SaaS API endpoints
```

---

## API Request/Response Models

### BacktestRequest (Standard)
```python
{
  "strategy": "v1_ce_fut",
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2019-12-31",
  "spot_adjustment_type": 0,
  "spot_adjustment": 1.0,
  "call_sell_position": 0,
  "legs": []
}
```

### DynamicStrategyRequest
```python
{
  "name": "Custom Strategy",
  "legs": [
    {
      "leg_number": 1,
      "instrument": "Option",
      "option_type": "CE",
      "position_type": "Sell",
      "strike_selection": "ATM",
      "entry_time": "09:20",
      "exit_time": "15:25"
    }
  ],
  "index": "NIFTY",
  "from_date": "2019-01-01",
  "to_date": "2019-12-31"
}
```

### BacktestResponse
```python
{
  "status": "success",
  "meta": {
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "from_date": "2019-01-01",
    "to_date": "2019-12-31"
  },
  "trades": [...],
  "summary": {
    "total_pnl": 12345.67,
    "count": 50,
    "win_pct": 65.0,
    "cagr_options": 15.5,
    "max_dd_pct": -8.5
  },
  "pivot": {
    "headers": ["Year", "Jan", "Feb", ...],
    "rows": [...]
  }
}
```

---

## Server Startup Commands

### Main Backend Server
```bash
# From project root
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Or using start script
python backend/start_server.py
```

### Frontend Development Server
```bash
# From frontend directory
cd frontend
npm run dev
# Runs on http://localhost:5173
```

### Alternative Servers
```bash
# Simple server
uvicorn backend.simple_server:app --reload --port 8000

# Backtest manager (Flask)
python backend/backtest_manager.py
# Runs on http://localhost:5000

# SaaS API server
uvicorn src.api.main:app --reload --port 8000
```

---

## API Testing Workflow

### 1. Start Backend
```bash
python backend/start_server.py
```

### 2. Test Health
```bash
curl http://localhost:8000/health
```

### 3. Test Backtest
```bash
curl -X POST http://localhost:8000/api/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "from_date": "2019-01-01",
    "to_date": "2019-12-31"
  }'
```

### 4. Run Test Suite
```bash
python test_complete_api.py
```

---

## Common API Issues and Solutions

### Issue 1: CORS Errors
**Solution**: Backend has CORS middleware configured in `main.py`
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Issue 2: Connection Refused
**Solution**: Ensure backend is running on correct port (8000)

### Issue 3: 404 Not Found
**Solution**: Check endpoint path matches router prefix

### Issue 4: 422 Validation Error
**Solution**: Check request payload matches Pydantic model

---

## API Documentation URLs

When server is running:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

---

*This document provides a complete mapping of all API endpoints and their callers in the system.*
