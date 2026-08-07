import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_err1.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
def c(n):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==n.lower(): return i
C={x:c(x) for x in ("Trade","Leg","Index","Type","Entry Date","Exit Date","Expiry","Strike","Exit Reason")}
print("cols:",{k:v for k,v in C.items()})
from collections import defaultdict
byt=defaultdict(list)
for r in rows[1:]:
    if r[C["Trade"]] in (None,""): continue
    byt[r[C["Trade"]]].append(r)
print("total trade rows:",sum(len(v) for v in byt.values()),"| trades:",len(byt))
for tid in sorted(byt,key=lambda x:(x is None,x)):
    rr=byt[tid]; r0=rr[0]
    legs=",".join("%s%s"%(str(x[C["Type"]]),("@"+str(x[C["Strike"]])) if x[C["Strike"]] not in (None,"") else "") for x in rr)
    print("Tr%-3s %s->%s [%d legs: %s] %s"%(tid,str(r0[C["Entry Date"]])[:11],str(r0[C["Exit Date"]])[:11],len(rr),legs[:50],str(r0[C["Exit Reason"]] or '')[:30]))
