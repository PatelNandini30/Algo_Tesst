import sys
import types
import unittest

try:
    from services.engine_rust import _check_overall_sl_target_py
except ModuleNotFoundError:  # pragma: no cover
    from backend.services.engine_rust import _check_overall_sl_target_py


def _leg(opt, pos="SELL", strike=100.0, prem=10.0, expiry="2020-01-31"):
    return {"segment": "OPTIONS", "option_type": opt, "position": pos,
            "strike": strike, "entry_premium": prem, "_resolved_expiry": expiry,
            "lots": 1, "lot_size": 1}


DAYS = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"]


class TestOverallUnderlying(unittest.TestCase):
    def test_underlying_pct_sl_fires_on_adverse_up_move(self):
        # CE SELL → adverse = up move. Spot +5% on day 2 vs 3% SL.
        spot = {"2020-01-01": 100.0, "2020-01-02": 105.0, "2020-01-03": 106.0}
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE")], "NIFTY", DAYS, 3.0, None, None,
            "underlying_pct", "", 0.0, spot)
        self.assertEqual(r, ("2020-01-02", "OVERALL_SL"))

    def test_underlying_pct_no_fire_on_favourable_move(self):
        # CE SELL, spot falls (favourable) → no SL.
        spot = {"2020-01-01": 100.0, "2020-01-02": 95.0, "2020-01-03": 94.0}
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE")], "NIFTY", DAYS, 3.0, None, None,
            "underlying_pct", "", 0.0, spot)
        self.assertIsNone(r)

    def test_off_returns_none(self):
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE")], "NIFTY", DAYS, None, None, None, "", "", 0.0, {})
        self.assertIsNone(r)


class TestOverallPremium(unittest.TestCase):
    def setUp(self):
        # Inject a fake algotest_native so the helper's function-local import
        # resolves to a controllable get_option_price.
        self._prices = {}
        fake = types.ModuleType("algotest_native")
        fake.get_option_price = lambda d, idx, k, ot, exp: self._prices.get((d, k, ot))
        self._orig = sys.modules.get("algotest_native")
        sys.modules["algotest_native"] = fake

    def tearDown(self):
        if self._orig is not None:
            sys.modules["algotest_native"] = self._orig
        else:
            sys.modules.pop("algotest_native", None)

    def test_premium_sl_fires_when_combined_loss_exceeds(self):
        # SELL straddle: entry prem 10+10=20. Day2 marks blow out to 40+5 → loss=(10-40)+(10-5)=-25.
        self._prices = {
            ("2020-01-02", 100.0, "CE"): 40.0,
            ("2020-01-02", 100.0, "PE"): 5.0,
        }
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE"), _leg("PE")], "NIFTY", DAYS, 20.0, None, None,
            "max_loss", "", 0.0, {})
        self.assertEqual(r, ("2020-01-02", "OVERALL_SL"))

    def test_premium_target_fires(self):
        # SELL straddle decays: 2+1 → profit=(10-2)+(10-1)=17 ≥ 15 target.
        self._prices = {
            ("2020-01-02", 100.0, "CE"): 2.0,
            ("2020-01-02", 100.0, "PE"): 1.0,
        }
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE"), _leg("PE")], "NIFTY", DAYS, None, 15.0, None,
            "max_loss", "max_loss", 0.0, {})
        self.assertEqual(r, ("2020-01-02", "OVERALL_TARGET"))

    def test_early_closed_leg_excluded_after_its_exit(self):
        # PE (leg 1) SL'd on 2020-01-01; on 2020-01-02 only CE counts.
        # CE alone: (10-40) = -30 ≤ -20 → fires; proves closed leg dropped, not all.
        self._prices = {("2020-01-02", 100.0, "CE"): 40.0}
        per_leg = [None, {"exit_reason": "STOP_LOSS", "exit_date": "2020-01-01"}]
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE"), _leg("PE")], "NIFTY", DAYS, 20.0, None, per_leg,
            "max_loss", "", 0.0, {})
        self.assertEqual(r, ("2020-01-02", "OVERALL_SL"))

    def test_scheduled_expiry_leg_not_excluded(self):
        # A leg with a plain EXPIRY reason must NOT be dropped (the original bug).
        self._prices = {
            ("2020-01-02", 100.0, "CE"): 40.0,
            ("2020-01-02", 100.0, "PE"): 5.0,
        }
        per_leg = [{"exit_reason": "EXPIRY", "exit_date": "2020-01-03"},
                   {"exit_reason": "EXPIRY", "exit_date": "2020-01-03"}]
        r = _check_overall_sl_target_py("2020-01-01", "2020-01-03", "2020-01-31",
            [_leg("CE"), _leg("PE")], "NIFTY", DAYS, 20.0, None, per_leg,
            "max_loss", "", 0.0, {})
        self.assertEqual(r, ("2020-01-02", "OVERALL_SL"))


if __name__ == "__main__":
    unittest.main()
