import logging
import os
from typing import Optional

import msgpack
import pandas as pd
import redis
import threading
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from database import reset_engine

logger = logging.getLogger(__name__)



def _generate_date_chunks(from_date: str, to_date: str, days: int = 120):
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d")
    except Exception:
        start = datetime.strptime("1900-01-01", "%Y-%m-%d")
    try:
        end = datetime.strptime(to_date, "%Y-%m-%d")
    except Exception:
        end = datetime.strptime("2099-12-31", "%Y-%m-%d")

    if start > end:
        start, end = end, start

    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=days - 1))
        yield current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        current = chunk_end + timedelta(days=1)



class MarketDataRepository:
    """
    PostgreSQL-backed repository for market data.
    Supports both legacy schema (002) and refactored schema (003).
    """

    _trading_calendar_cache_df: Optional[pd.DataFrame] = None
    _trading_calendar_cache_lock = threading.Lock()
    _columns_cache: dict = {}
    _columns_cache_lock = threading.Lock()

    def __init__(self, engine):
        self.engine = engine

    def _table_columns(self, table_name: str):
        cls = self.__class__
        if table_name in cls._columns_cache:
            return cls._columns_cache[table_name]
        with cls._columns_cache_lock:
            if table_name in cls._columns_cache:
                return cls._columns_cache[table_name]
            q = text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :t
                """
            )
            with self.engine.begin() as conn:
                cols = {r[0] for r in conn.execute(q, {"t": table_name}).fetchall()}
            cls._columns_cache[table_name] = cols
            return cols

    def _pick(self, cols: set, preferred: str, fallback: str):
        return preferred if preferred in cols else fallback

    def get_bhavcopy_by_date(self, date_str: str) -> pd.DataFrame:
        cols = self._table_columns("option_data")
        if not cols:
            return pd.DataFrame()
        date_col = self._pick(cols, "trade_date", "date")
        close_col = self._pick(cols, "close_price", "close")
        q = text(
            f"""
            SELECT
                instrument AS "Instrument",
                symbol AS "Symbol",
                expiry_date AS "ExpiryDate",
                option_type AS "OptionType",
                strike_price AS "StrikePrice",
                {close_col} AS "Close",
                {date_col} AS "Date"
            FROM option_data
            WHERE {date_col} = :d
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn, params={"d": date_str})
        if df.empty:
            return df
        df["Date"] = pd.to_datetime(df["Date"])
        df["ExpiryDate"] = pd.to_datetime(df["ExpiryDate"])
        return df

    def get_spot_data(self, symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
        cols = self._table_columns("spot_data")
        if not cols:
            return pd.DataFrame(columns=["Date", "Close"])
        date_col = self._pick(cols, "trade_date", "date")
        close_col = self._pick(cols, "close_price", "close")
        q = text(
            f"""
            SELECT
                {date_col} AS "Date",
                {close_col} AS "Close"
            FROM spot_data
            WHERE symbol = :symbol
              AND {date_col} >= :from_date
              AND {date_col} <= :to_date
            ORDER BY {date_col}
            """
        )

        from_date = from_date or "1900-01-01"
        to_date = to_date or "2099-12-31"

        try:
            # Spot data is ~250 rows/year — single query is always faster
            with self.engine.begin() as conn:
                df = pd.read_sql(
                    q,
                    conn,
                    params={
                        "symbol": symbol.upper(),
                        "from_date": from_date,
                        "to_date": to_date
                    },
                )
        except OperationalError as exc:
            logger.warning("Spot bulk fetch failed, resetting engine: %s", exc)
            reset_engine()
            raise

        if df is None or df.empty:
            return pd.DataFrame(columns=["Date", "Close"])

        df.drop_duplicates(inplace=True)
        if df.empty:
            return pd.DataFrame(columns=["Date", "Close"])
        df["Date"] = pd.to_datetime(df["Date"])
        return df[["Date", "Close"]]

    def get_expiry_data(self, symbol: str, expiry_type: str) -> pd.DataFrame:
        cols = self._table_columns("expiry_calendar")
        if not cols:
            return pd.DataFrame(columns=["Previous Expiry", "Current Expiry", "Next Expiry"])
        q = text(
            """
            SELECT
                previous_expiry AS "Previous Expiry",
                current_expiry AS "Current Expiry",
                next_expiry AS "Next Expiry"
            FROM expiry_calendar
            WHERE symbol = :symbol AND expiry_type = :expiry_type
            ORDER BY current_expiry
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn, params={"symbol": symbol.upper(), "expiry_type": expiry_type.lower()})
        for c in ["Previous Expiry", "Current Expiry", "Next Expiry"]:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c])
        return df

    def get_super_trend_segments(self, config: str, symbol: str = "NIFTY") -> pd.DataFrame:
        cols = self._table_columns("super_trend_segments")
        if not cols:
            return pd.DataFrame(columns=["start_date", "end_date"])
        q = text(
            """
            SELECT start_date, end_date
            FROM super_trend_segments
            WHERE symbol = :symbol AND config = :config
            ORDER BY start_date
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn, params={"symbol": symbol.upper(), "config": config})
        if not df.empty:
            df["start_date"] = pd.to_datetime(df["start_date"])
            df["end_date"] = pd.to_datetime(df["end_date"])
        return df

    def get_available_date_range(self) -> dict:
        cols = self._table_columns("option_data")
        if not cols:
            return {"min_date": None, "max_date": None}
        date_col = self._pick(cols, "trade_date", "date")
        q = text(f"SELECT MIN({date_col}) AS min_date, MAX({date_col}) AS max_date FROM option_data")
        with self.engine.begin() as conn:
            row = conn.execute(q).first()
        return {"min_date": row[0], "max_date": row[1]}

    def get_trading_calendar(self, from_date: str, to_date: str) -> pd.DataFrame:
        """
        Get all trading dates in a date range.
        Uses spot_data table first (smaller), falls back to option_data if needed.
        """
        cls = self.__class__
        if cls._trading_calendar_cache_df is not None:
            return self._filter_trading_calendar(from_date, to_date)

        # First try spot_data (much smaller table)
        cols = self._table_columns("spot_data")
        if cols:
            date_col = self._pick(cols, "trade_date", "date")
            q = text(
                f"""
                SELECT DISTINCT {date_col} AS date
                FROM spot_data
                WHERE {date_col} >= :from_date AND {date_col} <= :to_date
                ORDER BY {date_col}
                """
            )
            try:
                # Load the FULL spot_data calendar (no date filter) into class cache
                q_full = text(
                    f"""
                    SELECT DISTINCT {date_col} AS date
                    FROM spot_data
                    ORDER BY {date_col}
                    """
                )
                with self.engine.begin() as conn:
                    df_full = pd.read_sql(q_full, conn)
                if not df_full.empty:
                    df_full["date"] = pd.to_datetime(df_full["date"])
                    cls._trading_calendar_cache_df = df_full
                    return self._filter_trading_calendar(from_date, to_date)
            except Exception as e:
                print(f"[WARN] spot_data query failed: {e}")
        
        # Fallback to option_data (larger table)
        cols = self._table_columns("option_data")
        if not cols:
            return pd.DataFrame(columns=["date"])
        date_col = self._pick(cols, "trade_date", "date")
        self._ensure_trading_calendar_cache(date_col)
        return self._filter_trading_calendar(from_date, to_date)

    def _ensure_trading_calendar_cache(self, date_col: str):
        cls = self.__class__
        if cls._trading_calendar_cache_df is not None:
            return
        redis_client = None
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        try:
            redis_client = redis.Redis.from_url(redis_url)
            cached = redis_client.get("trading_calendar:full")
            if cached:
                records = msgpack.unpackb(cached, raw=False)
                df = pd.DataFrame(records)
                df["date"] = pd.to_datetime(df["date"])
                cls._trading_calendar_cache_df = df
                return
        except Exception:
            pass
        with self.__class__._trading_calendar_cache_lock:
            if cls._trading_calendar_cache_df is not None:
                return
            q = text(
                f"""
                SELECT DISTINCT {date_col} AS date
                FROM option_data
                ORDER BY {date_col}
                """
            )
            with self.engine.begin() as conn:
                df = pd.read_sql(q, conn)
            if df.empty:
                self.__class__._trading_calendar_cache_df = pd.DataFrame(columns=["date"])
                return
            df["date"] = pd.to_datetime(df["date"])
            self.__class__._trading_calendar_cache_df = df
            if redis_client:
                try:
                    redis_client.setex(
                        "trading_calendar:full",
                        86400,
                        msgpack.packb(
                            [
                                {
                                    "date": row["date"].strftime("%Y-%m-%d")
                                    if hasattr(row["date"], "strftime")
                                    else str(row["date"])
                                }
                                for row in df.to_dict("records")
                            ],
                            use_bin_type=True,
                        ),
                    )
                except Exception:
                    pass

    def _filter_trading_calendar(self, from_date: str, to_date: str) -> pd.DataFrame:
        df = self.__class__._trading_calendar_cache_df
        if df is None or df.empty:
            return pd.DataFrame(columns=["date"])
        mask = (df["date"] >= pd.to_datetime(from_date)) & (df["date"] <= pd.to_datetime(to_date))
        return df.loc[mask].copy()

    def get_bhavcopy_bulk(self, from_date: str, to_date: str, symbols: list = None) -> pd.DataFrame:
        """Bulk load all bhavcopy data for a date range in one query."""
        cols = self._table_columns("option_data")
        if not cols:
            return pd.DataFrame()
        date_col = self._pick(cols, "trade_date", "date")
        close_col = self._pick(cols, "close_price", "close")
        
        symbol_filter = ""
        if symbols:
            symbol_list = ", ".join([f"'{s.upper()}'" for s in symbols])
            symbol_filter = f"AND symbol IN ({symbol_list})"
        
        q = text(
            f"""
            SELECT
                instrument AS "Instrument",
                symbol AS "Symbol",
                expiry_date AS "ExpiryDate",
                option_type AS "OptionType",
                strike_price AS "StrikePrice",
                {close_col} AS "Close",
                {date_col} AS "Date"
            FROM option_data
            WHERE {date_col} >= :from_date
              AND {date_col} <= :to_date
              {symbol_filter}
            ORDER BY {date_col}, symbol, strike_price, option_type
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn, params={"from_date": from_date, "to_date": to_date})
        if df.empty:
            return df
        df["Date"] = pd.to_datetime(df["Date"])
        df["ExpiryDate"] = pd.to_datetime(df["ExpiryDate"])
        return df

    def get_spot_data_bulk(self, symbols: list, from_date: str, to_date: str) -> pd.DataFrame:
        """Bulk load spot data for multiple symbols in one query."""
        cols = self._table_columns("spot_data")
        if not cols:
            return pd.DataFrame()
        date_col = self._pick(cols, "trade_date", "date")
        close_col = self._pick(cols, "close_price", "close")
        
        symbol_list = ", ".join([f"'{s.upper()}'" for s in symbols])
        
        q = text(
            f"""
            SELECT
                symbol AS "Symbol",
                {date_col} AS "Date",
                {close_col} AS "Close"
            FROM spot_data
            WHERE symbol IN ({symbol_list})
              AND {date_col} >= :from_date
              AND {date_col} <= :to_date
            ORDER BY symbol, {date_col}
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn, params={"from_date": from_date, "to_date": to_date})
        if df.empty:
            return df
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    def get_options_bulk(self, symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
        """
        Bulk load ALL option data for a symbol across date range.
        Returns DataFrame with only required columns for fast in-memory lookups.

        FIX #2: Removed 60-day chunking loop.
        Original code fired 40+ sequential DB round-trips for a 7-year range
        (~43 chunks × ~150ms overhead = 6+ seconds in connection overhead alone).
        A single parameterised query over the full range is faster because:
          - One TCP round-trip instead of 43
          - PostgreSQL query planner can use the composite index optimally
          - pd.read_sql chunksize= streams rows without multiple connections
        The 60-day chunks were added for per-day memory safety, but bulk_load
        immediately converts this into a Polars DataFrame so memory is fine.
        """
        cols = self._table_columns("option_data")
        if not cols:
            return pd.DataFrame()
        date_col  = self._pick(cols, "trade_date", "date")
        close_col = self._pick(cols, "close_price", "close")

        from_date = from_date or "1900-01-01"
        to_date   = to_date   or "2099-12-31"

        q = text(
            f"""
            SELECT
                {date_col}      AS "Date",
                symbol          AS "Symbol",
                expiry_date     AS "ExpiryDate",
                option_type     AS "OptionType",
                strike_price    AS "StrikePrice",
                {close_col}     AS "Close"
            FROM option_data
            WHERE symbol      = :symbol
              AND {date_col}  >= :from_date
              AND {date_col}  <= :to_date
            ORDER BY {date_col}, expiry_date, strike_price, option_type
            """
        )

        try:
            dfs = []
            with self.engine.begin() as conn:
                for chunk in pd.read_sql(
                    q,
                    conn,
                    params={
                        "symbol": symbol.upper(),
                        "from_date": from_date,
                        "to_date": to_date,
                    },
                    chunksize=150_000,
                ):
                    if not chunk.empty:
                        dfs.append(chunk)
        except OperationalError as exc:
            logger.warning("Option bulk fetch failed, resetting engine: %s", exc)
            reset_engine()
            raise

        if not dfs:
            return pd.DataFrame()

        df = pd.concat(dfs, ignore_index=True)
        df.drop_duplicates(inplace=True)
        if df.empty:
            return df
        df["Date"]       = pd.to_datetime(df["Date"])
        df["ExpiryDate"] = pd.to_datetime(df["ExpiryDate"])
        return df
