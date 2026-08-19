"""
Parity at the level that actually ships: OPTIMIZE_RUST_LOOP=1 output VS mode-0 output.

tools/optim_metrics_parity compares the RAW function pair (Python
compute_optim_metrics vs algotest_native.compute_optim_metrics) and correctly
reports fut_pnl_total / fut_pnl_pct as <MISS> — the Rust engine does not emit
futures keys. That is not what a sweep sees, though: mode 0 builds its summary
through compute_xlsx_summary_metrics (which supplements those keys) and mode 1
through rust_authoritative_summary. THIS harness diffs those two end products,
overall AND patchwise, which is what a combo row is actually built from.

    docker compose exec -T -w /app worker-optimize python -m tools.mode1_parity
"""
import math
import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _cmetrics
from services.optimizer.rust_combo_loop import rust_authoritative_summary

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


def _diff(tag, d0, d1):
    keys = sorted(set(d0 or {}) | set(d1 or {}))
    bad = [(k, (d0 or {}).get(k, "<MISS>"), (d1 or {}).get(k, "<MISS>"))
           for k in keys if not _match((d0 or {}).get(k, "<MISS>"), (d1 or {}).get(k, "<MISS>"))]
    if bad:
        print(f"  {tag}: FAIL ({len(keys)} keys, {len(bad)} diverging)")
        for k, a, b in bad[:12]:
            print(f"    {k:<36} mode0={a}  mode1={b}")
    else:
        print(f"  {tag}: CLEAN ({len(keys)} keys)")
    return len(bad)


fails = 0
for name, payload in PAYLOADS:
    res = execute_algotest_job(dict(payload))
    trades = res.get("trades") or []
    summary = res.get("summary") or {}
    if not trades:
        print(f"\n=== {name}: no trades ===")
        continue
    df = pd.DataFrame(trades)
    fseg = payload.get("filter_segments")

    # ── mode 0: exactly what parallel.py's else-branch builds ────────────────
    opt0 = compute_optim_metrics(df, summary)
    flat0 = {**summary, **(opt0 or {})}
    over0 = _cmetrics(df, flat0, patchwise=False, filter_segments=fseg)
    flat0 = {**flat0, **(over0 or {})}
    pw0 = _cmetrics(df, flat0, patchwise=True, filter_segments=fseg)

    # ── mode 1: exactly what the Rust-authoritative branch builds ────────────
    flat1, pw1 = rust_authoritative_summary(df, summary, payload, filter_segments=fseg)

    print(f"\n=== {name} ===")
    fails += _diff("overall  ", flat0, flat1)
    fails += _diff("patchwise", pw0, pw1)

print(f"\n{'PARITY CLEAN — mode 1 == mode 0' if not fails else str(fails) + ' diverging key(s)'}")
raise SystemExit(1 if fails else 0)
