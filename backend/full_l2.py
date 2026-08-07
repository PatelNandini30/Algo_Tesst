import warnings; warnings.filterwarnings("ignore")
import collections
from services.algotest_job import execute_algotest_job
CE_W = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'WEEKLY',
        'strike_interval':100,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'},
        'spot_adjustment':{'enabled':True,'pct':1.0,'direction':'rise','units':'percent'}}
PE_Y = {'segment':'OPTIONS','option_type':'PE','position':'BUY','lots':1,'expiry':'YEARLY',
        'strike_interval':1000,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'},
        'spot_adjustment':{'enabled':True,'pct':1000,'direction':'rise','units':'points'}}
p = dict(index='NIFTY', from_date='2022-07-01', to_date='2026-06-30',
         strategy_type='positional', underlying='cash', expiry_type='YEARLY',
         rollover_cadence='weekly', yearly_exit_months_before=1, yearly_roll_months=['12'],
         entry_dte=1, exit_dte=1, slippage_pct=0, charges_enabled=False,
         square_off_mode='partial', rollover_toggle=True, no_cache=True,
         filter_entry_mode='fixed', filter_config='custom', spot_adjustment_enabled=False,
         filter_segments=[{"start":"2022-07-29","end":"2023-01-06"},
                          {"start":"2023-04-28","end":"2024-12-24"},{"start":"2025-03-28","end":"2026-03-18"}],
         legs=[CE_W, PE_Y])
tr = execute_algotest_job(p).get('trades') or []
# YEARLY_DATES
import datetime as _dt
_by={}
for t in tr: _by.setdefault(t['Trade'],[]).append(t)
_d=lambda x: _dt.datetime.strptime(x,'%d-%m-%Y')
_ks=sorted(_by,key=lambda k:(_d(_by[k][0]['Entry Date']),k))
print('yearly-leg adjustments (exit dates):')
for k in _ks:
    r=_by[k][0]
    if 'Leg 2' in str(r.get('Exit Reason','')):
        print('   %s -> %s   entry spot %-10s exit spot %s'%(
          r['Entry Date'], r['Exit Date'], r['Entry Spot'], r['Exit Spot']))
print()
c = collections.Counter(str(t.get('Exit Reason','')) for t in tr)
print('distinct Exit Reason values:')
for k, v in c.most_common():
    print('   %-56s %d' % (k, v))
