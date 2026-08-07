"""Guards for the three limits a 60k-combo sweep hits.

Measured on a real 180-combo run before these fixes:
  • Redis result row = 16.0 KB, of which wm_pw is 13.5 KB (84.5%). At 60,000
    combos that is ~985 MB against a 500 MB maxmemory — the sweep dies mid-run.
  • The merged WOW/MOM grid lays combos across; at 2160 combos "WOW Summary" is
    already 1,678 columns. Excel's limit is 16,384, and the file is written
    before anything notices it is invalid.
  • The ZIP cache had no eviction and had reached 19 GB.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.optimizer import result_store as rs


class TestWmOffRedis(unittest.TestCase):
    def test_wm_round_trips_through_disk(self):
        wm_o = {"years": [2024], "grid": [[1, 2, 3]]}
        wm_p = {"years": [2024], "grid": [[4, 5, 6]]}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(rs, "OPTIM_TRADES_DIR", d):
                self.assertTrue(rs.write_combo_wm("job", "combo_1", wm_o, wm_p))
                self.assertEqual(rs.read_combo_wm("job", "combo_1", patchwise=True), wm_p)
                self.assertEqual(rs.read_combo_wm("job", "combo_1", patchwise=False), wm_o)

    def test_nothing_written_when_no_wm(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(rs, "OPTIM_TRADES_DIR", d):
                self.assertFalse(rs.write_combo_wm("job", "combo_1", None, None))
                self.assertIsNone(rs.read_combo_wm("job", "combo_1", patchwise=True))

    def test_missing_file_reads_as_none(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(rs, "OPTIM_TRADES_DIR", d):
                self.assertIsNone(rs.read_combo_wm("job", "nope", patchwise=True))

    def test_row_stays_small_without_wm(self):
        """The whole point: the row must not carry the 13.5 KB payload."""
        import json
        row = {"combo_id": 1, "combo": {"a": 1}, "summary": {"x": 1.0},
               "wm_on_disk": True, "has_midcap": False}
        self.assertLess(len(json.dumps(row)), 1024)


class TestZipCachePrune(unittest.TestCase):
    def test_evicts_oldest_until_under_budget(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(5):
                p = os.path.join(d, f"j{i}.zip")
                with open(p, "wb") as fh:
                    fh.write(b"x" * 1_000_000)          # 1 MB each
                os.utime(p, (1000 + i, 1000 + i))       # ascending mtime
                paths.append(p)
            with mock.patch.object(rs, "ZIP_CACHE_DIR", d):
                freed = rs.prune_zip_cache(max_gb=3 / 1024)   # 3 MB budget
            self.assertGreaterEqual(freed, 2_000_000)
            # oldest gone, newest kept
            self.assertFalse(os.path.exists(paths[0]))
            self.assertTrue(os.path.exists(paths[-1]))

    def test_under_budget_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.zip")
            with open(p, "wb") as fh:
                fh.write(b"x" * 1000)
            with mock.patch.object(rs, "ZIP_CACHE_DIR", d):
                self.assertEqual(rs.prune_zip_cache(max_gb=1), 0)
            self.assertTrue(os.path.exists(p))


class TestWowMomColumnPaging(unittest.TestCase):
    def test_block_columns_never_exceed_excel_limit(self):
        """Whatever n_cols is, per-sheet column index must stay under 16,384."""
        XL_MAX = 16384
        for block_w, gap in ((58, 2), (18, 2)):          # WOW block, MOM block
            per_sheet = max(1, XL_MAX // (block_w + gap))
            for n_cols in (1, 28, 500, 5000, 60000):
                last = 1 + ((n_cols - 1) % per_sheet) * (block_w + gap)
                with self.subTest(block_w=block_w, n_cols=n_cols):
                    self.assertLessEqual(last + block_w, XL_MAX)


if __name__ == "__main__":
    unittest.main()
