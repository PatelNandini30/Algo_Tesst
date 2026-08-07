import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_optfile.xlsx", data_only=True)
ws=wb["Trade Sheet"]
rows=list(ws.iter_rows(values_only=True))
hdr=rows[0]
def col(name):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==name.lower(): return i
    return None
idx={c:col(c) for c in ("Trade","Leg","Index","Type","Entry Date","Exit Date","Expiry","Strike","Net P&L","Exit Reason")}
print("cols found:",{k:v for k,v in idx.items()})
print("\n%-3s %-3s %-4s %-11s %-11s %-11s %-9s %-9s %s"%("Tr","Lg","Typ","Entry","Exit","Expiry","Strike","NetP&L","Reason"))
for r in rows[1:]:
    def g(c): 
        i=idx.get(c); return r[i] if i is not None else ""
    if g("Trade") in (None,""): continue
    print("%-3s %-3s %-4s %-11s %-11s %-11s %-9s %-9s %s"%(str(g("Trade")),str(g("Leg")),str(g("Type"))[:4],str(g("Entry Date"))[:11],str(g("Exit Date"))[:11],str(g("Expiry"))[:11],str(g("Strike")),str(g("Net P&L")),str(g("Exit Reason"))[:26]))
