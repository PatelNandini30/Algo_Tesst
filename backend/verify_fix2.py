import sys, pandas as pd
sys.path.insert(0, '/app')
from services.algotest_job import execute_algotest_job
from services.optimizer import excel_builder as eb

req={"index":"NIFTY","from_date":"2019-01-01","to_date":"2022-12-31",
 "strategy_type":"positional","underlying":"cash","expiry_type":"WEEKLY",
 "entry_dte":1,"exit_dte":1,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial","rollover_toggle":True,"no_cache":True,
 "spot_adjustment_enabled":True,"spot_adjustment_pct":1,
 "spot_adjustment_direction":"both","spot_adjustment_combine_mode":"OR",
 "spot_adjustment_units":"NIFTY","spot_adjustment_confirm_days":1,
 "spot_adjustment_use_entry_close":False,
 "legs":[
   {"segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":2,"direction":"ITM"}},
   {"segment":"OPTIONS","option_type":"PE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,
    "strike_selection":{"type":"pct_of_atm","pct_of_atm":1.5,"direction":"OTM"}},
 ]}
res = execute_algotest_job(req)
trades = res.get("trades") or []
summary = res.get("summary") or {}
df = pd.DataFrame(trades)
print("num trades:", len(trades))

xbytes = eb.build_combo_xlsx(df, summary, combo_label="TestCombo", from_date=req["from_date"], to_date=req["to_date"])
with open("/tmp/combo_check.xlsx", "wb") as f:
    f.write(xbytes)
print("saved, bytes:", len(xbytes))
