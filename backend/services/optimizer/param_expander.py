"""
Parameter expansion for optimization.

A `param_spec` describes one optimizable parameter. Three forms:

    1. Numeric range:
       {"path": "legs[0].stopLoss.value", "kind": "range",
        "min": 10, "max": 50, "step": 5}
       → values [10, 15, 20, 25, 30, 35, 40, 45, 50]

    2. Explicit list of values:
       {"path": "entry_dte", "kind": "values", "values": [1, 2, 3, 7]}

    3. Enum (categorical, e.g. strike_type):
       {"path": "legs[0].strike_selection.strike_type",
        "kind": "enum", "values": ["ATM", "ITM1", "OTM1"]}

A combination is a dict mapping `path` → `value`. `apply_combo(payload, combo)`
returns a deep-copied payload with each path set to its combo value.

Paths use dot notation with bracketed indices for lists:
    legs[0].stopLoss.value
    spot_adjustment_pct
    legs[1].strike_selection.value
"""
from __future__ import annotations

import copy
import math
import re
from itertools import product
from typing import Any, Dict, Iterable, Iterator, List, Sequence, Tuple

_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _tokenize_path(path: str) -> List[Tuple[str, Any]]:
    """Split a path string into ('key', name) or ('idx', int) tokens."""
    tokens: List[Tuple[str, Any]] = []
    for match in _PATH_TOKEN_RE.finditer(path):
        name, idx = match.groups()
        if name is not None:
            tokens.append(("key", name))
        else:
            tokens.append(("idx", int(idx)))
    if not tokens:
        raise ValueError(f"Empty or invalid path: {path!r}")
    return tokens


def _set_by_path(obj: Any, path: str, value: Any) -> None:
    """Mutate `obj` in place: set the value at the given path."""
    tokens = _tokenize_path(path)
    cur = obj
    for i, (kind, key) in enumerate(tokens[:-1]):
        if kind == "key":
            if not isinstance(cur, dict):
                raise ValueError(f"Cannot descend into non-dict at {path}")
            if key not in cur or not isinstance(cur[key], (dict, list)):
                cur[key] = {} if tokens[i + 1][0] == "key" else []
            cur = cur[key]
        else:  # idx
            if not isinstance(cur, list):
                raise ValueError(f"Cannot index non-list at {path}")
            while len(cur) <= key:
                cur.append({})
            cur = cur[key]
    last_kind, last_key = tokens[-1]
    if last_kind == "key":
        if not isinstance(cur, dict):
            raise ValueError(f"Cannot set key on non-dict at {path}")
        cur[last_key] = value
    else:
        if not isinstance(cur, list):
            raise ValueError(f"Cannot set index on non-list at {path}")
        while len(cur) <= last_key:
            cur.append(None)
        cur[last_key] = value


def _expand_values(spec: Dict[str, Any]) -> List[Any]:
    """Return the list of values this spec spans."""
    kind = (spec.get("kind") or "range").lower()
    if kind == "range":
        lo = float(spec["min"])
        hi = float(spec["max"])
        step = float(spec.get("step", 1))
        if step <= 0:
            raise ValueError(f"step must be > 0 (got {step}) for {spec}")
        if hi < lo:
            raise ValueError(f"max < min for {spec}")
        n_steps = int(math.floor((hi - lo) / step + 1e-9)) + 1
        out: List[Any] = []
        for i in range(n_steps):
            v = lo + i * step
            # Preserve int when all bounds and step are integer-like.
            if (
                float(spec["min"]).is_integer()
                and float(spec["max"]).is_integer()
                and float(spec.get("step", 1)).is_integer()
            ):
                out.append(int(round(v)))
            else:
                # round to 6 dp to avoid floating-point dust like 0.30000000004
                out.append(round(v, 6))
        return out
    if kind in ("values", "enum"):
        vals = list(spec.get("values") or [])
        if not vals:
            raise ValueError(f"'values' must be a non-empty list for {spec}")
        return vals
    raise ValueError(f"Unknown spec kind {kind!r} in {spec}")


