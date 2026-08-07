"""Are the 19-07-2023 / 03-07-2024 neighbours re-entry mini-trades or cadence trades?"""
import warnings; warnings.filterwarnings("ignore")
import collections, datetime
from services.algotest_job import execute_algotest_job

CE_W = {'segment':'OPTIONS','option_type':'CE','position':'SELL','lots':1,'expiry':'WEEKLY',
        'strike_interval':100,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'}}
PE_Y = {'segment':'OPTIONS','option_type':'PE','position':'BUY','lots':1,'expiry':'YEARLY',
        'strike_interval':1000,'rollover_strike_mode':'fresh',
        'strike_selection':{'type':'strike_type','strike_type':'ATM'},
        'spot_adjustment':{'enabled':True,'pct':1000,'direction':'rise','units':'points'}}

p = dict(index='NIFTY', from_date='2022-07-01', to_date='2026-06-30',
         strategy_type='positional', underlying='cash', expiry_type='YEARLY',
         rollover_cadence='weekly', yearly_exit_months_before=1,
         yearly_roll_months=['12'], entry_dte=1, exit_dte=1, slippage_pct=0,
         charges_enabled=False, square_off_mode='partial', rollover_toggle=True,
         no_cache=True, filter_entry_mode='fixed', filter_config='custom',
         spot_adjustment_enabled=False,
         filter_segments=[{"start":"2022-07-29","end":"2023-01-06"},
                          {"start":"2023-04-28","end":"2024-03-28"},
                          {"start":"2024-04-30","end":"2025-01-07"},
                          {"start":"2025-03-28","end":"2026-03-20"},
                          {"start":"2026-04-30","end":"2026-06-30"}],
         legs=[CE_W, PE_Y])

tr = execute_algotest_job(p).get('trades') or []
by = collections.OrderedDict()
for t in tr: by.setdefault(t['Trade'], []).append(t)
base_max = None
ids = sorted(int(k) for k in by)
# mini-trades continue a counter past the base block; find the largest run of
# consecutive ids starting at 1 -> that's the base block.
base_max = 0
for i in ids:
    if i == base_max + 1: base_max = i
    else: break
print('base trade ids: 1..%d   ids above that are re-entry/bridge mini-trades' % base_max)
print()
for lo,hi in ((34,39),(80,85),(54,58)):
    print('=== trades %d..%d ===' % (lo,hi))
    for k in ids:
        if not (lo <= k <= hi): continue
        L = {int(r['Leg']): r for r in by[k]}
        kind = 'MINI' if k > base_max else 'sched'
        print('   id=%-5s %-6s %-11s -> %-11s spot %-10s -> %-10s CE %-9s PE %-9s %s' % (
            k, kind, L[1]['Entry Date'], L[1]['Exit Date'],
            L[1]['Entry Spot'], L[1]['Exit Spot'], L[1]['Strike'],
            L[2]['Strike'] if 2 in L else '-',
            '|'.join(sorted({str(x.get('Exit Reason','')) for x in by[k]}))))
    for k in ids:
        if k <= base_max: continue
        L = {int(r['Leg']): r for r in by[k]}
        import datetime as _dt
        d = _dt.datetime.strptime(L[1]['Entry Date'], '%d-%m-%Y')
        anchor = by[lo][0]['Entry Date']
        a = _dt.datetime.strptime(anchor, '%d-%m-%Y')
        if abs((d-a).days) <= 20:
            print('   id=%-5s MINI   %-11s -> %-11s spot %-10s -> %-10s CE %-9s PE %-9s %s' % (
                k, L[1]['Entry Date'], L[1]['Exit Date'], L[1]['Entry Spot'],
                L[1]['Exit Spot'], L[1]['Strike'], L[2]['Strike'] if 2 in L else '-',
                '|'.join(sorted({str(x.get('Exit Reason','')) for x in by[k]}))))
    print()
