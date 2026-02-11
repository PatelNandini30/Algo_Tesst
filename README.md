# AlgoTest Clone - Options Backtesting Platform

A full-stack web-based options backtesting platform modelled after AlgoTest that allows users to configure NIFTY options strategies through a GUI and run backtests against real historical bhavcopy data.

## Features

- Configure NIFTY options strategy parameters through an intuitive GUI
- Run backtests against real historical bhavcopy data stored in CSV files
- View detailed performance analytics including equity curves, drawdowns, monthly P&L heatmaps, and trade logs
- Support for multiple strategy types (V1-V9) with various parameters
- Responsive web interface built with React and Tailwind CSS

## Technology Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | React 18 + Vite, Tailwind CSS, Recharts, Lucide React, TanStack Query |
| **Backend** | Python FastAPI, Uvicorn ASGI server, Pandas, NumPy |
| **Data Store** | Flat file system (CSV files) |
| **State Mgmt** | React `useState` + `useReducer`, TanStack Query for API caching |

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
│   │   ├── v1_ce_fut.py           # V1: CE Sell + Future Buy
│   │   ├── v2_pe_fut.py           # V2: PE Sell + Future Buy
│   │   ├── v3_strike_breach.py    # V3: Strike-Breach Re-entry
│   │   ├── v4_strangle.py         # V4: Short Strangle
│   │   ├── v5_protected.py        # V5: Protected Sell strategies
│   │   ├── v6_inverse_strangle.py # V6: Inverse-base Short Strangle
│   │   ├── v7_premium.py          # V7: Premium-based strike selection
│   │   ├── v8_hsl.py              # V8: Hard Stop Loss strategies
│   │   ├── v8_ce_pe_fut.py        # V8: CE+PE+FUT combination
│   │   └── v9_counter.py          # V9: Counter-based Put expiry
│   └── analytics.py               # Summary and pivot table functions
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
    │   │   └── ResultsPanel.jsx
    │   └── main.jsx
    └── package.json
```

## Prerequisites

- Python 3.8+
- Node.js 16+
- Historical NSE bhavcopy CSV data in the required format

## Setup Instructions

### Backend Setup

1. Navigate to the project directory:
```bash
cd e:\Algo_Test_Software
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Start the backend server:
```bash
cd backend
uvicorn main:app --reload --port 8000
```

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd e:\Algo_Test_Software\frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm run dev
```

The application will be available at `http://localhost:3000`

## Data Requirements

The application requires the following CSV data files:

### 1. `cleaned_csvs/YYYY-MM-DD.csv`
Daily bhavcopy data with columns:
- `Instrument` (OPTIDX, FUTIDX)
- `Symbol` (NIFTY, BANKNIFTY, etc.)
- `ExpiryDate` (expiry date)
- `OptionType` (CE, PE, XX)
- `StrikePrice` (strike price)
- `Close` (closing price)
- `TurnOver` (turnover amount)
- `Date` (trade date)

### 2. `expiryData/NIFTY.csv` (Weekly) and `expiryData/NIFTY_Monthly.csv`
Expiry date information with columns:
- `Previous Expiry`
- `Current Expiry`
- `Next Expiry`

### 3. `strikeData/Nifty_strike_data.csv`
Spot price data with columns:
- `Ticker` (index symbol)
- `Date` (date)
- `Close` (closing spot price)

### 4. `Filter/base2.csv`
Market regime data with columns:
- `Start` (start date)
- `End` (end date)

## API Endpoints

- `POST /api/backtest` - Run backtest with strategy parameters
- `GET /api/expiry?index=NIFTY&type=weekly` - Get expiry dates
- `GET /api/strategies` - Get supported strategies
- `GET /api/data/dates?index=NIFTY` - Get available date range

## Strategy Types

The platform supports the following strategy types:

- **V1**: CE Sell + Future Buy
- **V2**: PE Sell + Future Buy
- **V3**: Strike-Breach Re-entry
- **V4**: Short Strangle (CE Sell + PE Sell)
- **V5**: Protected Sell (with optional protective leg)
- **V6**: Inverse-base Short Strangle (operates outside base2 ranges)
- **V7**: Premium-based strike selection
- **V8**: Hard Stop Loss and CE+PE+FUT combination
- **V9**: Counter-based Put expiry

## Adding New Strategies

To add a new strategy:

1. Create a new engine file in `backend/engines/`
2. Implement the strategy logic following the existing patterns
3. Update the strategy mapping in `backend/routers/backtest.py`
4. Add the strategy to the preset list in `frontend/src/components/ConfigPanel.jsx`

## License

This project is for educational purposes.