from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from celery.result import AsyncResult

from backend.schemas.intraday import IntradayBacktestRequest
from backend.services import backtest_cache
from worker.celery import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intraday", tags=["intraday"])

INTRADAY_DATA_DIR = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")
CELERY_TIMEOUT_S = int(os.environ.get("INTRADAY_CELERY_TIMEOUT", "60"))

ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.stream"


@router.get("/health")
def intraday_health():
    data_dir = Path(INTRADAY_DATA_DIR)
    symbols_ready = []
    earliest, latest = None, None
    if data_dir.exists():
        for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            snaps = data_dir / sym / "snapshots"
            if snaps.exists() and any(snaps.glob("*.arrow")):
                symbols_ready.append(sym)
                dates = sorted(f.stem for f in snaps.glob("*.arrow"))
                if dates:
                    if earliest is None or dates[0] < earliest:
                        earliest = dates[0]
                    if latest is None or dates[-1] > latest:
                        latest = dates[-1]
    snap_count = sum(
        len(list((data_dir / sym / "snapshots").glob("*.arrow")))
        for sym in symbols_ready
        if (data_dir / sym / "snapshots").exists()
    )
    return {
        "snapshot_count": snap_count,
        "symbols_ready": symbols_ready,
        "earliest_date": earliest,
        "latest_date": latest,
        "cache_warm": bool(symbols_ready),
    }


@router.post("/backtest")
async def run_intraday_backtest(req: IntradayBacktestRequest):
    cache_key = req.canonical_hash()
    slow_path = req.requires_slow_path()

    cached = backtest_cache.get_intraday_result(cache_key)
    if cached is not None:
        logger.info("[intraday] cache HIT key=%s", cache_key)
        return Response(content=cached, media_type=ARROW_CONTENT_TYPE)

    queue = "backtests_intraday_slow" if slow_path else "backtests_intraday"
    logger.info("[intraday] cache MISS key=%s queue=%s", cache_key, queue)

    task = celery_app.send_task(
        "worker.tasks_intraday.execute_intraday_backtest",
        args=[req.to_engine_config()],
        queue=queue,
    )
    try:
        arrow_bytes: bytes = task.get(timeout=CELERY_TIMEOUT_S, propagate=True)
    except Exception as exc:
        logger.error("[intraday] task failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    backtest_cache.set_intraday_result(cache_key, arrow_bytes)

    headers = {}
    if slow_path:
        headers["X-Slow-Path"] = "true"

    return Response(content=arrow_bytes, media_type=ARROW_CONTENT_TYPE, headers=headers)
