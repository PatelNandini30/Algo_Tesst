import os, time, pandas as pd
from services.optimizer import result_store as rs
from services.optimizer import runner as R

J='262c8e6e-e9f4-4759-8839-e3b8912d72b6'
d=rs.get_trades_dir(J)
f=[x for x in sorted(os.listdir(d)) if x.endswith('.csv') and x not in ('summary.csv','run_config.csv')]
p=os.path.join(d,f[0]); df=pd.read_csv(p)
if "Final MAE" not in df.columns and "MAE" in df.columns:
    df["Final MAE"]=pd.to_numeric(df["MAE"],errors="coerce")
pr=df.drop_duplicates(subset=["Trade"],keep="first")
agg=pr[["Trade"]].copy()
for c in ("Cumulative","Peak","DD","%DD","Net P&L"):
    if c in pr.columns: agg[c]=pr[c].values
print(f"  rows={len(df)}  trades={len(agg)}")

t0=time.perf_counter(); out=R._compute_live_dd_from_mae(df, agg); base=time.perf_counter()-t0
print(f"  current impl : {base*1000:.0f} ms")

# how much is the per-trade full scan?
t0=time.perf_counter()
for _,a in agg.iterrows():
    tid=str(a.get("Trade") or "")
    _ = df[df["Trade"]==tid]
scan=time.perf_counter()-t0
print(f"    of which per-trade df scans: {scan*1000:.0f} ms")
t0=time.perf_counter()
g={str(k): v for k,v in df.groupby("Trade")}
gb=time.perf_counter()-t0
print(f"    same lookup via one groupby : {gb*1000:.0f} ms   ({scan/max(gb,1e-9):.0f}x cheaper)")

# ── equivalence: groupby lookup vs per-trade scan ───────────────────────────
groups = {str(k): v for k, v in df.groupby("Trade")}
mism = 0
for _, a in agg.iterrows():
    tid = str(a.get("Trade") or "")
    if not tid: continue
    scan_legs = df[df["Trade"] == tid]
    grp_legs = groups.get(tid, df.iloc[0:0])
    if len(scan_legs) != len(grp_legs) or not scan_legs.equals(grp_legs.reindex(scan_legs.index)):
        mism += 1
print(f"  groupby vs scan mismatches: {mism} / {len(agg)}")
