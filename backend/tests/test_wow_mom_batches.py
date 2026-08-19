"""wow_mom_batches must cut WOW/MOM parts along grid ROW BANDS.

The merged grid lays adjustments across and row_key down. A flat
combos[off:off+chunk] slice cuts mid-band, and since each part re-derives its
axes from the combos it holds, every interior cut leaves holes — a real 38,416
combo sweep measured 440 empty slots in a 15x196 grid, with slot (0,0) empty so
the workbook opened on blank space. These pin the properties that prevent it.
"""
import unittest

from services.optimizer.runner import wow_mom_batches


def _combos(spec):
    """spec: [(row_key, count), ...] -> flat combo dicts in that order."""
    out = []
    for row_key, n in spec:
        out.extend({"row_key": row_key, "i": i} for i in range(n))
    return out


class TestWowMomBatches(unittest.TestCase):

    def test_band_is_never_split_across_parts(self):
        combos = _combos([("a", 196), ("b", 196), ("c", 196)])
        for part in wow_mom_batches(combos, 400):
            for key in {c["row_key"] for c in part}:
                held = sum(1 for c in part if c["row_key"] == key)
                self.assertEqual(held, 196, f"band {key} split across parts")

    def test_packs_whole_bands_up_to_the_budget(self):
        combos = _combos([("a", 196), ("b", 196), ("c", 196)])
        self.assertEqual([len(b) for b in wow_mom_batches(combos, 400)], [392, 196])

    def test_oversized_band_is_emitted_alone(self):
        # Splitting it would reintroduce the holes this exists to remove, so it
        # goes over budget on purpose rather than fragmenting the grid.
        combos = _combos([("a", 10), ("big", 900), ("c", 10)])
        self.assertEqual([len(b) for b in wow_mom_batches(combos, 400)], [10, 900, 10])

    def test_loses_nothing_and_preserves_order(self):
        combos = _combos([("a", 5), ("b", 7), ("c", 3), ("d", 9)])
        flat = [c for part in wow_mom_batches(combos, 10) for c in part]
        self.assertEqual(flat, combos)

    def test_real_sweep_shape_stays_band_aligned(self):
        combos = _combos([(f"r{i}", 196) for i in range(15)])
        for part in wow_mom_batches(combos, 2500):
            self.assertEqual(len(part) % 196, 0, "part is not band-aligned")

    def test_empty_input(self):
        self.assertEqual(wow_mom_batches([], 2500), [])


if __name__ == "__main__":
    unittest.main()
