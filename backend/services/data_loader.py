"""
High-Performance Data Loader using Polars

PHASE 5: High-Performance Data Loading

Key optimizations:
1. Polars instead of pandas (10-100x faster)
2. Only fetch required columns
3. Parameterized queries (SQL injection safe)
4. Batch loading for large datasets
5. Connection pooling from database.py
6. In-memory caching for frequently accessed data
7. Performance logging

Usage:
    from services.data_loader import get_loader, pl
    
    # Single option premium
    premium = get_loader().get_option_premium(
        symbol="NIFTY",
        date="2024-01-15",
        strike_price=22000,
        option_type="CE",
        expiry_date="2024-01-25"
    )
    
    # Bulk options for strike selection
    df = get_loader().get_strikes_for_selection(
        symbol="NIFTY",
        date="2024-01-15",
        expiry_date="2024-01-25",
        option_type="CE"
    )
"""

import hashlib
import os
import time
import logging
import tempfile
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from collections import OrderedDict
from functools import lru_cache
from datetime import datetime, date

import msgpack
import redis
import polars as pl
from sqlalchemy import text

# Import engine from database.py (uses connection pooling)
from database import get_engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _resolve_parquet_cache_dir() -> Path:
    candidates = []
    env_dir = os.getenv("PARQUET_CACHE_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path("/tmp/algotest_parquet_cache"))
    candidates.append(Path(tempfile.gettempdir()) / "algotest_parquet_cache")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok")
            test_file.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue

    fallback = Path(tempfile.gettempdir()) / f"algotest_parquet_cache_{os.getpid()}"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


PARQUET_CACHE_DIR = _resolve_parquet_cache_dir()

_LOOKUP_CACHE_TTL = int(os.getenv("LOOKUP_CACHE_TTL", "86400"))
_LOOKUP_KEY_PREFIX = "bulk"
_FULL_RANGE_FROM = "2000-01-01"
_FULL_RANGE_TO = "2026-12-31"
_redis_client: Optional[redis.Redis] = None
_full_range_loaded = False
_full_range_symbol: Optional[str] = None

def _get_redis_client() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    if not redis_url:
        return None

    try:
        client = redis.Redis.from_url(redis_url)
        client.ping()
        _redis_client = client
        return client
    except redis.RedisError as exc:
        logger.warning("[REDIS] Unable to connect to cache: %s", exc)
        return None


def _lookup_cache_key(symbol: str, from_date: str, to_date: str) -> str:
    normalized_symbol = symbol.upper()
    return f"{_LOOKUP_KEY_PREFIX}:{normalized_symbol}:{from_date}:{to_date}"


def load_lookup_cache_from_redis(
    symbol: str,
    from_date: str,
    to_date: str
) -> Optional[Dict[Tuple[str, str, int, str, str], float]]:
    client = _get_redis_client()
    if client is None:
        return None

    try:
        raw = client.get(_lookup_cache_key(symbol, from_date, to_date))
        if not raw:
            return None

        entries = msgpack.unpackb(raw, raw=False)
        lookup: Dict[Tuple[str, str, int, str, str], float] = {}
        for entry in entries:
            if not entry or len(entry) != 6:
                continue
            key = (entry[0], entry[1], entry[2], entry[3], entry[4])
            value = entry[5]
            lookup[key] = value

        return lookup
    except (redis.RedisError, msgpack.exceptions.UnpackException) as exc:
        logger.warning("[REDIS] Failed to load lookup cache: %s", exc)
    except Exception as exc:  # pragma: no cover - guard against corrupted payloads
        logger.warning("[REDIS] Corrupted lookup cache: %s", exc)
    return None


def store_lookup_cache_in_redis(
    symbol: str,
    from_date: str,
    to_date: str,
    lookup: Dict[Tuple[str, str, int, str, str], float]
) -> None:
    client = _get_redis_client()
    if client is None or not lookup:
        return

    payload = [
        [key[0], key[1], key[2], key[3], key[4], value]
        for key, value in lookup.items()
    ]
    try:
        packed = msgpack.packb(payload, use_bin_type=True)
        client.set(
            _lookup_cache_key(symbol, from_date, to_date),
            packed,
            ex=_LOOKUP_CACHE_TTL
        )
    except redis.RedisError as exc:
        logger.warning("[REDIS] Failed to store lookup cache: %s", exc)


def _full_range_cache_key(symbol: str) -> str:
    normalized_symbol = symbol.upper()
    return f"{_LOOKUP_KEY_PREFIX}:{normalized_symbol}:full"


def _load_full_range_from_redis(symbol: str) -> Optional[pl.DataFrame]:
    client = _get_redis_client()
    if client is None:
        return None

    try:
        raw = client.get(_full_range_cache_key(symbol))
        if not raw:
            return None

        payload = msgpack.unpackb(raw, raw=False)
        if not payload:
            return None

        return pl.DataFrame(payload)
    except (redis.RedisError, msgpack.exceptions.UnpackException) as exc:
        logger.warning("[REDIS] Failed to load full range cache: %s", exc)
    except Exception as exc:
        logger.warning("[REDIS] Corrupted full range cache: %s", exc)
    return None


def _store_full_range_in_redis(symbol: str, df: pl.DataFrame) -> None:
    client = _get_redis_client()
    if client is None or df is None or df.is_empty():
        return

    # Large full-range payloads are expensive to serialize and can exceed Redis maxmemory.
    # Keep Redis for smaller reusable snapshots and rely on Parquet for the heavy cache.
    if df.height > int(os.getenv("REDIS_FULL_RANGE_MAX_ROWS", "200000")):
        logger.info(
            "[REDIS] Skipping full range cache for %s (%s rows exceeds threshold)",
            symbol.upper(),
            df.height,
        )
        return

    try:
        df = df.with_columns([
            pl.col("Date").dt.strftime("%Y-%m-%d"),
            pl.col("ExpiryDate").dt.strftime("%Y-%m-%d")
        ])
        payload = [
            {
                key: (_serialize_cache_value(value) if value is not None else None)
                for key, value in row.items()
            }
            for row in df.to_dicts()
        ]
        packed = msgpack.packb(payload, use_bin_type=True)
        client.set(
            _full_range_cache_key(symbol),
            packed,
            ex=_LOOKUP_CACHE_TTL
        )
        logger.debug("[REDIS] Stored full range cache for %s", symbol.upper())
    except redis.RedisError as exc:
        logger.warning("[REDIS] Failed to store full range cache: %s", exc)


def _serialize_cache_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _as_date_value(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _is_full_range_loaded(symbol: str) -> bool:
    return (
        _full_range_loaded
        and _full_range_symbol == symbol.upper()
        and _bulk_options_df is not None
        and not _bulk_options_df.is_empty()
    )


class LRUCache:
    """
    Simple LRU Cache with max size.
    Avoids multiple queries for same data.
    """
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._cache: OrderedDict = OrderedDict()
    
    def get(self, key: Tuple) -> Optional[Any]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None
    
    def put(self, key: Tuple, value: Any):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self.max_size:
            self._cache.popitem(last=False)
    
    def clear(self):
        self._cache.clear()
    
    def __len__(self):
        return len(self._cache)


# Module-level shared caches — survive across requests in the same process
_shared_date_cache = LRUCache(max_size=1000)
_shared_premium_cache = LRUCache(max_size=5000)
_shared_trading_days_cache = LRUCache(max_size=100)
_shared_expiry_cache = LRUCache(max_size=200)
_shared_spot_cache = LRUCache(max_size=2000)
_shared_bulk_strikes_cache = LRUCache(max_size=2000)


class PerformanceTimer:
    """Context manager for timing operations"""
    def __init__(self, operation: str, log_level: int = logging.INFO):
        self.operation = operation
        self.log_level = log_level
        self.start_time = None
        
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
        
    def __exit__(self, *args):
        elapsed = time.perf_counter() - self.start_time
        logger.log(self.log_level, f"[PERF] {self.operation}: {elapsed*1000:.2f}ms")


class HighPerformanceLoader:
    """
    High-performance data loader using Polars.
    
    Optimizations:
    - Polars: 10-100x faster than pandas for columnar operations
    - Explicit columns: Only fetch what we need
    - Parameterized queries: SQL injection safe, better query plans
    - Connection pooling: Reuses connections from database.py pool
    - In-memory caching: Avoid repeated queries for same data
    - Batch loading: Load multiple dates in single query
    """
    
    def __init__(self, database_url: str = None):
        # Use pooled engine from database.py
        self._engine = get_engine()
        
        # Cache for schema info
        self._column_cache: Dict[str, List[str]] = {}
        
        # In-memory caches for avoiding repeated queries
        # Cache size ~1000 dates = ~1GB for options data
        self._date_cache = _shared_date_cache
        self._premium_cache = _shared_premium_cache
        self._trading_days_cache = _shared_trading_days_cache
        self._expiry_cache = _shared_expiry_cache
        self._spot_cache = _shared_spot_cache
        self._bulk_strikes_cache = _shared_bulk_strikes_cache
        
        logger.info(f"[INIT] HighPerformanceLoader initialized with Polars + Caching")
    
    def _execute_query(
        self, 
        query: str, 
        params: Dict[str, Any] = None,
        timeout: int = 30
    ) -> List[Dict]:
        """Execute query and return results as list of dicts"""
        with PerformanceTimer(f"SQL: {query[:80]}...", logging.DEBUG):
            with self._engine.connect() as conn:
                if params:
                    result = conn.execute(text(query), params)
                else:
                    result = conn.execute(text(query))
                
                # Convert to list of dicts for Polars
                columns = result.keys()
                rows = [dict(zip(columns, row)) for row in result.fetchall()]
                return rows
    
    def _query_to_polars(
        self, 
        query: str, 
        params: Dict[str, Any] = None
    ) -> pl.DataFrame:
        """Execute query and return Polars DataFrame"""
        rows = self._execute_query(query, params)
        if not rows:
            return pl.DataFrame()
        return pl.DataFrame(rows)
    
    # =========================================================================
    # OPTION DATA QUERIES - WITH CACHING
    # =========================================================================
    
    def get_option_premium(
        self,
        symbol: str,
        date: str,
        strike_price: float,
        option_type: str,
        expiry_date: str
    ) -> Optional[float]:
        """
        Get single option premium - most common backtest query.
        Uses caching to avoid repeated queries.
        
        Returns: float or None
        """
        # Check cache first
        cache_key = (symbol.upper(), date, strike_price, option_type.upper(), expiry_date)
        cached = self._premium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"[CACHE] get_option_premium HIT: {cache_key}")
            return cached
        
        query = """
            SELECT close 
            FROM option_data 
            WHERE symbol = :symbol 
              AND date = :date 
              AND expiry_date = :expiry_date 
              AND option_type = :option_type 
              AND strike_price = :strike_price
            LIMIT 1
        """
        
        with PerformanceTimer(f"get_option_premium({symbol}, {date}, {strike_price})"):
            rows = self._execute_query(query, {
                "symbol": symbol.upper(),
                "date": date,
                "expiry_date": expiry_date,
                "option_type": option_type.upper(),
                "strike_price": strike_price
            })
        
        result = float(rows[0]["close"]) if rows else None
        
        # Cache the result
        if result is not None:
            self._premium_cache.put(cache_key, result)
        
        return result
    
    def get_strikes_for_selection(
        self,
        symbol: str,
        date: str,
        expiry_date: str,
        option_type: str = None
    ) -> pl.DataFrame:
        """
        Get all strikes for a symbol/date/expiry - used for strike selection.
        Uses caching to avoid repeated queries for same date.
        
        Returns: Polars DataFrame with columns [strike_price, close, turnover]
        """
        # Check cache first
        cache_key = ("strikes", symbol.upper(), date, expiry_date, option_type.upper() if option_type else "ALL")
        cached_df = self._date_cache.get(cache_key)
        if cached_df is not None:
            logger.debug(f"[CACHE] get_strikes_for_selection HIT: {cache_key}")
            return cached_df
        
        if option_type:
            query = """
                SELECT 
                    strike_price,
                    close,
                    COALESCE(turnover, 0) as turnover
                FROM option_data 
                WHERE symbol = :symbol 
                  AND date = :date 
                  AND expiry_date = :expiry_date
                  AND option_type = :option_type
                ORDER BY strike_price
            """
            params = {
                "symbol": symbol.upper(),
                "date": date,
                "expiry_date": expiry_date,
                "option_type": option_type.upper()
            }
        else:
            query = """
                SELECT 
                    strike_price,
                    close,
                    COALESCE(turnover, 0) as turnover,
                    option_type
                FROM option_data 
                WHERE symbol = :symbol 
                  AND date = :date 
                  AND expiry_date = :expiry_date
                  AND option_type IN ('CE', 'PE')
                ORDER BY strike_price, option_type
            """
            params = {
                "symbol": symbol.upper(),
                "date": date,
                "expiry_date": expiry_date
            }
        
        with PerformanceTimer(f"get_strikes_for_selection({symbol}, {date}, {expiry_date})"):
            df = self._query_to_polars(query, params)
        
        # Cache the result
        self._date_cache.put(cache_key, df)
        
        logger.debug(f"[PERF] Loaded {len(df)} strikes for {symbol} on {date}")
        return df
    
    def get_date_options(
        self,
        symbol: str,
        date: str,
        expiry_dates: List[str] = None
    ) -> pl.DataFrame:
        """
        Batch load all options for a single date (all expiries).
        More efficient than multiple calls to get_strikes_for_selection.
        
        Returns: Polars DataFrame with all options for the date
        """
        # Check cache first
        cache_key = ("date_options", symbol.upper(), date)
        cached_df = self._date_cache.get(cache_key)
        if cached_df is not None:
            logger.debug(f"[CACHE] get_date_options HIT: {cache_key}")
            return cached_df
        
        if expiry_dates:
            query = """
                SELECT 
                    strike_price,
                    close,
                    expiry_date,
                    option_type,
                    COALESCE(turnover, 0) as turnover
                FROM option_data 
                WHERE symbol = :symbol 
                  AND date = :date 
                  AND expiry_date = ANY(:expiry_dates)
                  AND option_type IN ('CE', 'PE')
                ORDER BY expiry_date, option_type, strike_price
            """
            params = {
                "symbol": symbol.upper(),
                "date": date,
                "expiry_dates": expiry_dates
            }
        else:
            query = """
                SELECT 
                    strike_price,
                    close,
                    expiry_date,
                    option_type,
                    COALESCE(turnover, 0) as turnover
                FROM option_data 
                WHERE symbol = :symbol 
                  AND date = :date 
                  AND option_type IN ('CE', 'PE')
                ORDER BY expiry_date, option_type, strike_price
            """
            params = {
                "symbol": symbol.upper(),
                "date": date
            }
        
        with PerformanceTimer(f"get_date_options({symbol}, {date})"):
            df = self._query_to_polars(query, params)
        
        # Cache the result
        self._date_cache.put(cache_key, df)
        
        logger.debug(f"[PERF] Loaded {len(df)} options for {symbol} on {date}")
        return df
    
    def get_multi_date_options(
        self,
        symbol: str,
        dates: List[str]
    ) -> pl.DataFrame:
        """
        Batch load options for multiple dates in a single query.
        Most efficient for loading trading calendar data.
        
        Returns: Polars DataFrame with all options for all dates
        """
        if not dates:
            return pl.DataFrame()
        
        # Check cache for each date, collect uncached dates
        uncached_dates = []
        result_dfs = []
        
        for date in dates:
            cache_key = ("date_options", symbol.upper(), date)
            cached_df = self._date_cache.get(cache_key)
            if cached_df is not None:
                result_dfs.append(cached_df)
            else:
                uncached_dates.append(date)
        
        if uncached_dates:
            # Query all uncached dates at once
            query = """
                SELECT 
                    strike_price,
                    close,
                    expiry_date,
                    option_type,
                    COALESCE(turnover, 0) as turnover,
                    date
                FROM option_data 
                WHERE symbol = :symbol 
                  AND date = ANY(:dates)
                  AND option_type IN ('CE', 'PE')
                ORDER BY date, expiry_date, option_type, strike_price
            """
            
            with PerformanceTimer(f"get_multi_date_options({symbol}, {len(uncached_dates)} dates)"):
                df = self._query_to_polars(query, {
                    "symbol": symbol.upper(),
                    "dates": uncached_dates
                })
            
            # Split by date and cache each
            if not df.is_empty():
                for date in uncached_dates:
                    date_value = _as_date_value(date)
                    if date_value is None:
                        continue
                    date_df = df.filter(pl.col("date").cast(pl.Date) == date_value)
                    cache_key = ("date_options", symbol.upper(), date)
                    self._date_cache.put(cache_key, date_df)
                result_dfs.append(df)
        
        # Combine all DataFrames
        if result_dfs:
            return pl.concat(result_dfs)
        return pl.DataFrame()
    
    def get_option_chain(
        self,
        symbol: str,
        date: str,
        expiry_dates: List[str],
        option_types: List[str] = None
    ) -> pl.DataFrame:
        """
        Get option chain for multiple expiries - for bulk analysis.
        
        Returns: Polars DataFrame with all strikes across expiries
        """
        option_types = option_types or ['CE', 'PE']
        
        query = """
            SELECT 
                strike_price,
                close,
                expiry_date,
                option_type,
                COALESCE(turnover, 0) as turnover
            FROM option_data 
            WHERE symbol = :symbol 
              AND date = :date 
              AND expiry_date = ANY(:expiry_dates)
              AND option_type = ANY(:option_types)
            ORDER BY expiry_date, option_type, strike_price
        """
        
        with PerformanceTimer(f"get_option_chain({symbol}, {date}, {len(expiry_dates)} expiries)"):
            df = self._query_to_polars(query, {
                "symbol": symbol.upper(),
                "date": date,
                "expiry_dates": expiry_dates,
                "option_types": option_types
            })
        
        logger.debug(f"[PERF] Loaded {len(df)} rows for option chain")
        return df
    
    def get_bulk_options(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        columns: List[str] = None
    ) -> pl.DataFrame:
        """
        Bulk load option data for date range - for complete backtest.
        Uses multi-date loading with caching.
        
        Args:
            symbol: Index symbol (NIFTY, BANKNIFTY, etc.)
            from_date: Start date
            to_date: End date
            columns: List of columns to fetch (default: minimal set)
        
        Returns:
            Polars DataFrame with all options in date range
        """
        # Get trading days first (cached)
        trading_days_df = self.get_trading_days(symbol, from_date, to_date)
        
        if trading_days_df.is_empty:
            return pl.DataFrame()
        
        dates = trading_days_df["date"].to_list()
        
        # Use batch loading
        with PerformanceTimer(f"get_bulk_options({symbol}, {from_date} to {to_date})"):
            df = self.get_multi_date_options(symbol, dates)
        
        if columns and not df.is_empty:
            # Select only requested columns
            available_cols = [c for c in columns if c in df.columns]
            df = df.select(available_cols)
        
        logger.info(f"[PERF] Bulk loaded {len(df):,} rows for {symbol} ({from_date} to {to_date})")
        return df
    
    # =========================================================================
    # FUTURE DATA QUERIES
    # =========================================================================
    
    def get_future_price(
        self,
        symbol: str,
        date: str,
        expiry_date: str
    ) -> Optional[float]:
        """Get future price for symbol/date/expiry."""
        query = """
            SELECT close 
            FROM option_data 
            WHERE symbol = :symbol 
              AND date = :date 
              AND expiry_date = :expiry_date
              AND instrument LIKE 'FUT%'
            LIMIT 1
        """
        
        with PerformanceTimer(f"get_future_price({symbol}, {date})"):
            rows = self._execute_query(query, {
                "symbol": symbol.upper(),
                "date": date,
                "expiry_date": expiry_date
            })
        
        return float(rows[0]["close"]) if rows else None
    
    def get_all_futures_for_date(
        self,
        symbol: str,
        date: str
    ) -> pl.DataFrame:
        """Get all futures for a symbol/date."""
        query = """
            SELECT expiry_date, close
            FROM option_data 
            WHERE symbol = :symbol 
              AND date = :date 
              AND instrument LIKE 'FUT%'
            ORDER BY expiry_date
        """
        
        with PerformanceTimer(f"get_all_futures({symbol}, {date})"):
            return self._query_to_polars(query, {
                "symbol": symbol.upper(),
                "date": date
            })
    
    # =========================================================================
    # SPOT DATA QUERIES - WITH CACHING
    # =========================================================================
    
    def get_spot_price(
        self,
        symbol: str,
        date: str
    ) -> Optional[float]:
        """Get spot/underlying price for symbol/date. Uses caching."""
        # Check cache first
        cache_key = (symbol.upper(), date)
        cached = self._spot_cache.get(cache_key)
        if cached is not None:
            return cached
        
        # Try spot_data table first
        query = """
            SELECT close 
            FROM spot_data 
            WHERE symbol = :symbol 
              AND date = :date
            LIMIT 1
        """
        
        try:
            with PerformanceTimer(f"get_spot_price({symbol}, {date})"):
                rows = self._execute_query(query, {
                    "symbol": symbol.upper(),
                    "date": date
                })
            
            if rows:
                result = float(rows[0]["close"])
                self._spot_cache.put(cache_key, result)
                return result
        except Exception as e:
            logger.debug(f"spot_data query failed: {e}")
        
        # Fallback: Get ATM option as proxy
        result = self._get_atm_option_close(symbol, date)
        if result is not None:
            self._spot_cache.put(cache_key, result)
        return result
    
    def _get_atm_option_close(
        self, 
        symbol: str, 
        date: str
    ) -> Optional[float]:
        """Fallback: Get ATM option close as proxy for spot."""
        # Get approximate ATM strike
        query = """
            SELECT AVG(strike_price) as atm_strike
            FROM option_data 
            WHERE symbol = :symbol 
              AND date = :date 
              AND option_type = 'CE'
            LIMIT 1
        """
        
        rows = self._execute_query(query, {
            "symbol": symbol.upper(),
            "date": date
        })
        
        if not rows or not rows[0].get("atm_strike"):
            return None
        
        atm_strike = round(float(rows[0]["atm_strike"]) / 50) * 50
        
        # Get close for ATM strike
        query2 = """
            SELECT close 
            FROM option_data 
            WHERE symbol = :symbol 
              AND date = :date 
              AND strike_price = :strike
              AND option_type = 'CE'
            LIMIT 1
        """
        
        rows2 = self._execute_query(query2, {
            "symbol": symbol.upper(),
            "date": date,
            "strike": atm_strike
        })
        
        return float(rows2[0]["close"]) if rows2 else None
    
    def get_spot_data_range(
        self,
        symbol: str,
        from_date: str,
        to_date: str
    ) -> pl.DataFrame:
        """Get spot data for date range."""
        query = """
            SELECT date, close
            FROM spot_data 
            WHERE symbol = :symbol 
              AND date >= :from_date 
              AND date <= :to_date
            ORDER BY date
        """
        
        with PerformanceTimer(f"get_spot_data_range({symbol}, {from_date} to {to_date})"):
            return self._query_to_polars(query, {
                "symbol": symbol.upper(),
                "from_date": from_date,
                "to_date": to_date
            })
    
    # =========================================================================
    # TRADING CALENDAR - WITH CACHING
    # =========================================================================
    # TRADING DAYS CACHE - avoid repeated DISTINCT queries on 73GB table
    # =========================================================================
    _trading_days_cache: Dict[str, pl.DataFrame] = {}
    _trading_days_cache_order: List[str] = []
    _MAX_TRADING_DAYS_CACHE = 10  # Keep last 10 date ranges
    
    def _get_cached_trading_days(self, symbol: str, from_date: str, to_date: str) -> Optional[pl.DataFrame]:
        """Check module-level cache for trading days."""
        cache_key = f"trading_days:{symbol.upper()}:{from_date}:{to_date}"
        if cache_key in self._trading_days_cache:
            return self._trading_days_cache[cache_key]
        return None
    
    def _set_cached_trading_days(self, symbol: str, from_date: str, to_date: str, df: pl.DataFrame):
        """Cache trading days with LRU eviction."""
        cache_key = f"trading_days:{symbol.upper()}:{from_date}:{to_date}"
        self._trading_days_cache[cache_key] = df
        self._trading_days_cache_order.append(cache_key)
        # Evict old entries
        while len(self._trading_days_cache_order) > self._MAX_TRADING_DAYS_CACHE:
            old_key = self._trading_days_cache_order.pop(0)
            self._trading_days_cache.pop(old_key, None)
    
    def get_trading_days(
        self,
        symbol: str,
        from_date: str,
        to_date: str
    ) -> pl.DataFrame:
        """Get all trading days for symbol in date range. Uses caching."""
        # Check cache first (both instance and module level)
        cache_key = ("trading_days", symbol.upper(), from_date, to_date)
        cached_df = self._trading_days_cache.get(cache_key)
        if cached_df is not None:
            logger.debug(f"[CACHE] get_trading_days HIT: {cache_key}")
            return cached_df
        
        # Check module-level cache
        cached_df = self._get_cached_trading_days(symbol, from_date, to_date)
        if cached_df is not None:
            # Promote to instance cache
            self._trading_days_cache.put(cache_key, cached_df)
            return cached_df
        
        # Try spot_data first (much smaller) - use raw SQL with proper column detection
        from repositories.market_data_repository import MarketDataRepository
        from database import get_engine
        try:
            repo = MarketDataRepository(get_engine())
            spot_df = repo.get_spot_data([symbol], from_date, to_date)
            if not spot_df.empty:
                # Extract unique dates from spot data
                dates = spot_df['Date'].unique()
                dates = sorted(dates)
                result_df = pl.DataFrame({"Date": dates})
                # Cache it
                self._set_cached_trading_days(symbol, from_date, to_date, result_df)
                self._trading_days_cache.put(cache_key, result_df)
                logger.debug(f"[PERF] Found {len(result_df)} trading days from spot_data")
                return result_df
        except Exception as e:
            logger.debug(f"[CACHE] spot_data query failed: {e}")
        
        # Fallback to option_data with proper column detection
        cols = self._table_columns("option_data")
        if not cols:
            return pl.DataFrame()
        date_col = self._pick(cols, "trade_date", "date")
        
        query = text(f"""
            SELECT DISTINCT {date_col} AS Date
            FROM option_data
            WHERE symbol = :symbol
              AND {date_col} >= :from_date
              AND {date_col} <= :to_date
            ORDER BY {date_col}
        """)
        
        with PerformanceTimer(f"get_trading_days({symbol}, {from_date} to {to_date})"):
            with get_engine().begin() as conn:
                df = pd.read_sql(query, conn, params={
                    "symbol": symbol.upper(),
                    "from_date": from_date,
                    "to_date": to_date
                })
        
        if not df.empty:
            result_df = pl.from_pandas(df)
        else:
            result_df = pl.DataFrame()
        
        # Cache the result
        self._trading_days_cache.put(cache_key, result_df)
        self._set_cached_trading_days(symbol, from_date, to_date, result_df)
        
        logger.debug(f"[PERF] Found {len(result_df)} trading days for {symbol}")
        return result_df
    
    def get_date_range(self) -> tuple:
        """Get min/max dates in database."""
        query = """
            SELECT MIN(date) as min_date, MAX(date) as max_date 
            FROM option_data
        """
        
        rows = self._execute_query(query)
        if rows:
            return (rows[0]["min_date"], rows[0]["max_date"])
        return (None, None)
    
    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================
    
    def clear_cache(self):
        """Clear expired cache entries (keeps recently-loaded data)."""
        # For module-level shared caches, only clear if explicitly requested
        # by passing force=True — do not wipe on routine cleanup
        logger.info("[CACHE] Cache clear requested (module-level caches preserved)")

    def force_clear_cache(self):
        """Force-clear all shared caches. Call only between symbol switches."""
        _shared_date_cache.clear()
        _shared_premium_cache.clear()
        _shared_trading_days_cache.clear()
        _shared_expiry_cache.clear()
        _shared_spot_cache.clear()
        logger.info("[CACHE] All shared caches force-cleared")
    
    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "date_cache_size": len(self._date_cache),
            "premium_cache_size": len(self._premium_cache),
            "trading_days_cache_size": len(self._trading_days_cache),
            "expiry_cache_size": len(self._expiry_cache),
            "spot_cache_size": len(self._spot_cache)
        }
    
    # =========================================================================
    # EXPIRY CALENDAR - WITH CACHING
    # =========================================================================
    
    def get_expiry_dates(
        self,
        symbol: str,
        expiry_type: str,
        from_date: str,
        to_date: str
    ) -> pl.DataFrame:
        """Get expiry dates for symbol. Uses caching."""
        # Check cache first
        cache_key = ("expiry", symbol.upper(), expiry_type.lower(), from_date, to_date)
        cached_df = self._expiry_cache.get(cache_key)
        if cached_df is not None:
            logger.debug(f"[CACHE] get_expiry_dates HIT: {cache_key}")
            return cached_df
        
        query = """
            SELECT previous_expiry, current_expiry, next_expiry
            FROM expiry_calendar 
            WHERE symbol = :symbol 
              AND expiry_type = :expiry_type
              AND current_expiry >= :from_date
              AND current_expiry <= :to_date
            ORDER BY current_expiry
        """
        
        with PerformanceTimer(f"get_expiry_dates({symbol}, {expiry_type})"):
            df = self._query_to_polars(query, {
                "symbol": symbol.upper(),
                "expiry_type": expiry_type.lower(),
                "from_date": from_date,
                "to_date": to_date
            })
        
        # Cache the result
        self._expiry_cache.put(cache_key, df)
        
        return df


