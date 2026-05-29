import sys, json, logging
logging.basicConfig(level=logging.ERROR); sys.path.insert(0, '/app')
from services import rust_fast_path as rf
rf.load_cache_from_root('/data/cache/arrow/arrow-v2:bulk:NIFTY:full')
import algotest_native, pandas as pd
import redis as _r
c = _r.Redis(host='redis', port=6379, db=0)
meta = json.loads(c.get('celery-task-meta-c795d198-5ae6-4bf1-9ffc-e648150d78a9'))
payload = meta['args'][0]; py_trades = meta['result']['trades']
from base import get_trading_calendar, get_expiry_dates, get_spot_price_from_db
from engines.generic_algotest_engine import get_lot_size
from services import engine_rust as er   # REAL edited module, real shift logic

idx='NIFTY'; eff_from='2019-01-01'; eff_to='2026-01-23'
cal = get_trading_calendar(eff_from, eff_to)
days = pd.to_datetime(cal['date']).sort_values().dt.strftime('%Y-%m-%d').tolist()
exp_df = get_expiry_dates(idx, payload.get('expiry_type','weekly'), eff_from, eff_to)
col = 'Current Expiry' if 'Current Expiry' in exp_df.columns else exp_df.columns[0]
expiries = pd.to_datetime(exp_df[col]).sort_values().dt.strftime('%Y-%m-%d').unique().tolist()
spots = {d: float(get_spot_price_from_db(d, idx)) for d in days if get_spot_price_from_db(d, idx) is not None}
lot = int(get_lot_size(idx, days[0]))

res = er.run_rust_engine_pipeline(payload, expiry_dates=expiries, trading_days=days,
                                  lot_size=lot, spot_by_date=spots, square_off_mode='partial')
print('PIPELINE:', 'None (FALLBACK)' if res is None else f'{len(res)} priced rows')
if res is None:
    sys.exit(0)
records = er.priced_to_tradesheet_records(res, payload, lot)

def norm(d):
    s=str(d)
    for fmt in ('%Y-%m-%d','%d-%m-%Y','%d/%m/%Y'):
        try: return pd.to_datetime(s, format=fmt).strftime('%Y-%m-%d')
        except: pass
    try: return pd.to_datetime(s).strftime('%Y-%m-%d')
    except: return s

py = {norm(t['Entry Date']): t for t in py_trades}
ru = {norm(r['Entry Date']): r for r in records}
print(f'RUST trades={len(records)}  PYTHON trades={len(py_trades)}')
print('2020-03-26 in RUST?', '2020-03-26' in ru, '| in PYTHON?', '2020-03-26' in py)

only_py = sorted(set(py)-set(ru)); only_ru = sorted(set(ru)-set(py))
print(f'only in PYTHON ({len(only_py)}):', only_py[:15])
print(f'only in RUST   ({len(only_ru)}):', only_ru[:15])

mism=0; chk=0
for k in sorted(set(py)&set(ru)):
    chk+=1; p=py[k]; r=ru[k]
    if (float(p.get('Strike') or 0)!=float(r.get('Strike') or 0)
        or round(float(p.get('Entry Price') or 0),2)!=round(float(r.get('Entry Price') or 0),2)
        or round(float(p.get('Exit Price') or 0),2)!=round(float(r.get('Exit Price') or 0),2)):
        mism+=1
        if mism<=15:
            print(f'  MISMATCH {k}: strike py={p.get("Strike")} ru={r.get("Strike")} | entry py={p.get("Entry Price")} ru={r.get("Entry Price")} | exit py={p.get("Exit Price")} ru={r.get("Exit Price")} | reason py={p.get("Exit Reason")} ru={r.get("Exit Reason")}')
print(f'overlap checked={chk}  mismatches={mism}')
