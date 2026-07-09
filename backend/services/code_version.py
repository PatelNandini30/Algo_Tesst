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

_cached = None


def compute_code_version() -> str:
    """Short hex fingerprint of the calc code + Rust engine. Cached per process."""
    global _cached
    if _cached is not None:
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
    _cached = h.hexdigest()[:16]
    logger.info("[CODE_VERSION] %s", _cached)
    return _cached
