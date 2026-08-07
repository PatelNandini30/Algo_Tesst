import json
from services import rust_fast_path as rf
from services.algotest_job import execute_algotest_job
def canon(r): return json.dumps({"t":r.get("trades",[]),"s":r.get("summary",{})},sort_keys=True,default=str)
SEG=[("2019-03-29","2019-05-10"),("2022-07-29","2022-12-23"),("2023-04-28","2024-03-28")]
LEG={"index":"NIFTY","option_type":"PE","position":"BUY","lots":1,"expiry":"YEARLY","strike_interval":1000,
     "strike_selection":{"type":"ATM"},"rollover_strike_mode":"fixed"}
cfg=dict(index="NIFTY",from_date="2019-02-28",to_date="2024-03-28",expiry_type="YEARLY",rollover_cadence="monthly",
    yearly_exit_months_before=1,yearly_roll_months=["12"],entry_dte=1,exit_dte=1,rollover_toggle=True,no_cache=True,
    filter_config="custom",filter_entry_mode="fixed",filter_segments=[{"start":s,"end":e} for(s,e)in SEG],legs=[dict(LEG)])
_orig=rf.spot_series
def _nm(sym,days,loader=None):
    o={}
    for d in days:
        v=rf.get_spot_price(d,sym)
        if v is None and loader is not None: v=loader.get_spot_price(sym,d)
        if v is not None: o[d]=float(v)
    return o
rf.spot_series=_nm; ref=canon(execute_algotest_job(cfg))
rf.spot_series=_orig; rf.clear_spot_series_memo(); now=canon(execute_algotest_job(cfg))
print("SINGLE-INDEX parity memo-ON==memo-OFF:", ref==now)
