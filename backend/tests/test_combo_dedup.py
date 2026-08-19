"""The dedup fingerprint must merge only provably-identical strategies.

A wrong merge silently hands one strategy another's numbers — far worse than
running a duplicate. So these tests are mostly about what must NOT merge.

Field evidence: two real 14,400-combo sweeps collapsed to 4,900 fingerprints
with ZERO groups holding two different P&Ls (tools/combo_dedup_verify.py).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.optimizer.combo_dedup import effective_fingerprint, normalise_effective

def _leg(enabled, pct=1, direction="rise", strike=100):
    return {"segment": "OPTIONS", "option_type": "CE", "strike_interval": strike,
            "spot_adjustment": {"enabled": enabled, "pct": pct,
                                "direction": direction, "units": "percent"}}

def _payload(*legs, **kw):
    p = {"index": "NIFTY", "legs": list(legs)}
    p.update(kw)
    return p

class TestMergesOnlyInertDifferences(unittest.TestCase):
    def test_disabled_adjustment_ignores_pct_and_direction(self):
        """enabled=False -> pct/direction unreadable, so these are one strategy."""
        a = _payload(_leg(False, pct=1, direction="rise"))
        b = _payload(_leg(False, pct=2, direction="both"))
        self.assertEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_all_six_disabled_variants_collapse_to_one(self):
        """2 pct x 3 direction, all disabled -> exactly 1 fingerprint."""
        fps = {effective_fingerprint(_payload(_leg(False, pct=p, direction=d)))
               for p in (1, 2) for d in ("rise", "fall", "both")}
        self.assertEqual(len(fps), 1)

    def test_payload_level_adjustment_off(self):
        a = _payload(_leg(True), spot_adjustment_enabled=False, spot_adjustment_pct=1)
        b = _payload(_leg(True), spot_adjustment_enabled=False, spot_adjustment_pct=9)
        self.assertEqual(effective_fingerprint(a), effective_fingerprint(b))

class TestNeverMergesRealDifferences(unittest.TestCase):
    """Each of these MUST stay distinct — a merge here corrupts results."""

    def test_enabled_flag_itself_is_significant(self):
        self.assertNotEqual(effective_fingerprint(_payload(_leg(False))),
                            effective_fingerprint(_payload(_leg(True))))

    def test_enabled_adjustment_keeps_pct(self):
        self.assertNotEqual(effective_fingerprint(_payload(_leg(True, pct=1))),
                            effective_fingerprint(_payload(_leg(True, pct=2))))

    def test_enabled_adjustment_keeps_direction(self):
        self.assertNotEqual(effective_fingerprint(_payload(_leg(True, direction="rise"))),
                            effective_fingerprint(_payload(_leg(True, direction="fall"))))

    def test_strike_difference_is_never_merged(self):
        self.assertNotEqual(effective_fingerprint(_payload(_leg(False, strike=100))),
                            effective_fingerprint(_payload(_leg(False, strike=500))))

    def test_per_leg_independence(self):
        """Leg 1 disabled must not blank leg 2's live settings."""
        a = _payload(_leg(False), _leg(True, pct=1))
        b = _payload(_leg(False), _leg(True, pct=2))
        self.assertNotEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_leg_order_is_significant(self):
        """Leg order changes the anchor row; must not be normalised away."""
        a = _payload(_leg(True, pct=1), _leg(True, pct=2))
        b = _payload(_leg(True, pct=2), _leg(True, pct=1))
        self.assertNotEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_unrelated_payload_field_is_significant(self):
        a = _payload(_leg(True), expiry_type="WEEKLY")
        b = _payload(_leg(True), expiry_type="MONTHLY")
        self.assertNotEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_filter_segments_are_significant(self):
        a = _payload(_leg(True), filter_segments=[{"start": "2024-01-01", "end": "2024-06-30"}])
        b = _payload(_leg(True), filter_segments=[{"start": "2024-01-01", "end": "2024-12-31"}])
        self.assertNotEqual(effective_fingerprint(a), effective_fingerprint(b))

