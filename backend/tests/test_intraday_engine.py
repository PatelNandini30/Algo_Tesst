import unittest
import os
import struct
import tempfile
import json
import shutil


class TestIntradayEngineImport(unittest.TestCase):
    def test_wrapper_importable(self):
        from backend.services import intraday_engine
        self.assertTrue(hasattr(intraday_engine, "run_intraday_backtest"))

    def test_returns_bytes_for_empty_date_range(self):
        """With no snapshot files present, should return valid Arrow IPC with 0 rows."""
        from backend.services.intraday_engine import run_intraday_backtest
        import pyarrow as pa
        with tempfile.TemporaryDirectory() as tmp:
            symbol_dir = os.path.join(tmp, "NIFTY")
            os.makedirs(os.path.join(symbol_dir, "snapshots"))
            with open(os.path.join(symbol_dir, "expiries.json"), "w") as f:
                json.dump({}, f)
            config = {
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
                    "sl": {"type": "percent", "value": 50.0},
                    "target": None,
                }]
            }
            result = run_intraday_backtest(config, data_dir=tmp)
            self.assertIsInstance(result, bytes)
            reader = pa.ipc.open_stream(pa.BufferReader(result))
            table = reader.read_all()
            self.assertEqual(table.num_rows, 0)


class TestIntradayEngineGolden(unittest.TestCase):
    """End-to-end: synthetic snapshot → engine → tradesheet."""

    MINUTES = 375
    HEADER_SIZE = 32
    SPOT_ENTRY = 16
    SPOT_SIZE = MINUTES * SPOT_ENTRY        # 6000
    EXPIRY_SIZE = 2 + MINUTES * 4 + 11 * 2 * 4 * MINUTES * 4  # 133502

    def _make_snapshot(self, date_str: str, atm_x100: int, entry_close_x100: int, later_close_x100: int) -> bytes:
        import datetime
        epoch = datetime.date(1970, 1, 1)
        d = datetime.date.fromisoformat(date_str)
        date_days = (d - epoch).days

        symbol_bytes = b"NIFTY\x00" + b"\x00" * 10  # 16 bytes
        header = (
            b"ITDS"
            + struct.pack("<B", 1)
            + symbol_bytes
            + struct.pack("<i", date_days)
            + struct.pack("<B", 1)           # expiry_count=1
            + struct.pack("<H", self.MINUTES)
            + b"\x00\x00\x00\x00"           # padding to 32
        )
        assert len(header) == self.HEADER_SIZE

        spot = b""
        for _ in range(self.MINUTES):
            spot += struct.pack("<iiii", atm_x100, atm_x100, atm_x100, atm_x100)
        assert len(spot) == self.SPOT_SIZE

        expiry_idx_val = 0
        expiry_hdr = struct.pack("<h", expiry_idx_val)
        atm_arr = struct.pack(f"<{self.MINUTES}i", *([atm_x100] * self.MINUTES))

        entry_minute_idx = 5  # 09:20 = idx 5
        chain_size = 11 * 2 * 4 * self.MINUTES
        chain = bytearray(chain_size * 4)

        for i in range(chain_size):
            struct.pack_into("<i", chain, i * 4, 100)

        def chain_offset(s, t, field, m):
            return (s * 2 * 4 * self.MINUTES + t * 4 * self.MINUTES + field * self.MINUTES + m) * 4

        for m in range(self.MINUTES):
            px = entry_close_x100 if m <= entry_minute_idx else later_close_x100
            struct.pack_into("<i", chain, chain_offset(5, 0, 0, m), px)
            struct.pack_into("<i", chain, chain_offset(5, 0, 1, m), px)  # high
            struct.pack_into("<i", chain, chain_offset(5, 0, 2, m), px)  # low

        expiry_section = expiry_hdr + atm_arr + bytes(chain)
        assert len(expiry_section) == self.EXPIRY_SIZE

        return header + spot + expiry_section

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        symbol_dir = os.path.join(self.tmpdir, "NIFTY")
        snaps_dir = os.path.join(symbol_dir, "snapshots")
        os.makedirs(snaps_dir)
        with open(os.path.join(symbol_dir, "expiries.json"), "w") as f:
            json.dump({"0": "2024-01-04"}, f)
        snap_bytes = self._make_snapshot(
            "2024-01-01",
            atm_x100=2400000,       # ATM = 24000.00
            entry_close_x100=20000, # entry price = 200.00
            later_close_x100=10000, # price falls to 100.00 (50% drop)
        )
        with open(os.path.join(snaps_dir, "2024-01-01.arrow"), "wb") as f:
            f.write(snap_bytes)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sell_hits_target(self):
        """SELL CE at ATM with 50% target. Price drops from 200 to 100. Should hit target."""
        from backend.services.intraday_engine import run_intraday_backtest
        import pyarrow as pa

        config = {
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
                "target": {"type": "percent", "value": 50.0},
            }]
        }
        result = run_intraday_backtest(config, data_dir=self.tmpdir)
        reader = pa.ipc.open_stream(pa.BufferReader(result))
        table = reader.read_all()

        self.assertEqual(table.num_rows, 1)
        row = {col: table.column(col)[0].as_py() for col in table.schema.names}
        self.assertEqual(row["exit_reason"], "TARGET")
        self.assertAlmostEqual(row["entry_price"], 200.0)
        self.assertAlmostEqual(row["exit_price"], 100.0)
        self.assertAlmostEqual(row["pnl"], 100.0)   # SELL: entry - exit = 200 - 100


class TestTrailingSLParse(unittest.TestCase):
    def test_parse_leg_with_trailing_sl(self):
        """StrategySpec with trailing_sl must parse without error."""
        import algotest_native as n
        config = {
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
                "sl": {"type": "percent", "value": 100.0},
                "target": None,
                "trailing_sl": {"trigger_pct": 30.0, "trail_pct": 30.0},
            }]
        }
        import tempfile, os, json
        with tempfile.TemporaryDirectory() as tmp:
            sym = os.path.join(tmp, "NIFTY")
            os.makedirs(os.path.join(sym, "snapshots"))
            with open(os.path.join(sym, "expiries.json"), "w") as f:
                json.dump({}, f)
            result = n.run_intraday_backtest(json.dumps(config), tmp)
            self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
