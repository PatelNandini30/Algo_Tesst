import unittest

import pandas as pd
import polars as pl

from backend.services.backtest_cache import BacktestCache
from backend.services import data_loader


class TestBacktestCache(unittest.TestCase):
    def test_generate_key_ignores_internal_fields(self):
        cache = BacktestCache.__new__(BacktestCache)

        key_a = cache.generate_key(
            symbol='nifty',
            from_date='28/04/2024',
            to_date='28/04/2026',
            strategy_config={'legs': [1], 'request_id': 'abc', '_effective_from': '2024-04-28'},
        )
        key_b = cache.generate_key(
            symbol='NIFTY',
            from_date='2024-04-28',
            to_date='2026-04-28',
            strategy_config={'legs': [1], 'request_id': 'xyz', '_effective_from': '2024-04-28'},
        )

        self.assertEqual(key_a, key_b)

    def test_serialize_deserialize_round_trip(self):
        cache = BacktestCache.__new__(BacktestCache)
        payload = {
            'trades': [{'Trade': 1, 'Net P&L': 12.5}],
            'summary': {'total_pnl': 12.5},
            'pivot': {'headers': ['A'], 'rows': []},
        }

        serialized = cache._serialize_result(payload)
        restored = cache._deserialize_result(serialized)

        self.assertIsNotNone(restored)
        self.assertEqual(restored['summary']['total_pnl'], 12.5)
        self.assertEqual(restored['trades'][0]['Trade'], 1)


class TestBulkLookup(unittest.TestCase):
    def setUp(self):
        self._old_options = data_loader._bulk_options_df
        self._old_spot = data_loader._bulk_spot_df
        data_loader._shared_bulk_strikes_cache.clear()

        data_loader._bulk_options_df = pl.DataFrame({
            'Date': pl.Series(['2025-04-28', '2025-04-28']).str.strptime(pl.Date, format='%Y-%m-%d'),
            'ExpiryDate': pl.Series(['2025-05-05', '2025-05-05']).str.strptime(pl.Date, format='%Y-%m-%d'),
            'StrikePrice': [23000, 23100],
            'OptionType': ['CE', 'PE'],
            'Close': [101.5, 99.25],
        })
        data_loader._bulk_spot_df = pl.DataFrame({
            'Date': pl.Series(['2025-04-28']).str.strptime(pl.Date, format='%Y-%m-%d'),
            'Close': [22450.75],
        })

    def tearDown(self):
        data_loader._bulk_options_df = self._old_options
        data_loader._bulk_spot_df = self._old_spot
        data_loader._shared_bulk_strikes_cache.clear()

    def test_bulk_strikes_accepts_string_dates(self):
        df = data_loader.get_bulk_strikes_for_date(
            '2025-04-28',
            '2025-05-05',
            'CE',
        )
        self.assertEqual(df.height, 1)
        self.assertEqual(df['StrikePrice'][0], 23000)

    def test_bulk_spot_accepts_string_date(self):
        spot = data_loader.get_bulk_spot_price('2025-04-28')
        self.assertEqual(spot, 22450.75)


if __name__ == '__main__':
    unittest.main()
