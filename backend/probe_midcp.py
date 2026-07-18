import warnings; warnings.filterwarnings('ignore')
import polars as pl
import pandas as pd
from base import get_expiry_dates

OPT = '/data/cache/arrow/arrow-v2:bulk:MIDCPNIFTY:full/options.feather'
SPOT = '/data/cache/arrow/arrow-v2:bulk:MIDCPNIFTY:full/spot.feather'

o = pl.read_ipc(OPT).with_columns([
    pl.col('Date').cast(pl.Utf8).str.slice(0,10).alias('d'),
    pl.col('ExpiryDate').cast(pl.Utf8).str.slice(0,10).alias('e'),
])
sp = pl.read_ipc(SPOT).with_columns(pl.col('Date').cast(pl.Utf8).str.slice(0,10).alias('d'))
spot_map = {r['d']: r['Close'] for r in sp.iter_rows(named=True)}

exp = get_expiry_dates('MIDCPNIFTY','monthly','2022-01-01','2024-12-31')
col = [c for c in exp.columns if 'urrent' in c or 'xpiry' in c][0]
expiries = sorted(set(str(x)[:10] for x in exp[col].tolist()))

print(f"{'expiry':12} {'entryDay':10} {'spot':>8} {'ATM':>7} {'#rows@exp':>9} {'ATM_PE':>8} {'ATM_CE':>8} {'#strikes_that_day':>17}")
for e in expiries:
    sub = o.filter(pl.col('e')==e)
    if sub.height==0:
        print(f"{e:12} {'--':10} {'':>8} {'':>7} {0:>9}  NO EXPIRY CONTRACT"); continue
    # entry day = last trading day with data for this expiry that is <= expiry, pick expiry-eve (2nd last date)
    days = sorted(sub.select('d').unique().to_series().to_list())
    entry = days[-2] if len(days)>=2 else days[-1]
    spot = spot_map.get(entry)
    if spot is None:
        print(f"{e:12} {entry:10} {'NOSPOT':>8}"); continue
    atm = round(spot/100)*100
    day_rows = sub.filter(pl.col('d')==entry)
    nstr = day_rows.height
    pe = day_rows.filter((pl.col('StrikePrice')==atm) & (pl.col('OptionType')=='PE')).select('Close').to_series().to_list()
    ce = day_rows.filter((pl.col('StrikePrice')==atm) & (pl.col('OptionType')=='CE')).select('Close').to_series().to_list()
    pev = pe[0] if pe else None
    cev = ce[0] if ce else None
    print(f"{e:12} {entry:10} {spot:>8.1f} {atm:>7.0f} {sub.height:>9} {str(pev):>8} {str(cev):>8} {nstr:>17}")
