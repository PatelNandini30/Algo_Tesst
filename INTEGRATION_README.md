# AlgoTest Synchronous Integration

This is a complete synchronous integration between the React frontend and Python backend for backtesting trading strategies.

## 🚀 Quick Start

### Option 1: Automated Startup (Windows)
Double-click `start_integration.bat` to automatically start both servers and open your browser.

### Option 2: Manual Startup

**1. Start Backend Server:**
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**2. Start Frontend Server (in a new terminal):**
```bash
cd frontend
npm install  # First time only
npm run dev
```

**3. Open Browser:**
- Frontend UI: http://localhost:5173
- API Documentation: http://localhost:8000/docs
- Backend Health: http://localhost:8000/health

## ✅ Verification

Run the verification script to ensure everything is working:
```bash
python verify_integration.py
```

## 🎯 Key Features

### Dynamic Strategy Building
- **No hardcoded presets** - build strategies by selecting legs
- **Automatic engine inference** based on leg combination
- **Real-time validation** of strategy combinations

### Supported Engines
1. **V1** - CE Sell + Future Buy
2. **V2** - PE Sell + Future Buy  
3. **V3** - Strike Breach Re-entry
4. **V4** - Short Strangle
5. **V5** - Protected Strategies (Call/Put)
6. **V6** - Inverse Strangle
7. **V7** - Premium-Based Selection
8. **V8** - Hedged Bull Strategy
9. **V9** - Counter-Based Expiry

### Strategy Builder
Select from these leg types:
- **CE Sell** - Sell Call Options
- **PE Sell** - Sell Put Options
- **PE Buy** - Buy Put Options (Protection)
- **Future Buy** - Buy Futures

Special Modes:
- **Premium Mode** - Target premium-based strike selection
- **Breach Mode** - Re-entry on strike breaches
- **HSL Mode** - Hard Stop Loss protection
- **Protection** - Add protective legs

## 📊 UI Components

### Left Panel
- **Instrument Settings**: Index selection, expiry window
- **Leg Builder**: Dynamic leg selection with validation
- **Quick Presets**: One-click strategy setup

### Right Panel  
- **Strategy Parameters**: Strike percentages, multipliers
- **Re-Entry Settings**: Spot adjustment rules
- **Date Range**: Backtest period selection

### Results Display
- **KPI Cards**: Total P&L, Win Rate, CAGR, Drawdown
- **Charts**: Equity curve, Drawdown, Monthly P&L heatmap
- **Trade Log**: Detailed trade-by-trade results
- **Summary Statistics**: Comprehensive performance metrics

## 🔧 Technical Architecture

### Frontend (React/Vite)
- **ConfigPanel.jsx** - Main configuration interface
- **ResultsPanel.jsx** - Results visualization
- **Tailwind CSS** - Modern styling
- **Recharts** - Data visualization

### Backend (FastAPI)
- **main.py** - FastAPI application entry point
- **routers/backtest.py** - Backtest endpoint with engine routing
- **engines/** - Individual strategy engine modules
- **Pydantic models** - Request/response validation

### Data Flow
1. User selects legs and parameters in UI
2. Frontend infers engine and builds payload
3. Backend routes to appropriate engine function
4. Engine processes data and returns results
5. Frontend displays results with charts and tables

## 🧪 Testing

### Integration Test
```bash
python test_integration.py
```

### Manual Testing
1. Select "NIFTY" as index
2. Choose "CE Sell" + "Future Buy" legs
3. Set CE Strike % to 1.0
4. Set date range (e.g., 2023-01-01 to 2023-12-31)
5. Click "Start Backtest"
6. View results in the panel below

## 📁 Project Structure

```
Algo_Test_Software/
├── backend/                 # Python FastAPI backend
│   ├── engines/            # Strategy engine modules
│   ├── routers/            # API route handlers
│   ├── main.py            # FastAPI app
│   └── routers/backtest.py # Backtest endpoint
├── frontend/               # React frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── ConfigPanel.jsx  # Main UI
│   │   │   └── ResultsPanel.jsx # Results display
│   │   └── App.jsx
│   └── package.json
├── start_integration.bat   # Automated startup script
├── verify_integration.py   # Verification script
└── test_integration.py     # Integration tests
```

## ⚠️ Important Notes

- **Positional Only**: No intraday/BTST logic (as per specification)
- **No Time Inputs**: Entry/exit times removed from UI
- **Dynamic Validation**: Invalid leg combinations show warnings
- **Exact Column Matching**: Results show only columns returned by engine
- **Backend Truth**: UI only displays what backend provides

## 🛠️ Troubleshooting

**Backend not starting:**
- Ensure Python 3.8+ and required packages are installed
- Check if port 8000 is available
- Run `pip install -r requirements.txt`

**Frontend not starting:**
- Ensure Node.js is installed
- Run `npm install` in frontend directory
- Check if port 5173 is available

**API calls failing:**
- Verify both servers are running
- Check browser console for errors
- Ensure CORS is properly configured

## 📈 Performance Tips

- Use shorter date ranges for faster backtesting
- Limit trade log display to first 50 trades
- Use "All Data" button for full historical range
- Check console for detailed logs

The integration is now fully synchronous and ready for production use!