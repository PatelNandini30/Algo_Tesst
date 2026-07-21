"""Behavioural test for lot-quantity scaling in the generic multi-leg engine.

Self-contained (does NOT append to test_lot_quantity_scaling.py, which another
task is editing concurrently). Verifies that `_process_trade_legs` in
`backend/engines/generic_multi_leg.py`:

  - scales each leg's Net P&L by that leg's OWN `lots` (points x lots), and
  - emits `Qty = lots x lot_size` (not raw lots).

No market data is needed: `_get_bhav_data` is monkeypatched to return minimal
fixture DataFrames shaped like the real bhavcopy frames consumed at
generic_multi_leg.py:129 (`_get_all_strikes_for_expiry`) and :236
(`_get_bhav_data`).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from engines import generic_multi_leg
from engines.generic_multi_leg import _process_trade_legs
from strategies.strategy_types import (
    EntryCondition,
    EntryTimeType,
    ExitCondition,
    ExitTimeType,
    ExpiryType,
    InstrumentType,
    Leg,
    OptionType,
    PositionType,
    StrategyDefinition,
    StrikeSelection,
    StrikeSelectionType,
)


class TestMultiLegQtyConvention(unittest.TestCase):
    """Qty = lots x lot_size; Net P&L = points x that leg's own lots."""

    ENTRY_DATE = pd.Timestamp("2024-01-01")
    EXIT_DATE = pd.Timestamp("2024-01-02")
    CURR_EXPIRY = pd.Timestamp("2024-01-04")
    ENTRY_SPOT = 21500.0
    STRIKE = 21500.0

    # leg 1 (CE, BUY): points = exit - entry
    CE_ENTRY_CLOSE = 100.0
    CE_EXIT_CLOSE = 130.0
    # leg 2 (PE, SELL): points = entry - exit
    PE_ENTRY_CLOSE = 80.0
    PE_EXIT_CLOSE = 50.0

    def setUp(self):
        def _row(option_type, expiry, close):
            return {
                "Instrument": "OPTIDX",
                "Symbol": "NIFTY",
                "OptionType": option_type,
                "ExpiryDate": expiry,
                "StrikePrice": self.STRIKE,
                "Close": close,
            }

        bhav_entry = pd.DataFrame(
            [
                _row("CE", self.CURR_EXPIRY, self.CE_ENTRY_CLOSE),
                _row("PE", self.CURR_EXPIRY, self.PE_ENTRY_CLOSE),
            ]
        )
        bhav_exit = pd.DataFrame(
            [
                _row("CE", self.CURR_EXPIRY, self.CE_EXIT_CLOSE),
                _row("PE", self.CURR_EXPIRY, self.PE_EXIT_CLOSE),
            ]
        )

        entry_str = self.ENTRY_DATE.strftime("%Y-%m-%d")
        exit_str = self.EXIT_DATE.strftime("%Y-%m-%d")

        def _fake_get_bhav_data(date):
            date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
            if date_str == entry_str:
                return bhav_entry
            if date_str == exit_str:
                return bhav_exit
            return pd.DataFrame()

        self._orig_get_bhav_data = generic_multi_leg._get_bhav_data
        generic_multi_leg._get_bhav_data = _fake_get_bhav_data

        self.strategy_def = StrategyDefinition(
            name="multileg-qty-test",
            index="NIFTY",
            legs=[
                Leg(
                    leg_number=1,
                    instrument=InstrumentType.OPTION,
                    option_type=OptionType.CE,
                    position=PositionType.BUY,
                    lots=2,
                    expiry_type=ExpiryType.WEEKLY,
                    strike_selection=StrikeSelection(type=StrikeSelectionType.ATM),
                    entry_condition=EntryCondition(type=EntryTimeType.MARKET_OPEN),
                    exit_condition=ExitCondition(type=ExitTimeType.EXPIRY),
                ),
                Leg(
                    leg_number=2,
                    instrument=InstrumentType.OPTION,
                    option_type=OptionType.PE,
                    position=PositionType.SELL,
                    lots=1,
                    expiry_type=ExpiryType.WEEKLY,
                    strike_selection=StrikeSelection(type=StrikeSelectionType.ATM),
                    entry_condition=EntryCondition(type=EntryTimeType.MARKET_OPEN),
                    exit_condition=ExitCondition(type=ExitTimeType.EXPIRY),
                ),
            ],
        )

    def tearDown(self):
        generic_multi_leg._get_bhav_data = self._orig_get_bhav_data

    def test_qty_and_pnl_scale_by_lots(self):
        leg1_points = self.CE_EXIT_CLOSE - self.CE_ENTRY_CLOSE  # BUY: exit - entry
        leg2_points = self.PE_ENTRY_CLOSE - self.PE_EXIT_CLOSE  # SELL: entry - exit

        rows = _process_trade_legs(
            self.strategy_def,
            "NIFTY",
            self.ENTRY_DATE,
            self.EXIT_DATE,
            self.CURR_EXPIRY,
            self.CURR_EXPIRY,
            self.ENTRY_SPOT,
        )

        self.assertEqual(len(rows), 2)

        # leg 1: 2 lots x NIFTY lot size 65
        self.assertEqual(rows[0]["Qty"], 130)
        # leg 2: 1 lot x 65
        self.assertEqual(rows[1]["Qty"], 65)
        # P&L scales by that leg's own lots, not by lot_size
        self.assertAlmostEqual(rows[0]["Net P&L"], leg1_points * 2, places=2)
        self.assertAlmostEqual(rows[1]["Net P&L"], leg2_points * 1, places=2)


if __name__ == "__main__":
    unittest.main()
