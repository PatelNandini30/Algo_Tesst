# Pandas 2.x compatibility - MUST be first before pandas is imported anywhere
import pandas as pd

# Patch DataFrame.sort_values to handle 'by' keyword (removed in pandas 2.x)
_orig_df_sort = pd.DataFrame.sort_values
def _patched_df_sort(self, by=None, **kwargs):
    if by is not None:
        by_list = [by] if isinstance(by, str) else list(by)
        # pandas 2.x doesn't accept 'by' keyword - pass positionally
        return _orig_df_sort(self, by_list, **kwargs)
    return _orig_df_sort(self, **kwargs)
pd.DataFrame.sort_values = _patched_df_sort

# Patch Series.sort_values - pandas 2.x removed 'by' param from Series  
_orig_series_sort = pd.Series.sort_values
def _patched_series_sort(self, by=None, **kwargs):
    # For Series, we just ignore 'by' since you can't sort a Series by column name
    return _orig_series_sort(self, **kwargs)
pd.Series.sort_values = _patched_series_sort

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import sys

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Include routers
from routers import backtest, expiry, strategies
from routers.upload import router as upload_router

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
app.include_router(upload_router, prefix="/api", tags=["data"])

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

@app.get("/cache/stats")
def cache_stats():
    """Get cache statistics for monitoring."""
    stats = {
        "status": "ok"
    }
    
    try:
        from services.backtest_cache import get_backtest_cache
        redis_cache = get_backtest_cache()
        stats["redis"] = redis_cache.get_stats()
    except Exception as e:
        stats["redis"] = {"error": str(e)}
    
    try:
        from services.data_memory_cache import get_memory_cache
        memory_cache = get_memory_cache()
        stats["memory"] = memory_cache.get_stats()
    except Exception as e:
        stats["memory"] = {"error": str(e)}
    
    try:
        from database import get_pool_status
        stats["database"] = get_pool_status()
    except Exception as e:
        stats["database"] = {"error": str(e)}
    
    return stats

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