# ============================================================================
# PHASE 1: BULK DATA LOADING - THE KEY OPTIMIZATION
# ============================================================================
# These module-level variables hold the bulk-loaded DataFrames.
# Each backtest request loads its data here, engines filter in-memory.

_bulk_options_df: Optional[pl.DataFrame] = None
_bulk_spot_df: Optional[pl.DataFrame] = None
_bulk_expiry_df: Optional[pl.DataFrame] = None
_bulk_loaded_key: Optional[str] = None


def _compute_missing_date_ranges(cmin: str, cmax: str, rfrom: str, rto: str) -> list:
    """Given a cache covering [cmin, cmax] and a request for [rfrom, rto], return a list
    of (from, to) tuples for the date ranges that still need to be loaded from DB.
    Returns [] when the cache fully covers the request."""
    from datetime import datetime, timedelta

    def _parse(s):
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()

    def _shift(d, n):
        return (d + timedelta(days=n)).isoformat()

    cmin_d, cmax_d = _parse(cmin), _parse(cmax)
    rfrom_d, rto_d = _parse(rfrom), _parse(rto)
    missing = []
    if rfrom_d < cmin_d:
        missing.append((rfrom_d.isoformat(), _shift(cmin_d, -1)))
    if rto_d > cmax_d:
        missing.append((_shift(cmax_d, 1), rto_d.isoformat()))
    return missing


