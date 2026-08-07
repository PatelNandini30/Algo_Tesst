"""Does lot scaling reach the OPTIMIZER per-combo sheet and master summary?"""
import copy, io, sys
sys.path.insert(0,"/app")
import pandas as pd, openpyxl
from services.algotest_job import execute_algotest_job
from services.optimizer.excel_builder import compute_xlsx_summary_metrics, build_combo_xlsx
from tools.optim_bt_summary_parity import MAP as SHEET_MAP, _num, _sheet_kv

BASE={"index":"NIFTY","from_date":"2024-01-01","to_date":"2024-02-29",
 "strategy_type":"positional","underlying":"cash","expiry_window":"weekly_expiry",
 "entry_dte":1,"exit_dte":0,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial",
 "legs":[{"segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,
  "expiry":"WEEKLY","strike_interval":50,
  "strike_selection":{"type":"strike_type","strike_type":"ATM"}}]}

def run(n):
    p=copy.deepcopy(BASE)
    for l in p["legs"]: l["lots"]=n
    res=execute_algotest_job(p)
    tr=res.get("trades") or []
    df=pd.DataFrame(tr); A=res.get("summary") or {}
    C=compute_xlsx_summary_metrics(df,A,midcap_legs=None,patchwise=False,filter_segments=None)
    xb=build_combo_xlsx(df,A,combo_label=f"lots{n}",from_date=p["from_date"],to_date=p["to_date"])
    kv=_sheet_kv(openpyxl.load_workbook(io.BytesIO(xb))["Summary"])
    B={}
    for label,key in SHEET_MAP.items():
        if label in kv:
            v=_num(kv[label])
            if v is not None: B.setdefault(key,v)
    return A,B,C

A1,B1,C1=run(1); A2,B2,C2=run(2)
WATCH=["total_pnl","avg_profit_per_trade","max_dd_pct","actual_live_dd_max","avg_final_mae"]
print(f"{'metric':<26}{'BT 1':>10}{'BT 2':>10}{'combo 2':>10}{'master 2':>10}{'ratio':>8}")
for k in WATCH:
    a1,a2=A1.get(k),A2.get(k); b2,c2=B2.get(k),C2.get(k)
    if isinstance(a1,(int,float)) and isinstance(a2,(int,float)) and abs(a1)>1e-9:
        print(f"{k:<26}{a1:>10.4g}{a2:>10.4g}{(b2 if isinstance(b2,(int,float)) else float('nan')):>10.4g}{(c2 if isinstance(c2,(int,float)) else float('nan')):>10.4g}{a2/a1:>8.3f}")
# identity at lots=2: backtest == per-combo == master
bad=[]
for k in set(A2)&set(B2):
    if isinstance(A2[k],(int,float)) and isinstance(B2[k],(int,float)) and abs(A2[k]-B2[k])>0.011: bad.append(f"BT!=combo {k}: {A2[k]} vs {B2[k]}")
for k in set(A2)&set(C2):
    if isinstance(A2[k],(int,float)) and isinstance(C2[k],(int,float)) and abs(A2[k]-C2[k])>0.011: bad.append(f"BT!=master {k}: {A2[k]} vs {C2[k]}")
print("IDENTITY AT 2 LOTS:", "PASS" if not bad else bad[:6])
