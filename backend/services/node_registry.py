"""
LAN remote-worker node registry.

Remote Celery workers running on other in-house PCs (see `remote-worker/`)
heartbeat their own IP/core-count/RAM into Redis so the backend and frontend
can discover them and route jobs to them explicitly. This box's own local
capacity is intentionally NOT registered here — "no node selected" always
means "run locally", the unchanged default behavior.

Mechanism: one Redis hash key per node (`algotest:nodes:{node_id}`) holding a
JSON blob, with a TTL so a node that stops heartbeating (crashed, shut down,
network dropped) disappears from the list automatically — no explicit
deregistration needed.
"""
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_KEY_PREFIX = "algotest:nodes:"
_TTL_SECONDS = int(os.environ.get("NODE_REGISTRY_TTL_SECONDS", "45"))

_client = None


def _redis():
    global _client
    if _client is None:
        import redis  # redis-py ships with celery[redis]
        _client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
    return _client


def heartbeat(node_id: str, cpu_count: int, ram_mb: int, hostname: str = "", version: str = "") -> None:
    """Publish/refresh this node's presence. Safe to call repeatedly; fails silently.

    `version` is the node's code fingerprint (services/code_version.py) so the
    main box can flag a node running mismatched engine/optimizer code as stale
    and refuse to route jobs to it."""
    if not node_id:
        return
    payload = {
        "node_id": node_id,
        "ip": node_id,
        "hostname": hostname or node_id,
        "cpu_count": int(cpu_count) if cpu_count else 1,
        "ram_mb": int(ram_mb) if ram_mb else 0,
        "version": version or "",
        "queues": [f"backtests@{node_id}", f"optimize@{node_id}"],
        "last_seen": int(time.time()),
    }
    try:
        _redis().set(_KEY_PREFIX + node_id, json.dumps(payload), ex=_TTL_SECONDS)
    except Exception as exc:
        logger.warning("[NODE_REGISTRY] heartbeat error for %s: %s", node_id, exc)


def list_nodes() -> list:
    """Return live (non-expired) nodes. Never raises."""
    try:
        client = _redis()
        keys = client.keys(_KEY_PREFIX + "*")
        nodes = []
        for key in keys:
            raw = client.get(key)
            if not raw:
                continue
            try:
                nodes.append(json.loads(raw))
            except Exception:
                continue
        return nodes
    except Exception as exc:
        logger.warning("[NODE_REGISTRY] list_nodes error: %s", exc)
        return []


def get_node(node_id: str):
    """Return a single node's registration, or None if unknown/expired."""
    if not node_id:
        return None
    try:
        raw = _redis().get(_KEY_PREFIX + node_id)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("[NODE_REGISTRY] get_node error for %s: %s", node_id, exc)
        return None


def is_stale(node_id: str) -> bool:
    """True when the node is running a DIFFERENT code version than this box (a
    mismatched image). Fails open (returns False) when either version is unknown
    or on any error, so the guard can never wrongly block an up-to-date node."""
    if not node_id:
        return False
    try:
        from services.code_version import compute_code_version
        my = compute_code_version()
        node = get_node(node_id)
        nv = (node or {}).get("version") or ""
        return bool(my and nv and nv != my)
    except Exception:
        return False


# --- job -> node association (so cancel-before-completion can release the
# correct node's memory-gate reservation; normal completion/failure already
# knows node_id directly from the job spec) --------------------------------
_JOB_NODE_PREFIX = "algotest:job_node:"


def record_job_node(job_id: str, node_id: str, ttl_seconds: int = 16200) -> None:
    if not job_id or not node_id:
        return
    try:
        _redis().set(_JOB_NODE_PREFIX + job_id, node_id, ex=ttl_seconds)
    except Exception as exc:
        logger.debug("[NODE_REGISTRY] record_job_node error for %s: %s", job_id, exc)


def get_job_node(job_id: str):
    if not job_id:
        return None
    try:
        return _redis().get(_JOB_NODE_PREFIX + job_id)
    except Exception as exc:
        logger.debug("[NODE_REGISTRY] get_job_node error for %s: %s", job_id, exc)
        return None
