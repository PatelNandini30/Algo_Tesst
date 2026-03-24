import json
import hashlib
from typing import Optional

import msgpack
import redis

# DEPRECATED: use services.backtest_cache instead
class RedisBacktestCache:
    """
    Lightweight Redis cache for backtest results using msgpack serialization.
    """

    def __init__(self, redis_url: str, ttl: int = 86400):
        self.ttl = ttl
        self._client: Optional[redis.Redis] = None
        self._available = False

        if not redis_url:
            return

        try:
            client = redis.Redis.from_url(redis_url)
            client.ping()
            self._client = client
            self._available = True
        except redis.RedisError:
            self._client = None
            self._available = False

    def _key(self, params: dict) -> str:
        key_str = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.sha256(key_str.encode()).hexdigest()
        return f"bt:{digest}"

    def is_available(self) -> bool:
        return self._available and self._client is not None

    def get(self, params: dict) -> Optional[dict]:
        if not self.is_available():
            return None

        try:
            data = self._client.get(self._key(params))
            if not data:
                return None
            return msgpack.unpackb(data, raw=False)
        except redis.RedisError:
            self._available = False
            return None

    def set(self, params: dict, result: dict) -> None:
        if not self.is_available():
            return

        try:
            payload = msgpack.packb(result, use_bin_type=True)
            self._client.set(self._key(params), payload, ex=self.ttl)
        except redis.RedisError:
            self._available = False

# DEPRECATED: use services.backtest_cache instead
redis_cache: Optional[RedisBacktestCache] = None


def configure_cache(instance: RedisBacktestCache) -> RedisBacktestCache:
    global redis_cache
    redis_cache = instance
    return redis_cache
