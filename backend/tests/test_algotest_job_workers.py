import os
import unittest
from unittest.mock import patch

from backend.services.algotest_job import _get_backtest_worker_count


class TestAlgotestJobWorkers(unittest.TestCase):
    def test_backtest_workers_defaults_to_single_process(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_get_backtest_worker_count(), 1)

    def test_backtest_workers_can_be_enabled_by_env(self):
        with patch.dict(os.environ, {"BACKTEST_WORKERS": "3"}):
            self.assertEqual(_get_backtest_worker_count(), 3)

    def test_backtest_workers_never_drops_below_one(self):
        with patch.dict(os.environ, {"BACKTEST_WORKERS": "0"}):
            self.assertEqual(_get_backtest_worker_count(), 1)


if __name__ == '__main__':
    unittest.main()
