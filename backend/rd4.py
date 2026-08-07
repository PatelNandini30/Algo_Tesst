import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_optfile.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
def col(name):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==name.lower(): return i
    return None
ci={c:col(c) for c in ("Trade","Leg","Type","Entry Date","Exit Date","Strike","Entry Spot","Exit Spot","Expiry","Exit Reason")}
print("PE leg only — NIFTY spot vs strike (SA = Rise 1000pts, Fresh mode):")
print("%-3s %-11s %-11s %-8s %-9s %-9s %-11s %s"%("Tr","Entry","Exit","Strike","EntSpot","ExtSpot","Expiry","Reason"))
prev_strike=None
for r in rows[1:]:
    if ci["Leg"] is None: break
    if str(r[ci["Type"]])!="PE": continue
    stk=r[ci["Strike"]]; es=r[ci["Entry Spot"]]; xs=r[ci["Exit Spot"]]
    chg="" 
    if prev_strike is not None and stk!=prev_strike:
        chg=" <== strike %s->%s"%(prev_strike,stk)
    prev_strike=stk
    print("%-3s %-11s %-11s %-8s %-9s %-9s %-11s %s%s"%(str(r[ci["Trade"]]),str(r[ci["Entry Date"]])[:11],str(r[ci["Exit Date"]])[:11],str(stk),str(es),str(xs),str(r[ci["Expiry"]])[:11],str(r[ci["Exit Reason"]])[:20],chg))
