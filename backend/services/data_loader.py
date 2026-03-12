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

import os
import time
import logging
from typing import Optional, List, Dict, Any, Tuple
from collections import OrderedDict
from functools import lru_cache

import polars as pl
from sqlalchemy import text

# Import engine from database.py (uses connection pooling)
from database import get_engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        self._date_cache = LRUCache(max_size=1000)  # Cache for full date data
        self._premium_cache = LRUCache(max_size=5000)  # Cache for single premiums
        self._trading_days_cache = LRUCache(max_size=100)  # Cache for trading calendars
        self._expiry_cache = LRUCache(max_size=200)  # Cache for expiry dates
        self._spot_cache = LRUCache(max_size=2000)  # Cache for spot prices
        
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
                    date_df = df.filter(pl.col("date") == date)
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
    
    def get_trading_days(
        self,
        symbol: str,
        from_date: str,
        to_date: str
    ) -> pl.DataFrame:
        """Get all trading days for symbol in date range. Uses caching."""
        # Check cache first
        cache_key = ("trading_days", symbol.upper(), from_date, to_date)
        cached_df = self._trading_days_cache.get(cache_key)
        if cached_df is not None:
            logger.debug(f"[CACHE] get_trading_days HIT: {cache_key}")
            return cached_df
        
        query = """
            SELECT DISTINCT date 
            FROM option_data 
            WHERE symbol = :symbol
              AND date >= :from_date 
              AND date <= :to_date
            ORDER BY date
        """
        
        with PerformanceTimer(f"get_trading_days({symbol}, {from_date} to {to_date})"):
            df = self._query_to_polars(query, {
                "symbol": symbol.upper(),
                "from_date": from_date,
                "to_date": to_date
            })
        
        # Cache the result
        self._trading_days_cache.put(cache_key, df)
        
        logger.debug(f"[PERF] Found {len(df)} trading days for {symbol}")
        return df
    
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
        """Clear all caches - call between backtests."""
        self._date_cache.clear()
        self._premium_cache.clear()
        self._trading_days_cache.clear()
        self._expiry_cache.clear()
        self._spot_cache.clear()
        logger.info("[CACHE] All caches cleared")
    
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
