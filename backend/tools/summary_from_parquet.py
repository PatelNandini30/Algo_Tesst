"""Rebuild a finished sweep's Optimization Summary when Redis has evicted its rows.

result_store.get_all_results() reads ONLY the Redis list — it never falls back to
the parquet spill written at OPTIMIZE_PARQUET_SPILL_AT. So once the results key
expires (24h TTL, or eviction under memory pressure) the summary endpoint 404s
with "No results for this job" even though every number still exists on disk:

    /data/cache/optim_results/<job>.parquet          overall metrics + combo_label
    /data/cache/optim_zips/<job>.v22-pw-summary.json patchwise metrics by combo_id

The parquet has no combo_id, so the two cannot be joined directly. This
reconstructs the mapping the way the sweep itself assigns it — re-expand
param_specs, dedup with effective_fingerprint, and the 1-based position IS the
combo_id (runner stamps __combo_id__ from that index) — then joins on
combo_label, which is unique. The join is VERIFIED, not assumed: if the
reconstructed labels do not cover the parquet's labels exactly, it aborts.

    python -m tools.summary_from_parquet <job_id> [--overall] -o out.xlsx
"""
import argparse
import json
import os
import sys

import pandas as pd

from services.optimizer import result_store as rs
from services.optimizer.combo_labeler import label_combo, safe_filename
from services.optimizer.param_expander import apply_combo_for_optim, expand_param_specs
from services.optimizer.summary_workbook import build_summary_workbook


def rebuild_rows(job_id: str, patchwise: bool):
    meta = rs.get_meta(job_id) or {}
    bp = meta.get("base_payload") or {}
    specs = meta.get("param_specs") or []
    if not bp or not specs:
        raise SystemExit(f"job {job_id[:8]}: meta has no base_payload/param_specs — cannot rebuild")

    pq = os.path.join(rs.OPTIM_PARQUET_DIR, f"{job_id}.parquet")
    if not os.path.isfile(pq):
        raise SystemExit(f"no parquet spill at {pq}")
    df = pd.read_parquet(pq)
    metric_cols = [c for c in df.columns if not c.startswith("legs[") and c != "combo_label"]
    by_label = {str(r["combo_label"]): r for _, r in df.iterrows()}
    print(f"  parquet rows        : {len(df)}  ({len(metric_cols)} metric columns)")

    # combo_id -> patchwise summary
    pw_by_id = {}
    if patchwise:
        p = rs.zip_cache_path(job_id, True).replace(".zip", "-summary.json")
        if os.path.isfile(p):
            with open(p) as fh:
                pw_by_id = {r["combo_id"]: r["summary"] for r in (json.load(fh).get("rows") or [])}
            print(f"  patchwise overrides : {len(pw_by_id)}")
        else:
            # This tool re-implemented the exact bug it exists to recover from:
            # emitting OVERALL numbers under the patchwise default. The combo count,
            # labels and every column are correct, so nothing downstream can tell.
            raise SystemExit(
                "ABORT: patchwise requested (the default) but %s is missing, so no "
                "combo has patchwise metrics. Refusing to write an overall-basis "
                "workbook that looks patchwise. Use --overall to ask for overall "
                "deliberately, or rebuild with tools/summary_from_trades.py which "
                "recomputes patchwise from the per-combo CSVs." % p
            )

    # Re-expand exactly as the sweep did; 1-based position == combo_id.
    from services.optimizer.combo_dedup import effective_fingerprint
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
        rec = by_label.get(str(labels["combo_label"]))
        if rec is None:
            missing += 1
            continue
        summary = {c: (None if pd.isna(rec[c]) else rec[c]) for c in metric_cols}
        if patchwise and cid in pw_by_id:
            summary = {**summary, **pw_by_id[cid]}
        rows.append({
            "combo_id": cid,
            "combo": combo,
            "combo_label": labels["combo_label"],
            "combo_label_safe": f"{cid}_{safe_filename(labels['combo_label'])}",
            "combo_columns": {
                "expiry": labels["expiry"],
                "shifting": labels["shifting"],
                "put_strike_label": labels["put_strike_label"],
                "call_strike_label": labels["call_strike_label"],
                "spot_adjustment": labels["spot_adjustment"],
            },
            "summary": summary,
            "objective_value": summary.get("total_pnl"),
            "trade_count": int(summary.get("count") or 0),
        })

    print(f"  reconstructed combos: {cid}")
    print(f"  matched to parquet  : {len(rows)}")
    if missing:
        # Abort rather than ship a summary that silently drops combos.
        raise SystemExit(f"ABORT: {missing} reconstructed combos had no parquet row — join is unsafe")
    if len(rows) != len(df):
        raise SystemExit(f"ABORT: {len(rows)} rows vs {len(df)} parquet rows — join is incomplete")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--overall", action="store_true", help="overall basis (default: patchwise)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    rows = rebuild_rows(a.job_id, patchwise=not a.overall)
    xlsx = build_summary_workbook(rows, [], rules_sheet=None)
    with open(a.out, "wb") as fh:
        fh.write(xlsx)
    print(f"  wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
