"""
Memory-budget admission gate — prevents OOM by bounding the combined memory of
concurrently *active* heavy jobs (backtest / optimize).

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
  Redis hash `algotest:mem_gate:{node_id}` holds live reservations for that node
  {id -> "cost_mb:expiry"}. acquire(): drop expired entries, sum live costs,
  grant if used + cost <= budget OR used == 0 (a single oversized job runs
  alone, so nothing can ever deadlock waiting for room that will never exist).
  Each reservation carries a TTL so a crashed/killed/cancelled worker cannot leak
  budget — stale reservations are reclaimed automatically on the next acquire.

Per-node budgets: the gate is keyed by `node_id` (default "local", meaning this
box). Each node's budget is its own RAM pool — a job routed to a LAN remote
worker (see services/node_registry.py, remote-worker/) never competes with this
box's HEAVY_MEMORY_BUDGET_MB, and vice versa. A remote node's budget is derived
from its own heartbeat-reported RAM (see _budget_for_node) unless it isn't
registered yet, in which case the local budget is used as a safe fallback.

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
_HASH_KEY_PREFIX = "algotest:mem_gate:"
_LOCAL_NODE = "local"
# Fraction of a remote node's reported RAM it's allowed to commit to active jobs.
_REMOTE_BUDGET_FRACTION = float(os.environ.get("NODE_MEMORY_BUDGET_FRACTION", "0.7"))

_COSTS = {
    "backtest": int(os.environ.get("HEAVY_COST_BACKTEST_MB", "5000")),
    "optimize": int(os.environ.get("HEAVY_COST_OPTIMIZE_MB", "6000")),
}

# --- #2 DYNAMIC per-job cost: scale the reservation by the backtest date span --
# A 6-month job's hot set (feather/dataset) is far smaller than a 7-year job's,
# so reserving the same flat _COSTS[kind] for both over-reserves short jobs and
# needlessly blocks work that would have fit. Model: base + per_year × years,
# clamped to [floor, _COSTS[kind]] — i.e. NEVER more than today's flat value
# (no regression / no new OOM risk), only LESS for shorter ranges. The
# full-history (~7y) case lands right at the old flat value by construction.
_DYNAMIC_COST = os.environ.get("HEAVY_DYNAMIC_COST", "1").strip().lower() not in (
    "0", "off", "false", "no",
)
_COST_MODEL = {
    # base_mb, per_year_mb, floor_mb  (base + per_year*7 ≈ the flat _COSTS above)
    "backtest": (
        int(os.environ.get("HEAVY_COST_BACKTEST_BASE_MB", "2000")),
        int(os.environ.get("HEAVY_COST_BACKTEST_PER_YEAR_MB", "430")),
        int(os.environ.get("HEAVY_COST_BACKTEST_FLOOR_MB", "2000")),
    ),
    "optimize": (
        int(os.environ.get("HEAVY_COST_OPTIMIZE_BASE_MB", "2500")),
        int(os.environ.get("HEAVY_COST_OPTIMIZE_PER_YEAR_MB", "500")),
        int(os.environ.get("HEAVY_COST_OPTIMIZE_FLOOR_MB", "3000")),
    ),
}

# --- #1 LIVE-RAM guard: refuse admission when the BOX is physically low on RAM -
# The reservation budget (_BUDGET_MB) is blind to what the rest of the box (the
# desktop, other apps, other containers) is using right now. /proc/meminfo is
# NOT namespaced in Docker, so MemAvailable read here reflects the WHOLE HOST's
# free RAM. We add a second admission condition: never start a heavy job if,
# after it takes its cost, the box would drop below a safety floor — UNLESS no
# heavy job is currently reserved (the oversized-single-job escape hatch, which
# leans on swap and so still never OOM-kills). This is what actually enforces
# "no OOM even when the desktop is busy".
_USE_LIVE_RAM = os.environ.get("HEAVY_GATE_USE_LIVE_RAM", "1").strip().lower() not in (
    "0", "off", "false", "no",
)
_LIVE_FLOOR_MB = int(os.environ.get("HEAVY_GATE_LIVE_RAM_FLOOR_MB", "1500"))
# Which job kinds this guard actually applies to. Default: optimize only.
# Why: "optimize" forks OPTIMIZE_PARALLELISM (up to 6) worker processes whose
# combined memory bandwidth/usage genuinely fluctuates the box's free RAM by
# several GB second-to-second — that's the real OOM risk this guard exists
# for. "backtest" is a single Python process with a small, stable footprint;
# gating it on the same volatile live-RAM reading caused 30-60s admission
# delays for a backtest that would have fit comfortably under the plain
# reservation-budget gate (which already has ample headroom — this was a
# regression discovered 2026-07-06: backtests that ran fine alongside an
# optimize before this guard existed started showing "queued" for a long
# time after it was added). The reservation-budget gate (_ACQUIRE_LUA) still
# fully applies to backtests regardless of this setting — this only skips
# the EXTRA live-RAM check for kinds not listed here.
_LIVE_RAM_KINDS = {
    k.strip().lower()
    for k in os.environ.get("HEAVY_GATE_LIVE_RAM_KINDS", "optimize").split(",")
    if k.strip()
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
    """Estimated active-memory cost (MB) for a job kind (flat ceiling)."""
    return _COSTS.get(kind, 4000)


def _span_years(base_payload: dict) -> float:
    """Backtest date-range span in years, from the job payload. Returns a
    conservative full-history default (7y) if the dates can't be parsed, so an
    unparseable payload reserves the flat ceiling rather than under-reserving."""
    try:
        from datetime import date
        p = base_payload or {}
        df = str(p.get("date_from") or p.get("from_date") or p.get("_effective_from") or "")[:10]
        dt = str(p.get("date_to") or p.get("to_date") or p.get("_effective_to") or "")[:10]
        y1, m1, d1 = (int(x) for x in df.split("-"))
        y2, m2, d2 = (int(x) for x in dt.split("-"))
        days = (date(y2, m2, d2) - date(y1, m1, d1)).days
        return max(0.0, days / 365.25)
    except Exception:
        return 7.0


def cost_for_job(kind: str, base_payload: dict = None) -> int:
    """#2 — DYNAMIC active-memory reservation (MB), scaled by the job's date
    span: base + per_year × years, clamped to [floor, flat-ceiling]. Never
    exceeds the old flat cost_for(kind), so this only ever RELAXES the gate for
    short jobs (more of them fit); it never admits more than before."""
    ceiling = _COSTS.get(kind, 4000)
    if not _DYNAMIC_COST or kind not in _COST_MODEL:
        return ceiling
    base_mb, per_year_mb, floor_mb = _COST_MODEL[kind]
    years = _span_years(base_payload)
    est = int(base_mb + per_year_mb * years)
    est = max(floor_mb, min(ceiling, est))
    logger.info(
        "[MEM_GATE] dynamic cost %s: %.1fy -> %d MB (base=%d per_year=%d floor=%d ceiling=%d)",
        kind, years, est, base_mb, per_year_mb, floor_mb, ceiling,
    )
    return est


def _live_available_mb():
    """#1 — Whole-box free RAM (MB) from /proc/meminfo MemAvailable. /proc is
    NOT namespaced in Docker, so this reflects the HOST, including the desktop
    and every other container. Returns None if it can't be read (guard then
    simply no-ops — fail open)."""
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024  # kB -> MB
    except Exception:
        pass
    return None


def _hash_key(node_id: str) -> str:
    return _HASH_KEY_PREFIX + (node_id or _LOCAL_NODE)


def _budget_for_node(node_id: str) -> int:
    """This box keeps its own tuned HEAVY_MEMORY_BUDGET_MB. A remote node's
    budget is a fraction of its own heartbeat-reported RAM, so it's sized to
    that PC's actual hardware, not this box's. Falls back to the local budget
    if the node hasn't heartbeated yet (registry miss)."""
    if not node_id or node_id == _LOCAL_NODE:
        return _BUDGET_MB
    try:
        from services import node_registry
        node = node_registry.get_node(node_id)
        if node and node.get("ram_mb"):
            return max(500, int(node["ram_mb"] * _REMOTE_BUDGET_FRACTION))
    except Exception as exc:
        logger.debug("[MEM_GATE] budget lookup error for node %s: %s", node_id, exc)
    return _BUDGET_MB


