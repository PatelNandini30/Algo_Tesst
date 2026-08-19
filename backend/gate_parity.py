"""mode0 vs mode1 summaries for any job — used to earn each gate lift.
Asserts inputs, compares overall AND patchwise, reports per-field diffs."""
import os, sys, pandas as pd
from services.optimizer import result_store as rs
from services.optimizer.param_expander import apply_combo_for_optim
from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _cmetrics
from services.optimizer.rust_combo_loop import rust_authoritative_summary, rust_batch_unsupported

TOL = 5e-4
def match(a, b):
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= TOL + TOL * max(abs(fa), abs(fb))
    except (TypeError, ValueError):
        return str(a) == str(b)

for J in sys.argv[1:]:
    meta = rs.get_meta(J) or {}
    bp = meta.get('base_payload') or {}
    if not bp.get('legs'):
        print(f"  {J[:8]}: SKIP — no payload"); continue
    fseg = bp.get('filter_segments')
    mc_legs = bp.get('midcap_legs')
    mc_sa = bp.get('midcap_spot_adjustment')
    rows = rs.get_all_results(J)
    d = rs.get_trades_dir(J)
    n = clean = fields = 0
    bad = {}
    for r in rows:
        p = os.path.join(d, f"{r.get('combo_label_safe')}.csv")
        if not os.path.isfile(p):
            continue
        df = pd.read_csv(p)
        if df.empty:
            continue
        merged = apply_combo_for_optim(bp, r.get('combo') or {})
        base = dict(r.get('summary') or {})
        opt0 = compute_optim_metrics(df, base)
        flat0 = {**base, **(opt0 or {})}
        flat0 = {**flat0, **(_cmetrics(df, flat0, patchwise=False, filter_segments=fseg,
                                      midcap_legs=mc_legs, midcap_spot_adjustment=mc_sa) or {})}
        pw0 = _cmetrics(df, flat0, patchwise=True, filter_segments=fseg,
                        midcap_legs=mc_legs, midcap_spot_adjustment=mc_sa)
        flat1, pw1 = rust_authoritative_summary(df, base, merged, midcap_legs=mc_legs,
                                                midcap_spot_adjustment=mc_sa, filter_segments=fseg)
        diffs = []
        for tag, (a, b) in (('overall', (flat0, flat1)), ('patchwise', (pw0, pw1))):
            for k in set(a or {}) | set(b or {}):
                va, vb = (a or {}).get(k, '<MISS>'), (b or {}).get(k, '<MISS>')
                fields += 1
                if not match(va, vb):
                    key = f'{tag}.{k}'
                    diffs.append(key)
                    bad[key] = bad.get(key, 0) + 1
        n += 1
        if not diffs:
            clean += 1
    tag = (meta.get('zip_naming') or {}).get('level1', '')[:26]
    print(f"  {J[:8]} {tag:<26} gate={str(rust_batch_unsupported(bp)):<18} "
          f"combos={n} fields={fields} CLEAN={clean} DIFFS={n-clean}")
    for k, c in sorted(bad.items(), key=lambda kv: -kv[1])[:6]:
        print(f"      {k}  ({c})")
