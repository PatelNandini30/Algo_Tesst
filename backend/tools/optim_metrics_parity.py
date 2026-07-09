"""
Parity: Rust algotest_native.compute_optim_metrics VS Python
services.optimizer.metrics.compute_optim_metrics, key-by-key over the corpus.

The trades are enriched with Final MAE + Lowest NAV During Trade so the outlier LDD
rebuild and the prev-peak Live-DD paths actually execute (values need not be "real" —
parity is algorithm agreement; both sides get the IDENTICAL enriched input).

    docker exec -w /app algotest-backend python -m tools.optim_metrics_parity
"""
import math
import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from services.optimizer.metrics import compute_optim_metrics

try:
    import algotest_native
except ImportError:
    print("algotest_native not importable"); raise SystemExit(0)

TOL = 5e-4  # values are round(...,4)/round(...,2); guards last-digit repr only


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
    summary = res.get("summary") or {}
    if not trades:
        print(f"\n=== {name}: no trades ==="); continue

    df = pd.DataFrame(trades)
    # Enrich to exercise every path (Final MAE -> outlier rebuild; Lowest NAV -> live DD)
    if "MAE" in df.columns:
        df["Final MAE"] = pd.to_numeric(df["MAE"], errors="coerce")
    if "Peak" in df.columns:
        pk = pd.to_numeric(df["Peak"], errors="coerce")
        fm = pd.to_numeric(df.get("Final MAE"), errors="coerce") if "Final MAE" in df.columns else None
        if fm is not None:
            df["Lowest NAV During Trade"] = pk * (1.0 + fm / 100.0)
    enriched = df.where(df.notna(), None).to_dict("records")

    py_m = compute_optim_metrics(pd.DataFrame(enriched), summary)
    rust_m = algotest_native.compute_optim_metrics(enriched, summary)

    keys = sorted(set(py_m) | set(rust_m))
    diffs = [(k, py_m.get(k, "<MISS>"), rust_m.get(k, "<MISS>"))
             for k in keys if not _match(py_m.get(k, "<MISS>"), rust_m.get(k, "<MISS>"))]
    status = "PASS" if not diffs else "FAIL"
    if diffs:
        overall_fail += 1
    print(f"\n=== {name}: {status}  ({len(keys)} keys, {len(diffs)} diverging) ===")
    for k, pv, rv in diffs:
        print(f"  {k:38s} py={pv!r}  rust={rv!r}")

print(f"\n{'ALL PASS' if overall_fail == 0 else str(overall_fail) + ' payload(s) FAILED'}")
