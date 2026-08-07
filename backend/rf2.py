import warnings, datetime; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True)); hdr=rows[0]
def c(n):
    for i,h in enumerate(hdr):
        if h and str(h).strip().lower()==n.lower(): return i
C={x:c(x) for x in ("Trade","Type","Entry Date","Exit Date","Entry Spot","Exit Spot","Strike","Expiry","Exit Reason")}
from collections import defaultdict
byt=defaultdict(list)
for r in rows[1:]:
    if r[C["Trade"]] in (None,""): continue
    byt[r[C["Trade"]]].append(r)
print("total trades:",len(byt))
_d=lambda x:datetime.datetime.strptime(str(x)[:10],"%d-%m-%Y") if x else None
tids=sorted(byt,key=lambda k:_d(byt[k][0][C["Entry Date"]]) or datetime.datetime.min)
# find: PE strike changes UP (breach) but reason lacks 'PE'  OR  CE strike change up but reason lacks 'CE'
def st(legs):
    o={}
    for l in legs:
        t=str(l[C["Type"]])
        if t=='CE':o['CE']=l[C["Strike"]]
        elif t in ('PE','PUT'):o['PE']=l[C["Strike"]]
    return o
prev=None; miss_pe=0; miss_ce=0
print("\n-- adjustments where the reason omits the breaching leg --")
for i,k in enumerate(tids):
    cur=st(byt[k]); r0=byt[k][0]
    if prev is not None:
        cutreason=str(byt[tids[i-1]][0][C["Exit Reason"]] or '')
        cut_ent=byt[tids[i-1]][0]
        # PE rose (breach) at this boundary but cut reason lacks PE
        if cur.get('PE') and prev.get('PE') and cur['PE']>prev['PE'] and 'PE' not in cutreason:
            miss_pe+=1
            if miss_pe<=8: print("  cut Tr%s(exit %s): PE %s->%s | reason: %s"%(tids[i-1],cut_ent[C["Exit Date"]],prev['PE'],cur['PE'],cutreason[:44]))
    prev=cur
print("PE-breach cuts with reason missing PE:",miss_pe)
