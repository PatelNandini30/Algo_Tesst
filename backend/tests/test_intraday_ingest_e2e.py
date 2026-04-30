import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import pyarrow.parquet as pq
import polars as pl

from backend.services import intraday_publish
from backend.services.intraday_snapshot.format import MAGIC

FIXTURE = "backend/tests/fixtures/intraday/synthetic_one_day.csv"


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._manifest = {}

        def fake_get(symbol, trading_date):
            return self._manifest.get((symbol, trading_date))

        def fake_upsert(**kwargs):
            self._manifest[(kwargs["symbol"], kwargs["trading_date"])] = dict(kwargs)

        self.patches = [
            patch("backend.services.intraday_publish.intraday_manifest.get", side_effect=fake_get),
            patch("backend.services.intraday_publish.intraday_manifest.upsert", side_effect=fake_upsert),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_e2e_synthetic_day(self):
        intraday_publish.publish(
            symbol="NIFTY",
            trading_date=date(2024, 3, 15),
            source_path=FIXTURE,
            data_root=self.root,
        )
        # 1. Parquet exists, has canonical schema
        pq_path = os.path.join(self.root, "NIFTY", "options", "year=2024", "month=03", "options.parquet")
        self.assertTrue(os.path.exists(pq_path))
        table = pq.read_table(pq_path)
        self.assertIn("expiry_idx", table.column_names)

        # 2. Snapshot exists, has correct magic
        snap_path = os.path.join(self.root, "NIFTY", "snapshots", "2024-03-15.arrow")
        self.assertTrue(os.path.exists(snap_path))
        with open(snap_path, "rb") as f:
            self.assertEqual(f.read(4), MAGIC)

        # 3. Expiry dim exists
        dim_path = os.path.join(self.root, "NIFTY", "expiries.json")
        self.assertTrue(os.path.exists(dim_path))

        # 4. Manifest row recorded
        self.assertIn(("NIFTY", date(2024, 3, 15)), self._manifest)
        self.assertEqual(self._manifest[("NIFTY", date(2024, 3, 15))]["row_count"], 4)

    def test_e2e_idempotent_re_publish(self):
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=FIXTURE, data_root=self.root,
        )
        snap_path = os.path.join(self.root, "NIFTY", "snapshots", "2024-03-15.arrow")
        first_mtime = os.path.getmtime(snap_path)
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=FIXTURE, data_root=self.root,
        )
        self.assertEqual(first_mtime, os.path.getmtime(snap_path))


if __name__ == "__main__":
    unittest.main()
