import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMultiIndexCacheRecovery(unittest.TestCase):
    def test_missing_native_symbol_forces_full_reload(self):
        import algotest_native
        from services import multi_index_feature as mif

        old_activated = mif._bulk_engine_activated
        state = {"loaded": False}

        def full_load(*_args, **_kwargs):
            state["loaded"] = True
            return {}

        try:
            mif._bulk_engine_activated = True
            with mock.patch("services.rust_fast_path.ensure_symbol_merged", return_value=True), \
                 mock.patch("base.bulk_load_options", side_effect=full_load) as bulk, \
                 mock.patch("services.algotest_job._build_fast_lookup_from_bulk"), \
                 mock.patch.object(algotest_native, "is_loaded",
                                   side_effect=lambda: state["loaded"]), \
                 mock.patch.object(algotest_native, "cache_symbols",
                                   side_effect=lambda: ["NIFTY"] if state["loaded"] else []):
                mif._ensure_group_symbol_loaded(
                    "NIFTY", "2024-01-01", "2024-12-31",
                )
            bulk.assert_called_once_with("NIFTY", "2024-01-01", "2024-12-31")
        finally:
            mif._bulk_engine_activated = old_activated

    def test_reload_that_still_has_no_symbol_fails_loudly(self):
        import algotest_native
        from services import multi_index_feature as mif

        old_activated = mif._bulk_engine_activated
        try:
            mif._bulk_engine_activated = True
            with mock.patch("services.rust_fast_path.ensure_symbol_merged", return_value=False), \
                 mock.patch("base.bulk_load_options", return_value={}), \
                 mock.patch("services.algotest_job._build_fast_lookup_from_bulk"), \
                 mock.patch.object(algotest_native, "is_loaded", return_value=False), \
                 mock.patch.object(algotest_native, "cache_symbols", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "NIFTY.*not resident"):
                    mif._ensure_group_symbol_loaded(
                        "NIFTY", "2024-01-01", "2024-12-31",
                    )
        finally:
            mif._bulk_engine_activated = old_activated


class TestWarmQueueProtection(unittest.TestCase):
    def test_busy_backtest_queue_skips_background_warm(self):
        from routers import backtest

        request = {
            "index": "NIFTY",
            "from_date": "2025-01-01",
            "to_date": "2025-12-31",
        }
        with mock.patch.object(backtest, "_normalize_payload_dates", side_effect=lambda p: p), \
             mock.patch.object(backtest, "_real_backtest_active", return_value=False), \
             mock.patch.object(backtest, "_queue_depth", return_value=5), \
             mock.patch("services.optimizer.result_store._redis", return_value=None), \
             mock.patch.object(backtest.warm_backtest_cache_task, "apply_async") as enqueue:
            result = asyncio.run(backtest.warm_cache(request))

        self.assertEqual(result["status"], "skipped")
        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
