import unittest
from unittest.mock import MagicMock, patch


class TestIntradayCacheKeys(unittest.TestCase):
    def test_intraday_cache_key_format(self):
        from backend.services.backtest_cache import intraday_cache_key
        key = intraday_cache_key("abc123ff")
        self.assertEqual(key, "intraday:result:abc123ff")

    def test_get_returns_none_on_miss(self):
        with patch("backend.services.backtest_cache.get_redis") as mock_redis:
            mock_redis.return_value.get.return_value = None
            from backend.services.backtest_cache import get_intraday_result
            result = get_intraday_result("abc123ff")
            self.assertIsNone(result)

    def test_set_and_get_roundtrip(self):
        fake_bytes = b"FAKE_ARROW"
        store = {}
        mock_r = MagicMock()
        mock_r.get.side_effect = lambda k: store.get(k)
        mock_r.setex.side_effect = lambda k, ttl, v: store.update({k: v})
        with patch("backend.services.backtest_cache.get_redis", return_value=mock_r):
            from backend.services import backtest_cache
            backtest_cache.set_intraday_result("abc123ff", fake_bytes)
            result = backtest_cache.get_intraday_result("abc123ff")
            self.assertEqual(result, fake_bytes)
