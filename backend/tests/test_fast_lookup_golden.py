"""
Golden-master tests for fast_lookup.
Run before restarting workers: python -m pytest tests/test_fast_lookup_golden.py -v
All must pass before deploying.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from datetime import date


def _make_test_options_df():
    import polars as pl
    return pl.DataFrame({
        "Date":        [date(2024, 1, 15), date(2024, 1, 15), date(2024, 1, 16)],
        "Symbol":      ["NIFTY",           "NIFTY",           "NIFTY"],
        "ExpiryDate":  [date(2024, 1, 25), date(2024, 1, 25), date(2024, 1, 25)],
        "OptionType":  ["CE",              "PE",              "CE"],
        "StrikePrice": [22000.0,           22000.0,           22000.0],
        "Close":       [150.5,             140.25,            155.0],
    })


def _make_test_spot_df():
    import polars as pl
    return pl.DataFrame({
        "Date":   [date(2024, 1, 15), date(2024, 1, 16)],
        "Symbol": ["NIFTY",          "NIFTY"],
        "Close":  [22050.0,          22100.0],
    })


class TestFastLookupGolden:

    def setup_method(self):
        from services.fast_lookup import build_fast_lookup, clear_fast_lookup
        clear_fast_lookup()
        build_fast_lookup(_make_test_options_df(), _make_test_spot_df())

    def teardown_method(self):
        from services.fast_lookup import clear_fast_lookup
        clear_fast_lookup()

    def test_option_hit_ce(self):
        from services.fast_lookup import get_option_price_fast
        assert get_option_price_fast("2024-01-15", "NIFTY", 22000.0, "CE", "2024-01-25") == pytest.approx(150.5)

    def test_option_hit_pe(self):
        from services.fast_lookup import get_option_price_fast
        assert get_option_price_fast("2024-01-15", "NIFTY", 22000.0, "PE", "2024-01-25") == pytest.approx(140.25)

    def test_option_second_date(self):
        from services.fast_lookup import get_option_price_fast
        assert get_option_price_fast("2024-01-16", "NIFTY", 22000.0, "CE", "2024-01-25") == pytest.approx(155.0)

    def test_option_miss_returns_none(self):
        from services.fast_lookup import get_option_price_fast
        assert get_option_price_fast("2024-01-15", "NIFTY", 99999.0, "CE", "2024-01-25") is None

    def test_spot_hit(self):
        from services.fast_lookup import get_spot_price_fast
        assert get_spot_price_fast("2024-01-15", "NIFTY") == pytest.approx(22050.0)

    def test_spot_miss_returns_none(self):
        from services.fast_lookup import get_spot_price_fast
        assert get_spot_price_fast("1999-01-01", "NIFTY") is None

    def test_case_insensitive_index(self):
        from services.fast_lookup import get_option_price_fast, get_spot_price_fast
        assert get_option_price_fast("2024-01-15", "nifty", 22000.0, "ce", "2024-01-25") is not None
        assert get_spot_price_fast("2024-01-15", "nifty") is not None

    def test_timestamp_date_format(self):
        import pandas as pd
        from services.fast_lookup import get_option_price_fast
        result = get_option_price_fast(
            date=pd.Timestamp("2024-01-15"), index="NIFTY",
            strike=22000.0, opt_type="CE", expiry=pd.Timestamp("2024-01-25"),
        )
        assert result == pytest.approx(150.5)

    def test_clear_then_miss(self):
        from services.fast_lookup import clear_fast_lookup, get_option_price_fast
        clear_fast_lookup()
        assert get_option_price_fast("2024-01-15", "NIFTY", 22000.0, "CE", "2024-01-25") is None

    def test_strikes_index_ce(self):
        from services.fast_lookup import get_strikes_for_date_fast
        strikes = get_strikes_for_date_fast("2024-01-15", "NIFTY", "2024-01-25", "CE")
        assert strikes == [(22000.0, 150.5)]

    def test_strike_decimal_collision(self):
        import polars as pl
        from services.fast_lookup import build_fast_lookup, get_option_price_fast
        df = pl.DataFrame({
            "Date":        [date(2024, 1, 15), date(2024, 1, 15)],
            "Symbol":      ["NIFTY",           "NIFTY"],
            "ExpiryDate":  [date(2024, 1, 25), date(2024, 1, 25)],
            "OptionType":  ["CE",              "CE"],
            "StrikePrice": [22500.0,           22500.5],
            "Close":       [100.0,             200.0],
        })
        build_fast_lookup(df, None)
        r1 = get_option_price_fast("2024-01-15", "NIFTY", 22500.0, "CE", "2024-01-25")
        r2 = get_option_price_fast("2024-01-15", "NIFTY", 22500.5, "CE", "2024-01-25")
        assert r1 == pytest.approx(100.0)
        assert r2 == pytest.approx(200.0)
        assert r1 != r2
