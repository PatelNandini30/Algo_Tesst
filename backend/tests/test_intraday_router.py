import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient


class TestIntradayRouter(unittest.TestCase):
    def _make_client(self):
        from backend.main import app
        return TestClient(app)

    def test_health_returns_200(self):
        client = self._make_client()
        resp = client.get("/api/intraday/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("symbols_ready", data)

    def test_backtest_returns_422_on_bad_symbol(self):
        client = self._make_client()
        resp = client.post("/api/intraday/backtest", json={
            "symbol": "RELIANCE",
            "date_from": "2024-01-01",
            "date_to": "2024-01-01",
            "entry_time": "09:20",
            "legs": []
        })
        self.assertEqual(resp.status_code, 422)

    def test_backtest_returns_arrow_on_cache_hit(self):
        fake_arrow = b"\x00\x00\x00\x00"  # placeholder bytes
        with patch("backend.services.backtest_cache.get_intraday_result",
                   return_value=fake_arrow):
            client = self._make_client()
            resp = client.post("/api/intraday/backtest", json={
                "symbol": "NIFTY",
                "date_from": "2024-01-01",
                "date_to": "2024-01-01",
                "entry_time": "09:20",
                "square_off_time": "15:15",
                "legs": [{
                    "opt_type": "CE",
                    "action": "SELL",
                    "strike_selection": {"mode": "ATM", "value": 0},
                    "expiry": "WEEKLY",
                    "quantity": 1,
                    "sl": None,
                    "target": None,
                }]
            })
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, fake_arrow)
            self.assertEqual(resp.headers["content-type"],
                             "application/vnd.apache.arrow.stream")
