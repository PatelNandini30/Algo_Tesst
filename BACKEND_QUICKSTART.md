# AlgoTest Backend - Quick Start Guide

## 🚀 Complete End-to-End API Setup

This guide will help you start the backend API and test all endpoints.

---

## 📋 Prerequisites

Make sure you have the following installed:
- Python 3.8+
- Required packages: `fastapi`, `uvicorn`, `pandas`, `numpy`

Install dependencies:
```bash
pip install fastapi uvicorn pandas numpy python-multipart
```

---

## 🎯 Step 1: Start the Backend Server

### Option A: Using the startup script (Recommended)
```bash
cd backend
python start_server.py
```

### Option B: Using uvicorn directly
```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Option C: Using Python directly
```bash
cd backend
python main.py
```

You should see:
```
================================================================================
  ALGOTEST BACKEND SERVER
================================================================================
Backend directory: E:\Algo_Test_Software\backend
Parent directory: E:\Algo_Test_Software

Starting server...
  API URL: http://localhost:8000
  API Docs: http://localhost:8000/docs
  Health Check: http://localhost:8000/health

Press CTRL+C to stop the server
================================================================================

INFO:     Started server process [xxxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

---

## 🧪 Step 2: Test the API

### Option A: Run the complete test suite
```bash
python test_complete_api.py
```

This will test all endpoints and show you detailed results.

### Option B: Test manually using browser

1. **Health Check**: http://localhost:8000/health
2. **API Documentation**: http://localhost:8000/docs
3. **Root Endpoint**: http://localhost:8000/

### Option C: Test using curl

```bash
# Health check
curl http://localhost:8000/health

# Get strategies list
curl http://localhost:8000/api/strategies

# Get date range
curl http://localhost:8000/api/data/dates?index=NIFTY

# Run a backtest
curl -X POST http://localhost:8000/api/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "v1_ce_fut",
    "index": "NIFTY",
    "date_from": "2024-01-01",
    "date_to": "2024-12-31",
    "expiry_window": "weekly_expiry",
    "call_sell_position": 0.0,
    "spot_adjustment_type": "None",
    "spot_adjustment": 1.0,
    "call_sell": true,
    "future_buy": true
  }'
```

---

## 📡 Available API Endpoints

### 1. Health Check
- **URL**: `GET /health`
- **Description**: Check if the API is running
- **Response**:
```json
{
  "status": "healthy",
  "service": "AlgoTest Backend",
  "version": "1.0.0"
}
```

### 2. Root Endpoint
- **URL**: `GET /`
- **Description**: Get API information and available endpoints
- **Response**:
```json
{
  "message": "AlgoTest Clone API is running",
  "version": "1.0.0",
  "endpoints": {
    "backtest": "/api/backtest",
    "strategies": "/api/strategies",
    "date_range": "/api/data/dates",
    "health": "/health",
    "docs": "/docs"
  }
}
```

### 3. Get Strategies List
- **URL**: `GET /api/strategies`
- **Description**: Get all available strategies with their parameters
- **Response**:
```json
{
  "strategies": [
    {
      "name": "CE Sell + Future Buy (V1)",
      "version": "v1",
      "description": "Sell Call Option and Buy Future",
      "parameters": {...},
      "defaults": {...}
    },
    ...
  ]
}
```

### 4. Get Date Range
- **URL**: `GET /api/data/dates?index=NIFTY`
- **Description**: Get available date range for backtesting
- **Parameters**:
  - `index` (query): Index name (default: NIFTY)
- **Response**:
```json
{
  "min_date": "2000-06-12",
  "max_date": "2025-12-31"
}
```

### 5. Get Expiry Dates
- **URL**: `GET /api/expiry?index=NIFTY&type=weekly`
- **Description**: Get expiry dates for an index
- **Parameters**:
  - `index` (query): Index name (required)
  - `type` (query): Expiry type - "weekly" or "monthly" (required)
- **Response**:
```json
{
  "index": "NIFTY",
  "type": "weekly",
  "expiries": ["2024-01-04", "2024-01-11", ...]
}
```

### 6. Run Backtest
- **URL**: `POST /api/backtest`
- **Description**: Execute a backtest with specified parameters
- **Request Body**:
```json
{
  "strategy": "v1_ce_fut",
  "index": "NIFTY",
  "date_from": "2024-01-01",
  "date_to": "2024-12-31",
  "expiry_window": "weekly_expiry",
  "call_sell_position": 0.0,
  "put_sell_position": 0.0,
  "spot_adjustment_type": "None",
  "spot_adjustment": 1.0,
  "call_sell": true,
  "put_sell": false,
  "future_buy": true,
  "protection": false
}
```

- **Response**:
```json
{
  "status": "success",
  "meta": {
    "strategy": "CE Sell + Future Buy",
    "index": "NIFTY",
    "total_trades": 52,
    "date_range": "2024-01-01 to 2024-12-31"
  },
  "trades": [
    {
      "Entry Date": "2024-01-04",
      "Exit Date": "2024-01-11",
      "Entry Spot": 21725.7,
      "Exit Spot": 21453.1,
      "Call Strike": 21800,
      "Call EntryPrice": 145.5,
      "Call ExitPrice": 23.8,
      "Call P&L": 121.7,
      "Future EntryPrice": 21731.25,
      "Future ExitPrice": 21458.4,
      "Future P&L": -272.85,
      "Net P&L": -151.15,
      "Cumulative": 21574.55,
      "DD": 0,
      "%DD": 0
    },
    ...
  ],
  "summary": {
    "total_pnl": 1234.56,
    "count": 52,
    "win_pct": 65.38,
    "cagr_options": 5.68,
    "max_dd_pct": -12.34,
    "car_mdd": 0.46
  },
  "pivot": {
    "headers": ["Year", "Jan", "Feb", ..., "Grand Total"],
    "rows": [
      ["2024", 123.45, 234.56, ..., 1234.56]
    ]
  }
}
```

---

## 🎨 Frontend Integration

The frontend should make requests to these endpoints:

```javascript
// Example: Run backtest from frontend
const runBacktest = async (params) => {
  const response = await fetch('http://localhost:8000/api/backtest', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(params)
  });
  
  const data = await response.json();
  return data;
};
```

---

## 🔧 Troubleshooting

### Server won't start
1. Check if port 8000 is already in use:
   ```bash
   netstat -ano | findstr :8000
   ```
2. Kill the process or use a different port:
   ```bash
   uvicorn main:app --port 8001
   ```

### Import errors
1. Make sure you're in the backend directory
2. Check that all required packages are installed:
   ```bash
   pip install -r requirements.txt
   ```

### No trades returned
1. Check that your date range has data in `cleaned_csvs/`
2. Verify the strategy parameters are correct
3. Check the server logs for errors

### CORS errors from frontend
- The API is configured to allow all origins (`allow_origins=["*"]`)
- For production, update this in `backend/main.py`

---

## 📊 Strategy Parameters Guide

### Common Parameters (All Strategies)
- `index`: Index name (NIFTY, BANKNIFTY, etc.)
- `date_from`: Start date (YYYY-MM-DD)
- `date_to`: End date (YYYY-MM-DD)
- `expiry_window`: Expiry type (weekly_expiry, monthly_expiry, etc.)
- `spot_adjustment_type`: Re-entry mode (None, Rises, Falls, RisesOrFalls)
- `spot_adjustment`: Re-entry threshold percentage

### Strategy-Specific Parameters

#### V1 - CE Sell + Future Buy
- `call_sell_position`: % OTM for call strike (0 = ATM)
- `call_sell`: true
- `future_buy`: true

#### V2 - PE Sell + Future Buy
- `put_sell_position`: % OTM for put strike (0 = ATM)
- `put_sell`: true
- `future_buy`: true

#### V4 - Short Strangle
- `call_sell_position`: % OTM for call strike
- `put_sell_position`: % OTM for put strike
- `call_sell`: true
- `put_sell`: true

#### V5 - Protected Strategies
- `call_sell_position` or `put_sell_position`: Main leg strike
- `protection`: true/false
- `protection_pct`: % OTM for protective leg

#### V7 - Premium-Based
- `premium_multiplier`: Multiplier for premium target
- `call_premium`: Use call premium
- `put_premium`: Use put premium

#### V8 - Hedged Bull
- `call_sell_position`: % OTM for call strike
- `put_strike_pct_below`: % below call for put strike

#### V9 - Counter-Expiry
- `call_sell_position`: % OTM for call strike
- `put_strike_pct_below`: % below call for put strike
- `max_put_spot_pct`: Max put strike % below spot

---

## ✅ Success Checklist

- [ ] Server starts without errors
- [ ] Health check returns 200 OK
- [ ] Strategies list loads
- [ ] Date range endpoint works
- [ ] Backtest completes and returns trades
- [ ] Frontend can connect to API
- [ ] Results display correctly in UI

---

## 🎉 You're Ready!

Your backend API is now fully functional and ready to serve the frontend. The API provides:

✅ Complete backtest execution for all strategies  
✅ Real-time trade calculations  
✅ Summary statistics and analytics  
✅ Pivot tables for monthly P&L  
✅ Full trade logs with all details  

Start the server and test it with the provided test script!