def _merge_polars_with_pandas(existing: Optional[pl.DataFrame], new_dfs: list) -> Optional[pl.DataFrame]:
    """Concat existing pl.DataFrame with one or more pandas DataFrames; dedupe on
    the option primary key and sort by Date. Used by the delta-load path so we
    never keep stale duplicates if a date range overlaps."""
    parts = []
    if existing is not None and not existing.is_empty():
        parts.append(existing)
    for df in new_dfs:
        if df is not None and not df.empty:
            parts.append(pl.from_pandas(df))
    if not parts:
        return existing
    if len(parts) == 1:
        combined = parts[0]
    else:
        # `diagonal_relaxed`, not `vertical_relaxed`: the cached frame and a fresh
        # DB slice can differ in COLUMN COUNT (e.g. a 12-col feather-derived cache
        # vs a 13-col DB query that added a column). vertical_relaxed only relaxes
        # dtypes and raises "schema lengths differ" on a column-count mismatch,
        # which crashed every delta-load after a feather rebuild. diagonal unions
        # the columns and null-fills the gaps, so the merge is robust to either
        # source's schema; the dedup + downstream reads use a fixed column subset,
        # so the extra null column is harmless.
        combined = pl.concat(parts, how="diagonal_relaxed")
        try:
            combined = combined.unique(
                subset=["Date", "Symbol", "ExpiryDate", "OptionType", "StrikePrice"],
                keep="last",
            )
        except Exception:
            pass
    try:
        combined = combined.sort("Date")
    except Exception:
        pass
    return combined