class TestNormalisationSafety(unittest.TestCase):
    def test_does_not_mutate_the_caller_payload(self):
        p = _payload(_leg(False, pct=7))
        before = p["legs"][0]["spot_adjustment"]["pct"]
        normalise_effective(p)
        self.assertEqual(p["legs"][0]["spot_adjustment"]["pct"], before)

    def test_disabled_leg_keeps_its_flag(self):
        n = normalise_effective(_payload(_leg(False, pct=5)))
        self.assertEqual(n["legs"][0]["spot_adjustment"], {"enabled": False})

    def test_string_truthiness_matches_the_engine_not_an_allow_list(self):
        """UI sends "false"/"true" as strings in places.

        _truthy() must mirror the ENGINE's own truthiness test
        (engine_rust.py:_resolve_leg_sa does `not _c.get('enabled')`), under
        which ANY non-empty string -- including the string "false" itself --
        is truthy. A curated allow-list ({'1','true','yes','on'}) previously
        treated the STRING "false" as OFF and stripped pct/direction, silently
        merging two payloads the engine runs as genuinely different (one with
        pct=1/rise, one with pct=2/both) -- dropping a real combo from the
        sweep with no error. Real Python `False` is unaffected (see
        test_disabled_adjustment_ignores_pct_and_direction below) -- only the
        allow-list's stringly-typed special case was wrong.
        """
        a = _payload(_leg("false", pct=1))
        b = _payload(_leg("false", pct=2))
        self.assertNotEqual(effective_fingerprint(a), effective_fingerprint(b))


    def test_fingerprint_is_stable_across_key_order(self):
        a = {"index": "NIFTY", "legs": [], "expiry_type": "WEEKLY"}
        b = {"expiry_type": "WEEKLY", "legs": [], "index": "NIFTY"}
        self.assertEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_empty_and_malformed_payloads_do_not_raise(self):
        for p in ({}, {"legs": None}, {"legs": ["not-a-dict"]}):
            self.assertIsInstance(effective_fingerprint(p), str)

class TestWiringNotJustTheRule(unittest.TestCase):
    """The rule was correct but the WIRING silently disabled it.

    `__combo_id__` is stamped on each combo and rides into the merged payload via
    apply_combo_for_optim. Being unique per combo, it made every fingerprint
    unique -> zero merges, no error, no log. The verify tool missed it because it
    reads stored `combo` dicts, where the marker is already stripped.
    """

    def test_dispatch_markers_never_affect_the_fingerprint(self):
        base = _payload(_leg(False, pct=1))
        a = dict(base); a["__combo_id__"] = 1
        b = dict(base); b["__combo_id__"] = 9999
        b["__optim_callback__"] = object()
        self.assertEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_end_to_end_grid_collapses(self):
        """Full path exactly as the runner does it, on the REAL grid shape.

        NOTE: apply_combo_for_optim AUTO-ENABLES a leg's spot adjustment when you
        sweep its pct/direction. So a grid must carry an explicit `enabled` axis
        for the disabled variants to exist at all — which is what the user's real
        sweeps do. Without that axis every combo is genuinely adjusted and there
        is nothing to merge.
        """
        from services.optimizer.param_expander import apply_combo_for_optim
        base = {"index": "NIFTY",
                "legs": [{"spot_adjustment": {"enabled": False, "pct": 1,
                                              "direction": "rise"}}]}
        grid = [{"legs[0].spot_adjustment.enabled": e,
                 "legs[0].spot_adjustment.pct": p,
                 "legs[0].spot_adjustment.direction": d}
                for e in (False, True) for p in (1, 2)
                for d in ("rise", "fall", "both")]
        for i, c in enumerate(grid, 1):
            c["__combo_id__"] = i          # exactly what the runner stamps
        fps = {effective_fingerprint(apply_combo_for_optim(base, c)) for c in grid}
        # 12 grid combos -> 6 adjusted variants + 1 disabled = 7 strategies.
        # If __combo_id__ leaked in, this would be 12 (dedup silently disabled).
        self.assertEqual(len(fps), 7,
                         "expected 7 unique strategies from a 12-combo grid")

