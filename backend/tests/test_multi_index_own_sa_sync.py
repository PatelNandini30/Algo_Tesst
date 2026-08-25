"""Regression coverage for synchronized cross-index own spot adjustment."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import multi_index_feature as mif  # noqa: E402


def _leg(index, direction):
    return {
        "index": index,
        "segment": "OPTIONS",
        "expiry": "MONTHLY",
        "option_type": "CE",
        "position": "BUY" if direction == "fall" else "SELL",
        "spot_adjustment": {
            "enabled": True,
            "pct": 1,
            "units": "percent",
            "direction": direction,
        },
    }


class TestCrossIndexOwnSpotAdjustmentSync(unittest.TestCase):
    def test_monthly_midcp_nifty_own_sa_routes_to_fused_cascade(self):
        payload = {
            "index": "MIDCPNIFTY",
            "expiry_type": "MONTHLY",
            "sync_weekly_roll": True,
            "legs": [_leg("MIDCPNIFTY", "fall"), _leg("NIFTY", "rise")],
        }
        expected = {"status": "sentinel"}
        with patch.object(mif, "_run_sync_fused_groups", return_value=expected) as fused:
            actual = mif.run_sync_weekly_cadence(payload, "2026-03-20", "2026-04-30")
        self.assertIs(actual, expected)
        fused.assert_called_once()

    def test_stateful_monthly_walk_advances_both_contracts_at_earliest_boundary(self):
        mid = ("MIDCPNIFTY", "MONTHLY", "OPT")
        nifty = ("NIFTY", "MONTHLY", "OPT")
        series = {
            mid: ["2026-03-30", "2026-04-28", "2026-05-26"],
            nifty: ["2026-03-30", "2026-04-30", "2026-05-28"],
        }
        trading_days = [
            d.strftime("%Y-%m-%d")
            for d in __import__("pandas").date_range("2026-03-01", "2026-05-31", freq="B")
        ]

        def roll_series(index, frequency, _from, _to, segment):
            return list(series[(index, frequency, segment)])

        with patch.object(mif, "_roll_series", side_effect=roll_series):
            mid_cycles, mid_bounds = mif._stateful_advance_cycles(
                [mid, nifty], mid, "2026-03-20", "2026-05-20", 1, trading_days,
            )
            nifty_cycles, nifty_bounds = mif._stateful_advance_cycles(
                [mid, nifty], nifty, "2026-03-20", "2026-05-20", 1, trading_days,
            )

        self.assertEqual(mid_bounds, nifty_bounds)
        self.assertEqual(
            [(c["start"], c["end"]) for c in mid_cycles],
            [(c["start"], c["end"]) for c in nifty_cycles],
        )
        # After the shared March T-1 boundary, neither leg may remain in March.
        self.assertEqual(mid_cycles[1]["contract"], "2026-04-28")
        self.assertEqual(nifty_cycles[1]["contract"], "2026-04-30")


if __name__ == "__main__":
    unittest.main()
