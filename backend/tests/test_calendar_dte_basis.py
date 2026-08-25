import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import engine_rust  # noqa: E402


class TestCalendarDteBasis(unittest.TestCase):
    def setUp(self):
        self.trading_days = [
            "2026-03-26",
            "2026-03-27",
            "2026-03-30",
            "2026-03-31",
        ]

    def test_trading_days_remain_the_default(self):
        token = engine_rust._DTE_DAY_BASIS.set("trading")
        try:
            self.assertEqual(
                engine_rust._trading_day_n_before(
                    "2026-03-30", 2, self.trading_days
                ),
                "2026-03-26",
            )
        finally:
            engine_rust._DTE_DAY_BASIS.reset(token)

    def test_calendar_days_snap_back_to_a_trading_day(self):
        token = engine_rust._DTE_DAY_BASIS.set("calendar")
        try:
            self.assertEqual(
                engine_rust._trading_day_n_before(
                    "2026-03-30", 2, self.trading_days
                ),
                "2026-03-27",
            )
            self.assertEqual(
                engine_rust._trading_day_n_before(
                    "2026-03-31", 1, self.trading_days
                ),
                "2026-03-30",
            )
        finally:
            engine_rust._DTE_DAY_BASIS.reset(token)


if __name__ == "__main__":
    unittest.main()