class TestFutureFeaturesCannotBeSilentlySkipped(unittest.TestCase):
    """Guard for features that do not exist yet.

    The fingerprint hashes the WHOLE normalised payload, so any new field is
    included by default and can never be merged away by accident — a new feature
    is "significant" unless someone deliberately adds an inert rule for it.
    These tests pin that property so a future refactor cannot invert it.
    """

    def _distinct(self, mutate):
        base = _payload(_leg(True, pct=1), expiry_type="WEEKLY", index="NIFTY")
        other = mutate(_payload(_leg(True, pct=1), expiry_type="WEEKLY", index="NIFTY"))
        return effective_fingerprint(base) != effective_fingerprint(other)

    def test_a_brand_new_top_level_field_is_significant(self):
        def add(p):
            p["some_feature_added_next_year"] = True
            return p
        self.assertTrue(self._distinct(add), "new payload field was ignored")

    def test_a_brand_new_leg_field_is_significant(self):
        def add(p):
            p["legs"][0]["brand_new_leg_option"] = 5
            return p
        self.assertTrue(self._distinct(add), "new leg field was ignored")

    def test_changing_any_existing_field_is_significant(self):
        """Sweep every scalar in a representative payload; each must matter."""
        base = _payload(_leg(True, pct=1), expiry_type="WEEKLY", index="NIFTY",
                        entry_dte=1, exit_dte=1, square_off_mode="partial")
        fp0 = effective_fingerprint(base)
        import copy as _copy
        for key, val in list(base.items()):
            if key == "legs" or key.startswith("__"):
                continue
            mutated = _copy.deepcopy(base)
            mutated[key] = "CHANGED" if not isinstance(val, bool) else (not val)
            with self.subTest(field=key):
                self.assertNotEqual(fp0, effective_fingerprint(mutated),
                                    f"field {key!r} does not affect the fingerprint")

    def test_only_the_documented_inert_keys_are_dropped(self):
        """Exactly one leg key may be normalised away, and only when disabled."""
        n = normalise_effective(_payload(_leg(False, pct=5, direction="both")))
        self.assertEqual(set(n["legs"][0]["spot_adjustment"]), {"enabled"})
        # ...and when ENABLED, nothing is dropped.
        n2 = normalise_effective(_payload(_leg(True, pct=5, direction="both")))
        self.assertEqual(set(n2["legs"][0]["spot_adjustment"]),
                         {"enabled", "pct", "direction", "units"})

if __name__ == "__main__":
    unittest.main()

def _sw(mult, direction, other=1):
    """A straddle-width leg: strike = ATM ± mult × width."""
    return {"segment": "OPTIONS", "option_type": "CE",
            "strike_selection": {"type": "STRADDLE_WIDTH",
                                 "straddle_multiplier": mult,
                                 "straddle_direction": direction,
                                 "strike_interval": 100},
            "spot_adjustment": {"enabled": False}, "lots": other}

class TestStraddleDirectionInertAtZero(unittest.TestCase):
    """multiplier 0 -> ATM either way, so direction cannot matter.

    Proven on 980 real cases (job 204117dc): every pair differing only in
    straddle_direction with an identical P&L had multiplier 0.0, and NO non-zero
    multiplier ever produced a match.
    """

    def test_zero_multiplier_merges_plus_and_minus(self):
        self.assertEqual(effective_fingerprint(_payload(_sw(0.0, "+"))),
                         effective_fingerprint(_payload(_sw(0.0, "-"))))

    def test_zero_as_int_and_string_also_merge(self):
        a = effective_fingerprint(_payload(_sw(0, "+")))
        b = effective_fingerprint(_payload(_sw("0", "-")))
        self.assertEqual(a, b)

    def test_nonzero_multiplier_keeps_direction(self):
        for mult in (0.5, 1.0, 1.5, 2.0, -1.0):
            with self.subTest(mult=mult):
                self.assertNotEqual(effective_fingerprint(_payload(_sw(mult, "+"))),
                                    effective_fingerprint(_payload(_sw(mult, "-"))),
                                    f"direction wrongly merged at multiplier {mult}")

    def test_multiplier_itself_is_always_significant(self):
        self.assertNotEqual(effective_fingerprint(_payload(_sw(0.0, "+"))),
                            effective_fingerprint(_payload(_sw(0.5, "+"))))

    def test_per_leg_independence_at_zero(self):
        """Leg 1 at zero must not blank leg 2's direction."""
        a = _payload(_sw(0.0, "+"), _sw(1.0, "+"))
        b = _payload(_sw(0.0, "-"), _sw(1.0, "-"))
        self.assertNotEqual(effective_fingerprint(a), effective_fingerprint(b))

    def test_unparseable_multiplier_is_never_treated_as_zero(self):
        a = effective_fingerprint(_payload(_sw("abc", "+")))
        b = effective_fingerprint(_payload(_sw("abc", "-")))
        self.assertNotEqual(a, b, "unparseable multiplier must keep direction")

    def test_boolean_false_is_not_zero(self):
        """False == 0 in Python; it must NOT trigger the zero rule."""
        a = effective_fingerprint(_payload(_sw(False, "+")))
        b = effective_fingerprint(_payload(_sw(False, "-")))
        self.assertNotEqual(a, b)

if __name__ == "__main__":
    unittest.main()
