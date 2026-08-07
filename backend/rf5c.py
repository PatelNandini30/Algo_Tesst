import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f5.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
print("HDR:",[str(h) for h in hdr if h])
def col(n):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==n.lower(): return i
    return None
C={c:col(c) for c in ("Trade","Type","Entry Date","Exit Date","Strike","Entry Spot","Exit Spot","Net P&L","Exit Reason","Strike Shift Reason")}
print("idx:",C)
for r in rows[1:]:
    if r[C["Trade"]] in (None,""): continue
    print("Tr%s %s->%s Type=%s stk=%s es=%s xs=%s net=%s | %s | shift=%s"%(
        r[C["Trade"]],str(r[C["Entry Date"]])[:11],str(r[C["Exit Date"]])[:11],r[C["Type"]],r[C["Strike"]],r[C["Entry Spot"]],r[C["Exit Spot"]],r[C["Net P&L"]],
        str(r[C["Exit Reason"]] or '')[:24], str(r[C["Strike Shift Reason"]] or '')[:34]))
