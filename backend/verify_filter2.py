import pandas as pd
from services.algotest_job import execute_algotest_job
# Continuous run with a CUSTOM 2-window filter; window-1 ends 2019-05-10 (mid-run boundary)
req={"index":"NIFTY","from_date":"2019-03-29","to_date":"2019-10-31",
 "strategy_type":"positional","underlying":"cash","expiry_type":"WEEKLY",
 "entry_dte":1,"exit_dte":0,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial","rollover_toggle":True,"no_cache":True,
 "filter_config":"custom",
 "filter_segments":[{"start":"2019-03-29","end":"2019-05-10"},
                    {"start":"2019-09-30","end":"2019-10-31"}],
 "legs":[{"segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,
   "expiry":"WEEKLY","strike_interval":50,
   "strike_selection":{"type":"strike_type","strike_type":"ATM"}}]}
res=execute_algotest_job(req)
df=pd.DataFrame(res.get("trades") or [])
print("meta.filter_segments:", (res.get("meta") or {}).get("filter_segments"))
if df.empty: print("NO TRADES"); raise SystemExit
cols=[c for c in ["Entry Date","Exit Date","Expiry","Exit Reason"] if c in df.columns]
print(df[cols].to_string(index=False))
print("counts:", df["Exit Reason"].value_counts().to_dict())
