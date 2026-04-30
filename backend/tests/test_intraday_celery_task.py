import unittest
from unittest.mock import patch
from datetime import date

from backend.worker import tasks_intraday


class TestIntradayCeleryTask(unittest.TestCase):
    def test_task_is_registered(self):
        self.assertTrue(callable(tasks_intraday.ingest_intraday))

    def test_task_calls_publish(self):
        with patch(
            "backend.worker.tasks_intraday.intraday_publish.publish"
        ) as fake_publish:
            tasks_intraday.ingest_intraday(
                symbol="NIFTY",
                trading_date_iso="2024-03-15",
                source_path="/tmp/x.csv",
                data_root="/data/intraday",
            )
            fake_publish.assert_called_once()
            kwargs = fake_publish.call_args.kwargs
            self.assertEqual(kwargs["symbol"], "NIFTY")
            self.assertEqual(kwargs["trading_date"], date(2024, 3, 15))


if __name__ == "__main__":
    unittest.main()
