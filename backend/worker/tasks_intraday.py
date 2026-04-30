"""Celery tasks for intraday data ingestion."""
from datetime import date
from worker.celery import celery_app
from backend.services import intraday_publish


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
