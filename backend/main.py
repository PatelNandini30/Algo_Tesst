# Pandas 2.x compatibility - MUST be first before pandas is imported anywhere
import pandas as pd
import uvloop
uvloop.install()

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
from fastapi.middleware.gzip import GZipMiddleware
import os
import sys
import psutil
import redis as redis_lib

from database import get_pool_status

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

# Compress payloads > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)

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
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    return {"database": get_pool_status()}

@app.get("/health/stats")
async def health_stats():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    redis_status = {"status": "unavailable"}
    redis_url = os.getenv("REDIS_URL")

    if redis_url:
        try:
            client = redis_lib.Redis.from_url(redis_url)
            info = client.info("memory")
            redis_status = {
                "used_memory_mb": round(info.get("used_memory", 0) / 1e6, 1),
                "max_memory_mb": round(info.get("maxmemory", 0) / 1e6, 1),
                "backtest_queue_depth": client.llen("backtests"),
                "upload_queue_depth": client.llen("uploads"),
            }
        except Exception as exc:
            redis_status = {"error": str(exc)}

    return {
        "host": {
            "ram_total_gb": round(mem.total / 1e9, 2),
            "ram_used_gb": round(mem.used / 1e9, 2),
            "ram_free_gb": round(mem.available / 1e9, 2),
            "ram_percent": mem.percent,
            "swap_used_gb": round(swap.used / 1e9, 2),
            "swap_percent": swap.percent,
            "cpu_percent": psutil.cpu_percent(interval=0.1),
        },
        "redis": redis_status,
        "status": "healthy",
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
        stats["database"] = get_pool_status()
    except Exception as e:
        stats["database"] = {"error": str(e)}
    
    return stats

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
