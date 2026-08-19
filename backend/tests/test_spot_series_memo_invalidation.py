"""spot_series() memoizes {date: spot} keyed partly on _cache_version(), which
is a hardcoded constant ("arrow-v2") — it never changes, so it could never by
itself invalidate the memo across an in-process feather reload. The real
staleness signal is `_loaded_cache_signature` (options+spot feather mtime/size)
that build_cache/_activate_feather already update whenever the on-disk feather
is swapped and reloaded into the Rust cache. This test proves the memo key
now includes that signature, so a reload correctly produces a fresh fetch
instead of silently returning stale prices.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.rust_fast_path as rf


class _FakeLoader:
    def __init__(self, prices):
        self.prices = prices
        self.calls = 0

    def get_spot_price(self, sym, d):
        self.calls += 1
        return self.prices.get(d)


class SpotSeriesMemoInvalidationTest(unittest.TestCase):
    def setUp(self):
        rf.clear_spot_series_memo()
        self._orig_sig = rf._loaded_cache_signature

    def tearDown(self):
        rf._loaded_cache_signature = self._orig_sig
        rf.clear_spot_series_memo()

    def test_signature_change_busts_memo(self):
        days = ["2024-01-01", "2024-01-02"]

        rf._loaded_cache_signature = ("sig-A",)
        loader1 = _FakeLoader({"2024-01-01": 100.0, "2024-01-02": 101.0})
        out1 = rf.spot_series("NIFTY", days, loader1)
        self.assertEqual(out1, {"2024-01-01": 100.0, "2024-01-02": 101.0})
        self.assertEqual(loader1.calls, 2)

        # Same signature -> memo hit, loader not called again.
        loader_same = _FakeLoader({"2024-01-01": 999.0, "2024-01-02": 999.0})
        out_same = rf.spot_series("NIFTY", days, loader_same)
        self.assertEqual(out_same, out1)
        self.assertEqual(loader_same.calls, 0)

        # Feather reloaded in-process -> signature changes -> memo must miss
        # and refetch fresh values, not return the stale ones from sig-A.
        rf._loaded_cache_signature = ("sig-B",)
        loader2 = _FakeLoader({"2024-01-01": 200.0, "2024-01-02": 201.0})
        out2 = rf.spot_series("NIFTY", days, loader2)
        self.assertEqual(out2, {"2024-01-01": 200.0, "2024-01-02": 201.0})
        self.assertEqual(loader2.calls, 2)
        self.assertNotEqual(out2, out1)


if __name__ == "__main__":
    unittest.main()
