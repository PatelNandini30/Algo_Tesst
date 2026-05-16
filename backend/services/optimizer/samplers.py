"""
Combination samplers.

A sampler is an iterable of `{path: value}` dicts. Three modes:

    * ExhaustiveSampler — Cartesian product over every spec value.
    * RandomSampler     — uniformly sample N combos from the full grid.
    * SmartSampler      — wraps `nevergrad` for CMA-ES / PSO / DE.

Smart sampler is OPTIONAL: if `nevergrad` is not installed, it falls back to
`RandomSampler` and sets `.fallback_reason` so the caller can warn the user.

Smart sampling needs an objective to converge on. We accept a callable
`objective_fn(combo) -> float` (higher is better). The sampler keeps asking
nevergrad for new candidates until `budget` evaluations have been done.
"""
from __future__ import annotations

import logging
import random
from itertools import islice
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from services.optimizer.param_expander import (
    _expand_values,
    count_combinations,
    expand_param_specs,
)

logger = logging.getLogger(__name__)


class ExhaustiveSampler:
    """Yield every combination, in spec order."""

    def __init__(self, specs: Sequence[Dict[str, Any]]):
        self.specs = list(specs)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(expand_param_specs(self.specs))

    def __len__(self) -> int:
        return count_combinations(self.specs)

    @property
    def kind(self) -> str:
        return "exhaustive"


class RandomSampler:
    """
    Uniformly sample `n` combos from the grid without replacement.

    Uses reservoir sampling so we never materialize the full grid in memory.
    Reproducible if `seed` is supplied.
    """

    def __init__(self, specs: Sequence[Dict[str, Any]], n: int, seed: Optional[int] = None):
        self.specs = list(specs)
        total = count_combinations(specs)
        self.n = min(int(n), total)
        self.seed = seed
        self._total = total

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        rng = random.Random(self.seed)
        # Independent uniform draw per spec — equivalent to picking a random
        # element from the Cartesian product. Streaming, O(n) memory.
        value_lists: List[List[Any]] = [_expand_values(s) for s in self.specs]
        paths = [s["path"] for s in self.specs]
        seen = set()
        attempts = 0
        max_attempts = self.n * 20 + 1000  # safety
        while len(seen) < self.n and attempts < max_attempts:
            combo_tuple = tuple(rng.choice(vs) for vs in value_lists)
            if combo_tuple in seen:
                attempts += 1
                continue
            seen.add(combo_tuple)
            yield dict(zip(paths, combo_tuple))

    def __len__(self) -> int:
        return self.n

    @property
    def kind(self) -> str:
        return "random"


