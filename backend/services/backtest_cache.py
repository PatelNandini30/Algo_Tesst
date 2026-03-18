"""
Redis Result Cache for Backtests

PHASE 7: Redis Result Cache

Features:
- Cache backtest results in Redis
- Key format: backtest:{symbol}:{start_date}:{end_date}:{strategy_hash}
- 24 hour expiration
- JSON serialization

Usage:
    from services.backtest_cache import BacktestCache, get_backtest_cache
    
    cache = get_backtest_cache()
    
    # Generate cache key
    key = cache.generate_key(
        symbol="NIFTY",
        from_date="2020-01-01",
        to_date="2025-12-31",
        strategy_config={"legs": [...]}
    )
    
    # Check cache
    result = cache.get(key)
    
    if result is None:
        # Run backtest
        result = run_backtest(...)
        
        # Cache result
        cache.set(key, result)
"""

import os
import json
import hashlib
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import redis
import pandas as pd

logger = logging.getLogger(__name__)


# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
CACHE_TTL_SECONDS = int(os.getenv("BACKTEST_CACHE_TTL", "86400"))  # 24 hours


class BacktestCache:
    """
    Redis-based cache for backtest results.
    
    Features:
    - Automatic key generation from parameters
    - JSON serialization of results
    - 24 hour expiration
    - Hit/miss tracking
    """
    
    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        db: int = REDIS_DB,
        password: str = REDIS_PASSWORD,
        ttl: int = CACHE_TTL_SECONDS
    ):
        self._ttl = ttl
        self._hits = 0
        self._misses = 0
        
        try:
            self._redis = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            # Test connection
            self._redis.ping()
            self._available = True
            logger.info(f"[REDIS] Connected to {host}:{port}/{db}")
        except Exception as e:
            self._redis = None
            self._available = False
            logger.warning(f"[REDIS] Not available: {e}")
    
    def _serialize_result(self, result: Dict[str, Any]) -> str:
        """Serialize backtest result to JSON."""
        # Convert pandas DataFrames to dict
        serialized = {}
        
        if "trades_df" in result and result["trades_df"] is not None:
            serialized["trades"] = result["trades_df"].to_dict(orient="records")
        
        if "summary" in result:
            serialized["summary"] = result["summary"]
        
        if "pivot" in result:
            serialized["pivot"] = result["pivot"]
        
        return json.dumps(serialized, default=str)
    
    def _deserialize_result(self, data: str) -> Optional[Dict[str, Any]]:
        """Deserialize backtest result from JSON."""
        try:
            parsed = json.loads(data)
            result = {}
            
            if "trades" in parsed:
                result["trades_df"] = pd.DataFrame(parsed["trades"])
            
            if "summary" in parsed:
                result["summary"] = parsed["summary"]
            
            if "pivot" in parsed:
                result["pivot"] = parsed["pivot"]
            
            return result
        except Exception as e:
            logger.error(f"[REDIS] Deserialization error: {e}")
            return None
    
    def generate_key(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        strategy_config: Dict[str, Any] = None
    ) -> str:
        """
        Generate cache key from parameters.
        
        Key format: backtest:{symbol}:{from_date}:{to_date}:{strategy_hash}
        """
        parts = [
            "backtest",
            symbol.upper(),
            from_date,
            to_date
        ]
        
        if strategy_config:
            # Generate hash from strategy config
            config_str = json.dumps(strategy_config, sort_keys=True, default=str)
            config_hash = hashlib.md5(config_str.encode()).hexdigest()[:8]
            parts.append(config_hash)
        
        return ":".join(parts)
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get cached result.
        
        Returns None if not in cache.
        """
        if not self._available:
            return None
        
        try:
            data = self._redis.get(key)
            
            if data is not None:
                self._hits += 1
                logger.info(f"[REDIS] CACHE HIT: {key}")
                return self._deserialize_result(data)
            
            self._misses += 1
            logger.debug(f"[REDIS] CACHE MISS: {key}")
            return None
            
        except Exception as e:
            logger.error(f"[REDIS] Get error: {e}")
            return None
    
    def set(
        self,
        key: str,
        result: Dict[str, Any],
        ttl: int = None
    ) -> bool:
        """
        Cache result.
        
        Returns True if cached successfully.
        """
        if not self._available:
            return False
        
        try:
            data = self._serialize_result(result)
            expire = ttl or self._ttl
            
            self._redis.setex(key, expire, data)
            logger.info(f"[REDIS] CACHED: {key} (TTL: {expire}s)")
            return True
            
        except Exception as e:
            logger.error(f"[REDIS] Set error: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """Delete cached result."""
        if not self._available:
            return False
        
        try:
            self._redis.delete(key)
            logger.info(f"[REDIS] DELETED: {key}")
            return True
        except Exception as e:
            logger.error(f"[REDIS] Delete error: {e}")
            return False
    
    def clear_all(self) -> bool:
        """Clear all backtest cache entries."""
        if not self._available:
            return False
        
        try:
            # Find all backtest:* keys
            keys = self._redis.keys("backtest:*")
            if keys:
                self._redis.delete(*keys)
                logger.info(f"[REDIS] Cleared {len(keys)} entries")
            return True
        except Exception as e:
            logger.error(f"[REDIS] Clear error: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        
        return {
            "available": self._available,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%",
            "ttl_seconds": self._ttl
        }
    
    def is_available(self) -> bool:
        """Check if Redis is available."""
        return self._available


# Singleton instance
_cache_instance: Optional[BacktestCache] = None
_cache_lock = __import__('threading').Lock()


def get_backtest_cache() -> BacktestCache:
    """Get singleton backtest cache instance."""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = BacktestCache()
    return _cache_instance


def clear_backtest_cache() -> bool:
    """Clear all backtest cache entries."""
    return get_backtest_cache().clear_all()


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    return get_backtest_cache().get_stats()
