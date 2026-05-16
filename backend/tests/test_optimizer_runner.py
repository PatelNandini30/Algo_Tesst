"""
Integration smoke test for the optimizer runner.

We monkeypatch the heavy bits (market data load + engine call) so the test
runs without a database. The point is to verify that the runner:
  - validates correctly
  - iterates the right number of combos
  - persists progress + results via result_store
  - injects extra metrics
"""
import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd  # noqa: E402

from services.optimizer import result_store, runner  # noqa: E402


def _fake_trades_df():
    return pd.DataFrame(
        [
            {
                "Entry Date": "2024-01-01",
                "Exit Date": "2024-01-08",
                "Entry Spot": 20000,
                "Exit Spot": 20100,
                "Call P&L": 50,
                "Put P&L": -10,
                "Spot P&L": 100,
                "Net P&L": 40,
                "% P&L": 0.2,
                "Net P&L %": 0.2,
                "Trade": 1,
            }
        ]
    )


def _fake_summary():
    return {
        "total_pnl": 40,
        "count": 1,
        "win_pct": 100,
        "loss_pct": 0,
        "avg_win": 40,
        "avg_loss": 0,
        "expectancy": 0.4,
        "cagr_options": 12.0,
        "max_dd_pct": 0.0,
        "max_dd_pts": 0.0,
        "car_mdd": 0.0,
        "spot_change": 100.0,
        "cagr_spot": 5.0,
        "avg_profit_per_trade": 40,
    }


class FakeRedis:
    """Minimal in-memory shim for the redis methods result_store uses."""

    def __init__(self):
        self.kv = {}
        self.lists = {}

    def ping(self):
        return True

    def setex(self, key, ttl, value):
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.lists.pop(k, None)
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    def llen(self, key):
        return len(self.lists.get(key, []))

    def expire(self, key, ttl):
        return True


class TestRunnerSmoke(unittest.TestCase):
    def setUp(self):
        # Force sequential path so the in-process mocks apply — the parallel
        # path would spawn child processes that don't share our patches.
        os.environ["OPTIMIZE_PARALLELISM"] = "1"

        # Swap real Redis for a fake instance
        self._fake = FakeRedis()
        self._patches = [
            mock.patch.object(result_store, "_client", self._fake),
            mock.patch.object(result_store, "_redis", return_value=self._fake),
            mock.patch.object(runner, "_prepare_market_data", return_value={}),
            mock.patch.object(runner, "_teardown_market_data"),
            mock.patch.object(
                runner,
                "_run_single_backtest",
                return_value=(_fake_trades_df(), _fake_summary()),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.pop("OPTIMIZE_PARALLELISM", None)

    def test_runs_expected_combos(self):
        base = {
            "index": "NIFTY",
            "from_date": "2024-01-01",
            "to_date": "2024-12-31",
            "legs": [
                {
                    "segment": "OPTIONS",
                    "option_type": "CE",
                    "position": "SELL",
                    "strike_selection": {
                        "type": "pct_of_atm",
                        "value": 0.5,
                        "direction": "OTM",
                    },
                }
            ],
        }
        specs = [
            {"path": "entry_dte", "kind": "range", "min": 0, "max": 2, "step": 1},
            {"path": "exit_dte", "kind": "range", "min": 0, "max": 1, "step": 1},
        ]
        job_id = str(uuid.uuid4())
        res = runner.run_optimization(
            job_id,
            base_payload=base,
            param_specs=specs,
            method="exhaustive",
            objective="total_pnl",
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["total"], 6)  # 3 x 2

        rows = result_store.get_all_results(job_id)
        self.assertEqual(len(rows), 6)

        # Each row must have combo, summary and the new optim metrics
        for row in rows:
            self.assertIn("combo", row)
            self.assertIn("summary", row)
            self.assertIn("combo_label", row)
            self.assertIn("ce_pnl_total", row["summary"])
            self.assertIn("actual_live_dd_max", row["summary"])
            self.assertIn("car_mdd_live", row["summary"])

        meta = result_store.get_meta(job_id)
        self.assertEqual(meta["status"], "success")
        self.assertEqual(meta["done"], 6)

    def test_random_method_respects_n(self):
        base = {"index": "NIFTY"}
        specs = [
            {"path": "entry_dte", "kind": "range", "min": 0, "max": 4, "step": 1},
        ]
        job_id = str(uuid.uuid4())
        res = runner.run_optimization(
            job_id,
            base_payload=base,
            param_specs=specs,
            method="random",
            sample_n=3,
            seed=1,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["total"], 3)
        self.assertEqual(len(result_store.get_all_results(job_id)), 3)

    def test_validation_rejects_huge_exhaustive(self):
        specs = [
            {"path": "x", "kind": "range", "min": 0, "max": 10000, "step": 1},
            {"path": "y", "kind": "range", "min": 0, "max": 100, "step": 1},
        ]
        # Default OPTIMIZE_MAX_COMBOS = 100,000 — this grid is ~1M
        from services.optimizer.runner import OptimizationError

        with self.assertRaises(OptimizationError):
            runner.validate_request({"index": "NIFTY"}, specs, "exhaustive")


if __name__ == "__main__":
    unittest.main()
