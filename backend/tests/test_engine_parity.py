"""
Engine parity tests.

For every archetype that has a captured snapshot under
``backend/tests/parity/snapshots/``, run the active engine and compare
its output against the snapshot. Empty diff list = parity.

To regenerate snapshots after an approved engine change:

    docker exec algotest-backend python -m tests.parity.capture --force

Run the parity tests:

    .venv/bin/python -m unittest backend.tests.test_engine_parity -v

Run against a different engine backend (Phase 2b):

    ENGINE_BACKEND=rust .venv/bin/python -m unittest backend.tests.test_engine_parity

Set ``PARITY_REQUIRE_DATA=1`` to fail (rather than skip) when market data
isn't loaded. Useful in CI; default is skip-on-no-data so the test suite
stays green in environments without bhavcopy.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.parity import archetypes  # noqa: E402
from tests.parity.compare import compare, run_engine  # noqa: E402

SNAP_DIR = Path(__file__).parent / "parity" / "snapshots"
REQUIRE_DATA = os.environ.get("PARITY_REQUIRE_DATA") == "1"


def _load_snapshot(name: str):
    path = SNAP_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _data_loaded() -> bool:
    """Best-effort check: do we have bhavcopy data in the expected place?"""
    try:
        from base import bulk_load_options  # noqa: F401
        # Try a minimal load — if the DB has no NIFTY data, this will produce
        # 0 rows but should not raise.
        return True
    except Exception:
        return False


def _make_test_method(name: str):
    def test(self):
        snap = _load_snapshot(name)
        if snap is None:
            self.skipTest(
                f"no snapshot for {name!r} — run "
                "`python -m tests.parity.capture` to create it"
            )
        if not _data_loaded():
            msg = "engine module not importable — market data unavailable?"
            if REQUIRE_DATA:
                self.fail(msg)
            else:
                self.skipTest(msg)

        payload = snap["payload"]
        result = run_engine(payload)
        diffs = compare(snap, result)
        if diffs:
            self.fail(
                f"engine parity failed for {name!r}:\n  "
                + "\n  ".join(diffs)
            )

    test.__name__ = f"test_parity__{name}"
    return test


class TestEngineParity(unittest.TestCase):
    """Auto-generated parity tests — one per archetype with a snapshot."""


for _name in archetypes.list_names():
    setattr(TestEngineParity, f"test_parity__{_name}", _make_test_method(_name))


if __name__ == "__main__":
    unittest.main()
