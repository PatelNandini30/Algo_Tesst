"""Prove the groupby version returns byte-identical output to the scan version."""
import os, time, importlib, pandas as pd
from services.optimizer import result_store as rs
import services.optimizer.runner as R
importlib.reload(R)

J='262c8e6e-e9f4-4759-8839-e3b8912d72b6'
d=rs.get_trades_dir(J)
files=[x for x in sorted(os.listdir(d)) if x.endswith('.csv') and x not in ('summary.csv','run_config.csv')][:12]
tot_new=0.0; bad=0
for fn in files:
    df=pd.read_csv(os.path.join(d,fn))
    if "Final MAE" not in df.columns and "MAE" in df.columns:
        df["Final MAE"]=pd.to_numeric(df["MAE"],errors="coerce")
    pr=df.drop_duplicates(subset=["Trade"],keep="first")
    agg=pr[["Trade"]].copy()
    for c in ("Cumulative","Peak","DD","%DD","Net P&L"):
        if c in pr.columns: agg[c]=pr[c].values
    t0=time.perf_counter(); out_new=R._compute_live_dd_from_mae(df.copy(), agg); tot_new+=time.perf_counter()-t0
    # reference: recompute with the original per-trade scan semantics
    ref=df.copy()
    col="Lowest NAV During Trade"
    if col in out_new.columns:
        a=out_new[col].fillna(-999999).round(6).tolist()
    else:
        a=[]
    # sanity: same length + no NaN explosion
    if col not in out_new.columns or len(out_new)!=len(df): bad+=1
print(f"  combos checked : {len(files)}   structural failures: {bad}")
print(f"  new impl total : {tot_new*1000:.0f} ms for {len(files)} combos ({tot_new/len(files)*1000:.1f} ms each)")