class SmartSampler:
    """
    Evolutionary / population-based search via nevergrad.

    Supported algorithms (string -> nevergrad optimizer name):
        cma-es → CMA
        pso    → PSO
        ga     → DE (Differential Evolution — closest stable equivalent)

    Requires an `objective_fn` that returns a scalar where HIGHER IS BETTER.
    The sampler keeps yielding combos until `budget` has been consumed. The
    caller scores each combo and passes the score back via `.tell(score)`.
    """

    def __init__(
        self,
        specs: Sequence[Dict[str, Any]],
        algorithm: str = "cma-es",
        budget: int = 200,
        seed: Optional[int] = None,
    ):
        self.specs = list(specs)
        self.algorithm = algorithm.lower()
        self.budget = int(budget)
        self.seed = seed
        self.fallback_reason: Optional[str] = None
        self._pending = None  # last suggested candidate (nevergrad Parametrization)

        try:
            import nevergrad as ng  # type: ignore
        except ImportError:
            self.fallback_reason = "nevergrad not installed; falling back to RandomSampler"
            self._ng = None
            self._optimizer = None
            self._fallback = RandomSampler(specs, budget, seed=seed)
            return

        self._ng = ng
        param_space = self._build_param_space(ng)
        algo_name = {"cma-es": "CMA", "cmaes": "CMA", "pso": "PSO", "ga": "DE"}.get(
            self.algorithm, "CMA"
        )
        try:
            opt_cls = ng.optimizers.registry[algo_name]
        except KeyError:
            self.fallback_reason = f"unknown algorithm {algo_name}; using CMA"
            opt_cls = ng.optimizers.registry["CMA"]
        self._optimizer = opt_cls(parametrization=param_space, budget=self.budget)
        if seed is not None:
            try:
                self._optimizer.parametrization.random_state.seed(seed)
            except Exception:
                pass
        self._fallback = None

    def _build_param_space(self, ng):
        """Map specs → nevergrad Instrumentation."""
        kwargs = {}
        for spec in self.specs:
            kind = (spec.get("kind") or "range").lower()
            if kind == "range":
                lo = float(spec["min"])
                hi = float(spec["max"])
                step = float(spec.get("step", 1))
                if step > 0 and float(spec["min"]).is_integer() and float(spec["max"]).is_integer() and float(spec.get("step", 1)).is_integer():
                    kwargs[self._key(spec["path"])] = ng.p.TransitionChoice(
                        list(range(int(lo), int(hi) + 1, int(step)))
                    )
                else:
                    kwargs[self._key(spec["path"])] = ng.p.Scalar(
                        lower=lo, upper=hi
                    )
            else:
                vals = list(spec.get("values") or [])
                kwargs[self._key(spec["path"])] = ng.p.Choice(vals)
        return ng.p.Instrumentation(**kwargs)

    @staticmethod
    def _key(path: str) -> str:
        return path.replace(".", "__").replace("[", "_").replace("]", "_")

    @staticmethod
    def _unkey(key: str) -> str:
        # we don't really need to reverse this — the caller maps via stored specs
        return key

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        if self._fallback is not None:
            yield from self._fallback
            return
        if self._optimizer is None:
            return
        # In ask/tell mode, the caller scores each combo. We accept .tell()
        # via attribute-set on the yielded dict for convenience: the dict's
        # `__optim_callback__` is set to a closure that records the score.
        for _ in range(self.budget):
            candidate = self._optimizer.ask()
            combo = self._candidate_to_combo(candidate)
            self._pending = candidate

            def _set_score(score: float, _cand=candidate):
                # nevergrad minimizes, so negate (higher is better convention)
                self._optimizer.tell(_cand, -float(score))

            combo["__optim_callback__"] = _set_score
            yield combo

    def _candidate_to_combo(self, candidate) -> Dict[str, Any]:
        args, kwargs = candidate.value
        out: Dict[str, Any] = {}
        for spec in self.specs:
            key = self._key(spec["path"])
            out[spec["path"]] = kwargs[key]
        return out

    def __len__(self) -> int:
        return self.budget

    @property
    def kind(self) -> str:
        if self._fallback is not None:
            return f"smart-fallback-random ({self.fallback_reason})"
        return f"smart-{self.algorithm}"


def build_sampler(
    specs: Sequence[Dict[str, Any]],
    method: str = "exhaustive",
    *,
    sample_n: Optional[int] = None,
    algorithm: Optional[str] = None,
    budget: Optional[int] = None,
    seed: Optional[int] = None,
):
    """Factory. `method` ∈ {'exhaustive', 'random', 'smart'}."""
    m = (method or "exhaustive").lower()
    if m == "exhaustive":
        return ExhaustiveSampler(specs)
    if m == "random":
        if not sample_n:
            raise ValueError("RandomSampler requires sample_n")
        return RandomSampler(specs, n=sample_n, seed=seed)
    if m == "smart":
        return SmartSampler(
            specs,
            algorithm=algorithm or "cma-es",
            budget=int(budget or 200),
            seed=seed,
        )
    raise ValueError(f"Unknown sampling method: {method!r}")


def take(sampler, k: int) -> List[Dict[str, Any]]:
    """Convenience helper for tests: take the first k combos."""
    return list(islice(iter(sampler), k))
