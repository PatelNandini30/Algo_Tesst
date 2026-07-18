"""Verify the Rust WOW/MOM sheet is cell-identical to openpyxl.

openpyxl reference = write_wow_mom_combined (unchanged block writers).
Rust = wow_mom_ops(...) -> algotest_native.write_layout_sheet_xlsx.

    docker exec -w /app algotest-backend python -m tools.wowmom_writer_verify
"""
import warnings; warnings.filterwarnings("ignore")
import io, os, tempfile
from openpyxl import Workbook

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from services.optimizer import excel_builder as eb
from services.optimizer.wow_mom import write_wow_mom_combined, wow_mom_ops
from tools.xlsx_celldiff import celldiff
import algotest_native as an


def _cleaned_for(payload, has_midcap_flag=False, synth_midcap=False):
    res = execute_algotest_job(dict(payload))
    rows = res.get("trades") or []
    if not rows:
        return None, None
    mbt, msumm, has_mc = eb.compute_midcap_for_rows(rows, None, None, "NIFTYMIDCAP100")
    tm, g, sk = eb._aggregate_trades(rows, has_mc, mbt, patchwise=False, filter_segments=None)
    ko, hc, hp, hf = eb._build_key_order(rows, has_mc)
    if synth_midcap:
        has_mc = True
        mbt = {}
        for i, k in enumerate(sk):
            mbt[k] = {}
        # give combined columns so has_midcap ret/dd fields resolve
    cleaned = eb._build_cleaned_rows(rows, ko, tm, has_mc, mbt)
    return cleaned, has_mc


total = 0
for name, payload in PAYLOADS:
    cleaned, has_mc = _cleaned_for(payload)
    if cleaned is None:
        print(f"{name}: no trades, skip"); continue

    wb = Workbook(); wb.remove(wb.active)
    if not write_wow_mom_combined(wb, cleaned, has_mc, "combo-" + name):
        print(f"{name}: no wow sheet, skip"); continue
    buf = io.BytesIO(); wb.save(buf); py_bytes = buf.getvalue()

    ops = wow_mom_ops(cleaned, has_mc, "combo-" + name)
    tp = tempfile.mktemp(suffix=".xlsx")
    an.write_layout_sheet_xlsx(ops, tp)
    with open(tp, "rb") as f:
        rust_bytes = f.read()
    os.remove(tp)

    diffs = celldiff(py_bytes, rust_bytes, max_report=40)
    total += len(diffs)
    print(f"{name}: {len(ops['cells'])} cells, {len(ops['merges'])} merges -> {len(diffs)} diffs")
    for d in diffs[:20]:
        print("    ", d)

print(f"\nWOW/MOM TOTAL cell-diffs: {total}  {'-> CELL-IDENTICAL' if total == 0 else '(iterate)'}")
