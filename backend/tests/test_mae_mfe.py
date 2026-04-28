import unittest
from unittest.mock import patch

import pandas as pd

from backend.engines import generic_algotest_engine as engine


class TestMaeMfe(unittest.TestCase):
    def test_sell_uses_high_for_mae_and_low_for_mfe(self):
        mae, mfe = engine._calculate_mae_mfe_from_extremes(
            entry_price=100,
            position='SELL',
            entry_spot=1000,
            max_high=125,
            min_low=70,
        )

        self.assertEqual(mae, -2.5)
        self.assertEqual(mfe, 3.0)

    def test_buy_uses_low_for_mae_and_high_for_mfe(self):
        mae, mfe = engine._calculate_mae_mfe_from_extremes(
            entry_price=100,
            position='BUY',
            entry_spot=1000,
            max_high=130,
            min_low=75,
        )

        self.assertEqual(mae, -2.5)
        self.assertEqual(mfe, 3.0)

    def test_window_starts_after_entry_date_and_includes_exit_date(self):
        calendar = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-22', '2026-01-23', '2026-01-26', '2026-01-28'])
        })
        seen_dates = []

        def fake_ohlc(index, date_str, leg, expiry):
            seen_dates.append(date_str)
            values = {
                '2026-01-23': (110, 90),
                '2026-01-26': (140, 80),
                '2026-01-28': (120, 85),
            }
            return values.get(date_str)

        with patch.object(engine, '_get_ohlc_for_leg_on_date', side_effect=fake_ohlc):
            mae, mfe = engine._calculate_leg_mae_mfe(
                index='NIFTY',
                entry_date=pd.Timestamp('2026-01-22'),
                exit_date=pd.Timestamp('2026-01-28'),
                leg={
                    'segment': 'OPTION',
                    'option_type': 'CE',
                    'strike': 24000,
                    '_resolved_expiry': pd.Timestamp('2026-01-29'),
                },
                entry_price=100,
                position='SELL',
                entry_spot=1000,
                trading_calendar_df=calendar,
            )

        self.assertEqual(seen_dates, ['2026-01-23', '2026-01-26', '2026-01-28'])
        self.assertEqual(mae, -4.0)
        self.assertEqual(mfe, 2.0)


if __name__ == '__main__':
    unittest.main()
