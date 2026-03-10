#!/usr/bin/env python
"""
Quick parity check between CSV source files and PostgreSQL tables.

Compares:
- option_data vs cleaned_csvs for selected dates
- spot_data vs strikeData for selected symbols/date ranges
- expiry_calendar vs expiryData for selected symbols/types
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import CLEANED_CSV_DIR, STRIKE_DATA_DIR, EXPIRY_DATA_DIR, engine
from backend.repositories.market_data_repository import MarketDataRepository

FILTER_DIR = ROOT / "Filter"


def _parse_str_date(raw_value: Any) -> Optional[datetime]:
    """Replicate the parsing logic used by the base STR loader."""
    if pd.isna(raw_value):
        return None
    text = str(raw_value).strip()
    if not text:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _csv_super_trend(config_key: str) -> list:
    """Load STR segments directly from the legacy CSV, mirroring the Base helper."""
    file_map = {
        "5x1": FILTER_DIR / "STR5,1_5,1.csv",
        "5x2": FILTER_DIR / "STR5,2_5,2.csv",
    }
    path = file_map.get(config_key)
    if path is None or not path.exists():
        return []

    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    if "Start" not in df.columns or "End" not in df.columns:
        return []

    segments = []
    for _, row in df.iterrows():
        start_dt = _parse_str_date(row.get("Start"))
        end_dt = _parse_str_date(row.get("End"))
        if start_dt is None or end_dt is None or end_dt < start_dt:
            continue
        segments.append({"start": start_dt, "end": end_dt})
    segments.sort(key=lambda s: s["start"])
    return segments


def _csv_bhavcopy(date_str: str) -> pd.DataFrame:
    p = Path(CLEANED_CSV_DIR) / f"{date_str}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["Date"])
    df["ExpiryDate"] = pd.to_datetime(df["ExpiryDate"])
    keep = ["Instrument", "Symbol", "ExpiryDate", "OptionType", "StrikePrice", "Close", "TurnOver", "Date"]
    return df[[c for c in keep if c in df.columns]].copy()


def _csv_spot(symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
    p = Path(STRIKE_DATA_DIR) / f"{symbol}_strike_data.csv"
    if not p.exists():
        return pd.DataFrame(columns=["Date", "Close"])
    df = pd.read_csv(p)
    if df.empty:
        return pd.DataFrame(columns=["Date", "Close"])
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df[(df["Date"] >= pd.to_datetime(from_date)) & (df["Date"] <= pd.to_datetime(to_date))]
    if "Ticker" in df.columns:
        df = df[df["Ticker"].str.upper() == symbol.upper()]
    return df[["Date", "Close"]].reset_index(drop=True)


def _csv_expiry(symbol: str, expiry_type: str) -> pd.DataFrame:
    fn = f"{symbol}.csv" if expiry_type == "weekly" else f"{symbol}_Monthly.csv"
    p = Path(EXPIRY_DATA_DIR) / fn
    if not p.exists():
        return pd.DataFrame(columns=["Previous Expiry", "Current Expiry", "Next Expiry"])
    df = pd.read_csv(p)
    for c in ["Previous Expiry", "Current Expiry", "Next Expiry"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], dayfirst=True, errors="coerce")
    return df[["Previous Expiry", "Current Expiry", "Next Expiry"]].sort_values("Current Expiry").reset_index(drop=True)


def _compare_df(name: str, csv_df: pd.DataFrame, pg_df: pd.DataFrame, key_columns: list):
    """Print count/aggregate/sample comparison between CSV and Postgres results."""
    print(f"\n{name} comparison:")
    csv_count = len(csv_df)
    pg_count = len(pg_df)
    print(f"  rows -> csv={csv_count} pg={pg_count}  match={'YES' if csv_count == pg_count else 'NO'}")

    csv_close = pg_close = 0.0
    if "Close" in csv_df.columns or "Close" in pg_df.columns:
        csv_close = float(csv_df["Close"].sum()) if "Close" in csv_df.columns else 0.0
        pg_close = float(pg_df["Close"].sum()) if "Close" in pg_df.columns else 0.0
    print(f"  close sum -> csv={csv_close:.2f} pg={pg_close:.2f}")

    def _print_sample(prefix: str, df: pd.DataFrame):
        if df.empty:
            return f"{prefix}: <empty>"
        available_keys = [k for k in key_columns if k in df.columns]
        row = df.iloc[0][available_keys].to_dict() if available_keys else df.iloc[0].to_dict()
        return f"{prefix}: {row}"

    print("  sample csv/pg:")
    print(f"    {_print_sample('csv', csv_df)}")
    print(f"    {_print_sample('pg ', pg_df)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=["2025-06-12", "2025-01-02"])
    ap.add_argument("--symbols", nargs="+", default=["NIFTY", "BANKNIFTY"])
    ap.add_argument("--from-date", default="2024-01-01")
    ap.add_argument("--to-date", default="2025-01-31")
    args = ap.parse_args()

    repo = MarketDataRepository(engine)
    try:
        # Fail fast with clear message if PostgreSQL is unavailable.
        _ = repo.get_available_date_range()
    except Exception as e:
        print(f"PostgreSQL connection unavailable: {e}")
        print("Start postgres and re-run this script.")
        return

    print("== Bhavcopy comparison ==")
    for d in args.dates:
        c = _csv_bhavcopy(d)
        p = repo.get_bhavcopy_by_date(d)
        print(
            f"{d}: csv_rows={len(c)} pg_rows={len(p)} "
            f"csv_close_sum={round(c['Close'].sum(),2) if 'Close' in c else 0} "
            f"pg_close_sum={round(p['Close'].sum(),2) if 'Close' in p else 0}"
        )
        _compare_df(f"Bhavcopy {d}", c, p, ["Instrument", "Symbol", "OptionType", "StrikePrice", "ExpiryDate"])

    print("\n== Spot comparison ==")
    for s in args.symbols:
        c = _csv_spot(s, args.from_date, args.to_date)
        p = repo.get_spot_data(s, args.from_date, args.to_date)
        print(
            f"{s}: csv_rows={len(c)} pg_rows={len(p)} "
            f"csv_close_sum={round(c['Close'].sum(),2) if 'Close' in c else 0} "
            f"pg_close_sum={round(p['Close'].sum(),2) if 'Close' in p else 0}"
        )
        _compare_df(f"Spot {s}", c, p, ["Date", "Close"])

    print("\n== Expiry comparison ==")
    for s in args.symbols:
        for t in ("weekly", "monthly"):
            c = _csv_expiry(s, t)
            p = repo.get_expiry_data(s, t)
            print(f"{s}-{t}: csv_rows={len(c)} pg_rows={len(p)}")
            _compare_df(f"Expiry {s}-{t}", c, p, ["Current Expiry", "Next Expiry"])

    print("\n== Super Trend comparison ==")
    for config_key in ("5x1", "5x2"):
        csv_segments = _csv_super_trend(config_key)
        pg_segments_df = repo.get_super_trend_segments(config=config_key)
        pg_segments = []
        if not pg_segments_df.empty:
            for _, row in pg_segments_df.iterrows():
                start = pd.Timestamp(row["start_date"]).to_pydatetime()
                end = pd.Timestamp(row["end_date"]).to_pydatetime()
                if end >= start:
                    pg_segments.append({"start": start, "end": end})

        print(
            f"{config_key}: csv_segments={len(csv_segments)} pg_segments={len(pg_segments)} "
            f"csv_first={csv_segments[0] if csv_segments else 'N/A'} "
            f"pg_first={pg_segments[0] if pg_segments else 'N/A'}"
        )


if __name__ == "__main__":
    main()
