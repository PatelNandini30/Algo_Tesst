"""
Parity: Rust algotest_native.compute_summary_metrics VS Python
excel_builder.compute_xlsx_summary_metrics (_cxsm) — the LIVE-matching chronological
summary — key-by-key over the corpus, both overall and patchwise. Non-midcap.

    docker exec -w /app algotest-backend python -m tools.summary_metrics_parity
"""
import math
import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from services.optimizer.excel_builder import compute_xlsx_summary_metrics as cxsm

try:
    import algotest_native
except ImportError:
    print("algotest_native not importable"); raise SystemExit(0)

TOL = 5e-4


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
    S = res.get("summary") or {}
    if not trades:
        print(f"\n=== {name}: no trades ==="); continue
    for pw in (False, True):
        ref = cxsm(pd.DataFrame(trades), S, patchwise=pw, filter_segments=None)
        rust = algotest_native.compute_summary_metrics(trades, S, pw, None)
        keys = sorted(set(ref) | set(rust))
        diffs = [(k, ref.get(k, "<MISS>"), rust.get(k, "<MISS>"))
                 for k in keys if not _match(ref.get(k, "<MISS>"), rust.get(k, "<MISS>"))]
        tag = "patchwise" if pw else "overall  "
        status = "PASS" if not diffs else "FAIL"
        if diffs:
            overall_fail += 1
        print(f"{name[:34]:34s} {tag}: {status}  ({len(keys)} keys, {len(diffs)} diverging)")
        for k, rv, ru in diffs:
            print(f"      {k:38s} cxsm={rv!r}  rust={ru!r}")

print(f"\n{'ALL PASS' if overall_fail == 0 else str(overall_fail) + ' case(s) FAILED'}")
