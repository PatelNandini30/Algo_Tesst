import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from backend.services import intraday_publish

DATA_ROOT_FIXTURE_OPTS = "backend/tests/fixtures/intraday/synthetic_one_day.csv"


class TestPublishIdempotency(unittest.TestCase):
    """These tests stub the manifest layer to avoid Postgres dependency."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._stored = {}

        def fake_get(symbol, trading_date):
            return self._stored.get((symbol, trading_date))

        def fake_upsert(**kwargs):
            self._stored[(kwargs["symbol"], kwargs["trading_date"])] = dict(kwargs)

        self.patch_get = patch(
            "backend.services.intraday_publish.intraday_manifest.get",
            side_effect=fake_get,
        )
        self.patch_upsert = patch(
            "backend.services.intraday_publish.intraday_manifest.upsert",
            side_effect=fake_upsert,
        )
        self.patch_get.start()
        self.patch_upsert.start()

    def tearDown(self):
        self.patch_get.stop()
        self.patch_upsert.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_publish_creates_parquet_snapshot_and_manifest(self):
        intraday_publish.publish(
            symbol="NIFTY",
            trading_date=date(2024, 3, 15),
            source_path=DATA_ROOT_FIXTURE_OPTS,
            data_root=self.tmpdir,
        )
        parquet = os.path.join(
            self.tmpdir, "NIFTY", "options", "year=2024", "month=03", "options.parquet"
        )
        snapshot = os.path.join(self.tmpdir, "NIFTY", "snapshots", "2024-03-15.arrow")
        self.assertTrue(os.path.exists(parquet))
        self.assertTrue(os.path.exists(snapshot))
        self.assertIn(("NIFTY", date(2024, 3, 15)), self._stored)

    def test_re_publish_same_sha_is_noop(self):
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=DATA_ROOT_FIXTURE_OPTS, data_root=self.tmpdir,
        )
        first_mtime = os.path.getmtime(
            os.path.join(self.tmpdir, "NIFTY", "snapshots", "2024-03-15.arrow")
        )
        intraday_publish.publish(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_path=DATA_ROOT_FIXTURE_OPTS, data_root=self.tmpdir,
        )
        second_mtime = os.path.getmtime(
            os.path.join(self.tmpdir, "NIFTY", "snapshots", "2024-03-15.arrow")
        )
        self.assertEqual(first_mtime, second_mtime)


if __name__ == "__main__":
    unittest.main()
