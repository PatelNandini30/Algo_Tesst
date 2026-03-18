# Pandas 2.x compatibility - MUST be first before pandas is imported anywhere
import asyncio
import pandas as pd
import uvloop
uvloop.install()

# Patch DataFrame.sort_values to handle 'by' keyword (removed in pandas 2.x)
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

from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import os
import sys

from prometheus_fastapi_instrumentator import Instrumentator

from base import bulk_load_options
from database import get_pool_status
from services.redis_cache import RedisBacktestCache, configure_cache

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Include routers
from routers import backtest, expiry, strategies
from routers.upload import router as upload_router

# Create the FastAPI app
REDIS_URL = os.getenv("REDIS_URL")
BACKTEST_CACHE_TTL = int(os.getenv("BACKTEST_CACHE_TTL", "86400"))
if REDIS_URL:
    configure_cache(RedisBacktestCache(REDIS_URL, ttl=BACKTEST_CACHE_TTL))

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

Instrumentator().instrument(app).expose(app)

_warm_executor = ThreadPoolExecutor(max_workers=1)


@app.on_event("startup")
async def warm_cache_on_startup():
    async def _do_warm():
        try:
            await asyncio.sleep(5)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                _warm_executor,
                bulk_load_options,
                "NIFTY",
                "2000-01-01",
                "2026-12-31"
            )
            print("Startup cache warming complete")
        except Exception as exc:
            print(f"[STARTUP] Cache warming failed: {exc}")

    asyncio.create_task(_do_warm())

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
