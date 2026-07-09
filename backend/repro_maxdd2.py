import pandas as pd
from services.algotest_job import execute_algotest_job

req={"index":"NIFTY","from_date":"2019-01-01","to_date":"2026-06-30",
 "strategy_type":"positional","underlying":"cash","expiry_type":"WEEKLY",
 "entry_dte":1,"exit_dte":1,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial","rollover_toggle":True,"no_cache":True,
 "spot_adjustment_enabled":True,
 "spot_adjustment_pct":1,
 "spot_adjustment_direction":"both",
 "spot_adjustment_combine_mode":"OR",
 "spot_adjustment_units":"NIFTY",
 "spot_adjustment_confirm_days":1,
 "spot_adjustment_use_entry_close":False,
 "legs":[
   {"segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":2,"direction":"ITM"}},
   {"segment":"OPTIONS","option_type":"PE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":1.5,"direction":"OTM"}},
 ]}
res=execute_algotest_job(req)
df=pd.DataFrame(res.get("trades") or [])
df = df[df["%DD"].notna()].copy()
print("num trades w/ %DD:", len(df))
df["Exit Date"]=pd.to_datetime(df["Exit Date"], dayfirst=True)
df = df.sort_values("Exit Date").reset_index(drop=True)
df["yr"]=df["Exit Date"].dt.year

print("\n--- (A) current: min(global %DD) per year ---")
print(df.groupby("yr")["%DD"].min())

print("\n--- (B) year-reset peak-to-trough on Cumulative ---")
for yr, g in df.groupby("yr"):
    cum = g["Cumulative"].astype(float).values
    peak = cum[0]
    worst = 0.0
    for c in cum:
        peak = max(peak, c)
        dd = (c - peak) / peak * 100 if peak else 0.0
        worst = min(worst, dd)
    print(yr, round(worst, 4))

print("\noverall min %DD (global, matches summary Max Drawdown):", df["%DD"].min())
