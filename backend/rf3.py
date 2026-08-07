import warnings, datetime; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
idx={}
for i,h in enumerate(hdr):
    if h: idx[str(h).strip().lower()]=i
def g(r,name):
    i=idx.get(name.lower()); 
    return r[i] if i is not None else None
print("cols:", [str(h) for h in hdr if h])
from collections import defaultdict
byt=defaultdict(list)
for r in rows[1:]:
    t=g(r,"Trade")
    if t in (None,""): continue
    byt[t].append(r)
def num(x):
    try: return float(str(x).replace(",",""))
    except: return None
def dd(x):
    for f in ("%d-%m-%Y","%Y-%m-%d","%d-%b-%Y"):
        try: return datetime.datetime.strptime(str(x)[:10],f)
        except: pass
    return None
tids=sorted(byt,key=lambda k:dd(g(byt[k][0],"Entry Date")) or datetime.datetime.min)
print("trades:",len(tids))
# print first trade's legs to see structure
print("\n== sample trade (first) ==")
for r in byt[tids[0]]:
    print("  ",g(r,"Type"),"strike",g(r,"Strike"),"idx",g(r,"Index") or g(r,"Idx"),"exp",g(r,"Expiry"),"| reason:",g(r,"Exit Reason"))
