"""CRUD for the `intraday_imports` Postgres manifest table."""
from datetime import date
from typing import Optional, Dict, Any

from database import get_engine


def get(symbol: str, trading_date: date) -> Optional[Dict[str, Any]]:
    eng = get_engine()
    with eng.connect() as conn:
        result = conn.exec_driver_sql(
            """SELECT id, symbol, trading_date, source_format, source_sha256,
                      parquet_path, snapshot_path, row_count, expiry_count, ingested_at
               FROM intraday_imports
               WHERE symbol = %s AND trading_date = %s""",
            (symbol, trading_date),
        )
        row = result.fetchone()
        if row is None:
            return None
        return dict(row._mapping)


def upsert(
    *,
    symbol: str,
    trading_date: date,
    source_format: str,
    source_sha256: str,
    parquet_path: str,
    snapshot_path: str,
    row_count: int,
    expiry_count: int,
) -> None:
    eng = get_engine()
    with eng.begin() as conn:
        conn.exec_driver_sql(
            """INSERT INTO intraday_imports
               (symbol, trading_date, source_format, source_sha256,
                parquet_path, snapshot_path, row_count, expiry_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (symbol, trading_date) DO UPDATE SET
                 source_format = EXCLUDED.source_format,
                 source_sha256 = EXCLUDED.source_sha256,
                 parquet_path = EXCLUDED.parquet_path,
                 snapshot_path = EXCLUDED.snapshot_path,
                 row_count = EXCLUDED.row_count,
                 expiry_count = EXCLUDED.expiry_count,
                 ingested_at = NOW()""",
            (
                symbol, trading_date, source_format, source_sha256,
                parquet_path, snapshot_path, row_count, expiry_count,
            ),
        )
