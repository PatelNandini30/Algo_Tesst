"""Yearly leg must hold ONE strike per calendar month, even across spot-adj re-entries."""
import warnings; warnings.filterwarnings("ignore")
import collections, datetime, itertools
from services.algotest_job import execute_algotest_job

CE_W = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'WEEKLY',
        'strike_interval':100,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'},
        'spot_adjustment':{'enabled':True,'pct':1.0,'direction':'rise','units':'percent'}}
CE_Y = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'YEARLY',
        'strike_interval':1000,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'}}

p = dict(index='NIFTY', from_date='2019-03-29', to_date='2020-06-30',
         strategy_type='positional', underlying='cash', expiry_type='YEARLY',
         rollover_cadence='weekly', yearly_exit_months_before=1,
         yearly_roll_months=['12'], entry_dte=1, exit_dte=1, slippage_pct=0,
         charges_enabled=False, square_off_mode='partial', rollover_toggle=True,
         no_cache=True, spot_adjustment_enabled=False, filter_entry_mode='fixed',
         legs=[CE_W, CE_Y])

tr = execute_algotest_job(p).get('trades') or []
by = collections.OrderedDict()
for t in tr: by.setdefault(t['Trade'], []).append(t)
_d = lambda x: datetime.datetime.strptime(x, '%d-%m-%Y')
ks = sorted(by, key=lambda k: (_d(by[k][0]['Entry Date']), k))

rows = []
for k in ks:
    for r in by[k]:
        if int(r['Leg']) == 2:
            e = _d(r['Entry Date'])
            rows.append((e, k, r['Entry Date'], float(r['Strike']),
                         r.get('Exit Reason',''),
                         str(r.get('Strike Shift Reason','')) + ' | spot=' + str(r.get('Entry Spot',''))))
print('yearly-leg rows:', len(rows))
bad = 0
for month, grp in itertools.groupby(rows, key=lambda x: x[0].strftime('%Y-%m')):
    g = list(grp)
    uniq = sorted({r[3] for r in g})
    flag = '' if len(uniq) == 1 else '   <<< CHANGES INSIDE THE MONTH'
    if len(uniq) > 1: bad += 1
    print('  %s  n=%-3d strikes=%s%s' % (month, len(g), uniq, flag))
print()
print('months where the yearly strike changed mid-month:', bad)
print()
print('  May-2019 detail')
for e,k,ed,s,rsn,exp in rows:
    if e.strftime('%Y-%m') == '2019-05':
        print('   trade %-4s %-11s strike %-9s %-30s exp %s' % (k, ed, s, rsn, exp))
