import unittest
from unittest.mock import patch

from backend.engines.generic_algotest_engine import (
    _futures_only_next_monthly_schedule,
    _get_future_price_for_held_contract,
)

try:
    from backend.engines.generic_multi_leg import _strategy_uses_futures_only_next_monthly
except Exception:  # pragma: no cover - dependency gap in minimal test env
    _strategy_uses_futures_only_next_monthly = None


class _Leg:
    def __init__(self, instrument, expiry_type=None, segment=None, expiry=None):
        self.instrument = instrument
        self.expiry_type = expiry_type
        self.segment = segment
        self.expiry = expiry


class _Strategy:
    def __init__(self, legs):
        self.legs = legs


class TestFuturesNextMonthlySchedule(unittest.TestCase):
    def test_generic_algotest_engine_detects_futures_only_next_monthly(self):
        self.assertTrue(
            _futures_only_next_monthly_schedule([
                {'segment': 'FUTURES', 'expiry': 'next_monthly'},
            ])
        )
        self.assertFalse(
            _futures_only_next_monthly_schedule([
                {'segment': 'FUTURES', 'expiry': 'monthly'},
            ])
        )
        self.assertFalse(
            _futures_only_next_monthly_schedule([
                {'segment': 'OPTIONS', 'expiry': 'next_monthly'},
                {'segment': 'FUTURES', 'expiry': 'next_monthly'},
            ])
        )

    def test_held_contract_price_prefers_stored_futures_expiry(self):
        with patch(
            'backend.engines.generic_algotest_engine.get_future_price_from_db',
            return_value=123.45,
        ) as price_lookup, patch(
            'backend.engines.generic_algotest_engine._resolve_nearest_future_expiry',
            return_value='2025-03-27',
        ) as nearest_lookup:
            price, expiry = _get_future_price_for_held_contract(
                '2025-03-15',
                'NIFTY',
                {'futures_expiry': '2025-04-24'},
            )

        self.assertEqual(price, 123.45)
        self.assertEqual(expiry, '2025-04-24')
        price_lookup.assert_called_once_with(
            date='2025-03-15',
            index='NIFTY',
            expiry='2025-04-24',
        )
        nearest_lookup.assert_not_called()

    @unittest.skipUnless(
        _strategy_uses_futures_only_next_monthly is not None,
        "generic_multi_leg dependencies unavailable in this test environment",
    )
    def test_generic_multi_leg_detects_futures_only_next_monthly(self):
        self.assertTrue(
            _strategy_uses_futures_only_next_monthly(
                _Strategy([
                    _Leg(instrument='FUTURE', expiry_type='MONTHLY_T1'),
                ])
            )
        )
        self.assertFalse(
            _strategy_uses_futures_only_next_monthly(
                _Strategy([
                    _Leg(instrument='FUTURE', expiry_type='MONTHLY'),
                ])
            )
        )
        self.assertFalse(
            _strategy_uses_futures_only_next_monthly(
                _Strategy([
                    _Leg(instrument='OPTION', expiry_type='MONTHLY_T1'),
                    _Leg(instrument='FUTURE', expiry_type='MONTHLY_T1'),
                ])
            )
        )


if __name__ == '__main__':
    unittest.main()