def _live_ram_blocks(cost: int, node_id: str, kind: str = None) -> bool:
    """#1 — True if the BOX physically lacks the RAM for this job right now.
    Only applies to the local node (a remote node's own gate guards its own
    box) AND only to job kinds in _LIVE_RAM_KINDS (default: optimize only —
    see the comment there for why backtest is excluded). Honors the
    oversized-single-job escape: if NOTHING heavy is currently reserved here,
    never block — that lone job proceeds and leans on swap (the final
    backstop), so this guard can never deadlock a job forever."""
    if not _USE_LIVE_RAM or node_id not in (None, _LOCAL_NODE):
        return False
    if kind and kind.strip().lower() not in _LIVE_RAM_KINDS:
        return False
    avail = _live_available_mb()
    if avail is None:
        return False  # can't read -> fail open
    if (avail - cost) >= _LIVE_FLOOR_MB:
        return False  # enough real RAM for this job + safety floor
    # Not enough RAM. Escape hatch: proceed anyway if the gate is empty
    # (single job runs alone on swap; never OOM-killed).
    try:
        if _redis().hlen(_hash_key(node_id)) == 0:
            return False
    except Exception:
        return False  # fail open
    logger.info(
        "[MEM_GATE] live-RAM guard: box has %d MB free, job needs %d MB + %d MB floor "
        "-> waiting for RAM to free (node=%s)",
        avail, cost, _LIVE_FLOOR_MB, node_id or _LOCAL_NODE,
    )
    return True


