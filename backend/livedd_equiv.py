"""Value-by-value: groupby version vs the ORIGINAL per-trade-scan algorithm."""
import os, pandas as pd
from services.optimizer import result_store as rs
import services.optimizer.runner as R

def original(df, aggregated):
    """Verbatim copy of the pre-change loop (per-trade boolean scan)."""
    if df.empty or "MAE" not in df.columns or "MFE" not in df.columns:
        return df
    df = df.copy(); prev_cum = 100.0; trade_lowest_nav = {}
    for _, agg_row in aggregated.iterrows():
        tid = str(agg_row.get("Trade") or "")
        if not tid: continue
        trade_legs = df[df["Trade"] == tid]                     # ← the OLD lookup
        final_mae = (R._calc_final_mae_for_trade(trade_legs, R._trade_net_pnl_pct(trade_legs))
                     if not trade_legs.empty else None)
        cum = agg_row.get("Cumulative")
        trade_lowest_nav[tid] = (round(prev_cum*(1.0+float(final_mae)/100.0)*100)/100
                                 if final_mae is not None else None)
        if cum is not None:
            try: prev_cum = float(cum)
            except (TypeError, ValueError): pass
    seen=set(); vals=[]
    for _, row in df.iterrows():
        tid=str(row.get("Trade") or "")
        if tid and tid not in seen:
            seen.add(tid); vals.append(trade_lowest_nav.get(tid))
        else: vals.append(None)
    df["Lowest NAV During Trade"]=vals
    return df

J='262c8e6e-e9f4-4759-8839-e3b8912d72b6'
d=rs.get_trades_dir(J)
files=[x for x in sorted(os.listdir(d)) if x.endswith('.csv') and x not in ('summary.csv','run_config.csv')][:25]
COL="Lowest NAV During Trade"; checked=0; diffs=0; cells=0
for fn in files:
    df=pd.read_csv(os.path.join(d,fn))
    if "Final MAE" not in df.columns and "MAE" in df.columns:
        df["Final MAE"]=pd.to_numeric(df["MAE"],errors="coerce")
    pr=df.drop_duplicates(subset=["Trade"],keep="first")
    agg=pr[["Trade"]].copy()
    for c in ("Cumulative","Peak","DD","%DD","Net P&L"):
        if c in pr.columns: agg[c]=pr[c].values
    a=original(df.copy(), agg); b=R._compute_live_dd_from_mae(df.copy(), agg)
    if COL not in a.columns or COL not in b.columns: continue
    va=a[COL].tolist(); vb=b[COL].tolist(); checked+=1
    for x,y in zip(va,vb):
        cells+=1
        if (x is None) != (y is None): diffs+=1
        elif x is not None and abs(float(x)-float(y))>1e-9: diffs+=1
print(f"  combos compared : {checked}")
print(f"  cells compared  : {cells}")
print(f"  VALUE DIFFS     : {diffs}")
