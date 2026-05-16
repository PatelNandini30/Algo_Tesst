"""
Capture golden snapshots for every archetype.

Run via:
    docker exec algotest-worker-optimize python -m tests.parity.capture
    # or, equivalently, against the live backend container:
    docker exec algotest-backend python -m tests.parity.capture

Each archetype is run once and the resulting (trades, summary, pivot) is
saved to ``backend/tests/parity/snapshots/<name>.json``. The snapshot
includes the exact payload that produced it, so reviewers can re-run by
hand and stale snapshots can be detected.

Pass ``--only NAME1,NAME2`` to capture a subset. Pass ``--force`` to
overwrite existing snapshots. By default, existing snapshots are skipped.

Whenever the engine changes in a way that should change snapshots (e.g.,
the team approves a correctness fix), re-run with ``--force`` after the
review.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

# Allow running as a standalone script when invoked from /app inside the
# container.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.parity import archetypes  # noqa: E402
from tests.parity.compare import run_engine  # noqa: E402

SNAP_DIR = Path(__file__).parent / "snapshots"


def _path_for(name: str) -> Path:
    return SNAP_DIR / f"{name}.json"


def _serialize(obj):
    """Make pivot/summary safely JSON-serialisable."""
    import numpy as np
    import pandas as pd

    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, tuple):
        return [_serialize(x) for x in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_serialize(x) for x in obj.tolist()]
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, (datetime,)):
        return obj.strftime("%Y-%m-%d")
    if hasattr(obj, "item"):  # numpy scalars
        try:
            return obj.item()
        except Exception:
            pass
    return obj


def capture_one(name: str, payload: dict) -> dict:
    t0 = time.perf_counter()
    result = run_engine(payload)
    elapsed = time.perf_counter() - t0
    snapshot = {
        "name": name,
        "captured_at": datetime.utcnow().isoformat() + "Z",
        "engine_runtime_seconds": round(elapsed, 3),
        "payload": payload,
        "trades": result.trades,
        "summary": result.summary,
        "pivot": result.pivot,
    }
    return _serialize(snapshot)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated archetype names; default = all",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing snapshots",
    )
    parser.add_argument(
        "--out-dir",
        default=str(SNAP_DIR),
        help="output directory for snapshots",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    names = (
        [n.strip() for n in args.only.split(",") if n.strip()]
        if args.only
        else archetypes.list_names()
    )

    print(f"[CAPTURE] target dir: {out_dir}")
    print(f"[CAPTURE] {len(names)} archetypes selected")
    successes, skips, failures = 0, 0, 0
    for name in names:
        path = out_dir / f"{name}.json"
        if path.exists() and not args.force:
            print(f"  ⏭  {name}: already exists (use --force to overwrite)")
            skips += 1
            continue
        try:
            payload = archetypes.get(name)
            snap = capture_one(name, payload)
            path.write_text(json.dumps(snap, indent=2, default=str))
            n_trades = len(snap["trades"])
            print(f"  ✓ {name}: {n_trades} trades, {snap['engine_runtime_seconds']}s")
            successes += 1
        except Exception as exc:
            import traceback
            print(f"  ✗ {name}: {exc}")
            traceback.print_exc()
            failures += 1

    print(f"\n[CAPTURE] done — captured={successes} skipped={skips} failed={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