def count_combinations(param_specs: Sequence[Dict[str, Any]]) -> int:
    """Count the raw Cartesian product size without materializing it.

    NOTE: this is the *raw* product and ignores gated-param collapsing (see
    `effective_combo_count`). It stays O(1) and is used for the up-front
    MAX_COMBOS ceiling check (an upper bound is exactly what's wanted there).
    """
    total = 1
    for spec in param_specs:
        total *= len(_expand_values(spec))
    return total


# ── Gated (conditional) parameters ──────────────────────────────────────────
# When a gate path is swept to a FALSY value, the listed dependent params have
# no effect on the engine, so keeping them as separate axes would just produce
# functionally identical combos (e.g. every spot_adjustment_pct × direction
# pairing while spot adjustment is OFF). We drop the dependents from such combos
# and then dedupe, so the OFF branch collapses to a single combo per remaining
# axis while the ON branch keeps its full sweep. This means the optimizer never
# runs — nor emits ZIP files for — those duplicates.
_SPOT_ADJ_KEYS = {"spot_adjustment_pct", "spot_adjustment_direction", "spot_adjustment_value"}
_GATED_PARAMS: Dict[str, frozenset] = {
    "spot_adjustment_enabled": frozenset(_SPOT_ADJ_KEYS),
}


def _prune_gated_combo(combo: Dict[str, Any]) -> Dict[str, Any]:
    """Return `combo` with dependent params dropped whenever their gate is
    present in the combo and falsy. Returns the same object when nothing to
    prune, else a new dict."""
    to_drop: set = set()
    for gate_path, dependents in _GATED_PARAMS.items():
        if gate_path in combo and not combo[gate_path]:
            to_drop |= {d for d in dependents if d in combo}
    if not to_drop:
        return combo
    return {k: v for k, v in combo.items() if k not in to_drop}


