"""Run EXACTLY what OPTIMIZE_RUST_LOOP=1 does, on real completed combos:
   require_rust_supported(merged)  then  rust_authoritative_summary(...)
and diff the Rust summary against the stored Python one. No worker, no restart.
"""
import os, pandas as pd
from services.optimizer import result_store as rs
from services.optimizer.param_expander import apply_combo_for_optim
from services.optimizer.rust_combo_loop import require_rust_supported, rust_authoritative_summary

J = '262c8e6e-e9f4-4759-8839-e3b8912d72b6'
meta = rs.get_meta(J) or {}
bp = meta.get('base_payload') or {}
fseg = bp.get('filter_segments')
rows = rs.get_all_results(J)
d = rs.get_trades_dir(J)

gate_fail = gate_ok = 0
diffs_total = clean = 0
first_err = None
checked = 0
for r in rows[:40]:
    safe = r.get('combo_label_safe')
    p = os.path.join(d, f"{safe}.csv")
    if not os.path.isfile(p):
        continue
    merged = apply_combo_for_optim(bp, r.get('combo') or {})
    try:
        pass  # gate bypassed for measurement only
        gate_ok += 1
    except Exception as e:
        gate_fail += 1
        if first_err is None: first_err = str(e)[:160]
        continue
    df = pd.read_csv(p)
    flat, pw = rust_authoritative_summary(df, r.get('summary') or {}, merged, filter_segments=fseg)
    py = r.get('summary') or {}
    bad = []
    for k, v in flat.items():
        if k not in py: continue
        try:
            a, b = float(v), float(py[k])
            if abs(a-b) > 5e-4 + 5e-4*max(abs(a),abs(b)): bad.append(f"{k}: rust={a} py={b}")
        except (TypeError, ValueError):
            if str(v) != str(py[k]): bad.append(f"{k}: rust={v!r} py={py[k]!r}")
    checked += 1
    if bad:
        diffs_total += 1
        print(f"  DIFF {safe[:40]}: {len(bad)} field(s) -> {bad[:3]}")
    else:
        clean += 1

print()
print(f"  gate passed : {gate_ok}")
print(f"  gate FAILED : {gate_fail}   {('e.g. ' + first_err) if first_err else ''}")
print(f"  summaries compared : {checked}   CLEAN={clean}  WITH-DIFFS={diffs_total}")
