import unittest

from backend.services.leg_filter import (
    leg_segments,
    leg_window,
    normalize_segments,
    seg_iso,
)


class TestSegIso(unittest.TestCase):
    def test_dayfirst_string_is_not_flipped(self):
        # 10-May-2019, NOT 5-Oct-2019. This is the bug _seg_iso exists to prevent.
        self.assertEqual(seg_iso("10/05/2019"), "2019-05-10")

    def test_iso_string_passes_through(self):
        self.assertEqual(seg_iso("2019-05-10"), "2019-05-10")

    def test_datetime_is_formatted_not_reparsed(self):
        from datetime import datetime
        self.assertEqual(seg_iso(datetime(2019, 5, 10)), "2019-05-10")


class TestNormalizeSegments(unittest.TestCase):
    def test_dicts_become_sorted_iso_tuples(self):
        raw = [{"start": "05-06-2025", "end": "05-07-2025"},
               {"start": "05-04-2025", "end": "05-05-2025"}]
        self.assertEqual(
            normalize_segments(raw),
            [("2025-04-05", "2025-05-05"), ("2025-06-05", "2025-07-05")],
        )

    def test_bad_rows_are_skipped_not_fatal(self):
        raw = [{"start": "05-04-2025", "end": "05-05-2025"}, {"start": "", "end": ""}]
        self.assertEqual(normalize_segments(raw), [("2025-04-05", "2025-05-05")])

    def test_inverted_range_is_dropped(self):
        self.assertEqual(normalize_segments([{"start": "05-05-2025", "end": "05-04-2025"}]), [])

    def test_overlapping_ranges_are_merged(self):
        raw = [{"start": "2025-01-01", "end": "2025-06-30"},
               {"start": "2025-03-01", "end": "2025-03-31"}]
        self.assertEqual(normalize_segments(raw), [("2025-01-01", "2025-06-30")])

    def test_contained_range_is_absorbed(self):
        raw = [{"start": "2025-01-01", "end": "2025-06-30"},
               {"start": "2025-02-01", "end": "2025-02-15"}]
        self.assertEqual(normalize_segments(raw), [("2025-01-01", "2025-06-30")])

    def test_separate_ranges_stay_separate(self):
        raw = [{"start": "2025-01-01", "end": "2025-01-31"},
               {"start": "2025-06-01", "end": "2025-06-30"}]
        self.assertEqual(
            normalize_segments(raw),
            [("2025-01-01", "2025-01-31"), ("2025-06-01", "2025-06-30")],
        )


class TestLegSegments(unittest.TestCase):
    def test_absent_key_is_none(self):
        self.assertIsNone(leg_segments({"leg_id": 1}))

    def test_empty_list_is_none(self):
        # An uploaded-then-cleared file must behave exactly like no file at all.
        self.assertIsNone(leg_segments({"filter_segments": []}))

    def test_present_returns_tuples(self):
        leg = {"filter_segments": [{"start": "05-04-2025", "end": "05-06-2025"}]}
        self.assertEqual(leg_segments(leg), [("2025-04-05", "2025-06-05")])


class TestLegWindow(unittest.TestCase):
    MASK = [("2025-04-05", "2025-06-05")]

    def test_entry_outside_mask_drops_the_leg(self):
        taken, _, _ = leg_window(self.MASK, "2025-03-01", "2025-03-27")
        self.assertFalse(taken)

    def test_entry_inside_and_exit_inside_is_untouched(self):
        self.assertEqual(
            leg_window(self.MASK, "2025-04-10", "2025-04-24"),
            (True, "2025-04-24", False),
        )

    def test_exit_beyond_window_end_is_truncated(self):
        # Spec case 1: window ends 05-Jun, trade exits 26-Jun -> leg exits 05-Jun.
        self.assertEqual(
            leg_window(self.MASK, "2025-06-02", "2025-06-26"),
            (True, "2025-06-05", True),
        )

    def test_window_end_beyond_trade_exit_keeps_trade_exit(self):
        # Spec case 2: window ends 28-Jun, trade exits 26-Jun -> leg exits 26-Jun.
        mask = [("2025-04-05", "2025-06-28")]
        self.assertEqual(
            leg_window(mask, "2025-06-02", "2025-06-26"),
            (True, "2025-06-26", False),
        )

    def test_degenerate_window_drops_the_leg(self):
        # Entry lands ON the window's last day: truncated exit <= entry, so the
        # leg would have a zero/negative hold. Drop it instead of emitting it.
        self.assertEqual(leg_window(self.MASK, "2025-06-05", "2025-06-26")[0], False)

    def test_entry_on_window_boundaries_is_inclusive(self):
        self.assertTrue(leg_window(self.MASK, "2025-04-05", "2025-04-24")[0])

    def test_entry_inside_merged_overlapping_segment_is_taken(self):
        # Regression: normalize_segments must merge overlapping windows so
        # leg_window's rightmost-window bisect doesn't pick the wrong one.
        mask = normalize_segments([
            {"start": "2025-01-01", "end": "2025-06-30"},
            {"start": "2025-03-01", "end": "2025-03-31"},
        ])
        self.assertTrue(leg_window(mask, "2025-04-15", "2025-05-01")[0])


if __name__ == "__main__":
    unittest.main()
