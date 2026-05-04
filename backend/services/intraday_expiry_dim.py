"""Append-only (symbol-scoped) expiry-date → idx map persisted as JSON."""
import json
import os
from datetime import date
from typing import Dict, Iterable, Tuple


def load(path: str) -> Dict[date, int]:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        raw = json.load(f)
    # Format on disk: {idx_str: date_str} — matches the Rust engine's expectation.
    return {date.fromisoformat(v): int(k) for k, v in raw.items()}


def save(path: str, dim: Dict[date, int]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    # Write as {idx_str: date_str} — compatible with Rust engine (serde HashMap<String,String>).
    with open(tmp, "w") as f:
        json.dump({str(v): k.isoformat() for k, v in dim.items()}, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def assign(
    current: Dict[date, int],
    expiries: Iterable[date],
) -> Tuple[Dict[date, int], bool]:
    """Return (updated_map, dirty). Indices are assigned in input order;
    existing indices are preserved."""
    out = dict(current)
    next_idx = max(out.values(), default=-1) + 1
    dirty = False
    for e in expiries:
        if e not in out:
            out[e] = next_idx
            next_idx += 1
            dirty = True
    return out, dirty
