import sys, warnings
warnings.filterwarnings('ignore')
from base import bulk_load_options
from services.data_loader import get_bulk_options_df, get_bulk_spot_df
from services.rust_fast_path import build_cache
import polars as pl

args = [a for a in sys.argv[1:] if not a.startswith('-')]
no_bump = '--no-bump' in sys.argv
sym = args[0]
frm = args[1]
to  = args[2]
print(f'REBUILD {sym} {frm}..{to}', flush=True)

r = bulk_load_options(sym, frm, to)
print('bulk_load result keys:', list(r.keys()) if isinstance(r, dict) else r, flush=True)
odf = get_bulk_options_df()
sdf = get_bulk_spot_df()
print('odf:', None if odf is None else (odf.height, str(odf['Date'].min())[:10], str(odf['Date'].max())[:10]), flush=True)
print('sdf:', None if sdf is None else (sdf.height, str(sdf['Date'].min())[:10], str(sdf['Date'].max())[:10]), flush=True)

if odf is None or odf.is_empty():
    print('FATAL: no options df loaded', flush=True); sys.exit(1)

ok = build_cache(odf, sdf, cache_key=f'bulk:{sym.upper()}:full')
print('build_cache:', ok, flush=True)

# An explicit rebuild fixes the feather's DATA (e.g. un-truncates spot), so any
# result cached against the old (wrong) feather must be invalidated. This is the
# correct place for the bump — a one-off operator action, not every feather write.
if no_bump:
    print('data_version bump skipped (--no-bump)', flush=True)
else:
    try:
        from services.backtest_cache import bump_data_version
        v = bump_data_version()
        print('bumped data_version ->', v, flush=True)
    except Exception as e:
        print('data_version bump skipped:', e, flush=True)

o = pl.read_ipc(f'/data/cache/arrow/arrow-v2:bulk:{sym.upper()}:full/options.feather')
print(f'VERIFY options.feather: {o.height} rows, {str(o["Date"].min())[:10]} -> {str(o["Date"].max())[:10]}', flush=True)
sp = pl.read_ipc(f'/data/cache/arrow/arrow-v2:bulk:{sym.upper()}:full/spot.feather')
print(f'VERIFY spot.feather: {sp.height} rows, {str(sp["Date"].min())[:10]} -> {str(sp["Date"].max())[:10]}', flush=True)
print('DONE', flush=True)
