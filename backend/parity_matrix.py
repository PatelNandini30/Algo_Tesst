"""Rust-live vs Python-reference backtest parity matrix.
Proves the live Rust path matches the Python reference across a strategy corpus.
Divergence => the Rust engine differs from the source-of-truth Python engine.
"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd
from services.algotest_job import execute_algotest_job
from engines.generic_algotest_engine import run_algotest_backtest

def _leg(ot, pos='SELL', st='ATM'):
    return dict(segment='OPTIONS', option_type=ot, position=pos, lots=1,
                expiry='WEEKLY', strike_interval=50,
                strike_selection={'type': 'strike_type', 'strike_type': st})

def mk(index, frm, to, exp, e, x, roll, legs):
    for lg in legs:
        lg['expiry'] = exp
    return dict(index=index, from_date=frm, to_date=to, strategy_type='positional',
                underlying='cash', expiry_type=exp, entry_dte=e, exit_dte=x,
                slippage_pct=0, charges_enabled=False, square_off_mode='partial',
                rollover_toggle=roll, no_cache=True, legs=legs)

def ru(p):
    tr = execute_algotest_job(dict(p))['trades']
    df = pd.DataFrame(tr) if tr else pd.DataFrame()
    n = len(df[df['Leg'] == 1]) if len(df) and 'Leg' in df.columns else len(df)
    pnl = round(float(pd.to_numeric(df['Net P&L'], errors='coerce').sum()), 2) if len(df) and 'Net P&L' in df.columns else 0.0
    return n, pnl

def py(p):
    t = run_algotest_backtest(dict(p))
    for el in (t if isinstance(t, tuple) else (t,)):
        if isinstance(el, pd.DataFrame) and 'Entry Date' in el.columns:
            df = el
            n = len(df[df['Leg'] == 1]) if 'Leg' in df.columns else len(df)
            pnl = round(float(pd.to_numeric(df['Net P&L'], errors='coerce').sum()), 2) if 'Net P&L' in df.columns else 0.0
            return n, pnl
    return -1, 0.0

# (label, index, from, to, expiry, entry_dte, exit_dte, rollover, legs)
CASES = [
    ('NIFTY wk 1leg roll',   'NIFTY','2024-01-01','2024-06-30','WEEKLY',1,1,True,  [_leg('CE')]),
    ('NIFTY wk 1leg norol',  'NIFTY','2024-01-01','2024-06-30','WEEKLY',1,1,False, [_leg('CE')]),
    ('NIFTY wk straddle roll','NIFTY','2024-01-01','2024-06-30','WEEKLY',1,1,True, [_leg('CE'),_leg('PE')]),
    ('NIFTY mo 1leg roll',   'NIFTY','2024-01-01','2024-12-31','MONTHLY',2,1,True, [_leg('PE')]),
    ('NIFTY mo 1leg norol',  'NIFTY','2024-01-01','2024-12-31','MONTHLY',2,1,False,[_leg('PE')]),
    ('NIFTY mo straddle roll','NIFTY','2024-01-01','2024-12-31','MONTHLY',2,1,True,[_leg('CE'),_leg('PE')]),
    ('MIDCP wk 1leg roll',   'MIDCPNIFTY','2023-06-01','2024-06-30','WEEKLY',1,1,True, [_leg('PE')]),
    ('MIDCP wk 1leg norol',  'MIDCPNIFTY','2023-06-01','2024-06-30','WEEKLY',1,1,False,[_leg('PE')]),
    ('MIDCP mo 1leg roll',   'MIDCPNIFTY','2023-01-01','2024-06-30','MONTHLY',2,1,True, [_leg('PE')]),
    ('MIDCP mo 1leg norol',  'MIDCPNIFTY','2023-01-01','2024-06-30','MONTHLY',2,1,False,[_leg('PE')]),
    ('MIDCP mo straddle roll','MIDCPNIFTY','2023-01-01','2024-06-30','MONTHLY',2,1,True,[_leg('CE'),_leg('PE')]),
]

print(f"{'case':26} {'RUST n/pnl':>22} {'PY n/pnl':>22}  match")
ndiv = 0
for lbl, idx, frm, to, exp, e, x, roll, legs in CASES:
    p = mk(idx, frm, to, exp, e, x, roll, [dict(l) for l in legs])
    try:
        rn, rp = ru(p)
    except Exception as ex:
        rn, rp = ('ERR', str(ex)[:30])
    try:
        pn, pp = py(p)
    except Exception as ex:
        pn, pp = ('ERR', str(ex)[:30])
    ok = (rn == pn) and (isinstance(rp,(int,float)) and isinstance(pp,(int,float)) and abs(rp-pp) < 1.0)
    if not ok: ndiv += 1
    print(f"{lbl:26} {str(rn)+'/'+str(rp):>22} {str(pn)+'/'+str(pp):>22}  {'OK' if ok else 'DIVERGE'}")
print(f"\n{ndiv} divergence(s) of {len(CASES)} cases")
