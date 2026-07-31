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


from backend.services.leg_filter import apply_leg_filters


def _spec(trade_id, leg_id, entry, exit_):
    return {"trade_id": trade_id, "leg_id": leg_id,
            "entry_date": entry, "exit_date": exit_}


class TestApplyLegFilters(unittest.TestCase):
    # Leg 1 has no file; leg 2 is masked to April only.
    LEGS = [
        {"option_type": "CE"},
        {"option_type": "PE",
         "filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
    ]

    def test_no_leg_has_a_file_returns_input_unchanged(self):
        specs = [_spec(1, 1, "2025-03-05", "2025-03-27"),
                 _spec(1, 2, "2025-03-05", "2025-03-27")]
        legs = [{"option_type": "CE"}, {"option_type": "PE"}]
        self.assertEqual(apply_leg_filters(specs, legs), specs)

    def test_case_1_both_in_window_keeps_both_legs(self):
        specs = [_spec(1, 1, "2025-04-07", "2025-04-17"),
                 _spec(1, 2, "2025-04-07", "2025-04-17")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(len(out), 2)
        self.assertNotIn("_leg_filter_end", out[1])

    def test_case_2_masked_leg_is_absent_trade_survives(self):
        specs = [_spec(1, 1, "2025-03-05", "2025-03-27"),
                 _spec(1, 2, "2025-03-05", "2025-03-27")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual([(r["trade_id"], r["leg_id"]) for r in out], [(1, 1)])

    def test_truncated_leg_exits_early_and_is_tagged(self):
        specs = [_spec(2, 1, "2025-04-15", "2025-04-29"),
                 _spec(2, 2, "2025-04-15", "2025-04-29")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(out[0]["exit_date"], "2025-04-29")   # leg 1 untouched
        self.assertEqual(out[1]["exit_date"], "2025-04-20")   # leg 2 clamped
        self.assertTrue(out[1]["_leg_filter_end"])

    def test_trade_with_every_leg_masked_out_disappears(self):
        legs = [
            {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
            {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
        ]
        specs = [_spec(9, 1, "2025-03-05", "2025-03-27"),
                 _spec(9, 2, "2025-03-05", "2025-03-27")]
        self.assertEqual(apply_leg_filters(specs, legs), [])

    def test_leg_id_beyond_legs_list_is_left_alone(self):
        # Re-entry / synthetic rows can carry a leg_id with no config behind it.
        # They must pass through untouched, never be silently dropped.
        specs = [_spec(3, 7, "2025-03-05", "2025-03-27")]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(out, specs)

    def test_other_spec_keys_are_preserved(self):
        specs = [dict(_spec(1, 2, "2025-04-07", "2025-04-30"), strike=23000.0)]
        out = apply_leg_filters(specs, self.LEGS)
        self.assertEqual(out[0]["strike"], 23000.0)


if __name__ == "__main__":
    unittest.main()
