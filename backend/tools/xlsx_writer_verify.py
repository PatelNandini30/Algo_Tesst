"""Verify the Rust Trade-Sheet writer is cell-identical to openpyxl _write_trade_sheet.
    docker exec -w /app algotest-backend python -m tools.xlsx_writer_verify
"""
import warnings; warnings.filterwarnings("ignore")
import io, os, tempfile
import pandas as pd
from openpyxl import Workbook

from tools.parity_harness import PAYLOADS
from services.algotest_job import execute_algotest_job
from services.optimizer.excel_builder import (
    _build_key_order, _build_cleaned_rows, _write_trade_sheet,
    compute_midcap_for_rows, _aggregate_trades,
)
from tools.xlsx_celldiff import celldiff
import algotest_native as an

total = 0
for name, payload in PAYLOADS:
    res = execute_algotest_job(dict(payload))
    rows = res.get("trades") or []
    if not rows:
        print(f"{name}: no trades, skip"); continue

    mbt, msumm, has_mc = compute_midcap_for_rows(rows, None, None, "NIFTYMIDCAP100")
    tm, _g, _sk = _aggregate_trades(rows, has_mc, mbt, patchwise=False, filter_segments=None)
    key_order, hc, hp, hf = _build_key_order(rows, has_mc)
    cleaned = _build_cleaned_rows(rows, key_order, tm, has_mc, mbt)

    # openpyxl reference (Trade Sheet only)
    wb = Workbook(); wb.remove(wb.active)
    _write_trade_sheet(wb, cleaned, key_order)
    buf = io.BytesIO(); wb.save(buf); py_bytes = buf.getvalue()

    # Rust
    tp = tempfile.mktemp(suffix=".xlsx")
    an.write_trade_sheet_xlsx(cleaned, key_order, tp)
    with open(tp, "rb") as f:
        rust_bytes = f.read()
    os.remove(tp)

    diffs = celldiff(py_bytes, rust_bytes, max_report=25)
    total += len(diffs)
    print(f"{name}: {len(cleaned)} rows x {len(key_order)} cols -> {len(diffs)} cell-diffs")
    for d in diffs[:12]:
        print("   ", d)

print(f"\nTOTAL cell-diffs: {total}  {'-> CELL-IDENTICAL' if total == 0 else '(iterate)'}")
