"""Delta strike selection via ACTUAL deltas (services.delta_lookup +
engine_rust._apply_delta_to_specs).

Offline: the delta index is monkeypatched (no /data/cache feather needed) and a
stub algotest_native drives the tradeable-shift path — so this never touches the
shared feather.
"""
import sys
import types
import unittest

from services import delta_lookup
from services import engine_rust as E

KEY = ("NIFTY", "2020-01-01", "2020-01-30")
# Stored deltas are ABS (that's what delta_lookup._load writes). Clean, non-tie
# distances to 0.30 so ordering is unambiguous: 11000 (0.31) is the ideal.
_CE = [(10900.0, 0.42), (11000.0, 0.31), (11100.0, 0.26)]
_PE = [(10800.0, 0.30), (10700.0, 0.22)]


def setUpModule():
    delta_lookup._LOADED = True
    delta_lookup._INDEX = {KEY: {"CE": list(_CE), "PE": list(_PE)}}


def tearDownModule():
    delta_lookup._LOADED = False
    delta_lookup._INDEX = None
    sys.modules.pop("algotest_native", None)


def _payload():
    return {"index": "NIFTY", "legs": [
        {"segment": "OPTIONS", "index": "NIFTY",
         "strike_selection": {"type": "delta", "delta": 0.30}}]}


def _spec(strike=10500.0, ot="CE"):
    return {"trade_id": 1, "leg_id": 1, "index": "NIFTY",
            "entry_date": "2020-01-01", "expiry": "2020-01-30",
            "option_type": ot, "strike": strike}


class TestDeltaLookup(unittest.TestCase):
    def test_candidates_ordered_by_actual_delta_closeness(self):
        c = delta_lookup.candidates_by_delta("NIFTY", "2020-01-01", "2020-01-30", "CE", 0.30)
        # 11000 (|.32-.30|=.02) and 11100 (|.28-.30|=.02) tie → lower strike first, then 10900
        self.assertEqual(c, [11000.0, 11100.0, 10900.0])

    def test_pe_uses_abs_delta(self):
        c = delta_lookup.candidates_by_delta("NIFTY", "2020-01-01", "2020-01-30", "PE", 0.30)
        self.assertEqual(c[0], 10800.0)   # |−0.30 − 0.30|... abs(−0.30)=0.30 exact

    def test_no_data_keeps_original_strike(self):
        sys.modules.pop("algotest_native", None)
        specs = [_spec(strike=10500.0)]
        E._apply_delta_to_specs(specs, _payload(), E._delta_legs(_payload()), 65)
        # unknown expiry → no candidates → original strike untouched
        specs2 = [dict(_spec(), expiry="1999-01-01")]
        E._apply_delta_to_specs(specs2, _payload(), E._delta_legs(_payload()), 65)
        self.assertEqual(specs2[0]["strike"], 10500.0)

    def test_picks_ideal_when_tradeable(self):
        # stub native: everything tradeable → ideal (11000) wins, no shift
        stub = types.ModuleType("algotest_native")
        stub.get_option_price_tradeable = lambda *a, **k: 100.0
        sys.modules["algotest_native"] = stub
        specs = [_spec(strike=10500.0)]
        E._apply_delta_to_specs(specs, _payload(), E._delta_legs(_payload()), 65)
        self.assertEqual(specs[0]["strike"], 11000.0)
        self.assertIsNone(specs[0].get("requested_strike"))   # no shift

    def test_shift_when_ideal_illiquid_records_requested_strike(self):
        # stub native: 11000 untradeable, 11100 tradeable → shift, requested=11000
        stub = types.ModuleType("algotest_native")
        stub.get_option_price_tradeable = lambda d, i, strike, ot, ex: (None if float(strike) == 11000.0 else 90.0)
        sys.modules["algotest_native"] = stub
        specs = [_spec(strike=10500.0)]
        E._apply_delta_to_specs(specs, _payload(), E._delta_legs(_payload()), 65)
        self.assertEqual(specs[0]["strike"], 11100.0)          # shifted to tradeable
        self.assertEqual(specs[0]["requested_strike"], 11000.0)  # → Strike Shift Reason fires


if __name__ == "__main__":
    unittest.main()
