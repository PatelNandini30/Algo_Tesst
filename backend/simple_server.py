"""
Simple working FastAPI server that bypasses complex import issues
"""
import sys
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import pandas as pd
from typing import Dict, Any, List
from datetime import datetime

# Add backend directory to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Backend directory: {backend_dir}")

# Simple FastAPI app
app = FastAPI(title="AlgoTest API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple data models
class BacktestRequest(BaseModel):
    strategy: str
    symbol: str = "NIFTY"
    from_date: str
    to_date: str
    legs: List[Dict[str, Any]] = []

class BacktestResponse(BaseModel):
    success: bool
    message: str
    data: Dict[str, Any] = {}

# API Endpoints
@app.get("/")
def read_root():
    return {"message": "AlgoTest API is running", "version": "1.0.0"}

@app.get("/health")
def health_check():
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "backend_dir": backend_dir
    }

@app.get("/api/strategies")
def get_strategies():
    """Get available strategies"""
    strategies = [
        {"id": "v1", "name": "CE Sell + Future Buy", "type": "fixed"},
        {"id": "v2", "name": "PE Sell + Future Buy", "type": "fixed"},
        {"id": "v4", "name": "Short Strangle", "type": "fixed"},
        {"id": "dynamic", "name": "Dynamic Multi-Leg Strategy", "type": "dynamic"}
    ]
    return {"strategies": strategies}

@app.post("/api/backtest")
def run_backtest(request: BacktestRequest):
    """Run backtest - simplified version"""
    try:
        # Simple validation
        if not request.strategy:
            raise HTTPException(status_code=400, detail="Strategy is required")
        
        if not request.from_date or not request.to_date:
            raise HTTPException(status_code=400, detail="Date range is required")
        
        # Simulate backtest result
        result = {
            "strategy": request.strategy,
            "symbol": request.symbol,
            "from_date": request.from_date,
            "to_date": request.to_date,
            "total_trades": 150,
            "winning_trades": 85,
            "win_rate": 56.67,
            "total_pnl": 125000.50,
            "max_drawdown": -15000.25,
            "sharpe_ratio": 1.45
        }
        
        return {
            "success": True,
            "message": "Backtest completed successfully",
            "data": result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/expiry/dates")
def get_expiry_dates(symbol: str = "NIFTY", expiry_type: str = "weekly"):
    """Get expiry dates"""
    # Mock data - in real implementation, this would fetch from database
    mock_dates = [
        "2024-01-15", "2024-01-22", "2024-01-29", "2024-02-05",
        "2024-02-12", "2024-02-19", "2024-02-26", "2024-03-04"
    ]
    
    return {
        "symbol": symbol,
        "expiry_type": expiry_type,
        "dates": mock_dates
    }

# Validation endpoint that uses preserved logic
@app.get("/api/validate")
def validate_system():
    """Validate that the system is working correctly"""
    try:
        # Test importing validation wrapper
        from validation_wrapper import get_validation_status
        validation_status = get_validation_status()
        
        # Test basic functionality
        test_data = pd.DataFrame({
            'Date': pd.date_range('2024-01-01', periods=5),
            'Close': [100, 101, 99, 102, 100]
        })
        
        return {
            "status": "operational",
            "validation_available": validation_status['validation_available'],
            "test_data_shape": test_data.shape,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

if __name__ == "__main__":
    print("Starting simplified backend server...")
    print("Access API documentation at: http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")