import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f6.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
def c(n):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==n.lower(): return i
C={x:c(x) for x in ("Trade","Type","Entry Date","Exit Date","Strike","Entry Spot","Exit Spot","Exit Reason")}
print("%-3s %-11s %-11s %-8s %-9s %-9s %-7s %s"%("Tr","Entry","Exit","Strike","EntSpot","ExtSpot","rise","Reason"))
adj=0
for r in rows[1:]:
    if r[C["Trade"]] in (None,"") or str(r[C["Type"]]) not in ("PE","PUT"): continue
    es=r[C["Entry Spot"]]; xs=r[C["Exit Spot"]]
    try: ri="%.0f"%(float(xs)-float(es))
    except: ri="?"
    rr=str(r[C["Exit Reason"]] or '')
    if 'SPOT_ADJ' in rr: adj+=1
    print("%-3s %-11s %-11s %-8s %-9s %-9s %-7s %s"%(r[C["Trade"]],str(r[C["Entry Date"]])[:11],str(r[C["Exit Date"]])[:11],r[C["Strike"]],es,xs,ri,rr[:30]))
print("\nTotal SPOT_ADJ:",adj)
