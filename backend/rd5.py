import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_optfile.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
def col(n):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==n.lower(): return i
    return None
ci={c:col(c) for c in ("Trade","Type","Entry Date","Strike","Entry Spot","Strike Shift Reason","Exit Reason")}
print("PE leg: strike vs ATM(round spot to 1000) — is the strike a clean 1000-multiple at ATM?")
print("%-3s %-11s %-9s %-9s %-9s %s"%("Tr","Entry","EntSpot","Strike","ATM1000","note"))
for r in rows[1:]:
    if str(r[ci["Type"]]) not in ("PE","PUT"): continue
    es=r[ci["Entry Spot"]]; stk=r[ci["Strike"]]
    try: atm=round(float(es)/1000.0)*1000
    except: atm=None
    note=""
    if stk is not None and stk%1000!=0: note="<-- NOT 1000-multiple"
    if atm is not None and stk is not None and abs(stk-atm)>0: note+=" (off-ATM by %d)"%(stk-atm)
    print("%-3s %-11s %-9s %-9s %-9s %s"%(str(r[ci["Trade"]]),str(r[ci["Entry Date"]])[:11],es,stk,atm,note))
