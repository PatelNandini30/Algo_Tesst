import sys, pandas as pd
sys.path.insert(0, '/app')
from services.algotest_job import execute_algotest_job
from services.optimizer import excel_builder as eb

filter_segments = [
    {"start": "2019-01-01", "end": "2019-12-31"},
    {"start": "2020-01-01", "end": "2020-12-31"},
    {"start": "2021-01-01", "end": "2021-12-31"},
    {"start": "2022-01-01", "end": "2022-12-31"},
]

req={"index":"NIFTY","from_date":"2019-01-01","to_date":"2022-12-31",
 "strategy_type":"positional","underlying":"cash","expiry_type":"WEEKLY",
 "entry_dte":1,"exit_dte":1,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial","rollover_toggle":True,"no_cache":True,
 "filter_config":"custom","filter_segments":filter_segments,
 "filter_entry_mode":"fixed",
 "spot_adjustment_enabled":True,"spot_adjustment_pct":1,
 "spot_adjustment_direction":"both","spot_adjustment_combine_mode":"OR",
 "spot_adjustment_units":"NIFTY","spot_adjustment_confirm_days":1,
 "spot_adjustment_use_entry_close":False,
 "legs":[
   {"segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":2,"direction":"ITM"}},
   {"segment":"OPTIONS","option_type":"PE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":1.5,"direction":"OTM"}},
 ]}

# ---- 1) BACKTEST (execute_algotest_job) ----
res = execute_algotest_job(req)
trades = res.get("trades") or []
summary = res.get("summary") or {}
print("=== BACKTEST: num trades:", len(trades))

# Mirror ResultsPanel.jsx's exact JS logic in Python: patch-reset cumulative/peak,
# then the SAME buggy peak_ms scan (only updates when cum>=peak).
import datetime as dt
def parse_dmy(s):
    if not s: return None
    return dt.datetime.strptime(s, "%d-%m-%Y")

def seg_start(s):
    return dt.datetime.strptime(s["start"], "%Y-%m-%d")
seg_starts = sorted(seg_start(s) for s in filter_segments)

def seg_idx(entry_dt):
    i = -1
    for j, sm in enumerate(seg_starts):
        if sm <= entry_dt: i = j
        else: break
    return i

# only leg-1 rows (Cumulative populated)
rows = [t for t in trades if t.get("Cumulative") not in (None, "")]
rows.sort(key=lambda t: parse_dmy(t["Entry Date"]))

cumulative = 100.0; peak = 100.0
prev_seg = None
patched = []
for t in rows:
    ed = parse_dmy(t["Entry Date"])
    si = seg_idx(ed)
    if prev_seg is not None and si != prev_seg:
        cumulative = 100.0; peak = 100.0
    prev_seg = si
    pct = t.get("% P&L") or 0.0
    cumulative *= (1 + pct/100.0)
    peak = max(peak, cumulative)
    patched.append({"entry": ed, "exit": parse_dmy(t["Exit Date"]), "cum": cumulative, "peak": peak})

peak_ms = None; worst_dd = 0.0; worst_peak = None; worst_trough = None
for p in patched:
    cum, pk, xd = p["cum"], p["peak"], p["exit"]
    if cum >= pk - 1e-9:
        peak_ms = xd
    else:
        ddp = (cum/pk - 1) * 100
        if ddp < worst_dd:
            worst_dd = ddp; worst_trough = xd; worst_peak = peak_ms

dur = (worst_trough - worst_peak).days if (worst_peak and worst_trough) else 0
print(f"BACKTEST (patchwise, JS-logic replica): Max DD={worst_dd:.2f}%  Period={worst_peak.date() if worst_peak else None} -> {worst_trough.date() if worst_trough else None}  Days={dur}")

# ---- 2) OPTIMIZER per-combo sheet, patchwise=True ----
df = pd.DataFrame(trades)
xbytes = eb.build_combo_xlsx(df, summary, combo_label="SameCombo", from_date=req["from_date"], to_date=req["to_date"],
                              filter_name="custom", patchwise=True, filter_segments=filter_segments)
with open("/tmp/same_combo.xlsx", "wb") as f:
    f.write(xbytes)

import openpyxl
wb = openpyxl.load_workbook("/tmp/same_combo.xlsx")
ws = wb["Summary"]
for r in range(20, 24):
    vals = [ws.cell(row=r, column=c).value for c in range(1,5)]
    print("OPTIMIZER row", r, vals)
