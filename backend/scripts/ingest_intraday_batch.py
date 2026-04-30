#!/usr/bin/env python3
"""Batch-ingest a directory of intraday CSVs.

Usage:
    python -m backend.scripts.ingest_intraday_batch \
        --symbol NIFTY --data-root /data/intraday \
        --csv-dir /path/to/csvs/

CSVs are expected to follow the clean_2023 format (see FORMATS.md).
File naming convention: <SYMBOL>_<YYYY-MM-DD>.csv (e.g. NIFTY_2024-03-15.csv).
"""
import argparse
import os
import re
import sys
from datetime import date

from backend.services import intraday_publish

FILE_RE = re.compile(r"^(?P<symbol>[A-Z]+)_(?P<date>\d{4}-\d{2}-\d{2})\.csv$")


def _files_in_dir(csv_dir: str, symbol: str):
    out = []
    for name in sorted(os.listdir(csv_dir)):
        m = FILE_RE.match(name)
        if not m or m.group("symbol") != symbol:
            continue
        out.append((date.fromisoformat(m.group("date")), os.path.join(csv_dir, name)))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--csv-dir", required=True)
    args = p.parse_args(argv)

    files = _files_in_dir(args.csv_dir, args.symbol)
    if not files:
        print(f"No matching CSVs in {args.csv_dir} for symbol {args.symbol}", file=sys.stderr)
        return 1

    ok = 0
    fail = 0
    for trading_date, path in files:
        try:
            intraday_publish.publish(
                symbol=args.symbol,
                trading_date=trading_date,
                source_path=path,
                data_root=args.data_root,
            )
            print(f"OK  {trading_date} {path}")
            ok += 1
        except Exception as e:  # noqa: BLE001 - operational script reports errors
            print(f"ERR {trading_date} {path}: {e}", file=sys.stderr)
            fail += 1
    print(f"\nIngested: {ok} OK, {fail} failed")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
