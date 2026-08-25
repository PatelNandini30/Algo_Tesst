# Pandas 2.x compatibility - MUST be first before pandas is imported anywhere
import pandas as pd
import logging
from contextlib import asynccontextmanager

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
from fastapi.responses import ORJSONResponse
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
from routers.optimize import router as optimize_router

@asynccontextmanager
async def lifespan(app):
    try:
        from scripts.prebuild_cache import start_background_warmup
        start_background_warmup()
    except Exception as exc:
        logging.getLogger(__name__).warning(f"Warmup start failed: {exc}")
    # Preload the Midcap-overlay index-OHLC cache so the FIRST tradesheet download
    # after a (re)start can price the overlay. The native INDEX_OHLC cache is
    # per-process and loads lazily on first request; if that first request lands
    # before the cache is warm, compute_midcap_for_rows sees the engine as
    # unavailable and the Trade Sheet drops the Midcap/Combined columns. Best-effort
    # and non-fatal: a missing feather just leaves the lazy load to run later.
    for _sym in [s.strip().upper() for s in
                 os.environ.get("INDEX_OHLC_PRELOAD_SYMBOLS", "NIFTYMIDCAP100").split(",") if s.strip()]:
        try:
            from services import index_ohlc_store
            index_ohlc_store.ensure_index_ohlc_loaded(_sym)
        except Exception as exc:
            logging.getLogger(__name__).warning("index-OHLC preload for %s failed: %s", _sym, exc)
    yield

# Create the FastAPI app
app = FastAPI(
    title="AlgoTest Clone API",
    version="1.0.0",
    description="Complete backtesting API for options strategies",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

# NOT app.add_middleware(GZipMiddleware, ...). Starlette 0.27's GZipMiddleware
# never checks the response status and skips minimum_size on the streaming
# branch, so it gzipped 206 Partial Content — measured on the cached ZIP path:
# a Range request came back gzip-encoded with Content-Length stripped and
# Content-Range still describing the UNCOMPRESSED file, silently breaking the
# Range/resume support in this router and the browser's download progress bar.
# It also re-deflated already-compressed ZIPs for ~0% size gain. nginx already
# does JSON gzip in front of this (frontend/nginx.conf: gzip on; gzip_types
# application/json ...), so removing this duplicates nothing for browser
# clients; only same-origin callers that bypass nginx (LAN remote nodes on
# :8100, the API published directly on :8000) lose JSON compression, and those
# paths carry small meta JSON plus file downloads that must not be gzipped.

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
app.include_router(optimize_router, prefix="/api", tags=["optimize"])

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

def _maintenance_notice():
    """Operator-declared maintenance banner, or None.

    Set deliberately when locking the box for a rebuild:
        redis-cli set algotest:maintenance "Rebuilding backend — back in ~5 min"
        redis-cli del algotest:maintenance          # release

    Deliberately NOT inferred from health-check failures. The frontend used to
    decide "the server is restarting" whenever a few /health polls timed out —
    but a heavy sweep starves the API enough to blow the client's 4s timeout,
    so users saw a "Server is restarting…" screen while nothing had restarted
    and their jobs were running fine. Only an explicit flag shows that screen now.
    """
    try:
        from services.optimizer.result_store import _redis
        r = _redis()
        if r is None:
            return None
        v = r.get("algotest:maintenance")
        if not v:
            return None
        msg = v.decode() if isinstance(v, bytes) else str(v)
        return msg.strip() or "Maintenance in progress"
    except Exception:
        return None          # never let this check break /health


@app.get("/health")
def health_check():
    from scripts.prebuild_cache import is_warmup_complete
    from fastapi.responses import JSONResponse
    notice = _maintenance_notice()
    if not is_warmup_complete():
        # Not ready yet (background cache warmup still running after startup) —
        # 503 so Docker's own healthcheck AND the frontend's restart-overlay
        # both correctly treat this container as not-yet-usable, not just
        # "the HTTP process is listening".
        return JSONResponse(status_code=503,
                            content={"status": "warming", "maintenance": True,
                                     "message": notice or "Backend is starting up…"})
    if notice:
        return {"status": "ok", "maintenance": True, "message": notice}
    return {"status": "ok", "maintenance": False}


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
                "backtest_fast_queue_depth": client.llen("backtests_fast"),
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
        stats["memory"]["active"] = False
        stats["memory"]["note"] = (
            "not wired into the live backtest loading pipeline (no callers hit "
            "set()/get()) - these numbers are always zero, not real telemetry"
        )
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
