import unittest
from datetime import date
from backend.services.intraday_snapshot import format as snapfmt


class TestSnapshotFormat(unittest.TestCase):
    def test_magic_is_itds(self):
        self.assertEqual(snapfmt.MAGIC, b"ITDS")

    def test_version_is_one(self):
        self.assertEqual(snapfmt.VERSION, 1)

    def test_minutes_per_day_is_375(self):
        self.assertEqual(snapfmt.MINUTES_PER_DAY, 375)

    def test_strike_radius_is_5(self):
        self.assertEqual(snapfmt.STRIKE_RADIUS, 5)
        self.assertEqual(snapfmt.STRIKES_IN_CHAIN, 11)

    def test_pack_header_then_unpack_round_trips(self):
        packed = snapfmt.pack_header(
            symbol="NIFTY", trade_date=date(2024, 3, 15), expiry_count=4,
        )
        self.assertEqual(len(packed), snapfmt.HEADER_BYTES)
        u = snapfmt.unpack_header(packed)
        self.assertEqual(u["magic"], b"ITDS")
        self.assertEqual(u["version"], 1)
        self.assertEqual(u["symbol"], "NIFTY")
        self.assertEqual(u["trade_date"], date(2024, 3, 15))
        self.assertEqual(u["expiry_count"], 4)

    def test_pack_header_pads_short_symbol(self):
        packed = snapfmt.pack_header(
            symbol="NIFTY", trade_date=date(2024, 3, 15), expiry_count=4,
        )
        u = snapfmt.unpack_header(packed)
        self.assertEqual(u["symbol"], "NIFTY")

    def test_pack_header_rejects_long_symbol(self):
        with self.assertRaises(ValueError):
            snapfmt.pack_header(
                symbol="X" * 17,
                trade_date=date(2024, 3, 15),
                expiry_count=4,
            )

    def test_unpack_rejects_bad_magic(self):
        bad = b"XXXX" + bytes(snapfmt.HEADER_BYTES - 4)
        with self.assertRaises(ValueError):
            snapfmt.unpack_header(bad)

    def test_unpack_rejects_wrong_version(self):
        packed = bytearray(
            snapfmt.pack_header(
                symbol="NIFTY", trade_date=date(2024, 3, 15), expiry_count=4
            )
        )
        packed[4] = 99  # corrupt version byte
        with self.assertRaises(ValueError):
            snapfmt.unpack_header(bytes(packed))


if __name__ == "__main__":
    unittest.main()
