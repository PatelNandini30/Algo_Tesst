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


def _normalize_sql_date(value: str, fallback: str) -> str:
    """Normalize UI/API date strings before they reach PostgreSQL date params."""
    if not value:
        return fallback
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return pd.to_datetime(value, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        logger.warning("Could not normalize date %r; using fallback %s", value, fallback)
        return fallback



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

    def _pick_any(self, cols: set, names: list[str], fallback: str):
        for name in names:
            if name in cols:
                return name
        return fallback

    def get_bhavcopy_by_date(self, date_str: str) -> pd.DataFrame:
        cols = self._table_columns("option_data")
        if not cols:
            return pd.DataFrame()
        date_col = self._pick(cols, "trade_date", "date")
        close_col = self._pick(cols, "close_price", "close")
        open_col = self._pick_any(cols, ["open_price", "open"], close_col)
        high_col = self._pick_any(cols, ["high_price", "high"], close_col)
        low_col = self._pick_any(cols, ["low_price", "low"], close_col)
        q = text(
            f"""
            SELECT
                instrument AS "Instrument",
                symbol AS "Symbol",
                expiry_date AS "ExpiryDate",
                option_type AS "OptionType",
                strike_price AS "StrikePrice",
                {open_col} AS "Open",
                {high_col} AS "High",
                {low_col} AS "Low",
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

    def get_ohlc_for_option_range(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        expiry: str,
        option_type: str,
        strike: float,
    ):
        """
        Return (max_high, min_low) for a specific option contract across a date range.
        Uses idx_option_symbol_date_expiry_strike_type — one indexed heap-read per window
        instead of one full-date bhavcopy query per day.
        Returns None if no data found.
        """
        cols = self._table_columns("option_data")
        if not cols:
            return None
        date_col = self._pick(cols, "trade_date", "date")
        high_col = self._pick_any(cols, ["high_price", "high"], None)
        low_col = self._pick_any(cols, ["low_price", "low"], None)
        close_col = self._pick(cols, "close_price", "close")
        if high_col is None or low_col is None:
            return None
        expiry_col = self._pick(cols, "expiry_date", "expiry")
        # Per-VALUE settled-price substitution: replace ONLY the zero side.
        # When high > 0, use it as-is (pre-existing behavior). When high == 0,
        # substitute settled_price (or close as last resort). Same rule applied
        # independently to low. Matches the Rust feather's get_ohlc_range logic.
        q = text(
            f"""
            SELECT
                MAX(CASE WHEN {high_col} > 0 THEN {high_col}
                         ELSE COALESCE(settled_price, {close_col}) END) AS max_high,
                MIN(CASE WHEN {low_col} > 0 THEN {low_col}
                         ELSE COALESCE(settled_price, {close_col}) END) AS min_low
            FROM option_data
            WHERE symbol = :symbol
              AND {date_col} >= :from_date
              AND {date_col} <= :to_date
              AND {expiry_col} = :expiry
              AND option_type = :option_type
              AND ABS(strike_price - :strike) <= 0.5
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(q, {
                "symbol": str(symbol).upper(),
                "from_date": from_date,
                "to_date": to_date,
                "expiry": expiry,
                "option_type": str(option_type).upper(),
                "strike": float(strike),
            }).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return float(row[0]), float(row[1])

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

        from_date = _normalize_sql_date(from_date, "1900-01-01")
        to_date = _normalize_sql_date(to_date, "2099-12-31")

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

    def get_filter_date_segments(self, filter_key: str) -> pd.DataFrame:
        """Ordered [start_date, end_date] rows for one folder-based filter."""
        cols = self._table_columns("filter_date_sets")
        if not cols:
            return pd.DataFrame(columns=["start_date", "end_date"])
        q = text(
            """
            SELECT start_date, end_date
            FROM filter_date_sets
            WHERE filter_key = :fk
            ORDER BY seq
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn, params={"fk": filter_key})
        if not df.empty:
            df["start_date"] = pd.to_datetime(df["start_date"])
            df["end_date"] = pd.to_datetime(df["end_date"])
        return df

    def get_filter_date_catalog(self) -> pd.DataFrame:
        """
        One row per (group, filter) with segment count and date range, ordered
        for display. Empty frame when the table is absent/empty.
        """
        cols = self._table_columns("filter_date_sets")
        if not cols:
            return pd.DataFrame(columns=[
                "group_key", "group_label", "group_order", "filter_key",
                "filter_label", "filter_order", "seg_count", "min_start", "max_end",
            ])
        q = text(
            """
            SELECT group_key, group_label, MIN(group_order)  AS group_order,
                   filter_key, filter_label, MIN(filter_order) AS filter_order,
                   COUNT(*) AS seg_count,
                   MIN(start_date) AS min_start, MAX(end_date) AS max_end
            FROM filter_date_sets
            GROUP BY group_key, group_label, filter_key, filter_label
            ORDER BY group_order, filter_order, filter_label
            """
        )
        with self.engine.begin() as conn:
            df = pd.read_sql(q, conn)
        return df

    def get_available_date_range(self, symbol: str = None) -> dict:
        cols = self._table_columns("option_data")
        if not cols:
            return {"min_date": None, "max_date": None}
        date_col = self._pick(cols, "trade_date", "date")
        params = {}
        where = ""
        if symbol:
            where = "WHERE symbol = :symbol"
            params["symbol"] = symbol.upper()
        q = text(f"SELECT MIN({date_col}) AS min_date, MAX({date_col}) AS max_date FROM option_data {where}")
        with self.engine.begin() as conn:
            row = conn.execute(q, params).first()
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
        open_col = self._pick_any(cols, ["open_price", "open"], close_col)
        high_col = self._pick_any(cols, ["high_price", "high"], close_col)
        low_col = self._pick_any(cols, ["low_price", "low"], close_col)
        
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
                {open_col} AS "Open",
                {high_col} AS "High",
                {low_col} AS "Low",
                {close_col} AS "Close",
                COALESCE(contracts, 0) AS "Contracts",
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

        Keep this select list intentionally narrow.  The live production index
        idx_option_core_query covers these columns and `close`, so PostgreSQL can
        serve the bulk load as an index-only scan.  Adding OHLC/instrument here
        forces heap reads plus a sort on large date ranges.

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
        high_col  = self._pick_any(cols, ["high_price", "high"], close_col)
        low_col   = self._pick_any(cols, ["low_price",  "low"],  close_col)
        open_col  = self._pick_any(cols, ["open_price", "open"], close_col)

        from_date = _normalize_sql_date(from_date, "1900-01-01")
        to_date = _normalize_sql_date(to_date, "2099-12-31")

        q = text(
            f"""
            SELECT
                {date_col}      AS "Date",
                symbol          AS "Symbol",
                expiry_date     AS "ExpiryDate",
                option_type     AS "OptionType",
                strike_price    AS "StrikePrice",
                {open_col}      AS "Open",
                {high_col}      AS "High",
                {low_col}       AS "Low",
                {close_col}     AS "Close",
                COALESCE(contracts, 0) AS "Contracts",
                COALESCE(settled_price, {close_col}) AS "SettledPrice"
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
        if df.empty:
            return df
        df["Date"]       = pd.to_datetime(df["Date"])
        df["ExpiryDate"] = pd.to_datetime(df["ExpiryDate"])
        return df