def bulk_load(symbol: str, from_date: str, to_date: str) -> dict:
    """
    Load ALL option data for symbol/date-range into memory ONCE.
    This is the "on-ramp to the new highway" - call this before the engine loop.

    After this call, all lookups happen in-memory (microseconds).

    FIX: Always validate that cached data covers the FULL requested date range.
    Previously, if data was "loaded" for a symbol, it would skip DB queries even
    if the cached data only covered a smaller range (e.g., 2025 only instead of 2024-2026).

    Returns dict with stats about loaded data.
    """
    global _bulk_options_df, _bulk_spot_df, _bulk_expiry_df, _bulk_loaded_key, _full_range_loaded, _full_range_symbol

    symbol_upper = symbol.upper()
    cache_key = _full_range_cache_key(symbol_upper)
    key = hashlib.md5(cache_key.encode()).hexdigest()
    parquet_path = Path(PARQUET_CACHE_DIR) / f"{key}.parquet"

    cached_data_valid = False
    # Date ranges still needed from DB. None = full range needed; [] = cache covers all;
    # [(from, to), ...] = delta-load only these missing slices.
    delta_ranges: Optional[list] = None

    if _is_full_range_loaded(symbol_upper) and _bulk_options_df is not None:
        # Validate cached data covers the requested date range
        min_date = str(_bulk_options_df["Date"].min())[:10]
        max_date = str(_bulk_options_df["Date"].max())[:10]
        if min_date <= from_date and max_date >= to_date:
            logger.info(f"[BULK] Full range already loaded for {symbol_upper} ({min_date} to {max_date})")
            cached_data_valid = True
            delta_ranges = []
        else:
            # Don't discard! Compute only the missing slices.
            delta_ranges = _compute_missing_date_ranges(min_date, max_date, from_date, to_date)
            logger.info(
                "[BULK] In-memory cache (%s->%s) partial for request (%s->%s); delta-loading %s",
                min_date, max_date, from_date, to_date, delta_ranges,
            )
            cached_data_valid = True
    else:
        if parquet_path.exists():
            age = time.time() - os.path.getmtime(parquet_path)
            # Historical option data doesn't change — 30-day TTL avoids a 113s
            # full DB reload every 24 hours on an HDD-backed PostgreSQL.
            parquet_ttl = int(os.environ.get("PARQUET_CACHE_TTL_SECONDS", str(30 * 86400)))
            if age < parquet_ttl:
                try:
                    start_cache = time.perf_counter()
                    _bulk_options_df = pl.read_parquet(parquet_path)
                    # Invalidate Parquet caches built before Contracts column was
                    # added — the strike-shift validator needs it to detect zero-
                    # turnover strikes. Re-fetching from Postgres is the only way
                    # to populate it.
                    if (_bulk_options_df is not None
                            and not _bulk_options_df.is_empty()
                            and ("Contracts" not in _bulk_options_df.columns
                                 or "SettledPrice" not in _bulk_options_df.columns)):
                        logger.info("[BULK] Parquet cache missing Contracts/SettledPrice column — invalidating to force fresh DB load")
                        try:
                            parquet_path.unlink()
                        except Exception:
                            pass
                        _bulk_options_df = None
                        raise RuntimeError("parquet cache invalidated: missing Contracts/SettledPrice")
                    _full_range_loaded = True
                    _full_range_symbol = symbol_upper
                    _bulk_loaded_key = cache_key
                    elapsed_cache = time.perf_counter() - start_cache
                    logger.info(f"[BULK] Loaded from Parquet cache in {elapsed_cache:.2f}s")

                    if _bulk_options_df is not None and not _bulk_options_df.is_empty():
                        min_date = str(_bulk_options_df["Date"].min())[:10]
                        max_date = str(_bulk_options_df["Date"].max())[:10]
                        if min_date > from_date or max_date < to_date:
                            # Don't discard! Delta-load only the missing slices.
                            delta_ranges = _compute_missing_date_ranges(min_date, max_date, from_date, to_date)
                            logger.info(
                                "[BULK] Parquet (%s->%s) partial for request (%s->%s); delta-loading %s",
                                min_date, max_date, from_date, to_date, delta_ranges,
                            )
                            cached_data_valid = True
                        else:
                            cached_data_valid = True
                            delta_ranges = []
                except Exception as exc:
                    logger.warning(f"[BULK] Parquet cache load failed: {exc}")
                    _bulk_options_df = None

        if not _is_full_range_loaded(symbol_upper):
            cached_df = _load_full_range_from_redis(symbol_upper)
            if cached_df is not None and not cached_df.is_empty():
                min_date = str(cached_df["Date"].min())[:10]
                max_date = str(cached_df["Date"].max())[:10]
                if min_date <= from_date and max_date >= to_date:
                    _bulk_options_df = cached_df
                    _full_range_loaded = True
                    _full_range_symbol = symbol_upper
                    _bulk_loaded_key = cache_key
                    cached_data_valid = True
                    delta_ranges = []
                    logger.info("[BULK] Loaded from Redis cache for %s (%s to %s)", symbol_upper, min_date, max_date)
                else:
                    _bulk_options_df = cached_df
                    _full_range_loaded = True
                    _full_range_symbol = symbol_upper
                    _bulk_loaded_key = cache_key
                    delta_ranges = _compute_missing_date_ranges(min_date, max_date, from_date, to_date)
                    cached_data_valid = True
                    logger.info(
                        "[BULK] Redis cache (%s->%s) partial for request (%s->%s); delta-loading %s",
                        min_date, max_date, from_date, to_date, delta_ranges,
                    )

    need_full_range_load = not cached_data_valid
    if need_full_range_load:
        load_mode = "FULL DB LOAD"
    elif delta_ranges:
        load_mode = f"DELTA LOAD {delta_ranges}"
    else:
        load_mode = "using cache"
    logger.info(
        "[BULK] Loading %s %s -> %s (%s)",
        symbol_upper,
        from_date,
        to_date,
        load_mode,
    )
    start_time = time.perf_counter()

    try:
        from engines.generic_multi_leg import _bhav_pandas_cache
        _bhav_pandas_cache.clear()
        logger.debug("[BULK] Cleared bhav pandas cache")
    except Exception:
        pass  # engine not imported yet — that's fine

    from repositories.market_data_repository import MarketDataRepository
    from database import get_engine
    from concurrent.futures import ThreadPoolExecutor

    repo = MarketDataRepository(get_engine())

    # Decide what to fetch from DB:
    #   need_full_range_load=True → no cache at all → query full requested range
    #   delta_ranges=[] → cache covers everything → no options query needed
    #   delta_ranges=[...] → cache is partial → query only the missing slices
    options_load_ranges: list = []
    if need_full_range_load:
        options_load_ranges = [(from_date, to_date)]
    elif delta_ranges:
        options_load_ranges = list(delta_ranges)

    with ThreadPoolExecutor(max_workers=max(3, len(options_load_ranges) + 2)) as executor:
        options_futures = [
            executor.submit(repo.get_options_bulk, symbol_upper, r_from, r_to)
            for (r_from, r_to) in options_load_ranges
        ]
        spot_future = executor.submit(repo.get_spot_data, symbol_upper, from_date, to_date)
        expiry_future = executor.submit(repo.get_expiry_data, symbol_upper, "weekly")

        options_results = [f.result() for f in options_futures]
        spot_df = spot_future.result()
        expiry_df = expiry_future.result()

    options_loaded_from_db = any(df is not None and not df.empty for df in options_results)

    if need_full_range_load:
        # Replacing whatever was in memory with a fresh full-range load.
        merged_options = options_results[0] if options_results else None
        if merged_options is not None and not merged_options.empty:
            _bulk_options_df = pl.from_pandas(merged_options)
        else:
            # UNCONDITIONAL reset, not `elif _bulk_options_df is None:`. A
            # full-range load means the caller wants THIS symbol/range replacing
            # whatever was cached — a zero-row DB result for it does not mean
            # "keep the old data", it means "this symbol/range has no data". The
            # old `elif` left a PRIOR symbol's frame in place whenever it was
            # already non-None, and the code below then stamped the NEW symbol's
            # name onto it (no Symbol-column filter exists on any read path), so
            # every subsequent price lookup for the new symbol silently served
            # the old symbol's option prices/strikes.
            _bulk_options_df = pl.DataFrame()
            logger.warning("[BULK] No option data available (cache and DB) for %s", symbol_upper)
    elif options_loaded_from_db:
        # Delta path: merge new slices into the cache we already had.
        before_rows = len(_bulk_options_df) if _bulk_options_df is not None else 0
        _bulk_options_df = _merge_polars_with_pandas(_bulk_options_df, options_results)
        after_rows = len(_bulk_options_df) if _bulk_options_df is not None else 0
        logger.info(
            "[BULK] Delta-merged %d new option rows (was %d, now %d)",
            sum(0 if df is None else len(df) for df in options_results),
            before_rows,
            after_rows,
        )

    if _bulk_options_df is not None and not _bulk_options_df.is_empty():
        _full_range_loaded = True
        _full_range_symbol = symbol_upper
        _bulk_loaded_key = cache_key
        if options_loaded_from_db:
            try:
                # After a delta or full load, save the (possibly expanded) Parquet.
                # Skip only when the in-memory result is strictly narrower than the
                # existing Parquet (defensive — should not happen in delta mode).
                _should_save = True
                if parquet_path.exists():
                    try:
                        _ex = pl.scan_parquet(parquet_path).select(["Date"]).collect()
                        _ex_min = str(_ex["Date"].min())[:10]
                        _ex_max = str(_ex["Date"].max())[:10]
                        _new_min = str(_bulk_options_df["Date"].min())[:10]
                        _new_max = str(_bulk_options_df["Date"].max())[:10]
                        if _new_min > _ex_min or _new_max < _ex_max:
                            logger.info(
                                "[BULK] Keeping wider Parquet (%s->%s); new data (%s->%s) is narrower - skipping overwrite",
                                _ex_min, _ex_max, _new_min, _new_max,
                            )
                            _should_save = False
                    except Exception:
                        pass
                if _should_save:
                    _bulk_options_df.write_parquet(parquet_path)
                    logger.info(
                        "[BULK] Saved Parquet cache (%s rows, %s -> %s)",
                        len(_bulk_options_df),
                        str(_bulk_options_df["Date"].min())[:10],
                        str(_bulk_options_df["Date"].max())[:10],
                    )
            except Exception as exc:
                logger.warning(f"[BULK] Failed to save Parquet cache: {exc}")
            _store_full_range_in_redis(symbol_upper, _bulk_options_df)

    logger.info("[BULK] Loading spot...")
    if not spot_df.empty:
        _bulk_spot_df = pl.from_pandas(spot_df)
        logger.info(f"[BULK] Loaded {len(_bulk_spot_df)} spot rows")
    else:
        _bulk_spot_df = pl.DataFrame()
        logger.warning("[BULK] No spot data returned!")

    if not expiry_df.empty:
        filtered_expiry = expiry_df
        if from_date:
            filtered_expiry = filtered_expiry[filtered_expiry["Current Expiry"] >= pd.to_datetime(from_date)]
        if to_date:
            filtered_expiry = filtered_expiry[filtered_expiry["Current Expiry"] <= pd.to_datetime(to_date)]
        _bulk_expiry_df = pl.from_pandas(filtered_expiry)
        logger.info(f"[BULK] Loaded {len(_bulk_expiry_df)} expiry rows")
    else:
        _bulk_expiry_df = pl.DataFrame()
        logger.warning("[BULK] No expiry data returned!")

    elapsed = time.perf_counter() - start_time
    logger.info(f"[BULK] Load complete in {elapsed:.2f}s")

    return _get_bulk_stats()