def _try_acquire(rid: str, cost: int, node_id: str, kind: str = None) -> bool:
    # #1 live-RAM guard: even if the reservation budget has room, don't start a
    # job the box can't physically hold right now (desktop/other apps busy).
    if _live_ram_blocks(cost, node_id, kind):
        return False
    now = int(time.time())
    budget = _budget_for_node(node_id)
    res = _redis().eval(
        _ACQUIRE_LUA, 1, _hash_key(node_id), now, cost, budget, rid, now + _TTL,
    )
    return int(res) == 1


def _force_reserve(rid: str, cost: int, node_id: str) -> None:
    """Record a reservation unconditionally (used on timeout-proceed so the job
    is still counted against the budget while it runs)."""
    now = int(time.time())
    key = _hash_key(node_id)
    _redis().hset(key, rid, f"{cost}:{now + _TTL}")
    _redis().expire(key, 86400)


def acquire(rid: str, cost: int, on_wait=None, node_id: str = None, kind: str = None) -> bool:
    """Block until `cost` MB fits the budget for `node_id` (default: local, this
    box), then reserve it.

    `kind` ("backtest" / "optimize") gates whether the #1 live-RAM guard (see
    _LIVE_RAM_KINDS) applies on top of the reservation-budget check, which
    always applies regardless of kind.

    Returns True when the job may proceed (reserved, or timed-out-with-proceed,
    or gate disabled, or Redis unavailable). Returns False only when it timed out
    and HEAVY_GATE_ON_TIMEOUT='fail'. `on_wait` (optional) is called once the
    first time the job has to wait, so the caller can surface a 'queued' status.
    """
    if not _ENABLED or not rid:
        return True
    node_id = node_id or _LOCAL_NODE
    deadline = time.time() + _WAIT_MAX
    waited = False
    while True:
        try:
            if _try_acquire(rid, cost, node_id, kind):
                if waited:
                    logger.info("[MEM_GATE] %s acquired %d MB after waiting (node=%s)", rid, cost, node_id)
                return True
        except Exception as exc:
            logger.warning("[MEM_GATE] acquire error (%s) — proceeding without gate", exc)
            return True  # fail open
        if time.time() >= deadline:
            if _ON_TIMEOUT == "fail":
                logger.warning(
                    "[MEM_GATE] %s waited %ds for %d MB budget (node=%s) — rejecting (policy=fail)",
                    rid, _WAIT_MAX, cost, node_id,
                )
                return False
            logger.warning(
                "[MEM_GATE] %s waited %ds for %d MB budget (node=%s) — proceeding anyway (swap backstop)",
                rid, _WAIT_MAX, cost, node_id,
            )
            try:
                _force_reserve(rid, cost, node_id)
            except Exception:
                pass
            return True
        if not waited:
            logger.info(
                "[MEM_GATE] %s waiting for %d MB (node=%s budget busy) — job queued",
                rid, cost, node_id,
            )
            if on_wait is not None:
                try:
                    on_wait()
                except Exception:
                    pass
            waited = True
        time.sleep(_POLL)


def release(rid: str, node_id: str = None) -> None:
    """Release a reservation. Safe to call for an unknown/already-released id."""
    if not _ENABLED or not rid:
        return
    try:
        _redis().hdel(_hash_key(node_id or _LOCAL_NODE), rid)
    except Exception as exc:
        logger.debug("[MEM_GATE] release error for %s: %s", rid, exc)


def stats(node_id: str = None) -> dict:
    """Current reservations for a node (for /health or debugging). Never raises."""
    node_id = node_id or _LOCAL_NODE
    try:
        now = int(time.time())
        raw = _redis().hgetall(_hash_key(node_id)) or {}
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
        return {"node_id": node_id, "budget_mb": _budget_for_node(node_id), "used_mb": used, "reservations": live}
    except Exception as exc:
        return {"error": str(exc)}
