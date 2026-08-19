"""Rebuild a finished sweep's merged WOW/MOM parts ZIP, in place.

Why this exists: the parts ZIP is built once, inside run_optimization's finalize
block. When the CHUNKING changes (parts are now cut along grid row-bands, not a
flat combo count — see runner.wow_mom_batches) an already-finished sweep keeps
its old, hole-ridden ZIP, and re-running a 38,416-combo sweep to fix a layout
bug is absurd. Everything needed is already on disk: per-combo WOW/MOM payloads
(result_store.read_combo_wm) and combo_columns in the stored rows.

Derives row/adjustment keys with runner._strike_display / _adj_display — the
SAME functions the sweep itself uses — so a rebuilt ZIP is identical to what the
sweep would now produce, rather than a second implementation that can drift.

    python -m tools.wow_mom_rebuild <job_id> [--overall] [--chunk N] [--dry-run]

Writes result_store.wow_mom_parts_zip_path(job_id, patchwise) atomically via a
.building temp, so an interrupted run never leaves a truncated ZIP in place.
"""
import argparse
import gc
import os
import sys
import zipfile

from services.optimizer import result_store
from services.optimizer.runner import _adj_display, _strike_display, wow_mom_batches
from services.optimizer.wow_mom import (
    _OpsWorkbook,
    adj_label_from_combo_label,
    write_merged_wow_mom,
)


def build_combos(job_id: str, patchwise: bool):
    """Mirror runner._read_combos for a COMPLETED job (wm already on disk)."""
    rows = result_store.get_all_results(job_id)
    if not rows:
        raise SystemExit(f"no stored results for job {job_id}")

    combos, missing = [], 0
    for r in rows:
        safe = r.get("combo_label_safe")
        wm = r.get("wm_pw" if patchwise else "wm_overall")
        if wm is None and r.get("wm_on_disk"):
            wm = result_store.read_combo_wm(job_id, safe, patchwise=patchwise)
        if wm is None:
            missing += 1
            continue

        cc = r.get("combo_columns") or {}
        strike_disp = _strike_display(cc)
        adj_label = _adj_display(cc.get("spot_adjustment"))
        if adj_label == "No Adj":
            adj_label = adj_label_from_combo_label(r.get("combo_label") or "") or adj_label
        combos.append({
            "title": f"{strike_disp} | {adj_label}" if cc else (r.get("combo_label") or safe),
            "cleaned": None,
            "wm": wm,
            "has_midcap": bool(r.get("has_midcap")),
            "adj_key": adj_label,
            "adj_label": adj_label,
            "row_key": "|".join([strike_disp, str(cc.get("expiry") or ""),
                                 str(cc.get("shifting") or "")]),
            "variant_label": "",
            "yearly": False,
        })
    return combos, missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--overall", action="store_true", help="overall basis (default: patchwise)")
    ap.add_argument("--chunk", type=int,
                    default=int(os.environ.get("OPTIM_WOW_MOM_PART_COMBOS", "2500")))
    ap.add_argument("--dry-run", action="store_true", help="report the part plan, write nothing")
    a = ap.parse_args()
    patchwise = not a.overall

    combos, missing = build_combos(a.job_id, patchwise)
    if missing:
        print(f"WARNING: {missing} combos had no WOW/MOM payload and were skipped")
    batches = wow_mom_batches(combos, a.chunk)
    sizes = [len(b) for b in batches]
    print(f"combos={len(combos)} parts={len(batches)} chunk={a.chunk}")
    print(f"part sizes: {sizes}")
    bands = [len({c['row_key'] for c in b}) for b in batches]
    print(f"row-bands per part: {bands}")
    if a.dry_run:
        return 0

    out = result_store.wow_mom_parts_zip_path(a.job_id, patchwise)
    tmp = out + ".building"
    import algotest_native as native

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zh:
        for i, batch in enumerate(batches, 1):
            wb = _OpsWorkbook()
            try:
                if not write_merged_wow_mom(wb, batch):
                    print(f"  part{i:02d}: EMPTY, skipped")
                    continue
                pf = f"{tmp}.part{i:02d}"
                native.write_layout_workbook_xlsx(wb.to_ops(), pf)
                zh.write(pf, f"WOW_MOM_part{i:02d}.xlsx")
                os.remove(pf)
                print(f"  part{i:02d}: {len(batch)} combos, "
                      f"{len({c['row_key'] for c in batch})} bands")
            finally:
                # Freed before the next part — keeps peak memory at part size.
                del wb
                gc.collect()
    os.replace(tmp, out)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