def _get_bulk_stats() -> dict:
    """Return stats about currently loaded bulk data."""
    return {
        "options_rows": len(_bulk_options_df) if _bulk_options_df is not None else 0,
        "spot_rows": len(_bulk_spot_df) if _bulk_spot_df is not None else 0,
        "expiry_rows": len(_bulk_expiry_df) if _bulk_expiry_df is not None else 0,
        "loaded_key": _bulk_loaded_key
    }


def bulk_clear():
    """
    Clear bulk-loaded data from memory.
    MUST be called in try/finally to prevent memory leaks and stale data.
    """
    global _bulk_options_df, _bulk_spot_df, _bulk_expiry_df, _bulk_loaded_key, _full_range_loaded, _full_range_symbol
    
    _bulk_options_df = None
    _bulk_spot_df = None
    _bulk_expiry_df = None
    _bulk_loaded_key = None
    _full_range_loaded = False
    _full_range_symbol = None
    _shared_bulk_strikes_cache.clear()
    
    logger.info("[BULK] Cleared bulk data from memory")


def is_bulk_loaded() -> bool:
    """Check if bulk data is currently loaded."""
    return _bulk_options_df is not None and _bulk_loaded_key is not None


def get_bulk_option_price(
    date: str,
    strike_price: float,
    option_type: str,
    expiry_date: str
) -> Optional[float]:
    """
    Fast in-memory lookup for single option price.
    Filters the bulk-loaded Polars DataFrame (microseconds vs DB round-trip).
    """
    if _bulk_options_df is None or _bulk_options_df.is_empty():
        return None
    
    try:
        date_value = _as_date_value(date)
        expiry_value = _as_date_value(expiry_date)
        if date_value is None or expiry_value is None:
            return None
        result = _bulk_options_df.filter(
            (pl.col("Date").cast(pl.Date) == date_value) &
            (pl.col("StrikePrice") == strike_price) &
            (pl.col("OptionType") == option_type.upper()) &
            (pl.col("ExpiryDate").cast(pl.Date) == expiry_value)
        )
        
        if result.is_empty():
            return None
        
        return result["Close"][0]
    except Exception as e:
        logger.warning(f"[BULK] Lookup failed: {e}")
        return None


