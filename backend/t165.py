"""Reproduce the desk's Jun-Dec roll config and inspect the trade 165 area."""
import warnings; warnings.filterwarnings("ignore")
import collections, datetime
from services.algotest_job import execute_algotest_job

CE_W = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'WEEKLY',
        'strike_interval':100,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ITM1'},
        'spot_adjustment':{'enabled':True,'pct':1.0,'direction':'rise','units':'percent'}}
PE_Y = {'segment':'OPTIONS','option_type':'PE','position':'BUY','lots':1,'expiry':'YEARLY',
        'strike_interval':1000,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'},
        'spot_adjustment':{'enabled':True,'pct':1000,'direction':'rise','units':'points'}}

p = dict(index='NIFTY', from_date='2019-03-29', to_date='2026-06-30',
         strategy_type='positional', underlying='cash', expiry_type='YEARLY',
         rollover_cadence='weekly', yearly_exit_months_before=1,
         yearly_roll_months=['06','12'], entry_dte=1, exit_dte=1, slippage_pct=0,
         charges_enabled=False, square_off_mode='partial', rollover_toggle=True,
         no_cache=True, filter_entry_mode='fixed', filter_config='custom',
         spot_adjustment_enabled=False,
         filter_segments=[{"start":"2019-03-29","end":"2019-05-08"},
                          {"start":"2019-09-30","end":"2020-02-26"},
                          {"start":"2022-07-29","end":"2022-12-21"},
                          {"start":"2023-04-28","end":"2024-10-23"},
                          {"start":"2025-03-28","end":"2026-01-19"},
                          {"start":"2026-04-30","end":"2026-06-22"}],
         legs=[CE_W, PE_Y])

tr = execute_algotest_job(p).get('trades') or []
by = collections.OrderedDict()
for t in tr: by.setdefault(t['Trade'], []).append(t)
_d = lambda x: datetime.datetime.strptime(x, '%d-%m-%Y')
ks = sorted(by, key=lambda k:(_d(by[k][0]['Entry Date']), k))
print('trades:', len(ks))
print()
print('=== the 25-06-2025 / 27-06-2025 area (desk trades 164/165) ===')
for k in ks:
    e = by[k][0]['Entry Date']
    if not ('06-2025' in e or '07-2025' in e): continue
    L = {int(r['Leg']): r for r in by[k]}
    s = float(L[1]['Entry Spot']); atm = round(s/100)*100
    print('  T%-5s %-11s -> %-11s spot %-10s  CE %-9s (ITM1 = %.0f)  PE %-9s  %s' % (
        k, e, L[1]['Exit Date'], s, L[1]['Strike'], atm-100,
        L[2]['Strike'] if 2 in L else '-',
        '|'.join(sorted({str(x.get('Exit Reason','')) for x in by[k]}))))
