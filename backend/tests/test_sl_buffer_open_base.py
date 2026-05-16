"""
Unit tests for the SL-with-Buffer exit-price computation under the corrected
semantics:

  • SL_price is the price level at which SL fires (computed from entry and the
    SL value in 'pct'/'points' modes).
  • Each holding day, check whether the option's day-high (SELL) or day-low
    (BUY) reached SL_price. If not, no exit.
  • If reached but the day opened on the SL-favorable side of SL_price
    (open <= SL_price for SELL, open >= SL_price for BUY), it was an intraday
    hit -> exit at SL_price exactly. No buffer.
  • If the day OPENED past SL_price (open > SL_price for SELL, open < SL_price
    for BUY), the price gapped past SL at the open -> apply buffer:
        SELL: override = min(day_high, open * (1 + buffer_pct/100))
        BUY:  override = max(day_low,  open * (1 - buffer_pct/100))
  • Open missing -> treat as non-gap (safest: assume SL got hit during the
    session, exit at SL_price).
"""
import unittest

from engines.generic_algotest_engine import _compute_sl_buffer_exit


class TestSLBufferExitFormula(unittest.TestCase):

    # ── No hit ────────────────────────────────────────────────────────────
    def test_sell_no_hit_when_high_below_sl(self):
        # SELL, SL_price=200, day_high=190 -> SL never reached today.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=140.0, day_high=190.0, day_low=130.0,
            buffer_pct=10.0,
        )
        self.assertFalse(hit)
        self.assertIsNone(override)

    def test_buy_no_hit_when_low_above_sl(self):
        # BUY, SL_price=50, day_low=60 -> SL never reached today.
        hit, override = _compute_sl_buffer_exit(
            position='BUY', sl_price=50.0,
            day_open=80.0, day_high=90.0, day_low=60.0,
            buffer_pct=10.0,
        )
        self.assertFalse(hit)
        self.assertIsNone(override)

    # ── Intraday hit, no gap → exit at SL_price exactly ───────────────────
    def test_sell_intraday_hit_exits_at_sl_price(self):
        # SELL, SL_price=200, open=180 (<=SL), high=235, close=215.
        # Walked through SL during the session. Exit at SL_price=200 flat.
        # NO buffer regardless of how violent the day got after the hit.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=180.0, day_high=235.0, day_low=175.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 200.00, places=2)

    def test_buy_intraday_hit_exits_at_sl_price(self):
        # BUY, SL_price=50, open=70 (>=SL), low=40. Walked through SL during
        # the session. Exit at SL_price=50 flat.
        hit, override = _compute_sl_buffer_exit(
            position='BUY', sl_price=50.0,
            day_open=70.0, day_high=80.0, day_low=40.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 50.00, places=2)

    def test_sell_open_exactly_at_sl_is_not_a_gap(self):
        # open == SL_price is the boundary. Per spec, gap is open > SL_price
        # (strict), so this is treated as an intraday hit. Exit at SL_price.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=200.0, day_high=240.0, day_low=198.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 200.00, places=2)

    # ── Gap → buffer applies, capped at day extreme ───────────────────────
    def test_sell_gap_buffer_below_high(self):
        # SELL, SL_price=200, open=250 (>SL). buffer=10%.
        # buffer_price = 250*1.10 = 275. day_high=290 -> override = min(275,290) = 275.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=250.0, day_high=290.0, day_low=245.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 275.00, places=2)

    def test_sell_gap_buffer_capped_at_high(self):
        # User's example: SELL, SL_price=200, open=250, buffer=10%.
        # buffer_price=275 but day_high=260 -> override = min(275,260) = 260.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=250.0, day_high=260.0, day_low=240.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 260.00, places=2)

    def test_buy_gap_buffer_above_low(self):
        # BUY mirror: SL_price=50, open=30 (<SL). buffer=10%.
        # buffer_price = 30*0.90 = 27. day_low=20 -> override = max(27,20) = 27.
        hit, override = _compute_sl_buffer_exit(
            position='BUY', sl_price=50.0,
            day_open=30.0, day_high=42.0, day_low=20.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 27.00, places=2)

    def test_buy_gap_buffer_floored_at_low(self):
        # BUY: SL_price=50, open=30, buffer=80%. buffer_price=30*0.20=6.0.
        # day_low=20 -> override = max(6, 20) = 20.0.
        hit, override = _compute_sl_buffer_exit(
            position='BUY', sl_price=50.0,
            day_open=30.0, day_high=42.0, day_low=20.0,
            buffer_pct=80.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 20.00, places=2)

    # ── Missing data fallbacks ─────────────────────────────────────────────
    def test_sell_open_missing_treats_as_non_gap(self):
        # If open is unavailable we can't decide gap vs intraday. Safest
        # default: assume intraday hit, exit at SL_price.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=None, day_high=250.0, day_low=180.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 200.00, places=2)

    def test_sell_gap_high_missing_uses_buffer_uncapped(self):
        # If high is unavailable on a gap day, fall back to the buffer price
        # without a cap.
        hit, override = _compute_sl_buffer_exit(
            position='SELL', sl_price=200.0,
            day_open=250.0, day_high=None, day_low=240.0,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 275.00, places=2)

    def test_buy_gap_low_missing_uses_buffer_unfloored(self):
        hit, override = _compute_sl_buffer_exit(
            position='BUY', sl_price=50.0,
            day_open=30.0, day_high=42.0, day_low=None,
            buffer_pct=10.0,
        )
        self.assertTrue(hit)
        self.assertAlmostEqual(override, 27.00, places=2)


if __name__ == "__main__":
    unittest.main()
