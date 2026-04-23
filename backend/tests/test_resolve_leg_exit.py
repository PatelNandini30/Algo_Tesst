import unittest

import pandas as pd

from backend.engines.generic_algotest_engine import _resolve_leg_exit


class TestResolveLegExit(unittest.TestCase):
    def test_partial_mode_skipped_leg_number_does_not_break_exit_date(self):
        # Scenario: strategy configured 2 legs, but leg 1 was skipped due to missing data.
        # trade['legs'] therefore contains only one leg (originally "leg_number=2"),
        # and per_leg_results aligns to trade['legs'] order: index 0 corresponds to that remaining leg.
        per_leg_results = [{
            'triggered': True,
            'exit_date': pd.Timestamp('2026-01-10'),
            'exit_reason': 'STOP_LOSS',
        }]

        trade_exit_date = pd.Timestamp('2026-01-15')  # last standing leg / scheduled exit

        leg_exit_date, leg_exit_reason = _resolve_leg_exit(
            per_leg_results=per_leg_results,
            trade_exit_date=trade_exit_date,
            trade_exit_reason='EXPIRY',
            leg_idx=0,
        )

        self.assertEqual(leg_exit_date, pd.Timestamp('2026-01-10'))
        self.assertEqual(leg_exit_reason, 'STOP_LOSS')


if __name__ == '__main__':
    unittest.main()

