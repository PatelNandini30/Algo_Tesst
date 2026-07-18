"""
Parity check: Rust algotest_native.compute_mae_mfe_batch VS Python
runner._compute_mae_mfe_batch, per-row MAE/MFE on the same trades, over the corpus.

Both compute the SAME function; the Rust reads OHLC from the shared cache, the Python
from the pandas OHLC frame (same data). Exit 0; prints diffs.

    docker exec -w /app algotest-backend python -m tools.mae_parity
"""
import math
import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from services.optimizer import runner as _r
from services.optimizer.runner import _compute_mae_mfe_batch
from routers.optimize import _get_ohlc_pandas_for_index

try:
    import algotest_native
except ImportError:
    print("algotest_native not importable"); raise SystemExit(0)

TOL = 5e-5  # values are round(...,4); tolerance guards last-digit repr only


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


overall_fail = 0
for name, payload in PAYLOADS:
    res = execute_algotest_job(dict(payload))
    trades = res.get("trades") or []
    if not trades:
        print(f"\n=== {name}: no trades, skipped ==="); continue
    index = str(payload.get("index") or "NIFTY").upper()

    try:
        ohlc_pd, tdays = _get_ohlc_pandas_for_index(index)
    except Exception as e:
        print(f"\n=== {name}: OHLC load failed: {e} ==="); continue

    # Python path (pandas fast-path via _RUST_CONTEXT). Force the Python engine
    # (_MAE_PYTHON_REF) since we are diffing it against the Rust path.
    _prev = _r._RUST_CONTEXT
    _prev_ref = _r._MAE_PYTHON_REF
    _r._RUST_CONTEXT = {"ohlc_df_pandas": ohlc_pd, "trading_days": tdays}
    _r._MAE_PYTHON_REF = True
    try:
        py_df = _compute_mae_mfe_batch(pd.DataFrame(trades).copy(), index, tdays)
    finally:
        _r._RUST_CONTEXT = _prev
        _r._MAE_PYTHON_REF = _prev_ref
    py_mae = list(py_df["MAE"]); py_mfe = list(py_df["MFE"])

    # Rust path (reads OHLC from the shared cache)
    rust_pairs = algotest_native.compute_mae_mfe_batch(trades, index, tdays)
    rust_mae = [p[0] for p in rust_pairs]; rust_mfe = [p[1] for p in rust_pairs]

    diffs = []
    for i in range(len(trades)):
        for col, pv, rv in (("MAE", py_mae[i], rust_mae[i]), ("MFE", py_mfe[i], rust_mfe[i])):
            a, b = _num(pv), _num(rv)
            if a is None or b is None:
                if str(pv) != str(rv):
                    diffs.append((i, col, pv, rv))
            elif abs(a - b) > TOL:
                diffs.append((i, col, pv, rv))
    status = "PASS" if not diffs else "FAIL"
    if diffs:
        overall_fail += 1
    print(f"\n=== {name}: {status}  ({len(trades)} rows, {len(diffs)} diverging) ===")
    for i, col, pv, rv in diffs[:25]:
        t = trades[i]
        print(f"  row {i} [{t.get('Type')} {t.get('Strike')} {t.get('Exit Reason')}] {col}: py={pv!r} rust={rv!r}")

print(f"\n{'ALL PASS' if overall_fail == 0 else str(overall_fail) + ' payload(s) FAILED'}")
