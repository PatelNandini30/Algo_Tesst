#!/usr/bin/env python3
"""
Batch-dispatch ingest_intraday_csv Celery tasks for all CSV files in a symbol directory.

Usage:
    python backend/scripts/backfill_intraday.py \\
        --symbol NIFTY \\
        --source-dir /path/to/NIFTY/csvs \\
        --format clean_2023 \\
        --workers 2

Dispatches one Celery task per CSV file onto the 'uploads' queue.
The worker ingests, validates, writes Parquet, builds DaySnapshot, and
updates the manifest. Re-running is safe (idempotent via SHA256).
"""

import argparse
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Batch ingest intraday CSVs")
    parser.add_argument("--symbol", required=True,
                        choices=["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
    parser.add_argument("--source-dir", required=True,
                        help="Directory containing the CSV files for this symbol")
    parser.add_argument("--format", default="clean_2023",
                        choices=["clean_2023", "raw_2017"],
                        help="CSV format hint passed to the ingest handler")
    parser.add_argument("--workers", type=int, default=2,
                        help="Max concurrent tasks in flight (rate limiter)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be dispatched, don't send")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        print(f"ERROR: source-dir does not exist: {source_dir}", file=sys.stderr)
        sys.exit(1)

    csv_files = sorted(source_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {source_dir}")
        sys.exit(0)

    print(f"Found {len(csv_files)} CSV files for {args.symbol}")

    if args.dry_run:
        for f in csv_files[:5]:
            print(f"  would ingest: {f.name}")
        if len(csv_files) > 5:
            print(f"  ... and {len(csv_files) - 5} more")
        return

    from backend.worker.celery import app as celery_app
    in_flight = []
    dispatched = 0
    errors = 0

    for csv_path in csv_files:
        task = celery_app.send_task(
            "worker.tasks_intraday.ingest_intraday_csv",
            args=[args.symbol, str(csv_path), args.format],
            queue="uploads",
        )
        in_flight.append(task)
        dispatched += 1

        while len(in_flight) >= args.workers:
            done = [t for t in in_flight if t.ready()]
            for t in done:
                in_flight.remove(t)
                if t.failed():
                    errors += 1
                    print(f"FAILED: {t.id}", file=sys.stderr)
            if not done:
                time.sleep(0.5)

        if dispatched % 100 == 0:
            print(f"  dispatched {dispatched}/{len(csv_files)}...")

    for t in in_flight:
        try:
            t.get(timeout=300)
        except Exception as e:
            errors += 1
            print(f"FAILED: {t.id}: {e}", file=sys.stderr)

    print(f"\nDone. dispatched={dispatched} errors={errors}")


if __name__ == "__main__":
    main()
