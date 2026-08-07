import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f5.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
def col(n):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==n.lower(): return i
    return None
C={c:col(c) for c in ("Trade","Type","Entry Date","Exit Date","Expiry","Strike","Entry Spot","Exit Spot","Net P&L","Exit Reason","Strike Shift Reason")}
print("%-3s %-11s %-11s %-8s %-9s %-9s %-8s %-22s %s"%("Tr","Entry","Exit","Strike","EntSpot","ExtSpot","Net","Reason","ShiftReason"))
prev=None
for r in rows[1:]:
    if str(r[C["Type"]]) not in ("PE","PUT"): continue
    stk=r[C["Strike"]]; es=r[C["Entry Spot"]]; xs=r[C["Exit Spot"]]
    chg=""
    try:
        if prev is not None and stk!=prev: chg="  stk %s->%s"%(prev,stk)
    except: pass
    prev=stk
    print("%-3s %-11s %-11s %-8s %-9s %-9s %-8s %-22s %s%s"%(str(r[C["Trade"]]),str(r[C["Entry Date"]])[:11],str(r[C["Exit Date"]])[:11],stk,es,xs,r[C["Net P&L"]],str(r[C["Exit Reason"]] or '')[:22],str(r[C["Strike Shift Reason"]] or '')[:30],chg))
