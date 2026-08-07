"""Phase 0b parity harness — Python summary path vs Rust-authoritative summary,
over a corpus of SL / Target / Trail single-index combos (the shapes the batch
gate currently rejects as `leg*-sl_target_trail`).

READ-ONLY. Mode stays 0. Proves whether the Rust summary already reproduces the
Python summary for these trades (=> gate can be lifted) or lists exact diffs
(=> the spec for what to port). Nothing in production changes.
"""
import sys, math, json
sys.path.insert(0, "/app")

from services.algotest_job import execute_algotest_job
from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.excel_builder import compute_xlsx_summary_metrics
from services.optimizer.rust_combo_loop import rust_authoritative_summary
import pandas as pd

_COMMON = {
    "index": "NIFTY", "from_date": "2024-01-01", "to_date": "2024-06-30",
    "strategy_type": "positional", "underlying": "cash", "expiry_type": "WEEKLY",
    "entry_dte": 1, "exit_dte": 1, "slippage_pct": 0, "charges_enabled": False,
    "square_off_mode": "partial", "rollover_toggle": True, "no_cache": True,
}

def _leg(ot, pos="SELL", sl=None, tp=None, trail=None, strike=None, buf=False):
    sel = strike or {"type": "strike_type", "strike_type": "ATM"}
    leg = {"segment": "OPTIONS", "option_type": ot, "position": pos, "lots": 1,
           "expiry": "WEEKLY", "strike_interval": 50, "strike_selection": sel}
    if sl:    leg["stopLoss"] = sl
    if tp:    leg["targetProfit"] = tp
    if trail: leg["trailSL"] = trail
    if buf:   leg["buffer_strike_enabled"] = True
    return leg

_ITM2 = {"type": "strike_type", "strike_type": "ITM2"}
_OTM3 = {"type": "strike_type", "strike_type": "OTM3"}
_PREM = {"type": "closest_premium", "premium": 100}

CORPUS = {
    # base variants
    "sl_pct_30":        [_leg("CE", sl={"mode": "pct", "value": 30}), _leg("PE", sl={"mode": "pct", "value": 30})],
    "sl_pct_50":        [_leg("CE", sl={"mode": "pct", "value": 50})],
    "sl_points_40":     [_leg("PE", sl={"mode": "points", "value": 40})],
    "target_60":        [_leg("CE", tp={"mode": "pct", "value": 60})],
    "sl30_target_80":   [_leg("CE", sl={"mode": "pct", "value": 30}, tp={"mode": "pct", "value": 80})],
    "trail_20_10":      [_leg("CE", trail={"mode": "pct", "trigger": 20, "move": 10})],
    # SL combined with non-ATM strike selections
    "sl_on_ITM2":       [_leg("CE", sl={"mode": "pct", "value": 40}, strike=_ITM2)],
    "sl_on_OTM3":       [_leg("PE", sl={"mode": "pct", "value": 40}, strike=_OTM3)],
    "sl_on_premium":    [_leg("CE", sl={"mode": "pct", "value": 35}, strike=_PREM)],
    # multi-leg + mixed
    "4leg_condor_sl":   [_leg("CE", sl={"mode":"pct","value":30}, strike=_OTM3), _leg("PE", sl={"mode":"pct","value":30}, strike=_OTM3),
                          _leg("CE","BUY", strike={"type":"strike_type","strike_type":"OTM5"}), _leg("PE","BUY", strike={"type":"strike_type","strike_type":"OTM5"})],
    "sl_plus_target_2leg": [_leg("CE", sl={"mode":"pct","value":25}, tp={"mode":"pct","value":70}),
                             _leg("PE", sl={"mode":"pct","value":25}, tp={"mode":"pct","value":70})],
    # buffer-strike (also caught by _leg_has_exit_scan)
    "buffer_strike_sl": [_leg("CE", sl={"mode": "pct", "value": 30}, buf=True)],
    # tight SL => most trades hit stop (stress exit-reason paths)
    "sl_tight_10":      [_leg("CE", sl={"mode": "pct", "value": 10}), _leg("PE", sl={"mode": "pct", "value": 10})],
}

# also vary the date range for a subset (longer horizon, different regime)
_RANGES = {
    "2024H1": ("2024-01-01", "2024-06-30"),
    "2y":     ("2022-07-01", "2024-06-30"),
    "vol2020":("2020-01-01", "2020-06-30"),
}

def _same(a, b, tol=1e-6):
    fa = None if (a is None or isinstance(a, bool)) else _f(a)
    fb = None if (b is None or isinstance(b, bool)) else _f(b)
    if fa is not None and fb is not None:
        if math.isnan(fa) and math.isnan(fb): return True
        return abs(fa - fb) <= tol + tol * max(abs(fa), abs(fb))
    return str(a) == str(b)

def _f(x):
    try: return float(x)
    except Exception: return None

def _diff(py, rust, label):
    d = []
    for k in sorted(set(py or {}) | set(rust or {})):
        pv, rv = (py or {}).get(k), (rust or {}).get(k)
        if not _same(pv, rv):
            d.append(f"    {label}.{k}: py={pv!r} rust={rv!r}")
    return d

overall_ok = True
_cases = []
for name, legs in CORPUS.items():
    _cases.append((name, legs, "2024H1"))
# a few key shapes across other ranges
for rk in ("2y", "vol2020"):
    _cases.append((f"sl_pct_30@{rk}", CORPUS["sl_pct_30"], rk))
    _cases.append((f"sl30_target_80@{rk}", CORPUS["sl30_target_80"], rk))
    _cases.append((f"trail_20_10@{rk}", CORPUS["trail_20_10"], rk))

_n_ident = 0
for name, legs, rk in _cases:
    frm, to = _RANGES[rk]
    payload = {**_COMMON, "from_date": frm, "to_date": to, "legs": legs}
    try:
        r = execute_algotest_job(dict(payload))
        trades = r.get("trades") or []
        summary = r.get("summary") or {}
        if not trades:
            print(f"\n=== {name} === (0 trades — skipped)"); continue
        tdf = pd.DataFrame(trades)

        # ---- PYTHON path (production, mode 0) ----
        py_opt = compute_optim_metrics(tdf, summary)
        py_flat = {**summary, **py_opt}
        py_over = compute_xlsx_summary_metrics(tdf, py_flat, patchwise=False, filter_segments=None)
        py_flat = {**py_flat, **py_over}
        py_pw = compute_xlsx_summary_metrics(tdf, py_flat, patchwise=True, filter_segments=None)

        # ---- RUST authoritative path (mode 1, dormant) ----
        rust_flat, rust_pw = rust_authoritative_summary(tdf, summary, payload, filter_segments=None)

        diffs = _diff(py_flat, rust_flat, "flat") + _diff(py_pw or {}, rust_pw or {}, "pw")
        status = "IDENTICAL" if not diffs else f"{len(diffs)} DIFFS"
        print(f"=== {name:22s} trades={len(trades):4d}  {status}")
        for line in diffs[:12]:
            print(line)
        if diffs: overall_ok = False
        else: _n_ident += 1
    except Exception as exc:
        import traceback
        print(f"=== {name:22s} ERROR {type(exc).__name__}: {exc}")
        traceback.print_exc()
        overall_ok = False

print("\n" + "=" * 60)
print(f"{_n_ident}/{len(_cases)} cases byte-identical")
print("PHASE 0b VERDICT: Rust summary == Python summary for SL/Target/Trail corpus"
      if overall_ok else "PHASE 0b: diffs exist — see above (spec for the port)")
