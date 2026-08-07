import warnings, datetime; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
idx={str(h).strip().lower():i for i,h in enumerate(hdr) if h}
def g(r,name):
    i=idx.get(name.lower()); return r[i] if i is not None else None
from collections import defaultdict
byt=defaultdict(list)
for r in rows[1:]:
    t=g(r,"Trade")
    if t not in (None,""): byt[t].append(r)
def dd(x):
    for f in ("%d-%m-%Y","%Y-%m-%d","%d-%b-%Y"):
        try: return datetime.datetime.strptime(str(x)[:10],f)
        except: pass
    return None
def ds(x): return dd(x).strftime("%d-%b-%y") if dd(x) else str(x)
tids=sorted(byt,key=lambda k:dd(g(byt[k][0],"Entry Date")) or datetime.datetime.min)
for k in tids[:8]:
    legs=byt[k]; r0=legs[0]
    print("Tr%s  %s -> %s  EntrySpot=%s ExitSpot=%s"%(k,ds(g(r0,"Entry Date")),ds(g(r0,"Exit Date")),g(r0,"Entry Spot"),g(r0,"Exit Spot")))
    for r in legs:
        typ=g(r,"Type")
        if typ=="FUT": continue
        print("     %-3s strike=%-7s exp=%s  reason=%s"%(typ,g(r,"Strike"),ds(g(r,"Expiry")),g(r,"Exit Reason")))
