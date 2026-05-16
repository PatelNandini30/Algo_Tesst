"""Tests for services.optimizer.samplers."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.optimizer.samplers import (  # noqa: E402
    ExhaustiveSampler,
    RandomSampler,
    SmartSampler,
    build_sampler,
    take,
)


_SPECS = [
    {"path": "a", "kind": "range", "min": 1, "max": 3, "step": 1},
    {"path": "b", "kind": "values", "values": [10, 20]},
]


class TestExhaustive(unittest.TestCase):
    def test_yields_all_combos(self):
        s = ExhaustiveSampler(_SPECS)
        self.assertEqual(len(s), 6)
        combos = list(s)
        self.assertEqual(len(combos), 6)
        unique = {(c["a"], c["b"]) for c in combos}
        self.assertEqual(len(unique), 6)


class TestRandom(unittest.TestCase):
    def test_n_unique_combos(self):
        s = RandomSampler(_SPECS, n=4, seed=42)
        combos = list(s)
        self.assertEqual(len(combos), 4)
        unique = {(c["a"], c["b"]) for c in combos}
        self.assertEqual(len(unique), 4)

    def test_reproducibility_with_seed(self):
        s1 = list(RandomSampler(_SPECS, n=3, seed=99))
        s2 = list(RandomSampler(_SPECS, n=3, seed=99))
        self.assertEqual(s1, s2)

    def test_cap_at_grid_size(self):
        s = RandomSampler(_SPECS, n=100, seed=1)
        combos = list(s)
        # Grid has only 6 combos
        self.assertEqual(len(combos), 6)


class TestSmart(unittest.TestCase):
    def test_smart_falls_back_or_runs(self):
        """If nevergrad is installed, sampler yields `budget` combos; else falls back to Random."""
        s = SmartSampler(_SPECS, algorithm="cma-es", budget=5, seed=7)
        combos = take(s, 5)
        self.assertLessEqual(len(combos), 5)
        # In both paths the combo dict must include both paths
        for c in combos:
            self.assertIn("a", c)
            self.assertIn("b", c)


class TestFactory(unittest.TestCase):
    def test_build_exhaustive(self):
        s = build_sampler(_SPECS, method="exhaustive")
        self.assertEqual(len(s), 6)

    def test_build_random(self):
        s = build_sampler(_SPECS, method="random", sample_n=3, seed=1)
        self.assertEqual(len(list(s)), 3)

    def test_build_random_requires_n(self):
        with self.assertRaises(ValueError):
            build_sampler(_SPECS, method="random")


if __name__ == "__main__":
    unittest.main()
