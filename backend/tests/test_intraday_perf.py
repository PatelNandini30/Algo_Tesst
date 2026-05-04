"""
Performance regression test for intraday backtest.
Run explicitly: python -m unittest backend.tests.test_intraday_perf -v
NOT included in discover (requires real data at INTRADAY_DATA_DIR).
SLAs: single 1-year NIFTY straddle p50 < 700 ms, p95 < 1100 ms.
"""
import os
import statistics
import time
import unittest

INTRADAY_DATA_DIR = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")
NIFTY_SNAPS = os.path.join(INTRADAY_DATA_DIR, "NIFTY", "snapshots")


@unittest.skipUnless(
    os.path.exists(NIFTY_SNAPS) and len(os.listdir(NIFTY_SNAPS)) > 200,
    "requires 200+ NIFTY snapshots; run after backfill"
)
class TestIntradayPerf(unittest.TestCase):
    CONFIG = {
        "symbol": "NIFTY",
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
        "entry_time": "09:20",
        "square_off_time": "15:15",
        "legs": [
            {"opt_type": "CE", "action": "SELL",
             "strike_selection": {"mode": "ATM", "value": 0},
             "expiry": "WEEKLY", "quantity": 1,
             "sl": {"type": "percent", "value": 50.0}, "target": None},
            {"opt_type": "PE", "action": "SELL",
             "strike_selection": {"mode": "ATM", "value": 0},
             "expiry": "WEEKLY", "quantity": 1,
             "sl": {"type": "percent", "value": 50.0}, "target": None},
        ]
    }

    def test_single_1year_straddle_latency(self):
        from backend.services.intraday_engine import run_intraday_backtest
        samples = []
        for _ in range(10):
            t0 = time.perf_counter()
            result = run_intraday_backtest(self.CONFIG)
            samples.append(time.perf_counter() - t0)

        p50 = statistics.median(samples) * 1000
        p95 = sorted(samples)[int(len(samples) * 0.95)] * 1000
        print(f"\n[perf] p50={p50:.0f}ms p95={p95:.0f}ms over {len(samples)} runs")
        self.assertLess(p50, 700, f"p50 {p50:.0f}ms exceeds 700ms SLA")
        self.assertLess(p95, 1100, f"p95 {p95:.0f}ms exceeds 1100ms SLA")
        self.assertGreater(len(result), 0, "empty result bytes")
