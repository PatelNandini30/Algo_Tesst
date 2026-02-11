# AlgoTest Clone - Complete Integration Solution

## Problem Solved ✅

The path configuration issue has been fixed. The backend now correctly looks for data files in:
- `E:\Algo_Test_Software\strikeData\Nifty_strike_data.csv`
- `E:\Algo_Test_Software\expiryData\NIFTY.csv`
- `E:\Algo_Test_Software\Filter\base2.csv`

## How to Run (Step by Step)

### Method 1: Automated Startup (Recommended)
1. Double-click `start_complete.bat` 
2. Wait for both servers to start
3. Open http://localhost:3000 in your browser

### Method 2: Manual Startup

**Terminal 1 - Backend:**
```cmd
cd E:\Algo_Test_Software\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 - Frontend:**
```cmd
cd E:\Algo_Test_Software\frontend
npm run dev
```

## Verification Steps

After starting both servers, run:
```cmd
python test_integration.py
```

This will test:
- ✅ Health endpoint
- ✅ Strategies endpoint  
- ✅ Data dates endpoint
- ✅ Backtest endpoint (with sample data)

## Expected Results

When everything is working correctly:
- **Backend**: http://localhost:8000 should show FastAPI docs
- **Frontend**: http://localhost:3000 should show the AlgoTest interface
- **API Integration**: Backtest requests should return successful results
- **Data Access**: All CSV files should be found and processed

## Troubleshooting

If you still get errors:

1. **Check ports**: Make sure ports 8000 and 3000 are free
2. **Verify data files**: Confirm files exist in the data directories
3. **Restart servers**: Stop and restart both backend and frontend
4. **Check firewall**: Ensure Windows firewall isn't blocking the ports

## API Endpoints Now Working

- `GET /health` - Server health check
- `GET /api/strategies` - Available strategy list
- `GET /api/data/dates` - Date range information
- `GET /api/expiry` - Expiry date data
- `POST /api/backtest` - Run backtests with any strategy

The integration is now complete and should work exactly like AlgoTest with proper API connectivity between frontend and backend.