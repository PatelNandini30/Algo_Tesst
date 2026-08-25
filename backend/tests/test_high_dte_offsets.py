import unittest
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.engine_rust import _trading_day_n_before  # noqa: E402


class TestHighDteOffsets(unittest.TestCase):
    def test_fifty_trading_day_offset_is_supported(self):
        trading_days = [f"2026-01-{day:02d}" for day in range(1, 32)]
        trading_days += [f"2026-02-{day:02d}" for day in range(1, 29)]

        self.assertEqual(
            _trading_day_n_before("2026-02-28", 50, trading_days),
            "2026-01-09",
        )

    def test_entry_t50_precedes_exit_t2(self):
        trading_days = [f"2026-01-{day:02d}" for day in range(1, 32)]
        trading_days += [f"2026-02-{day:02d}" for day in range(1, 29)]

        entry = _trading_day_n_before("2026-02-28", 50, trading_days)
        exit_ = _trading_day_n_before("2026-02-28", 2, trading_days)

        self.assertLess(entry, exit_)


if __name__ == "__main__":
    unittest.main()
