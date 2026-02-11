# AlgoTest Clone - Startup Instructions

## Prerequisites
Make sure you have:
1. Python 3.8+ installed
2. Node.js 16+ installed
3. All dependencies installed from requirements.txt

## Manual Startup Instructions

### Step 1: Start Backend Server
1. Open a new terminal/command prompt
2. Navigate to the backend directory:
   ```
   cd e:\Algo_Test_Software\backend
   ```
3. Run the backend server:
   ```
   python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

### Step 2: Start Frontend Server
1. Open another terminal/command prompt
2. Navigate to the frontend directory:
   ```
   cd e:\Algo_Test_Software\frontend
   ```
3. Install dependencies (first time only):
   ```
   npm install
   ```
4. Run the frontend server:
   ```
   npm run dev
   ```

### Step 3: Access the Application
1. Open your web browser
2. Go to: http://localhost:3000
3. The backend API will be available at: http://localhost:8000

## Troubleshooting

### If backend fails to start:
- Make sure all Python dependencies are installed: `pip install -r requirements.txt`
- Check if port 8000 is available
- Verify that all required data files exist in the data directories

### If frontend fails to start:
- Make sure Node.js is installed: `node --version`
- Check if port 3000 is available
- Try clearing node_modules and reinstalling: `rm -rf node_modules && npm install`

### Common Issues:
1. **Import errors**: Make sure __init__.py files exist in routers and engines directories
2. **Port conflicts**: Change ports in the startup commands if needed
3. **Missing dependencies**: Install all required packages from requirements.txt and package.json

## Data Requirements
Make sure the following data directories contain the required CSV files:
- `data/cleaned_csvs/` - Daily bhavcopy data files
- `data/expiryData/` - Expiry date information
- `data/strikeData/` - Strike price data
- `data/Filter/` - Market regime filter data

The application is now ready to use once both servers are running successfully!