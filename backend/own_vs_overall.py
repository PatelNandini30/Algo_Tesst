"""Does per-leg ('own') SA on a YEARLY leg match trade-level ('overall') SA at the same %?"""
import warnings; warnings.filterwarnings("ignore")
import collections, datetime
from services.algotest_job import execute_algotest_job

def base_payload(**kw):
    p = dict(index='NIFTY', from_date='2019-03-29', to_date='2020-06-30',
             strategy_type='positional', underlying='cash', expiry_type='YEARLY',
             rollover_cadence='weekly', yearly_exit_months_before=1,
             yearly_roll_months=['12'], entry_dte=1, exit_dte=1, slippage_pct=0,
             charges_enabled=False, square_off_mode='partial', rollover_toggle=True,
             no_cache=True, filter_entry_mode='fixed')
    p.update(kw); return p

CE_W = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'WEEKLY',
        'strike_interval':100,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'}}
def CE_Y(sa=None):
    d = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'YEARLY',
         'strike_interval':1000,'rollover_strike_mode':'fresh',
         'strike_selection':{'type':'strike_type','strike_type':'ATM'}}
    if sa: d['spot_adjustment'] = sa
    return d

RUNS = {
 'OWN (per-leg 1% on the YEARLY leg)': base_payload(
     spot_adjustment_enabled=False,
     legs=[dict(CE_W), CE_Y({'enabled':True,'pct':1.0,'direction':'rise','units':'percent'})]),
 'OVERALL (trade-level 1%)': base_payload(
     spot_adjustment_enabled=True, spot_adjustment_pct=1.0,
     spot_adjustment_direction='rise', spot_adjustment_units='percent',
     legs=[dict(CE_W), CE_Y()]),
}

out = {}
for name, p in RUNS.items():
    tr = execute_algotest_job(p).get('trades') or []
    by = collections.OrderedDict()
    for t in tr: by.setdefault(t['Trade'], []).append(t)
    _d = lambda x: datetime.datetime.strptime(x, '%d-%m-%Y')
    ks = sorted(by, key=lambda k: (_d(by[k][0]['Entry Date']), k))
    sa_dates = [by[k][0]['Exit Date'] for k in ks
                if any('SPOT_ADJ' in (r.get('Exit Reason') or '') for r in by[k])]
    out[name] = (len(ks), sa_dates)
    print('%-38s trades=%-4d SPOT_ADJ=%d' % (name, len(ks), len(sa_dates)))

a, b = list(out.values())
print()
print('identical trade count :', a[0] == b[0])
print('identical SA dates    :', a[1] == b[1])
only_own = [d for d in a[1] if d not in b[1]]
only_all = [d for d in b[1] if d not in a[1]]
print('fired ONLY under own    (%d): %s' % (len(only_own), only_own[:10]))
print('fired ONLY under overall(%d): %s' % (len(only_all), only_all[:10]))
