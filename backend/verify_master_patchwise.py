import sys, pandas as pd
sys.path.insert(0, '/app')
from services.algotest_job import execute_algotest_job
from services.optimizer import excel_builder as eb

filter_segments = [
    {"start": "2019-01-01", "end": "2019-12-31"},
    {"start": "2020-01-01", "end": "2020-12-31"},
    {"start": "2021-01-01", "end": "2021-12-31"},
    {"start": "2022-01-01", "end": "2022-12-31"},
]

req={"index":"NIFTY","from_date":"2019-01-01","to_date":"2022-12-31",
 "strategy_type":"positional","underlying":"cash","expiry_type":"WEEKLY",
 "entry_dte":1,"exit_dte":1,"slippage_pct":0,"charges_enabled":False,
 "square_off_mode":"partial","rollover_toggle":True,"no_cache":True,
 "filter_config":"custom","filter_segments":filter_segments,
 "filter_entry_mode":"fixed",
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

for pw in (False, True):
    m = eb.compute_xlsx_summary_metrics(df, summary, patchwise=pw, filter_segments=filter_segments if pw else None)
    print("patchwise=", pw, "max_dd_pct=", m.get("max_dd_pct"), "mdd_start=", m.get("mdd_start_date"), "mdd_end=", m.get("mdd_end_date"), "mdd_dur=", m.get("mdd_duration_days"))
