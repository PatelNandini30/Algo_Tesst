"""Profile the per-combo POST-ENGINE steps (the ~1.4s that isn't elapsed_ms).
Uses a completed job's on-disk trades CSV — no market data, no engine run.
"""
import os, time, glob
import pandas as pd
from services.optimizer import result_store as rs

J = 'dc907e3f-ec70-4991-98d3-a59693b17677'
d = rs.get_trades_dir(J)
csvs = [f for f in sorted(os.listdir(d)) if f.endswith('.csv') and f not in ('summary.csv','run_config.csv')]
rows = {r['combo_label_safe']: r for r in rs.get_all_results(J)}

# pick a combo with a mid-sized tradesheet
pick = None
for f in csvs:
    p = os.path.join(d, f)
    if 40_000 < os.path.getsize(p) < 400_000:
        pick = f; break
pick = pick or csvs[0]
label = pick[:-4]
row = rows.get(label) or {}
df = pd.read_csv(os.path.join(d, pick))
base_summary = dict(row.get('summary') or {})
meta = rs.get_meta(J) or {}
bp = meta.get('base_payload') or {}
fseg = bp.get('filter_segments')
print(f"combo   : {label[:60]}")
print(f"rows    : {len(df)}  cols {len(df.columns)}")
print(f"filter  : {len(fseg or [])} segment(s)")
print()

def t(fn, name, n=3):
    best = None
    for _ in range(n):
        t0 = time.perf_counter(); out = fn(); dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    print(f"  {name:<34} {best*1000:8.1f} ms")
    return out

from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.excel_builder import (
    compute_xlsx_summary_metrics as _cmetrics, build_cleaned_for_combo as _bcc)
from services.optimizer.wow_mom import _wm_from_cleaned

opt = t(lambda: compute_optim_metrics(df, base_summary), "compute_optim_metrics  [PY]")
flat = {**base_summary, **(opt or {})}
t(lambda: _cmetrics(df, flat, patchwise=False, filter_segments=fseg), "_cmetrics overall      [RUST]")
t(lambda: _cmetrics(df, flat, patchwise=True,  filter_segments=fseg), "_cmetrics patchwise    [RUST]")
cleaned = t(lambda: _bcc(df, None, None, 'NIFTYMIDCAP100', False, fseg), "build_cleaned_for_combo[PY]")
if isinstance(cleaned, tuple): cleaned = cleaned[0]
try:
    t(lambda: _wm_from_cleaned(cleaned, False), "_wm_from_cleaned       [PY]")
except Exception as e:
    print(f"  _wm_from_cleaned                 skipped ({type(e).__name__})")

print()
print("  --- workbook build (2x per combo: overall + patchwise) ---")
from services.optimizer.excel_builder import build_combo_xlsx
xb = t(lambda: build_combo_xlsx(df, flat, combo_label=label,
        from_date=bp.get('from_date',''), to_date=bp.get('to_date',''),
        filter_name='custom' if fseg else '', patchwise=False,
        filter_segments=fseg), "build_combo_xlsx overall  [RUST]")
print(f"  {'-> xlsx bytes':<34} {len(xb) if xb else 0}")
t(lambda: build_combo_xlsx(df, flat, combo_label=label,
        from_date=bp.get('from_date',''), to_date=bp.get('to_date',''),
        filter_name='custom' if fseg else '', patchwise=True,
        filter_segments=fseg), "build_combo_xlsx patchwise[RUST]")
