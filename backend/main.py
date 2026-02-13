from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import sys

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Include routers
from routers import backtest, expiry, strategies

# Create the FastAPI app
app = FastAPI(
    title="AlgoTest Clone API",
    version="1.0.0",
    description="Complete backtesting API for options strategies"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(backtest.router, prefix="/api", tags=["backtest"])
app.include_router(expiry.router, prefix="/api", tags=["expiry"])
app.include_router(strategies.router, prefix="/api", tags=["strategies"])

@app.get("/")
def read_root():
    return {
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

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "AlgoTest Backend",
        "version": "1.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)