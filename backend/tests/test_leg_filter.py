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


from backend.services.leg_filter import apply_leg_filters, resolve_leg_window

# April 2025 sessions used by the tests below. 2025-04-20 is a SUNDAY and is
# deliberately absent, so any truncation landing on it must snap back to the
# 18th. Weekends only -- holidays are irrelevant to what is being asserted.
TD = [
    "2025-03-05", "2025-03-27",
    "2025-04-01", "2025-04-02", "2025-04-03", "2025-04-04",
    "2025-04-07", "2025-04-08", "2025-04-09", "2025-04-10", "2025-04-11",
    "2025-04-15", "2025-04-16", "2025-04-17", "2025-04-18",
    "2025-04-21", "2025-04-22", "2025-04-23", "2025-04-24", "2025-04-25",
    "2025-04-28", "2025-04-29", "2025-04-30",
]


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
        self.assertEqual(apply_leg_filters(specs, legs, TD), specs)

    def test_case_1_both_in_window_keeps_both_legs(self):
        specs = [_spec(1, 1, "2025-04-07", "2025-04-17"),
                 _spec(1, 2, "2025-04-07", "2025-04-17")]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual(len(out), 2)
        self.assertNotIn("_leg_filter_end", out[1])

    def test_case_2_masked_leg_is_absent_trade_survives(self):
        specs = [_spec(1, 1, "2025-03-05", "2025-03-27"),
                 _spec(1, 2, "2025-03-05", "2025-03-27")]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual([(r["trade_id"], r["leg_id"]) for r in out], [(1, 1)])

    def test_truncated_leg_exits_early_and_is_tagged(self):
        specs = [_spec(2, 1, "2025-04-15", "2025-04-29"),
                 _spec(2, 2, "2025-04-15", "2025-04-29")]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual(out[0]["exit_date"], "2025-04-29")   # leg 1 untouched
        # Window ends on Sun 20-Apr; snapped back to the last session, Fri 18th.
        self.assertEqual(out[1]["exit_date"], "2025-04-18")   # leg 2 clamped
        self.assertTrue(out[1]["_leg_filter_end"])

    def test_trade_with_every_leg_masked_out_disappears(self):
        legs = [
            {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
            {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]},
        ]
        specs = [_spec(9, 1, "2025-03-05", "2025-03-27"),
                 _spec(9, 2, "2025-03-05", "2025-03-27")]
        self.assertEqual(apply_leg_filters(specs, legs, TD), [])

    def test_leg_id_beyond_legs_list_is_left_alone(self):
        # Re-entry / synthetic rows can carry a leg_id with no config behind it.
        # They must pass through untouched, never be silently dropped.
        specs = [_spec(3, 7, "2025-03-05", "2025-03-27")]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual(out, specs)

    def test_truncated_exit_snaps_back_to_a_trading_day(self):
        # C1 regression. Without the snap the exit_date stays Sun 20-Apr, which
        # has no price: simulate_one returns entry=exit=net_pnl=0 and the row
        # survives booking ZERO P&L instead of exiting the prior Friday.
        specs = [_spec(2, 2, "2025-04-15", "2025-04-29")]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual(out[0]["exit_date"], "2025-04-18")
        self.assertIn(out[0]["exit_date"], TD)

    def test_snap_reaching_the_entry_drops_the_leg(self):
        # C1, second half. Entry Fri 18-Apr, window ends Sun 20-Apr -> snaps back
        # to the 18th == entry. Zero-length hold: the leg must be DROPPED, exactly
        # as the futures twin does, not emitted as a same-day round trip.
        specs = [_spec(4, 1, "2025-04-18", "2025-04-29"),
                 _spec(4, 2, "2025-04-18", "2025-04-29")]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual([(r["trade_id"], r["leg_id"]) for r in out], [(4, 1)])

    def test_empty_trading_days_leaves_the_boundary_unsnapped(self):
        # Degenerate input must not crash or drop everything; the raw boundary
        # is the only thing left to use.
        specs = [_spec(2, 2, "2025-04-15", "2025-04-29")]
        out = apply_leg_filters(specs, self.LEGS, [])
        self.assertEqual(out[0]["exit_date"], "2025-04-20")

    def test_other_spec_keys_are_preserved(self):
        specs = [dict(_spec(1, 2, "2025-04-07", "2025-04-30"), strike=23000.0)]
        out = apply_leg_filters(specs, self.LEGS, TD)
        self.assertEqual(out[0]["strike"], 23000.0)


class TestResolveLegWindowIsShared(unittest.TestCase):
    """The options post-pass and the futures builders MUST agree, byte for byte,
    on the same uploaded file -- two implementations of this rule is what
    produced the unsnapped-exit bug. Both now call resolve_leg_window.
    """

    LEG = {"filter_segments": [{"start": "01-04-2025", "end": "20-04-2025"}]}

    def test_matches_what_apply_leg_filters_does_to_a_spec(self):
        taken, exit_, trunc = resolve_leg_window(
            self.LEG, "2025-04-15", "2025-04-29", TD
        )
        out = apply_leg_filters(
            [_spec(1, 1, "2025-04-15", "2025-04-29")], [self.LEG], TD
        )
        self.assertEqual((taken, exit_, trunc), (True, "2025-04-18", True))
        self.assertEqual(out[0]["exit_date"], exit_)

    def test_leg_without_a_file_is_a_pure_passthrough(self):
        self.assertEqual(
            resolve_leg_window({}, "2025-04-15", "2025-04-29", TD),
            (True, "2025-04-29", False),
        )

    def test_untruncated_leg_keeps_the_trade_exit_verbatim(self):
        # Trade exits BEFORE the window end -- earliest wins, nothing to snap,
        # nothing to tag.
        self.assertEqual(
            resolve_leg_window(self.LEG, "2025-04-07", "2025-04-17", TD),
            (True, "2025-04-17", False),
        )


if __name__ == "__main__":
    unittest.main()
