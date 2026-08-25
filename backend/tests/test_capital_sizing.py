"""Capital-weighted per-leg sizing (_apply_capital_sizing).

qty = alloc% x total_capital / fill_price (decimal, NO lot_size). Fixed capital,
no compounding. v1 re-derives every rollover; v2 freezes across rollovers and
re-derives only at a filter-segment boundary. Opt-in ⇒ disabled is a no-op.
"""
import unittest

from services.engine_rust import _apply_capital_sizing


def _row(tid, lid, ep, xp, pos="BUY", ed="2023-01-05", xd="2023-01-27"):
    return {
        "trade_id": tid, "leg_id": lid, "entry_price": ep, "exit_price": xp,
        "position": pos, "entry_date": ed, "exit_date": xd,
        "lots": 1, "lot_size": 50, "option_type": "FUT",
    }


CAP = 100e7  # ₹100 cr


class TestCapitalSizing(unittest.TestCase):
    def test_v1_resizes_every_rollover(self):
        p = {"legs": [{"segment": "FUTURES", "capital_alloc_pct": 100}],
             "capital_sizing": {"enabled": True, "total_capital": CAP, "version": "v1"}}
        rows = [_row(1, 1, 100, 110, ed="2023-01-05"),
                _row(2, 1, 105, 115, ed="2023-02-05")]
        _apply_capital_sizing(rows, p)
        self.assertAlmostEqual(rows[0]["_cap_qty"], CAP / 100, places=4)
        self.assertAlmostEqual(rows[1]["_cap_qty"], CAP / 105, places=4)  # re-derived
        self.assertAlmostEqual(rows[0]["net_pnl"], (110 - 100) * CAP / 100, places=2)
        self.assertAlmostEqual(rows[1]["net_pnl"], (115 - 105) * CAP / 105, places=2)

    def test_v2_freezes_across_rollover_without_filter(self):
        p = {"legs": [{"segment": "FUTURES", "capital_alloc_pct": 100}],
             "capital_sizing": {"enabled": True, "total_capital": CAP, "version": "v2"}}
        rows = [_row(1, 1, 100, 110, ed="2023-01-05"),
                _row(2, 1, 105, 115, ed="2023-02-05")]
        _apply_capital_sizing(rows, p)
        self.assertAlmostEqual(rows[0]["_cap_qty"], CAP / 100, places=4)
        self.assertAlmostEqual(rows[1]["_cap_qty"], CAP / 100, places=4)  # frozen at first fill

    def test_disabled_is_byte_identical_noop(self):
        rows = [_row(1, 1, 100, 110)]
        before = dict(rows[0])
        _apply_capital_sizing(rows, {"legs": [{}]})
        self.assertEqual(rows[0], before)
        self.assertNotIn("_cap_qty", rows[0])

    def test_alloc_may_exceed_100_leverage(self):
        p = {"legs": [{"capital_alloc_pct": 150}],
             "capital_sizing": {"enabled": True, "total_capital": CAP, "version": "v1"}}
        rows = [_row(1, 1, 100, 110)]
        _apply_capital_sizing(rows, p)
        self.assertAlmostEqual(rows[0]["_cap_qty"], 1.5 * CAP / 100, places=4)

    def test_mixed_leg_only_sized_leg_changes_and_total_resums(self):
        p = {"legs": [{"capital_alloc_pct": 50}, {}],
             "capital_sizing": {"enabled": True, "total_capital": CAP, "version": "v1"}}
        rows = [_row(1, 1, 100, 110), _row(1, 2, 50, 40, pos="SELL")]
        _apply_capital_sizing(rows, p)
        self.assertIn("_cap_qty", rows[0])
        self.assertNotIn("_cap_qty", rows[1])           # unsized leg untouched
        self.assertAlmostEqual(rows[1]["net_pnl"], (50 - 40) * 1, places=6)
        total = min(rows, key=lambda r: r["leg_id"])["net_pnl"]
        expected = (110 - 100) * (0.5 * CAP / 100) + (50 - 40) * 1
        self.assertAlmostEqual(total, expected, places=2)


if __name__ == "__main__":
    unittest.main()
