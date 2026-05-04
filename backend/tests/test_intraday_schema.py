import unittest


class TestIntradaySchema(unittest.TestCase):
    def test_valid_single_leg_request(self):
        from backend.schemas.intraday import IntradayBacktestRequest
        req = IntradayBacktestRequest.model_validate({
            "symbol": "NIFTY",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "entry_time": "09:20",
            "square_off_time": "15:15",
            "legs": [{
                "opt_type": "CE",
                "action": "SELL",
                "strike_selection": {"mode": "ATM", "value": 0},
                "expiry": "WEEKLY",
                "quantity": 1,
                "sl": {"type": "percent", "value": 50.0},
                "target": None,
            }]
        })
        self.assertEqual(req.symbol, "NIFTY")
        self.assertEqual(len(req.legs), 1)

    def test_rejects_unsupported_symbol(self):
        from backend.schemas.intraday import IntradayBacktestRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            IntradayBacktestRequest.model_validate({
                "symbol": "RELIANCE",
                "date_from": "2024-01-01",
                "date_to": "2024-01-31",
                "entry_time": "09:20",
                "square_off_time": "15:15",
                "legs": [],
            })

    def test_canonical_hash_stable(self):
        from backend.schemas.intraday import IntradayBacktestRequest
        req = IntradayBacktestRequest.model_validate({
            "symbol": "NIFTY",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
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
        h1 = req.canonical_hash()
        h2 = req.canonical_hash()
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)  # 8-byte hex = 16 chars
