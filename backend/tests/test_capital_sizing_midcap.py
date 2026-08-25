"""Capital-weighting the Midcap Hypothetical-Future overlay.

The Rust overlay engine prices the Midcap leg as points x lots; when a Midcap
leg carries capital_alloc_pct + capital_total, _apply_capital_weight_to_midcap
rescales it in place to points x qty (qty = alloc% x capital / Midcap entry).
v1 re-derives per trade; v2 freezes per filter segment. Opt-in ⇒ no-op otherwise.
"""
import copy
import unittest

from services.optimizer.excel_builder import _apply_capital_weight_to_midcap

CAP = 100_000_000  # ₹10 cr


def _res(*rows):
    return {"available": True, "summary": {}, "results": list(rows)}


def _row(tid, entry, legpnl, combined, mae=-0.5, mfe=0.8):
    return {"trade_id": tid, "available": True, "Midcap Entry Spot": entry,
            "Midcap Leg P&L": legpnl, "Midcap Leg P&L %": 1.25,
            "Combined Net P&L": combined, "Combined Net P&L %": 2.0,
            "Midcap MAE": mae, "Midcap MFE": mfe}


PROJ = [{"trade_id": "1", "entry_date": "2023-01-05"},
        {"trade_id": "2", "entry_date": "2023-02-05"}]


class TestMidcapCapitalSizing(unittest.TestCase):
    def test_v1_rescales_each_trade_by_own_entry(self):
        legs = [{"lots": 1, "capital_alloc_pct": 30, "capital_total": CAP, "capital_version": "v1"}]
        res = _res(_row("1", 12000, 150.0, 1025.0), _row("2", 12150, 200.0, 1100.0))
        _apply_capital_weight_to_midcap(res, PROJ, legs, None)
        r1, r2 = res["results"]
        self.assertAlmostEqual(r1["Midcap Leg P&L"], 150 * (0.30 * CAP / 12000), places=2)
        self.assertAlmostEqual(r2["Midcap Leg P&L"], 200 * (0.30 * CAP / 12150), places=1)
        # Combined = recovered nifty (1025-150=875) + new midcap
        self.assertAlmostEqual(r1["Combined Net P&L"], 875 + 150 * (0.30 * CAP / 12000), places=1)
        # MAE % of total capital = mae_pct × alloc_fraction = -0.5 × 0.30 = -0.15
        self.assertAlmostEqual(r1["Midcap MAE"], -0.5 * 0.30, places=2)

    def test_v2_freezes_qty_within_filter_segment(self):
        legs = [{"lots": 1, "capital_alloc_pct": 30, "capital_total": CAP, "capital_version": "v2"}]
        res = _res(_row("1", 12000, 150.0, 1025.0), _row("2", 12150, 200.0, 1100.0))
        segs = [{"start": "2023-01-01", "end": "2023-12-31"}]   # both trades in one segment
        _apply_capital_weight_to_midcap(res, PROJ, legs, segs)
        # both anchor on T1's 12000 -> factor 2500
        self.assertAlmostEqual(res["results"][1]["Midcap Leg P&L"], 200 * 2500, places=1)

    def test_disabled_is_noop(self):
        res = _res(_row("1", 12000, 150.0, 1025.0))
        before = copy.deepcopy(res)
        _apply_capital_weight_to_midcap(res, PROJ, [{"lots": 1}], None)
        self.assertEqual(res, before)

    def test_alloc_may_exceed_100(self):
        legs = [{"lots": 1, "capital_alloc_pct": 150, "capital_total": CAP, "capital_version": "v1"}]
        res = _res(_row("1", 12000, 100.0, 1000.0))
        _apply_capital_weight_to_midcap(res, PROJ, legs, None)
        self.assertAlmostEqual(res["results"][0]["Midcap Leg P&L"], 100 * (1.5 * CAP / 12000), places=1)


if __name__ == "__main__":
    unittest.main()
