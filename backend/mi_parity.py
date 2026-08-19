"""mode0 vs mode1 summaries on REAL multi-index (yearly+futures sync) combos.
Asserts its inputs before comparing — an earlier run silently used an empty payload."""
import os, pandas as pd
from services.optimizer import result_store as rs
from services.optimizer.param_expander import apply_combo_for_optim
from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _cmetrics
from services.optimizer.rust_combo_loop import rust_authoritative_summary, rust_batch_unsupported

J='2e133fd4-2dec-4601-8ee1-ede745e02b6e'
meta=rs.get_meta(J) or {}; bp=meta.get('base_payload') or {}
fseg=bp.get('filter_segments')
assert bp.get('legs'), 'FAIL: no legs'
assert bp.get('multi_index_mode'), 'FAIL: not multi-index'
print(f"  payload: multi_index={bp.get('multi_index_mode')} sync_roll={bp.get('sync_weekly_roll')} "
      f"legs={len(bp['legs'])} filter_segs={len(fseg or [])} expiry={bp.get('expiry_type')}")
print(f"  gate says: {rust_batch_unsupported(bp)}")

rows=rs.get_all_results(J); d=rs.get_trades_dir(J)
assert rows, 'FAIL: no stored results'
TOL=5e-4
def match(a,b):
    try:
        fa,fb=float(a),float(b); return abs(fa-fb)<=TOL+TOL*max(abs(fa),abs(fb))
    except (TypeError,ValueError): return str(a)==str(b)

n=clean=0; fields=0; bad={}
for r in rows:
    p=os.path.join(d, f"{r.get('combo_label_safe')}.csv")
    if not os.path.isfile(p): continue
    df=pd.read_csv(p)
    if df.empty: continue
    merged=apply_combo_for_optim(bp, r.get('combo') or {})
    base=dict(r.get('summary') or {})
    opt0=compute_optim_metrics(df, base)
    flat0={**base, **(opt0 or {})}
    flat0={**flat0, **(_cmetrics(df, flat0, patchwise=False, filter_segments=fseg) or {})}
    pw0=_cmetrics(df, flat0, patchwise=True, filter_segments=fseg)
    flat1, pw1 = rust_authoritative_summary(df, base, merged, filter_segments=fseg)
    diffs=[]
    for tag,(a,b) in (('overall',(flat0,flat1)),('patchwise',(pw0,pw1))):
        for k in set(a or {}) | set(b or {}):
            va,vb=(a or {}).get(k,'<MISS>'),(b or {}).get(k,'<MISS>')
            fields+=1
            if not match(va,vb):
                key=f'{tag}.{k}'; diffs.append(key); bad[key]=bad.get(key,0)+1
    n+=1
    if not diffs: clean+=1
print()
print(f"  combos compared : {n}")
print(f"  fields compared : {fields}")
print(f"  CLEAN           : {clean}   WITH-DIFFS: {n-clean}")
for k,c in sorted(bad.items(), key=lambda kv:-kv[1])[:10]: print(f"    {k}  ({c} combos)")
