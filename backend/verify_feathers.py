#!/usr/bin/env python3
"""Post-deploy feather self-heal — guarantees every ``arrow-v2:bulk:<SYM>:full``
feather pair is a COMPLETE, consistent mirror of Postgres (no missing/truncated
data).

Why this exists
---------------
``start.sh`` warms can race the worker restart and truncate the **spot** feather:
options load wide (from the Parquet/feather shortcut) while spot loads
request-bounded (from a narrow DB query), producing an inconsistent pair; base.py
then detects the inconsistency and deletes it, and the next warm rewrites it
short. ``rust_fast_path.build_cache`` now blocks that at RUNTIME (its guard refuses
to shrink either the options *or* the spot range), but a warm racing the very
restart that loads the new guard can still slip one truncation through.

This script runs AFTER the stack is healthy and rebuilds any symbol whose feather
is short vs the DB. It is **cache-only** — it never touches backtest logic — and a
healthy feather is a no-op (a cheap Date-range check), so it is safe to run on
every deploy.
"""
import warnings
warnings.filterwarnings("ignore")
import shutil

import polars as pl
from sqlalchemy import text

from database import get_engine
from services import rust_fast_path as _rf
from services.data_loader import get_bulk_options_df, get_bulk_spot_df
from base import bulk_load_options

try:
    from services.data_loader import _get_redis_client
except Exception:  # pragma: no cover
    _get_redis_client = lambda: None  # noqa: E731


def _db_extents(sym):
    eng = get_engine()
    with eng.connect() as c:
        o = c.execute(text("SELECT MIN(date)::text, MAX(date)::text FROM option_data WHERE symbol=:s"), {"s": sym}).fetchone()
        s = c.execute(text("SELECT MIN(date)::text, MAX(date)::text FROM spot_data WHERE symbol=:s"), {"s": sym}).fetchone()
    return (o[0], o[1]), (s[0], s[1])


def _extent(p):
    df = pl.scan_ipc(str(p)).select(["Date"]).collect()
    return str(df["Date"].min())[:10], str(df["Date"].max())[:10]


def main():
    root = _rf._cache_root()
    dirs = sorted(root.glob("arrow-v2:bulk:*:full"))
    print(f"[VERIFY_FEATHERS] scanning {len(dirs)} full feather(s)", flush=True)
    rebuilt = []
    for d in dirs:
        parts = d.name.split(":")
        if len(parts) < 4:
            continue
        sym = parts[2]
        op, sp = d / "options.feather", d / "spot.feather"
        (db_o_min, db_o_max), (db_s_min, db_s_max) = _db_extents(sym)
        if db_o_min is None and db_s_min is None:
            print(f"  {sym}: no DB rows, skip", flush=True)
            continue
        need, why = False, []
        if not op.exists() or not sp.exists():
            need, why = True, ["missing feather file"]
        else:
            try:
                o_min, o_max = _extent(op)
                s_min, s_max = _extent(sp)
                # Criteria are IDEMPOTENT — they compare against the ACHIEVABLE range
                # (what a fresh full-span bulk_load can produce), never the raw DB min,
                # because spot data may legitimately not reach the options start (e.g.
                # NIFTY options begin 2000-06-12 but DB spot begins 2000-01-03; a
                # "must reach DB spot min" test would rebuild forever). A healthy
                # feather must be a no-op.
                if db_o_min and o_min > db_o_min:
                    need = True; why.append(f"opt start {o_min}>{db_o_min}")
                if db_o_max and o_max < db_o_max:
                    need = True; why.append(f"opt end {o_max}<{db_o_max}")
                if db_s_max and s_max < db_s_max:
                    need = True; why.append(f"spot end {s_max}<{db_s_max}")
                if s_max < o_max:
                    need = True; why.append(f"spot<opt end {s_max}<{o_max}")
                # spot start clipped: only if it starts AFTER both the options start
                # AND the DB spot start (i.e. it's a real clip, not a data-availability
                # limit like BANKNIFTY, whose spot only exists from 2020 while options
                # go back to 2005).
                if o_min and db_s_min and s_min > o_min and s_min > db_s_min:
                    need = True; why.append(f"spot start {s_min}>opt {o_min} & db {db_s_min}")
            except Exception as e:
                need, why = True, [f"unreadable: {e}"]
        if not need:
            print(f"  {sym}: OK", flush=True)
            continue
        print(f"  {sym}: TRUNCATED ({'; '.join(why)}) -> rebuilding", flush=True)
        lo = min([x for x in (db_o_min, db_s_min) if x])
        hi = max([x for x in (db_o_max, db_s_max) if x])
        try:
            shutil.rmtree(d, ignore_errors=True)
            cli = _get_redis_client()
            if cli is not None:
                try:
                    cli.delete(f"bulk:{sym}:full")
                except Exception:
                    pass
            bulk_load_options(sym, lo, hi)
            odf, sdf = get_bulk_options_df(), get_bulk_spot_df()
            if odf is None or odf.is_empty():
                print(f"  {sym}: no options after reload, skip", flush=True)
                continue
            _rf.build_cache(odf, sdf, cache_key=f"bulk:{sym}:full")
            o_min, o_max = _extent(op)
            s_min, s_max = _extent(sp)
            print(
                f"  {sym}: REBUILT opt {odf.height} ({o_min}->{o_max}) "
                f"spot {0 if sdf is None else sdf.height} ({s_min}->{s_max})",
                flush=True,
            )
            rebuilt.append(sym)
        except Exception as e:
            print(f"  {sym}: rebuild FAILED: {e}", flush=True)
    print(f"[VERIFY_FEATHERS] done; rebuilt {len(rebuilt)}: {rebuilt}", flush=True)


if __name__ == "__main__":
    main()
