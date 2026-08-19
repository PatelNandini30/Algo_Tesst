"""Rebuild an Optimization Summary by RECOMPUTING metrics from the per-combo CSVs.

Last-resort recovery, for when a job has lost every faster source:

    Redis rows           evicted at OPTIMIZE_RESULT_TTL (24h)
    parquet spill        only written above OPTIMIZE_PARQUET_SPILL_AT (10,000 combos)
    <job>.v22-pw-summary.json   written near the END of finalize — absent if the
                                worker was killed (e.g. cgroup OOM during WOW/MOM)

What always survives is optim_trades/<job>/*.csv — the tradesheets themselves.
This reads each one and recomputes the metrics with the SAME functions the sweep
uses (compute_optim_metrics + compute_xlsx_summary_metrics), so the numbers are
the sweep's own, not a second implementation.

Patchwise is the point: a summary built without patchwise metrics silently carries
OVERALL numbers under a "_patchwise_" filename (measured on job 788dbf1f: DD%
-44.66 overall vs -25.47 patchwise).

    python -m tools.summary_from_trades <job_id> [--overall] -o out.xlsx
"""
import argparse
import os
import sys

import pandas as pd

from services.optimizer import result_store as rs
from services.optimizer.combo_dedup import effective_fingerprint
from services.optimizer.combo_labeler import label_combo, safe_filename
from services.optimizer.excel_builder import compute_xlsx_summary_metrics as _cmetrics
from services.optimizer.metrics import compute_optim_metrics
from services.optimizer.param_expander import apply_combo_for_optim, expand_param_specs
from services.optimizer.summary_workbook import build_summary_workbook


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--overall", action="store_true", help="overall basis (default: patchwise)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    patchwise = not a.overall

    meta = rs.get_meta(a.job_id) or {}
    bp = meta.get("base_payload") or {}
    specs = meta.get("param_specs") or []
    if not bp or not specs:
        raise SystemExit(f"{a.job_id[:8]}: meta has no base_payload/param_specs — cannot rebuild")
    fseg = bp.get("filter_segments")
    mc_legs = bp.get("midcap_legs")
    mc_sa = bp.get("midcap_spot_adjustment")

    tdir = rs.get_trades_dir(a.job_id)
    if not os.path.isdir(tdir):
        raise SystemExit(f"no trades dir at {tdir}")
    on_disk = {f for f in os.listdir(tdir) if f.endswith(".csv")}
    print(f"  per-combo CSVs on disk : {len(on_disk)}")
    print(f"  basis                  : {'patchwise' if patchwise else 'overall'}")

    # combo_id is the 1-based position in the deduped expansion — the same value
    # the runner stamps as __combo_id__, so the CSV filename can be reconstructed.
    seen, rows, missing = set(), [], 0
    cid = 0
    for combo in expand_param_specs(specs):
        merged = apply_combo_for_optim(bp, combo)
        fp = effective_fingerprint(merged)
        if fp in seen:
            continue
        seen.add(fp)
        cid += 1
        labels = label_combo(merged)
        safe = f"{cid}_{safe_filename(labels['combo_label'])}"
        fn = f"{safe}.csv"
        if fn not in on_disk:
            missing += 1
            continue
        df = pd.read_csv(os.path.join(tdir, fn))
        if df.empty:
            missing += 1
            continue
        base = {}
        opt = compute_optim_metrics(df, base) or {}
        flat = {**base, **opt}
        flat = {**flat, **(_cmetrics(df, flat, patchwise=False, filter_segments=fseg,
                                    midcap_legs=mc_legs, midcap_spot_adjustment=mc_sa) or {})}
        if patchwise:
            pw = _cmetrics(df, flat, patchwise=True, filter_segments=fseg,
                           midcap_legs=mc_legs, midcap_spot_adjustment=mc_sa) or {}
            flat = {**flat, **pw}
        rows.append({
            "combo_id": cid,
            "combo": combo,
            "combo_label": labels["combo_label"],
            "combo_label_safe": safe,
            "combo_columns": {
                "expiry": labels["expiry"],
                "shifting": labels["shifting"],
                "put_strike_label": labels["put_strike_label"],
                "call_strike_label": labels["call_strike_label"],
                "spot_adjustment": labels["spot_adjustment"],
                "leg_cols": labels.get("leg_cols") or [],
                "overall_adjustment": labels.get("overall_adjustment") or "",
                "midcap_leg": labels.get("midcap_leg") or "",
                "midcap_adj": labels.get("midcap_adj") or "",
            },
            "summary": flat,
            "objective_value": flat.get("total_pnl"),
            "trade_count": int(flat.get("count") or 0),
        })
        if len(rows) % 500 == 0:
            print(f"    recomputed {len(rows)} …", flush=True)

    print(f"  combos reconstructed   : {cid}")
    print(f"  recomputed             : {len(rows)}")
    if missing:
        print(f"  WARNING: {missing} combos had no CSV / no trades and were skipped")

    xlsx = build_summary_workbook(rows, [], rules_sheet=None)
    with open(a.out, "wb") as fh:
        fh.write(xlsx)
    print(f"  wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB, {len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
