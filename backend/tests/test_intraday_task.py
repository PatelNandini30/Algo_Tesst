import unittest
from unittest.mock import patch


class TestIntradayTask(unittest.TestCase):
    def test_task_calls_engine_and_returns_bytes(self):
        fake_bytes = b"FAKE_ARROW_IPC"
        with patch("backend.services.intraday_engine.run_intraday_backtest",
                   return_value=fake_bytes) as mock_engine:
            from backend.worker.tasks_intraday import execute_intraday_backtest
            result = execute_intraday_backtest({"symbol": "NIFTY", "date_from": "2024-01-01",
                                                "date_to": "2024-01-01", "entry_time": "09:20",
                                                "square_off_time": "15:15", "legs": []})
            mock_engine.assert_called_once()
            self.assertEqual(result, fake_bytes)
