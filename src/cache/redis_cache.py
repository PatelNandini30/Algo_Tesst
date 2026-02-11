"""
Redis caching layer for strategy execution results.
Provides caching, invalidation, and statistics tracking.
"""
import json
import hashlib
import redis
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis-based caching for strategy execution results."""
    
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, 
                 default_ttl: int = 3600):
        """
        Initialize Redis cache connection.
        
        Args:
            host: Redis server host
            port: Redis server port
            db: Redis database number
            default_ttl: Default time-to-live in seconds (1 hour default)
        """
        self.redis_client = redis.Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        self.default_ttl = default_ttl
        self._test_connection()
    
    def _test_connection(self):
        """Test Redis connection on initialization."""
        try:
            self.redis_client.ping()
            logger.info("Redis connection established successfully")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def _generate_cache_key(self, strategy_name: str, parameters: Dict[str, Any]) -> str:
        """
        Generate a unique cache key from strategy name and parameters.
        
        Args:
            strategy_name: Name of the strategy
            parameters: Strategy parameters dictionary
            
        Returns:
            Unique cache key string
        """
        # Sort parameters for consistent hashing
        param_str = json.dumps(parameters, sort_keys=True)
        param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]
        return f"strategy:{strategy_name}:{param_hash}"
    
    def get_cached_result(self, strategy_name: str, parameters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached result for a strategy execution.
        
        Args:
            strategy_name: Name of the strategy
            parameters: Strategy parameters
            
        Returns:
            Cached result dictionary or None if not found
        """
        cache_key = self._generate_cache_key(strategy_name, parameters)
        
        try:
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                # Update access statistics
                stats_key = f"{cache_key}:stats"
                self.redis_client.hincrby(stats_key, "access_count", 1)
                self.redis_client.hset(stats_key, "last_accessed", datetime.utcnow().isoformat())
                
                logger.info(f"Cache HIT for {strategy_name}")
                return json.loads(cached_data)
            
            logger.info(f"Cache MISS for {strategy_name}")
            return None
            
        except redis.RedisError as e:
            logger.error(f"Redis error during get: {e}")
            return None
    
    def set_cached_result(self, strategy_name: str, parameters: Dict[str, Any], 
                         result: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """
        Store strategy execution result in cache.
        
        Args:
            strategy_name: Name of the strategy
            parameters: Strategy parameters
            result: Execution result to cache
            ttl: Time-to-live in seconds (uses default if None)
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = self._generate_cache_key(strategy_name, parameters)
        ttl = ttl or self.default_ttl
        
        try:
            # Store the result
            result_json = json.dumps(result)
            self.redis_client.setex(cache_key, ttl, result_json)
            
            # Initialize statistics
            stats_key = f"{cache_key}:stats"
            stats_data = {
                "created_at": datetime.utcnow().isoformat(),
                "access_count": 0,
                "last_accessed": "",
                "strategy_name": strategy_name
            }
            self.redis_client.hset(stats_key, mapping=stats_data)
            self.redis_client.expire(stats_key, ttl)
            
            logger.info(f"Cached result for {strategy_name} with TTL {ttl}s")
            return True
            
        except redis.RedisError as e:
            logger.error(f"Redis error during set: {e}")
            return False
    
    def invalidate_cache(self, strategy_name: Optional[str] = None, 
                        parameters: Optional[Dict[str, Any]] = None) -> int:
        """
        Invalidate cached results.
        
        Args:
            strategy_name: Strategy name (invalidates all if None)
            parameters: Specific parameters (invalidates all for strategy if None)
            
        Returns:
            Number of keys deleted
        """
        try:
            if strategy_name and parameters:
                # Invalidate specific cache entry
                cache_key = self._generate_cache_key(strategy_name, parameters)
                stats_key = f"{cache_key}:stats"
                deleted = self.redis_client.delete(cache_key, stats_key)
                logger.info(f"Invalidated cache for {strategy_name} with specific parameters")
                return deleted
            
            elif strategy_name:
                # Invalidate all entries for a strategy
                pattern = f"strategy:{strategy_name}:*"
                keys = self.redis_client.keys(pattern)
                if keys:
                    deleted = self.redis_client.delete(*keys)
                    logger.info(f"Invalidated {deleted} cache entries for {strategy_name}")
                    return deleted
                return 0
            
            else:
                # Invalidate all strategy caches
                pattern = "strategy:*"
                keys = self.redis_client.keys(pattern)
                if keys:
                    deleted = self.redis_client.delete(*keys)
                    logger.info(f"Invalidated all {deleted} cache entries")
                    return deleted
                return 0
                
        except redis.RedisError as e:
            logger.error(f"Redis error during invalidation: {e}")
            return 0
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get overall cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        try:
            # Get all strategy cache keys
            cache_keys = self.redis_client.keys("strategy:*")
            stats_keys = [k for k in cache_keys if k.endswith(":stats")]
            data_keys = [k for k in cache_keys if not k.endswith(":stats")]
            
            total_hits = 0
            strategy_stats = {}
            
            for stats_key in stats_keys:
                stats = self.redis_client.hgetall(stats_key)
                if stats:
                    strategy_name = stats.get("strategy_name", "unknown")
                    access_count = int(stats.get("access_count", 0))
                    total_hits += access_count
                    
                    if strategy_name not in strategy_stats:
                        strategy_stats[strategy_name] = {
                            "cached_entries": 0,
                            "total_hits": 0
                        }
                    
                    strategy_stats[strategy_name]["cached_entries"] += 1
                    strategy_stats[strategy_name]["total_hits"] += access_count
            
            # Get memory usage
            memory_info = self.redis_client.info("memory")
            
            return {
                "total_cached_entries": len(data_keys),
                "total_cache_hits": total_hits,
                "memory_used_mb": round(memory_info.get("used_memory", 0) / (1024 * 1024), 2),
                "memory_peak_mb": round(memory_info.get("used_memory_peak", 0) / (1024 * 1024), 2),
                "by_strategy": strategy_stats,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except redis.RedisError as e:
            logger.error(f"Redis error getting stats: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    def health_check(self) -> Dict[str, Any]:
        """
        Check Redis connection health.
        
        Returns:
            Health status dictionary
        """
        try:
            start_time = datetime.utcnow()
            self.redis_client.ping()
            latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
                "timestamp": datetime.utcnow().isoformat()
            }
        except redis.RedisError as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }


# Global cache instance
_cache_instance: Optional[RedisCache] = None


def get_cache(host: str = "localhost", port: int = 6379, db: int = 0) -> RedisCache:
    """
    Get or create global cache instance.
    
    Args:
        host: Redis server host
        port: Redis server port
        db: Redis database number
        
    Returns:
        RedisCache instance
    """
    global _cache_instance
    
    if _cache_instance is None:
        _cache_instance = RedisCache(host=host, port=port, db=db)
    
    return _cache_instance