def get_bulk_spot_price(date: str) -> Optional[float]:
    """
    Fast in-memory lookup for spot price on a date.
    """
    if _bulk_spot_df is None or _bulk_spot_df.is_empty():
        return None
    
    try:
        date_value = _as_date_value(date)
        if date_value is None:
            return None
        result = _bulk_spot_df.filter(pl.col("Date").cast(pl.Date) == date_value)
        
        if result.is_empty():
            return None
        
        return result["Close"][0]
    except Exception as e:
        logger.warning(f"[BULK] Spot lookup failed: {e}")
        return None


def get_bulk_strikes_for_date(
    date: str,
    expiry_date: str,
    option_type: str = None
) -> pl.DataFrame:
    """
    Get all strikes for a specific date/expiry combination.
    Returns Polars DataFrame for fast filtering.
    """
    if _bulk_options_df is None or _bulk_options_df.is_empty():
        return pl.DataFrame()
    
    try:
        date_value = _as_date_value(date)
        expiry_value = _as_date_value(expiry_date)
        if date_value is None:
            return pl.DataFrame()
        cache_key = (
            date_value.isoformat(),
            expiry_value.isoformat() if expiry_value is not None else "",
            option_type.upper() if option_type else "",
        )
        cached = _shared_bulk_strikes_cache.get(cache_key)
        if cached is not None:
            return cached
        result = _bulk_options_df.filter(pl.col("Date").cast(pl.Date) == date_value)
        if expiry_value is not None:
            result = result.filter(pl.col("ExpiryDate").cast(pl.Date) == expiry_value)
        
        if option_type:
            result = result.filter(pl.col("OptionType") == option_type.upper())
        
        _shared_bulk_strikes_cache.put(cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"[BULK] Strikes lookup failed: {e}")
        return pl.DataFrame()


