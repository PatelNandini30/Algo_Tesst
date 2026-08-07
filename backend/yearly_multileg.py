"""Multi-leg: does the YEARLY leg's 1000pt breach fire off the cycle anchor?"""
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
_d = lambda x: datetime.datetime.strptime(x, '%d-%m-%Y')
ks = sorted(by, key=lambda k:(_d(by[k][0]['Entry Date']), k))
print('trades:', len(ks))
print(' breach -> re-entry pairs (CE should HOLD, PE should re-strike)')
prev=None
for k in ks:
    L = {int(r['Leg']): r for r in by[k]}
    r1 = L.get(1, {}); r2 = L.get(2, {})
    rsn='|'.join(sorted({str(x.get('Exit Reason','')) for x in by[k]}))
    if prev and prev[1]==r1.get('Entry Date') and 'SPOT_ADJ' in prev[4]:
        ce_move = 'MOVED' if prev[2]!=r1.get('Strike') else 'held '
        pe_move = 'MOVED' if prev[3]!=r2.get('Strike') else 'held '
        print('  %s breach@%s  CE %s->%s %s  PE %s->%s %s' % (
            prev[0], prev[1], prev[2], r1.get('Strike'), ce_move,
            prev[3], r2.get('Strike'), pe_move))
    prev=(k, r1.get('Exit Date'), r1.get('Strike'), r2.get('Strike'), rsn)
sa = [k for k in ks if any('SPOT_ADJ' in str(r.get('Exit Reason','')) for r in by[k])]
print()
print('SPOT_ADJ trades:', len(sa))
print('any exit on 07-11-2022?', [k for k in ks if by[k][0]['Exit Date']=='07-11-2022'] or 'NONE')
