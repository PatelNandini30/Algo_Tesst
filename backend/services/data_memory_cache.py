"""
In-Memory Data Cache for Backtesting

PHASE 6: In-Memory Data Cache

Features:
- LRU cache with 5GB memory limit
- Caches last 5 years of data
- Popular symbols (NIFTY, BANKNIFTY)
- Thread-safe operations

Usage:
    from services.data_memory_cache import DataMemoryCache, get_memory_cache
    
    cache = get_memory_cache()
    
    # Check if data is in cache
    df = cache.get_options("NIFTY", "2020-01-01", "2025-12-31")
    
    if df is None:
        # Load from database and cache
        df = loader.get_bulk_options("NIFTY", "2020-01-01", "2025-12-31")
        cache.set_options("NIFTY", df)
"""

import os
import sys
import time
import logging
import hashlib
import threading
from typing import Optional, Dict, Any, Tuple
from collections import OrderedDict
from dataclasses import dataclass, field

import polars as pl

logger = logging.getLogger(__name__)


# Configuration
MAX_MEMORY_BYTES = 5 * 1024 * 1024 * 1024  # 5GB
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY"]
CACHE_YEARS = 5


@dataclass
class CacheEntry:
    """Single cache entry with memory tracking."""
    data: pl.DataFrame
    size_bytes: int
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0


class DataMemoryCache:
    """
    In-memory LRU cache with memory limit.
    
    Features:
    - 5GB memory limit
    - LRU eviction
    - Thread-safe
    - Memory tracking
    """
    
    def __init__(
        self,
        max_memory_bytes: int = MAX_MEMORY_BYTES,
        default_symbols: list = None
    ):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_memory = max_memory_bytes
        self._current_memory = 0
        self._lock = threading.RLock()
        self._default_symbols = default_symbols or DEFAULT_SYMBOLS
        self._hits = 0
        self._misses = 0
        
        logger.info(
            f"[CACHE] Memory cache initialized: "
            f"max={max_memory_bytes/(1024**3):.1f}GB, "
            f"symbols={self._default_symbols}"
        )
    
    def _estimate_dataframe_size(self, df: pl.DataFrame) -> int:
        """Estimate memory size of DataFrame in bytes."""
        if df.is_empty():
            return 0
        
        # Estimate: ~100 bytes per row + column data size
        # This is a rough estimate
        n_rows = len(df)
        n_cols = len(df.columns)
        
        # Assume average 50 bytes per cell
        return n_rows * n_cols * 50
    
    def _generate_key(
        self,
        data_type: str,
        symbol: str,
        from_date: str = None,
        to_date: str = None
    ) -> str:
        """Generate cache key."""
        parts = [data_type, symbol.upper()]
        if from_date:
            parts.append(from_date)
        if to_date:
            parts.append(to_date)
        return ":".join(parts)
    
    def get(
        self,
        data_type: str,
        symbol: str,
        from_date: str = None,
        to_date: str = None
    ) -> Optional[pl.DataFrame]:
        """
        Get data from cache.
        
        Returns None if not in cache.
        """
        key = self._generate_key(data_type, symbol, from_date, to_date)
        
        with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                
                entry = self._cache[key]
                entry.last_accessed = time.time()
                entry.access_count += 1
                self._hits += 1
                
                logger.debug(f"[CACHE] HIT: {key}")
                return entry.data
            
            self._misses += 1
            logger.debug(f"[CACHE] MISS: {key}")
            return None
    
    def set(
        self,
        data_type: str,
        symbol: str,
        df: pl.DataFrame,
        from_date: str = None,
        to_date: str = None
    ) -> bool:
        """
        Add data to cache.
        
        Returns True if cached, False if skipped (memory limit).
        """
        if df is None or df.is_empty:
            return False
        
        # Only cache popular symbols to save memory
        if symbol.upper() not in self._default_symbols:
            logger.debug(f"[CACHE] Skipping {symbol} - not in popular symbols")
            return False
        
        key = self._generate_key(data_type, symbol, from_date, to_date)
        size = self._estimate_dataframe_size(df)
        
        with self._lock:
            # Evict until we have space
            while (self._current_memory + size > self._max_memory) and self._cache:
                self._evict_lru()
            
            # Check again after eviction
            if self._current_memory + size > self._max_memory:
                logger.warning(f"[CACHE] Cannot fit {key} - data too large ({size/(1024**2):.1f}MB)")
                return False
            
            # Add to cache
            entry = CacheEntry(data=df, size_bytes=size)
            self._cache[key] = entry
            self._current_memory += size
            
            logger.debug(
                f"[CACHE] SET: {key} ({len(df)} rows, {size/(1024**2):.1f}MB)"
            )
            return True
    
    def _evict_lru(self):
        """Evict least recently used entry."""
        if not self._cache:
            return
        
        # Remove oldest entry
        key, entry = self._cache.popitem(last=False)
        self._current_memory -= entry.size_bytes
        
        logger.debug(f"[CACHE] EVICT: {key} ({entry.size_bytes/(1024**2):.1f}MB)")
    
    def clear(self):
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()
            self._current_memory = 0
            logger.info("[CACHE] Cleared all data")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1f}%",
                "entries": len(self._cache),
                "memory_used_mb": self._current_memory / (1024**2),
                "memory_limit_mb": self._max_memory / (1024**2),
                "memory_usage_pct": f"{self._current_memory / self._max_memory * 100:.1f}%"
            }
    
    # Convenience methods
    
    def get_options(
        self,
        symbol: str,
        from_date: str,
        to_date: str
    ) -> Optional[pl.DataFrame]:
        """Get cached options data."""
        return self.get("options", symbol, from_date, to_date)
    
    def set_options(
        self,
        symbol: str,
        df: pl.DataFrame,
        from_date: str,
        to_date: str
    ) -> bool:
        """Cache options data."""
        return self.set("options", symbol, df, from_date, to_date)
    
    def get_spot(
        self,
        symbol: str,
        from_date: str,
        to_date: str
    ) -> Optional[pl.DataFrame]:
        """Get cached spot data."""
        return self.get("spot", symbol, from_date, to_date)
    
    def set_spot(
        self,
        symbol: str,
        df: pl.DataFrame,
        from_date: str,
        to_date: str
    ) -> bool:
        """Cache spot data."""
        return self.set("spot", symbol, df, from_date, to_date)


# Singleton instance
_cache_instance: Optional[DataMemoryCache] = None
_cache_lock = threading.Lock()


def get_memory_cache() -> DataMemoryCache:
    """Get singleton memory cache instance."""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = DataMemoryCache()
    return _cache_instance


def clear_memory_cache():
    """Clear the memory cache."""
    cache = get_memory_cache()
    cache.clear()
    logger.info("[CACHE] Memory cache cleared")


def get_cache_stats() -> Dict[str, Any]:
    """Get memory cache statistics."""
    return get_memory_cache().get_stats()