def get_bulk_expiry_dates(
    from_date: str = None,
    to_date: str = None
) -> pl.DataFrame:
    """
    Get expiry dates from bulk-loaded data.
    """
    if _bulk_expiry_df is None or _bulk_expiry_df.is_empty():
        return pl.DataFrame()
    
    result = _bulk_expiry_df
    
    if from_date:
        from_value = _as_date_value(from_date)
        if from_value is not None:
            result = result.filter(pl.col("Current Expiry").cast(pl.Date) >= from_value)
    if to_date:
        to_value = _as_date_value(to_date)
        if to_value is not None:
            result = result.filter(pl.col("Current Expiry").cast(pl.Date) <= to_value)
    
    return result


def get_bulk_spot_df() -> Optional[pl.DataFrame]:
    """Get the full bulk-loaded spot DataFrame."""
    return _bulk_spot_df


def get_bulk_options_df() -> Optional[pl.DataFrame]:
    """Get the full bulk-loaded options DataFrame."""
    return _bulk_options_df


# Singleton instance
_loader_instance: Optional[HighPerformanceLoader] = None


def get_loader(database_url: str = None) -> HighPerformanceLoader:
    """
    Get singleton HighPerformanceLoader instance.
    
    Usage:
        from services.data_loader import get_loader, pl
        
        loader = get_loader()
        
        # Get single premium
        premium = loader.get_option_premium(
            symbol="NIFTY",
            date="2024-01-15",
            strike_price=22000,
            option_type="CE", 
            expiry_date="2024-01-25"
        )
        
        # Get strikes for selection (returns Polars DataFrame)
        strikes_df = loader.get_strikes_for_selection(
            symbol="NIFTY",
            date="2024-01-15",
            expiry_date="2024-01-25",
            option_type="CE"
        )
        
        # Filter using Polars (much faster than pandas)
        otm_strikes = strikes_df.filter(
            pl.col("strike_price") > 22000
        )
        
        # Clear cache between backtests
        loader.clear_cache()
        
        # Get cache stats
        stats = loader.get_cache_stats()
    """
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = HighPerformanceLoader(database_url)
        # Log pool status on first use
        from database import get_pool_status, log_pool_status
        log_pool_status()
    return _loader_instance


def reset_loader():
    """Reset the singleton instance (useful for testing)"""
    global _loader_instance
    if _loader_instance:
        _loader_instance.clear_cache()
    _loader_instance = None
