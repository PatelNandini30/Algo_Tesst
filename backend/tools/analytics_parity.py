"""
Parity check: Rust algotest_native.compute_analytics_summary VS Python
base.compute_analytics, field-by-field on the same trades, over the corpus.

Feeds each the IDENTICAL per-leg tradesheet (from a real run) so both take the same
has_series_b branch and the same DD-MM-YYYY lexicographic order. Exit 0; prints diffs.

    docker exec -w /app algotest-backend python -m tools.analytics_parity
"""
import math
import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from base import compute_analytics

try:
    import algotest_native
except ImportError:
    print("algotest_native not importable"); raise SystemExit(0)

TOL = 1e-9


def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _match(a, b):
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= TOL + TOL * max(abs(fa), abs(fb))
    return (a is None and b is None) or str(a) == str(b)


overall_fail = 0
for name, payload in PAYLOADS:
    res = execute_algotest_job(dict(payload))
    trades = res.get("trades") or []
    if not trades:
        print(f"\n=== {name}: no trades, skipped ==="); continue
    _, py_sum = compute_analytics(pd.DataFrame(trades))
    rust_sum = algotest_native.compute_analytics_summary(trades)

    keys = sorted(set(py_sum) | set(rust_sum))
    diffs = []
    for k in keys:
        pv, rv = py_sum.get(k, "<MISSING>"), rust_sum.get(k, "<MISSING>")
        if not _match(pv, rv):
            diffs.append((k, pv, rv))
    status = "PASS" if not diffs else "FAIL"
    if diffs:
        overall_fail += 1
    print(f"\n=== {name}: {status}  ({len(keys)} keys, {len(diffs)} diverging) ===")
    for k, pv, rv in diffs:
        print(f"  {k:26s} py={pv!r}  rust={rv!r}")

print(f"\n{'ALL PASS' if overall_fail == 0 else str(overall_fail) + ' payload(s) FAILED'}")
