"""Celery tasks for intraday data ingestion and backtesting."""
from __future__ import annotations

import logging
from datetime import date

from worker.celery import celery_app
from backend.services import intraday_publish

logger = logging.getLogger(__name__)


@celery_app.task(name="intraday.ingest", queue="uploads", acks_late=True)
def ingest_intraday(
    *,
    symbol: str,
    trading_date_iso: str,
    source_path: str,
    data_root: str,
    source_format_name: str = "clean_2023",
) -> dict:
    intraday_publish.publish(
        symbol=symbol,
        trading_date=date.fromisoformat(trading_date_iso),
        source_path=source_path,
        data_root=data_root,
        source_format_name=source_format_name,
    )
    return {"status": "ok", "symbol": symbol, "trading_date": trading_date_iso}


@celery_app.task(
    name="worker.tasks_intraday.execute_intraday_backtest",
    bind=True,
    max_retries=0,
    acks_late=True,
    track_started=True,
)
def execute_intraday_backtest(self, config: dict) -> bytes:
    """Run intraday backtest and return Arrow IPC bytes."""
    from backend.services.intraday_engine import run_intraday_backtest
    symbol = config.get("symbol", "?")
    date_from = config.get("date_from", "?")
    date_to = config.get("date_to", "?")
    logger.info("[intraday] start symbol=%s range=%s..%s", symbol, date_from, date_to)
    result = run_intraday_backtest(config)
    logger.info("[intraday] done symbol=%s bytes=%d", symbol, len(result))
    return result
