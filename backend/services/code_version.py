"""
Code-version fingerprint for the LAN remote-worker staleness guard.

Why this exists: a remote worker (remote-worker/) runs a *baked image*, not the
live bind-mounted source the main box runs. If its image is older than the main
box's code, it silently executes different engine/optimizer logic — producing
wrong results (violating the "optim == backtest, trade-for-trade" rule) or
crashing (as the P<=1 parallel bug did). This module fingerprints the actual
calculation code so the main box can detect a mismatched node and refuse to
route jobs to it.

What's hashed: the calculation-relevant Python packages (services / worker /
engines / strategies) plus base.py, AND the compiled Rust engine (.so). We hash
a *curated* set — not every .py under /app — so scratch files at the repo root
(repro_*.py, verify_*.py, etc.) can't cause false "stale" flags. The .so is
included so a Rust rebuild is detected even when no Python changed.

Both sides run this same module from their own /app, so identical code → identical
hash. The result is cached (code can't change within a live process).
"""
import glob
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# /app in Docker; falls back to the backend dir two levels up from this file.
_APP_DIR = os.environ.get(
    "APP_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_HASH_DIRS = ("services", "worker", "engines", "strategies")
_HASH_FILES = ("base.py",)
_SO_GLOBS = ("/usr/local/lib/python*/site-packages/algotest_native*.so",)

# Env vars that SELECT A CALCULATION PATH. Identical code + a different value
# here = different numbers, which is exactly what this guard claims to catch.
# The LAN node shipped without MIXED_FUT_RUST while the main box set "1", so a
# mixed FUTURES+OPTIONS strategy silently lost its OPTION leg on that node and
# the node still reported itself up to date. Hashing the VALUES (not just the
# names) makes any such drift a version mismatch.
#
# Only add a var here if changing it changes RESULTS. Performance/pool-size knobs
# (parallelism, memory budgets, cache dirs) must NOT be listed — they differ
# legitimately per node and would flag every node stale.
_HASH_ENV = (
    "MIXED_FUT_RUST",
    "FAST_LOOKUP_MODE",
    "ENGINE_BACKEND",
    "OPTIMIZE_RUST_LOOP",
    "OPTIMIZE_RUST_XLSX",
    "BACKTEST_INCLUDE_MAE_MFE",
    "OPTIMIZE_SKIP_MAE_MFE",
)

_cached = None


def compute_code_version(fresh: bool = False) -> str:
    """Short hex fingerprint of the calc code + Rust engine. Cached per process.

    `fresh=True` bypasses the cache and recomputes from the current on-disk
    source. The main box runs bind-mounted source that changes UNDER the live
    process, so its staleness-baseline must be recomputed each time — otherwise
    the backend keeps comparing remotes against a fingerprint frozen at its
    startup and flags every node "outdated" until it's restarted. Baked remote
    images are immutable, so their own heartbeat can keep using the cache.
    """
    global _cached
    if _cached is not None and not fresh:
        return _cached
    h = hashlib.sha256()
    files = []
    for d in _HASH_DIRS:
        base = os.path.join(_APP_DIR, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(root, n))
    for f in _HASH_FILES:
        p = os.path.join(_APP_DIR, f)
        if os.path.isfile(p):
            files.append(p)
    for path in sorted(files):
        try:
            rel = os.path.relpath(path, _APP_DIR)
            with open(path, "rb") as fh:
                h.update(rel.encode())
                h.update(b"\0")
                h.update(fh.read())
                h.update(b"\0")
        except Exception as exc:
            logger.debug("[CODE_VERSION] skip %s: %s", path, exc)
    for pat in _SO_GLOBS:
        for so in sorted(glob.glob(pat)):
            try:
                with open(so, "rb") as fh:
                    h.update(os.path.basename(so).encode())
                    h.update(b"\0")
                    h.update(fh.read())
            except Exception as exc:
                logger.debug("[CODE_VERSION] skip so %s: %s", so, exc)
    for _name in _HASH_ENV:
        h.update(_name.encode())
        h.update(b"=")
        # Normalised so "1"/" 1 " are the same value but "1"/"0" are not.
        h.update(str(os.environ.get(_name, "")).strip().lower().encode())
        h.update(b"\0")
    _cached = h.hexdigest()[:16]
    logger.info("[CODE_VERSION] %s", _cached)
    return _cached