def _combo_identity(combo: Dict[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    """Hashable, order-independent identity of a combo for dedup."""
    return tuple(sorted(combo.items(), key=lambda kv: kv[0]))


def _has_gated(param_specs: Sequence[Dict[str, Any]]) -> bool:
    return any((spec.get("path") in _GATED_PARAMS) for spec in param_specs)


def expand_param_specs(
    param_specs: Sequence[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """
    Yield combos. Each combo is `{path: value}` for every spec.

    Order: lexicographic over input spec order. Reproducible.

    When a gated toggle (see `_GATED_PARAMS`) is part of the sweep, combos are
    pruned + deduped so the disabled branch doesn't emit duplicates. Grids with
    no gated toggle take the original zero-overhead cartesian path unchanged.
    """
    if not param_specs:
        yield {}
        return
    paths = [spec["path"] for spec in param_specs]
    value_lists = [_expand_values(spec) for spec in param_specs]
    if not _has_gated(param_specs):
        for combo_values in product(*value_lists):
            yield dict(zip(paths, combo_values))
        return
    seen: set = set()
    for combo_values in product(*value_lists):
        combo = _prune_gated_combo(dict(zip(paths, combo_values)))
        identity = _combo_identity(combo)
        if identity in seen:
            continue
        seen.add(identity)
        yield combo


def effective_combo_count(param_specs: Sequence[Dict[str, Any]]) -> int:
    """Number of DISTINCT combos actually run — equals the raw Cartesian
    product unless a gated toggle is swept, in which case redundant combos are
    collapsed. Materializes only when a gate is present (bounded by the
    MAX_COMBOS check on the raw product done by the caller first)."""
    if not param_specs:
        return 1
    if not _has_gated(param_specs):
        return count_combinations(param_specs)
    return sum(1 for _ in expand_param_specs(param_specs))


def apply_combo(payload: Dict[str, Any], combo: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-copied payload with every path in `combo` overridden."""
    new_payload = copy.deepcopy(payload)
    for path, value in combo.items():
        _set_by_path(new_payload, path, value)
    return new_payload


# Regex to match legs[N].strike_selection.value paths
_STRIKE_VALUE_RE = re.compile(r"^legs\[(\d+)\]\.strike_selection\.value$")
# Regex to match legs[N].strike_selection.straddle_multiplier / straddle_direction
_STRADDLE_WIDTH_RE = re.compile(
    r"^legs\[(\d+)\]\.strike_selection\.(straddle_multiplier|straddle_direction)$"
)
# _SPOT_ADJ_KEYS is defined above (near _GATED_PARAMS) so the gating rule can
# reference it at module load.


def apply_combo_for_optim(payload: Dict[str, Any], combo: Dict[str, Any]) -> Dict[str, Any]:
    """
    Like apply_combo, but also applies implied parameter changes required for
    optimizer combos to take effect:

    1. When any `spot_adjustment_pct` / `spot_adjustment_direction` key is in
       the combo, set `spot_adjustment_enabled = True` so the engine doesn't
       ignore the values.

    2. When `legs[N].strike_selection.value` is in the combo, also set
       `legs[N].strike_selection.type = "pct_of_atm"` so both the Python
       engine (PCT_OF_ATM branch) and Rust engine (StrikeSel::PctOfAtm) use
       the value.  The schema labels this param "Strike offset (pct_of_atm
       value)" — switching the leg to pct_of_atm mode is intentional.

    3. When any `midcap_spot_adjustment.*` key is in the combo, ensure
       midcap_spot_adjustment is a dict with enabled=True (the engine ignores it
       otherwise), and seed it BEFORE apply_combo so nested _set_by_path works
       even when the base payload had it null/disabled.

    4. When `legs[N].strike_selection.straddle_multiplier` or
       `.straddle_direction` is in the combo, also set
       `legs[N].strike_selection.type = "straddle_width"` so both engines use
       the swept width/direction (same pattern as the pct_of_atm case above).
    """
    midcap_sa_implied = any(str(k).startswith("midcap_spot_adjustment") for k in combo)
    if midcap_sa_implied and not isinstance((payload or {}).get("midcap_spot_adjustment"), dict):
        # Seed the nested dict on a copy so apply_combo's _set_by_path can target it.
        payload = copy.deepcopy(payload)
        payload["midcap_spot_adjustment"] = {"enabled": True, "units": "percent"}

    new_payload = apply_combo(payload, combo)

    spot_adj_implied = any(k in _SPOT_ADJ_KEYS for k in combo)
    if spot_adj_implied and "spot_adjustment_enabled" not in combo:
        new_payload["spot_adjustment_enabled"] = True

    if midcap_sa_implied:
        msa = new_payload.get("midcap_spot_adjustment")
        if not isinstance(msa, dict):
            msa = {}
            new_payload["midcap_spot_adjustment"] = msa
        msa["enabled"] = True
        msa.setdefault("units", "percent")

    for path in combo:
        m = _STRIKE_VALUE_RE.match(path)
        if m:
            leg_idx = int(m.group(1))
            type_path = f"legs[{leg_idx}].strike_selection.type"
            current_type = get_by_path(new_payload, type_path, "")
            if str(current_type).lower() not in ("pct_of_atm", "percent_of_atm"):
                _set_by_path(new_payload, type_path, "pct_of_atm")
            continue
        m = _STRADDLE_WIDTH_RE.match(path)
        if m:
            leg_idx = int(m.group(1))
            type_path = f"legs[{leg_idx}].strike_selection.type"
            current_type = get_by_path(new_payload, type_path, "")
            if str(current_type).lower() != "straddle_width":
                _set_by_path(new_payload, type_path, "straddle_width")

    return new_payload


def get_by_path(obj: Any, path: str, default: Any = None) -> Any:
    """Read a value at path. Returns `default` if any segment is missing."""
    try:
        cur = obj
        for kind, key in _tokenize_path(path):
            if kind == "key":
                if not isinstance(cur, dict) or key not in cur:
                    return default
                cur = cur[key]
            else:
                if not isinstance(cur, list) or key >= len(cur):
                    return default
                cur = cur[key]
        return cur
    except Exception:
        return default
