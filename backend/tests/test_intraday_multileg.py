import struct
import tempfile
import os
import json
import unittest


def make_snapshot(tmpdir: str, date_str: str, atm_x100: int,
                  ce_prices: list, pe_prices: list) -> None:
    """Write a synthetic snapshot where CE and PE prices vary per minute."""
    MINUTES = 375
    HEADER_SIZE = 32
    SPOT_ENTRY = 16
    SPOT_SIZE = MINUTES * SPOT_ENTRY
    EXPIRY_SIZE = 2 + MINUTES * 4 + 11 * 2 * 4 * MINUTES * 4

    import datetime
    epoch = datetime.date(1970, 1, 1)
    d = datetime.date.fromisoformat(date_str)
    date_days = (d - epoch).days

    symbol_bytes = b"NIFTY\x00" + b"\x00" * 10
    header = (
        b"ITDS" + struct.pack("<B", 1) + symbol_bytes
        + struct.pack("<i", date_days)
        + struct.pack("<B", 1)   # expiry_count
        + struct.pack("<H", MINUTES)
        + b"\x00\x00\x00\x00"
    )

    spot = b"".join(struct.pack("<iiii", atm_x100, atm_x100, atm_x100, atm_x100)
                    for _ in range(MINUTES))

    expiry_hdr = struct.pack("<h", 0)
    atm_arr = struct.pack(f"<{MINUTES}i", *([atm_x100] * MINUTES))

    chain_size = 11 * 2 * 4 * MINUTES
    chain = bytearray(chain_size * 4)
    for i in range(chain_size):
        struct.pack_into("<i", chain, i * 4, 100)

    def off(s, t, field, m):
        return (s * 2 * 4 * MINUTES + t * 4 * MINUTES + field * MINUTES + m) * 4

    # Fill s=5 (ATM), CE (t=0) and PE (t=1), fields 0/1/2 (close/high/low)
    for m in range(MINUTES):
        ce = ce_prices[m] if m < len(ce_prices) else 100
        pe = pe_prices[m] if m < len(pe_prices) else 100
        for field in range(3):
            struct.pack_into("<i", chain, off(5, 0, field, m), ce)
            struct.pack_into("<i", chain, off(5, 1, field, m), pe)

    expiry_section = expiry_hdr + atm_arr + bytes(chain)
    sym_dir = os.path.join(tmpdir, "NIFTY")
    snaps_dir = os.path.join(sym_dir, "snapshots")
    os.makedirs(snaps_dir, exist_ok=True)
    with open(os.path.join(sym_dir, "expiries.json"), "w") as f:
        json.dump({"0": "2024-01-04"}, f)  # Thursday expiry
    with open(os.path.join(snaps_dir, f"{date_str}.arrow"), "wb") as f:
        f.write(header + spot + expiry_section)


class TestIntradayMultiLeg(unittest.TestCase):
    def test_short_straddle_both_hit_target(self):
        """SELL CE + SELL PE at ATM. Both drop 50% → both hit target."""
        from backend.services.intraday_engine import run_intraday_backtest
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmp:
            ENTRY = 5  # idx = 09:20
            CE = [20000] * (ENTRY + 1) + [10000] * 370   # 200.00 → 100.00
            PE = [15000] * (ENTRY + 1) + [7500] * 370    # 150.00 → 75.00
            make_snapshot(tmp, "2024-01-01", 2400000, CE, PE)

            config = {
                "symbol": "NIFTY",
                "date_from": "2024-01-01",
                "date_to": "2024-01-01",
                "entry_time": "09:20",
                "square_off_time": "15:15",
                "legs": [
                    {"opt_type": "CE", "action": "SELL",
                     "strike_selection": {"mode": "ATM", "value": 0},
                     "expiry": "WEEKLY", "quantity": 1,
                     "sl": None, "target": {"type": "percent", "value": 50.0}},
                    {"opt_type": "PE", "action": "SELL",
                     "strike_selection": {"mode": "ATM", "value": 0},
                     "expiry": "WEEKLY", "quantity": 1,
                     "sl": None, "target": {"type": "percent", "value": 50.0}},
                ]
            }
            result = run_intraday_backtest(config, data_dir=tmp)
            reader = pa.ipc.open_stream(pa.BufferReader(result))
            table = reader.read_all()

            self.assertEqual(table.num_rows, 2)
            reasons = set(table.column("exit_reason").to_pylist())
            self.assertEqual(reasons, {"TARGET"})
            total_pnl = sum(table.column("pnl").to_pylist())
            # CE pnl = 200 - 100 = 100; PE pnl = 150 - 75 = 75; total = 175
            self.assertAlmostEqual(total_pnl, 175.0)
