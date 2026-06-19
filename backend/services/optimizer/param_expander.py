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
    """Count the total Cartesian product size without materializing it."""
    total = 1
    for spec in param_specs:
        total *= len(_expand_values(spec))
    return total


def expand_param_specs(
    param_specs: Sequence[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """
    Yield combos. Each combo is `{path: value}` for every spec.

    Order: lexicographic over input spec order. Reproducible.
    """
    if not param_specs:
        yield {}
        return
    paths = [spec["path"] for spec in param_specs]
    value_lists = [_expand_values(spec) for spec in param_specs]
    for combo_values in product(*value_lists):
        yield dict(zip(paths, combo_values))


def apply_combo(payload: Dict[str, Any], combo: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-copied payload with every path in `combo` overridden."""
    new_payload = copy.deepcopy(payload)
    for path, value in combo.items():
        _set_by_path(new_payload, path, value)
    return new_payload


# Regex to match legs[N].strike_selection.value paths
_STRIKE_VALUE_RE = re.compile(r"^legs\[(\d+)\]\.strike_selection\.value$")
_SPOT_ADJ_KEYS = {"spot_adjustment_pct", "spot_adjustment_direction", "spot_adjustment_value"}


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
