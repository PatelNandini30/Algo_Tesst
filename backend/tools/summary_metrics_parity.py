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
from services.optimizer.excel_builder import compute_midcap_for_rows
import services.optimizer.excel_builder as _eb
_eb._SUMMARY_PYTHON_REF = True  # this harness diffs the Python engine vs Rust — force Python

_MIDCAP_LEGS = [{"midcap_mode": "hypothetical", "position": "buy", "lots": 1, "symbol": "NIFTYMIDCAP100"}]

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
    rows = pd.DataFrame(trades).where(pd.DataFrame(trades).notna(), None).to_dict("records")
    by_trade, mc_summ, _has = compute_midcap_for_rows(rows, _MIDCAP_LEGS, None, "NIFTYMIDCAP100")
    for pw in (False, True):
        for mode in ("nomidcap", "midcap"):
            if mode == "nomidcap":
                ref = cxsm(pd.DataFrame(trades), S, patchwise=pw, filter_segments=None)
                rust = algotest_native.compute_summary_metrics(trades, S, pw, None, None, None)
            else:
                ref = cxsm(pd.DataFrame(trades), S, midcap_legs=_MIDCAP_LEGS,
                           midcap_spot_adjustment=None, midcap_symbol="NIFTYMIDCAP100",
                           patchwise=pw, filter_segments=None)
                rust = algotest_native.compute_summary_metrics(trades, S, pw, None, by_trade, mc_summ)
            keys = sorted(set(ref) | set(rust))
            diffs = [(k, ref.get(k, "<MISS>"), rust.get(k, "<MISS>"))
                     for k in keys if not _match(ref.get(k, "<MISS>"), rust.get(k, "<MISS>"))]
            tag = f"{'patchwise' if pw else 'overall  '}/{mode:9s}"
            status = "PASS" if not diffs else "FAIL"
            if diffs:
                overall_fail += 1
            print(f"{name[:30]:30s} {tag}: {status}  ({len(keys)} keys, {len(diffs)} diverging)")
            for k, rv, ru in diffs:
                print(f"      {k:38s} cxsm={rv!r}  rust={ru!r}")

print(f"\n{'ALL PASS' if overall_fail == 0 else str(overall_fail) + ' case(s) FAILED'}")
