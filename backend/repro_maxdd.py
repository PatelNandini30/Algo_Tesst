import pandas as pd
from services.algotest_job import execute_algotest_job
req={"index":"NIFTY","from_date":"2019-01-01","to_date":"2026-06-30",
 "strategy_type":"positional","underlying":"cash","expiry_type":"WEEKLY",
 "entry_dte":1,"exit_dte":1,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial","rollover_toggle":True,"no_cache":True,
 "legs":[
   {"segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":2,"direction":"ITM"}},
   {"segment":"OPTIONS","option_type":"PE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":1.5,"direction":"OTM"}},
 ]}
res=execute_algotest_job(req)
df=pd.DataFrame(res.get("trades") or [])
print("num trades", len(df))
print(df[["Exit Date","%DD","Cumulative","Peak","DD"]].head(20).to_string())
df["Exit Date"]=pd.to_datetime(df["Exit Date"], dayfirst=True)
df["yr"]=df["Exit Date"].dt.year
print(df.groupby("yr")["%DD"].min())
print("overall min %DD:", df["%DD"].min())
