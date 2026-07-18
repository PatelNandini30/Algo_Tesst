#!/usr/bin/env python3
"""
import_filter_dates.py — folder-based filter-date importer.

Walks a "Filter Dates" root that contains one sub-folder per UI *group* and one
CSV per selectable *filter*, and (re)loads them into the `filter_date_sets`
table. This REPLACES the old 5x1 / 5x2 / base2 model that lived in
`super_trend_segments`.

Rules (fixed by product decision):
  * one filter per CSV, grouped by folder,
  * the folder name is the group label, the file name (sans .csv) is the
    filter label -- both kept EXACTLY as on disk,
  * dates are stored verbatim (CSV is day-first dd-mm-yyyy),
  * each CSV has a header of either `Start,End` or `Entry,Exit` (same meaning),
  * the whole table is truncated and rebuilt on every run (idempotent).

Usage (inside the backend container, DB reachable as `postgres`):
    python import_filter_dates.py                 # default root: ./filter_dates
    python import_filter_dates.py --root /some/dir
    python import_filter_dates.py --dry-run
"""
import argparse
import csv
import os
import re
import sys
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from sqlalchemy import create_engine, text  # noqa: E402
from database import DATABASE_URL  # noqa: E402

DEFAULT_ROOT = os.path.join(_HERE, "filter_dates")

# Header pairs we accept (lower-cased). Both encode a [start, end] range.
_START_KEYS = ("start", "entry", "from", "start_date")
_END_KEYS = ("end", "exit", "to", "end_date")

_DATE_FMTS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%Y")


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _parse_date(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _read_csv_segments(path: str):
    """Return [(seq, start_date, end_date), ...] for one filter CSV."""
    segs = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return segs
    header = [(c or "").strip().lower() for c in rows[0]]
    # Locate the start/end columns from the header; default to first two cols.
    s_idx = next((i for i, h in enumerate(header) if h in _START_KEYS), 0)
    e_idx = next((i for i, h in enumerate(header) if h in _END_KEYS), 1)
    seq = 0
    for r in rows[1:]:
        if len(r) <= max(s_idx, e_idx):
            continue
        sd = _parse_date(r[s_idx])
        ed = _parse_date(r[e_idx])
        if sd is None or ed is None:
            raise ValueError(f"unparseable dates {r[s_idx]!r},{r[e_idx]!r} in {path}")
        if ed < sd:
            raise ValueError(f"end<start ({sd}..{ed}) in {path}")
        seq += 1
        segs.append((seq, sd, ed))
    return segs


def discover(root: str):
    """Yield group/filter records from the folder tree."""
    if not os.path.isdir(root):
        raise SystemExit(f"root not found: {root}")
    records = []
    for g_order, folder in enumerate(sorted(os.listdir(root))):
        gdir = os.path.join(root, folder)
        if not os.path.isdir(gdir):
            continue
        group_key = _slug(folder)
        for f_order, fname in enumerate(sorted(os.listdir(gdir))):
            if not fname.lower().endswith(".csv"):
                continue
            fpath = os.path.join(gdir, fname)
            label = fname[:-4]  # strip .csv, keep exact name
            filter_key = f"{group_key}__{_slug(label)}"
            segs = _read_csv_segments(fpath)
            records.append({
                "group_key": group_key,
                "group_label": folder,
                "group_order": g_order,
                "filter_key": filter_key,
                "filter_label": label,
                "filter_order": f_order,
                "source_file": fname,
                "segments": segs,
            })
    return records


def main():
    ap = argparse.ArgumentParser(description="Folder-based filter-date importer")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    records = discover(args.root)
    total_rows = sum(len(r["segments"]) for r in records)
    print(f"root: {args.root}")
    print(f"groups: {len({r['group_key'] for r in records})}  "
          f"filters: {len(records)}  segments: {total_rows}")
    for r in records:
        print(f"  [{r['group_label']}] {r['filter_label']}  "
              f"-> key={r['filter_key']}  rows={len(r['segments'])}")

    # Guard against duplicate filter_keys (would violate uniqueness of a filter).
    keys = [r["filter_key"] for r in records]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise SystemExit(f"duplicate filter_key(s): {sorted(dupes)}")

    if args.dry_run:
        print("dry-run: no DB writes")
        return

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE filter_date_sets RESTART IDENTITY"))
        ins = text(
            "INSERT INTO filter_date_sets "
            "(group_key, group_label, group_order, filter_key, filter_label, "
            " filter_order, source_file, seq, start_date, end_date) VALUES "
            "(:gk, :gl, :go, :fk, :fl, :fo, :sf, :seq, :sd, :ed)"
        )
        payload = []
        for r in records:
            for seq, sd, ed in r["segments"]:
                payload.append({
                    "gk": r["group_key"], "gl": r["group_label"], "go": r["group_order"],
                    "fk": r["filter_key"], "fl": r["filter_label"], "fo": r["filter_order"],
                    "sf": r["source_file"], "seq": seq, "sd": sd, "ed": ed,
                })
        if payload:
            conn.execute(ins, payload)
    print(f"OK: wrote {total_rows} segments across {len(records)} filters")


if __name__ == "__main__":
    main()
