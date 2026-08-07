"""User's real config + real filter CSV: does OWN now equal OVERALL?"""
import warnings; warnings.filterwarnings("ignore")
import collections, datetime
from services.algotest_job import execute_algotest_job

SEGS = [("2019-03-29","2019-05-10"), ("2019-09-30","2020-02-28"),
        ("2022-07-29","2023-01-06"), ("2023-04-28","2024-03-28"),
        ("2024-04-30","2025-01-07"), ("2025-03-28","2026-03-20"),
        ("2026-04-30","2026-06-30")]

def PE(sa=None):
    d = {'segment':'OPTIONS','option_type':'PE','position':'BUY','lots':1,'expiry':'YEARLY',
         'strike_interval':1000,'rollover_strike_mode':'fixed',
         'strike_selection':{'type':'strike_type','strike_type':'ATM'}}
    if sa: d['spot_adjustment'] = sa
    return d

def payload(**kw):
    p = dict(index='NIFTY', from_date='2019-02-28', to_date='2026-06-30',
             strategy_type='positional', underlying='cash', expiry_type='YEARLY',
             rollover_cadence='monthly', yearly_exit_months_before=1,
             yearly_roll_months=['12'], entry_dte=0, exit_dte=0, slippage_pct=0,
             charges_enabled=False, square_off_mode='partial', rollover_toggle=True,
             no_cache=True, filter_entry_mode='fixed', filter_config='custom',
             filter_segments=[{"start":s,"end":e} for s,e in SEGS])
    p.update(kw); return p

RUNS = {
 'OWN     (leg: rise 1000pts)': payload(spot_adjustment_enabled=False,
    legs=[PE({'enabled':True,'pct':1000,'direction':'rise','units':'points'})]),
 'OVERALL (trade: rise 1000pts)': payload(spot_adjustment_enabled=True,
    spot_adjustment_pct=1000, spot_adjustment_direction='rise',
    spot_adjustment_units='points', legs=[PE()]),
}

res = {}
for name, p in RUNS.items():
    tr = execute_algotest_job(p).get('trades') or []
    by = collections.OrderedDict()
    for t in tr: by.setdefault(t['Trade'], []).append(t)
    _d = lambda x: datetime.datetime.strptime(x, '%d-%m-%Y')
    ks = sorted(by, key=lambda k: (_d(by[k][0]['Entry Date']), k))
    import re as _re  # STRIP_LEG: leg attribution is cosmetic, compare the token
    _norm = lambda r: _re.sub(r'\s*\(Leg \d+[^)]*\)', '', str(r or ''))
    rows = [(by[k][0]['Entry Date'], by[k][0]['Exit Date'],
             by[k][0]['Strike'], _norm(by[k][0].get('Exit Reason',''))) for k in ks]
    sa = [r[1] for r in rows if 'SPOT_ADJ' in str(r[3])]
    res[name] = rows
    print('%-30s trades=%-4d SPOT_ADJ=%d' % (name, len(rows), len(sa)))
    print('    SA dates:', sa)

ka,kb = list(res.keys()); a, b = res[ka], res[kb]
print()
print('rows 10-22   OWN(entry,exit,strike,reason)   |   OVERALL')
for i in range(10, min(23,len(a),len(b))):
    m = '' if a[i]==b[i] else '   <<< DIFF'
    print('  %-2d %-11s %-11s %-9s %-14s | %-9s %-14s%s' % (
        i, a[i][0], a[i][1], a[i][2], str(a[i][3])[:14], b[i][2], str(b[i][3])[:14], m))

print()
print('IDENTICAL:', a == b)
if a != b:
    for i,(ra,rb) in enumerate(zip(a,b)):
        if ra != rb:
            print('  first divergence row', i)
            print('   OWN    ', ra)
            print('   OVERALL', rb)
            break
    print('  len own=%d overall=%d' % (len(a), len(b)))
