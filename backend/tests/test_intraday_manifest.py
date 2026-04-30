import os
import unittest
from datetime import date
import psycopg2

from backend.services import intraday_manifest

DSN = (
    f"host={os.environ.get('POSTGRES_HOST', 'localhost')} "
    f"port={os.environ.get('POSTGRES_PORT', '5432')} "
    f"dbname={os.environ.get('POSTGRES_DB', 'algotest')} "
    f"user={os.environ.get('POSTGRES_USER', 'algotest')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'algotest_password')}"
)


def _can_reach_postgres():
    try:
        with psycopg2.connect(DSN, connect_timeout=2) as _:
            return True
    except Exception:
        return False


@unittest.skipUnless(_can_reach_postgres(), "Postgres not reachable")
class TestManifest(unittest.TestCase):
    def setUp(self):
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM intraday_imports WHERE symbol='NIFTY' AND trading_date=%s",
                (date(2024, 3, 15),),
            )

    def tearDown(self):
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM intraday_imports WHERE symbol='NIFTY' AND trading_date=%s",
                (date(2024, 3, 15),),
            )

    def test_get_returns_none_when_missing(self):
        self.assertIsNone(intraday_manifest.get("NIFTY", date(2024, 3, 15)))

    def test_upsert_inserts_when_missing(self):
        intraday_manifest.upsert(
            symbol="NIFTY",
            trading_date=date(2024, 3, 15),
            source_format="clean_2023",
            source_sha256="a" * 64,
            parquet_path="/data/intraday/NIFTY/options/year=2024/month=03/options.parquet",
            snapshot_path="/data/intraday/NIFTY/snapshots/2024-03-15.arrow",
            row_count=400_000,
            expiry_count=4,
        )
        row = intraday_manifest.get("NIFTY", date(2024, 3, 15))
        self.assertIsNotNone(row)
        self.assertEqual(row["source_sha256"], "a" * 64)
        self.assertEqual(row["row_count"], 400_000)

    def test_upsert_updates_when_present(self):
        intraday_manifest.upsert(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_format="clean_2023", source_sha256="a" * 64,
            parquet_path="/p", snapshot_path="/s",
            row_count=100, expiry_count=2,
        )
        intraday_manifest.upsert(
            symbol="NIFTY", trading_date=date(2024, 3, 15),
            source_format="clean_2023", source_sha256="b" * 64,
            parquet_path="/p2", snapshot_path="/s2",
            row_count=200, expiry_count=3,
        )
        row = intraday_manifest.get("NIFTY", date(2024, 3, 15))
        self.assertEqual(row["source_sha256"], "b" * 64)
        self.assertEqual(row["row_count"], 200)


if __name__ == "__main__":
    unittest.main()
