import unittest
from backend.services.leg_filter import split_windows

# A dense trading-day list around the real cases (Jan–Feb 2020 weekly-ish).
TD = [
    "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08",
    "2020-01-09", "2020-01-30", "2020-02-03", "2020-02-04", "2020-02-05",
    "2020-02-06",
]


class TestSplitWindows(unittest.TestCase):
    def test_no_ranges_single_window_out(self):
        out = split_windows("2020-01-02", "2020-01-09", [], TD)
        self.assertEqual(out, [{"seg_start": "2020-01-02",
                                "seg_end": "2020-01-09", "in_range": False}])

    def test_entry_split_range_starts_midtrade(self):
        # Range [06-Jan → 04-Feb]; trade 02→09-Jan. Split at 06-Jan.
        out = split_windows("2020-01-02", "2020-01-09",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [
            {"seg_start": "2020-01-02", "seg_end": "2020-01-06", "in_range": False},
            {"seg_start": "2020-01-06", "seg_end": "2020-01-09", "in_range": True},
        ])

    def test_exit_split_range_ends_midtrade(self):
        # Range ends 04-Feb; trade 30-Jan→06-Feb. Split at 04-Feb.
        out = split_windows("2020-01-30", "2020-02-06",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [
            {"seg_start": "2020-01-30", "seg_end": "2020-02-04", "in_range": True},
            {"seg_start": "2020-02-04", "seg_end": "2020-02-06", "in_range": False},
        ])

    def test_boundary_on_entry_no_split(self):
        out = split_windows("2020-01-06", "2020-01-09",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [{"seg_start": "2020-01-06",
                                "seg_end": "2020-01-09", "in_range": True}])

    def test_whole_window_outside_all_ranges(self):
        out = split_windows("2020-01-02", "2020-01-03",
                            [("2020-01-06", "2020-02-04")], TD)
        self.assertEqual(out, [{"seg_start": "2020-01-02",
                                "seg_end": "2020-01-03", "in_range": False}])

    def test_two_boundaries_in_one_trade(self):
        # A short range fully inside the trade -> three sub-windows: out/in/out.
        out = split_windows("2020-01-02", "2020-01-09",
                            [("2020-01-06", "2020-01-08")], TD)
        self.assertEqual([w["in_range"] for w in out], [False, True, False])
        self.assertEqual([w["seg_start"] for w in out],
                         ["2020-01-02", "2020-01-06", "2020-01-08"])


if __name__ == "__main__":
    unittest.main()
