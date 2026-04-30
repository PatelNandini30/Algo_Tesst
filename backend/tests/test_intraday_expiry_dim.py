import json
import os
import tempfile
import unittest
from datetime import date

from backend.services import intraday_expiry_dim


class TestExpiryDim(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.dim_path = os.path.join(self.tmpdir, "expiries.json")

    def test_load_returns_empty_when_missing(self):
        m = intraday_expiry_dim.load(self.dim_path)
        self.assertEqual(m, {})

    def test_assign_indices_to_new_expiries(self):
        m = {}
        out, dirty = intraday_expiry_dim.assign(
            m, [date(2024, 3, 21), date(2024, 3, 28)]
        )
        self.assertTrue(dirty)
        self.assertEqual(out[date(2024, 3, 21)], 0)
        self.assertEqual(out[date(2024, 3, 28)], 1)

    def test_assign_preserves_existing_indices(self):
        m = {date(2024, 3, 21): 0}
        out, dirty = intraday_expiry_dim.assign(m, [date(2024, 3, 21), date(2024, 3, 28)])
        self.assertEqual(out[date(2024, 3, 21)], 0)
        self.assertEqual(out[date(2024, 3, 28)], 1)
        self.assertTrue(dirty)

    def test_assign_no_change_returns_dirty_false(self):
        m = {date(2024, 3, 21): 0, date(2024, 3, 28): 1}
        out, dirty = intraday_expiry_dim.assign(m, [date(2024, 3, 21)])
        self.assertFalse(dirty)
        self.assertEqual(out, m)

    def test_save_then_load_roundtrip(self):
        m = {date(2024, 3, 21): 0, date(2024, 3, 28): 1}
        intraday_expiry_dim.save(self.dim_path, m)
        self.assertTrue(os.path.exists(self.dim_path))
        loaded = intraday_expiry_dim.load(self.dim_path)
        self.assertEqual(loaded, m)

    def test_save_uses_atomic_rename(self):
        m = {date(2024, 3, 21): 0}
        intraday_expiry_dim.save(self.dim_path, m)
        self.assertFalse(os.path.exists(self.dim_path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
