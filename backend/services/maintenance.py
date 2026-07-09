"""Temporary maintenance lockout.

When the Redis flag `algotest:maintenance` is set, the job-submission endpoints
reject new backtests/optimizations with HTTP 503 — from ANY node/browser, since
they all hit this one backend. Toggle it live (no restart) with:

    docker exec algotest-redis redis-cli SET algotest:maintenance 1   # LOCK
    docker exec algotest-redis redis-cli DEL algotest:maintenance     # UNLOCK

Only the submit endpoints are gated. The engine itself is untouched, so the
parity harness (which calls execute_algotest_job directly, not the API) still
runs while the lock is on.
"""
import logging
import os

logger = logging.getLogger(__name__)

_FLAG = "algotest:maintenance"


def is_maintenance() -> bool:
    """True when the maintenance flag is set. Fails OPEN (returns False) on any
    Redis error so a transient hiccup can never wrongly block normal work."""
    try:
        import redis
        c = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        return bool(c.get(_FLAG))
    except Exception as exc:
        logger.debug("[MAINTENANCE] flag check failed (%s) — allowing", exc)
        return False
