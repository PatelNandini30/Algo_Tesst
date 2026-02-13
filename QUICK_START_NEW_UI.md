# Quick Start Guide - New Professional UI

## What's New

I've created a completely redesigned professional UI that matches the AlgoTest style you requested:

### Key Features:
1. **Clean Strategy Selection** - Browse all available strategies in a sidebar
2. **Dynamic Parameter Configuration** - Parameters automatically adjust based on selected strategy
3. **Smart Filters** - Toggle filters on/off, only apply when you want them
4. **Professional Design** - Modern, clean interface with proper spacing and colors
5. **Better UX** - Clear visual feedback, loading states, and error messages

## How to Start

### 1. Start the Backend Server

```bash
cd backend
python start_server.py
```

The backend will run on `http://localhost:8000`

### 2. Start the Frontend

Open a new terminal:

```bash
cd frontend
npm install  # Only needed first time
npm run dev
```

The frontend will run on `http://localhost:3000`

## How to Use the New UI

### Step 1: Select a Strategy
- Click on any strategy from the left sidebar
- You'll see strategies like:
  - V1: CE Sell + Future Buy
  - V2: PE Sell + Future Buy
  - V4: Short Strangle
  - V8: Hedged Bull
  - And more...

### Step 2: Configure Parameters
- The right panel shows all parameters for the selected strategy
- Each parameter has a clear description
- Default values are pre-filled

### Step 3: Apply Filters (Optional)
- Click "Show Filters" button at the top
- Set your preferences:
  - Index (NIFTY, BANKNIFTY, etc.)
  - Expiry Window
  - Date Range
- Filters only apply when you run the backtest

### Step 4: Run Backtest
- Click the "Run Backtest" button
- Wait for results (loading indicator will show)
- Results will display with:
  - KPI cards (Total P&L, Win Rate, CAGR, etc.)
  - Equity curve chart
  - Drawdown chart
  - Monthly P&L heatmap
  - Trade-by-trade log

## API Endpoints

The new UI uses these endpoints:

- `GET /api/strategies` - Fetch all available strategies
- `POST /api/backtest` - Run a backtest with selected strategy
- `GET /api/data/dates` - Get available date range

## Troubleshooting

### Backend not starting?
```bash
cd backend
python -c "import sys; print(sys.path)"
python start_server.py
```

### Frontend not loading?
```bash
cd frontend
npm install
npm run dev
```

### API calls failing?
- Check backend is running on port 8000
- Check frontend proxy in `vite.config.js`
- Open browser console (F12) to see errors

## Next Steps

You can now:
1. Test different strategies
2. Adjust parameters and see results
3. Compare multiple backtests
4. Export results to CSV

The old "Legacy Strategies" tab is still available if you need it, but the new UI is much cleaner and easier to use!
