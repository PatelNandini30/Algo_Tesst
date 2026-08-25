"""Regression coverage for EOD fixed-IV Delta strike selection."""
import sys
import types
import unittest


DATE = "2025-01-02"
EXPIRY = "2025-02-01"
SPOT = 100.0
CHAIN = {float(strike): 1.0 for strike in range(80, 121)}


def _install_native_stub():
    native = types.ModuleType("algotest_native")
    native.get_strikes_for_date = lambda *_args: sorted(CHAIN.items())
    native.get_option_price_tradeable = lambda _d, _i, strike, _o, _e: CHAIN.get(strike)
    native.get_option_price = lambda _d, _i, strike, _o, _e: CHAIN.get(strike)
    sys.modules["algotest_native"] = native


class TestDeltaMath(unittest.TestCase):
    def test_call_put_absolute_delta_are_complements(self):
        from services.engine_rust import _bs_delta

        call = _bs_delta(SPOT, SPOT, 30, 0.13, True)
        put = _bs_delta(SPOT, SPOT, 30, 0.13, False)
        self.assertAlmostEqual(call + put, 1.0, places=10)

    def test_lower_call_delta_is_further_otm(self):
        from services.engine_rust import _bs_delta

        self.assertLess(
            _bs_delta(SPOT, 110.0, 30, 0.13, True),
            _bs_delta(SPOT, 105.0, 30, 0.13, True),
        )


class TestDeltaStrikeResolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = sys.modules.get("algotest_native")
        _install_native_stub()
        from services.engine_rust import _compute_strike_for_leg_python
        cls.resolve = staticmethod(_compute_strike_for_leg_python)

    @classmethod
    def tearDownClass(cls):
        if cls._saved is None:
            sys.modules.pop("algotest_native", None)
        else:
            sys.modules["algotest_native"] = cls._saved

    def _pick(self, option_type, target=0.30, gap=1.0):
        leg = {
            "option_type": option_type,
            "strike_selection": {"type": "delta", "delta": target},
        }
        return self.resolve(
            leg, SPOT, gap, entry_date=DATE, expiry=EXPIRY, index="NIFTY"
        )

    def test_30_delta_call_selects_otm_call(self):
        self.assertGreater(self._pick("CE"), SPOT)

    def test_30_delta_put_selects_otm_put(self):
        self.assertLess(self._pick("PE"), SPOT)

    def test_leg_grid_is_respected(self):
        self.assertEqual(self._pick("CE", gap=5.0) % 5.0, 0.0)


class TestDeltaValidationAndLabels(unittest.TestCase):
    def test_invalid_delta_is_rejected(self):
        from services.engine_rust import _assert_known_strike_modes

        with self.assertRaisesRegex(ValueError, "between 0.01 and 0.99"):
            _assert_known_strike_modes({
                "legs": [{
                    "segment": "OPTIONS",
                    "strike_selection": {"type": "delta", "delta": 1.2},
                }]
            })

    def test_missing_delta_uses_default(self):
        from services.engine_rust import _assert_known_strike_modes

        _assert_known_strike_modes({
            "legs": [{
                "segment": "OPTIONS",
                "strike_selection": {"type": "delta"},
            }]
        })

    def test_optimizer_filename_uses_delta_token(self):
        from services.optimizer.combo_labeler import _strike_label

        self.assertEqual(_strike_label({"type": "delta", "delta": 0.30}), "D30")

    def test_optimizer_rules_sheet_explains_approximation(self):
        from services.optimizer.rules_sheet import _strike_label

        label = _strike_label({"type": "delta", "delta": 0.30})
        self.assertIn("30Δ", label)
        self.assertIn("fixed-IV", label)


if __name__ == "__main__":
    unittest.main()
