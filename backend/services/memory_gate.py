"""
Memory-budget admission gate — prevents OOM by bounding the combined memory of
concurrently *active* heavy jobs (backtest / optimize / intraday).

This module is pure orchestration: it only controls WHEN a Celery task's body is
allowed to start. It NEVER touches any backtest calculation, pricing, strike
resolution, analytics, or result — the engine runs exactly as before once a slot
is granted.

Why this is enough to stop OOM:
  - The crash/freeze happened when several heavy jobs were *actively* processing
    at the same instant, each with a multi-GB hot working set.
  - This gate guarantees the sum of *active* job costs stays within a budget that
    fits in RAM. Idle warm caches in other workers are not the problem — on the
    NVMe SSD they page out to swap cheaply; only the active hot sets must fit.

Mechanism (atomic, via a Redis Lua script):
  Redis hash `algotest:mem_gate` holds live reservations {id -> "cost_mb:expiry"}.
  acquire(): drop expired entries, sum live costs, grant if
      used + cost <= budget   OR   used == 0   (a single oversized job runs alone,
      so nothing can ever deadlock waiting for room that will never exist).
  Each reservation carries a TTL so a crashed/killed/cancelled worker cannot leak
  budget — stale reservations are reclaimed automatically on the next acquire.

All Redis errors fail OPEN (task proceeds): a bug in the gate must never be able
to block backtests. The OS swap remains the final backstop.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

# --- Tunables (all overridable via env; no code change needed to retune) -------
_ENABLED = os.environ.get("HEAVY_MEMORY_GATE", "1").strip().lower() not in (
    "0", "off", "false", "no",
)
_BUDGET_MB = int(os.environ.get("HEAVY_MEMORY_BUDGET_MB", "7000"))
_TTL = int(os.environ.get("HEAVY_RESERVATION_TTL_SECONDS", "2400"))
_WAIT_MAX = int(os.environ.get("HEAVY_GATE_WAIT_MAX_SECONDS", "600"))
_POLL = float(os.environ.get("HEAVY_GATE_POLL_SECONDS", "3"))
# On wait timeout: 'proceed' (run anyway, lean on swap) or 'fail' (reject the job).
_ON_TIMEOUT = os.environ.get("HEAVY_GATE_ON_TIMEOUT", "proceed").strip().lower()
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_HASH_KEY = "algotest:mem_gate"

_COSTS = {
    "backtest": int(os.environ.get("HEAVY_COST_BACKTEST_MB", "5000")),
    "optimize": int(os.environ.get("HEAVY_COST_OPTIMIZE_MB", "6000")),
    "intraday": int(os.environ.get("HEAVY_COST_INTRADAY_MB", "2000")),
}

# Atomic acquire: reclaim expired reservations, then grant iff it fits OR the
# gate is currently empty (oversized-single-job escape hatch).
_ACQUIRE_LUA = """
local now = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local budget = tonumber(ARGV[3])
local rid = ARGV[4]
local expiry = ARGV[5]
local all = redis.call('HGETALL', KEYS[1])
local used = 0
for i = 1, #all, 2 do
  local field = all[i]
  local val = all[i + 1]
  local c = tonumber(string.match(val, '^(%d+):'))
  local e = tonumber(string.match(val, ':(%d+)$'))
  if e ~= nil and e < now then
    redis.call('HDEL', KEYS[1], field)
  elseif c ~= nil then
    used = used + c
  end
end
if (used + cost <= budget) or (used == 0) then
  redis.call('HSET', KEYS[1], rid, cost .. ':' .. expiry)
  redis.call('EXPIRE', KEYS[1], 86400)
  return 1
end
return 0
"""

_client = None


def _redis():
    global _client
    if _client is None:
        import redis  # redis-py ships with celery[redis]
        _client = redis.Redis.from_url(_REDIS_URL)
    return _client


def cost_for(kind: str) -> int:
    """Estimated active-memory cost (MB) for a job kind."""
    return _COSTS.get(kind, 4000)


def _try_acquire(rid: str, cost: int) -> bool:
    now = int(time.time())
    res = _redis().eval(
        _ACQUIRE_LUA, 1, _HASH_KEY, now, cost, _BUDGET_MB, rid, now + _TTL,
    )
    return int(res) == 1


def _force_reserve(rid: str, cost: int) -> None:
    """Record a reservation unconditionally (used on timeout-proceed so the job
    is still counted against the budget while it runs)."""
    now = int(time.time())
    _redis().hset(_HASH_KEY, rid, f"{cost}:{now + _TTL}")
    _redis().expire(_HASH_KEY, 86400)


def acquire(rid: str, cost: int, on_wait=None) -> bool:
    """Block until `cost` MB fits the budget, then reserve it.

    Returns True when the job may proceed (reserved, or timed-out-with-proceed,
    or gate disabled, or Redis unavailable). Returns False only when it timed out
    and HEAVY_GATE_ON_TIMEOUT='fail'. `on_wait` (optional) is called once the
    first time the job has to wait, so the caller can surface a 'queued' status.
    """
    if not _ENABLED or not rid:
        return True
    deadline = time.time() + _WAIT_MAX
    waited = False
    while True:
        try:
            if _try_acquire(rid, cost):
                if waited:
                    logger.info("[MEM_GATE] %s acquired %d MB after waiting", rid, cost)
                return True
        except Exception as exc:
            logger.warning("[MEM_GATE] acquire error (%s) — proceeding without gate", exc)
            return True  # fail open
        if time.time() >= deadline:
            if _ON_TIMEOUT == "fail":
                logger.warning(
                    "[MEM_GATE] %s waited %ds for %d MB budget — rejecting (policy=fail)",
                    rid, _WAIT_MAX, cost,
                )
                return False
            logger.warning(
                "[MEM_GATE] %s waited %ds for %d MB budget — proceeding anyway (swap backstop)",
                rid, _WAIT_MAX, cost,
            )
            try:
                _force_reserve(rid, cost)
            except Exception:
                pass
            return True
        if not waited:
            logger.info(
                "[MEM_GATE] %s waiting for %d MB (budget %d MB busy) — job queued",
                rid, cost, _BUDGET_MB,
            )
            if on_wait is not None:
                try:
                    on_wait()
                except Exception:
                    pass
            waited = True
        time.sleep(_POLL)


def release(rid: str) -> None:
    """Release a reservation. Safe to call for an unknown/already-released id."""
    if not _ENABLED or not rid:
        return
    try:
        _redis().hdel(_HASH_KEY, rid)
    except Exception as exc:
        logger.debug("[MEM_GATE] release error for %s: %s", rid, exc)


def stats() -> dict:
    """Current reservations (for /health or debugging). Never raises."""
    try:
        now = int(time.time())
        raw = _redis().hgetall(_HASH_KEY) or {}
        live = {}
        used = 0
        for k, v in raw.items():
            kid = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            try:
                c_str, e_str = val.split(":")
                c, e = int(c_str), int(e_str)
            except Exception:
                continue
            if e >= now:
                live[kid] = c
                used += c
        return {"budget_mb": _BUDGET_MB, "used_mb": used, "reservations": live}
    except Exception as exc:
        return {"error": str(exc)}
