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
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import CLEANED_CSV_DIR, STRIKE_DATA_DIR, EXPIRY_DATA_DIR, engine
from backend.repositories.market_data_repository import MarketDataRepository


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

    print("\n== Spot comparison ==")
    for s in args.symbols:
        c = _csv_spot(s, args.from_date, args.to_date)
        p = repo.get_spot_data(s, args.from_date, args.to_date)
        print(
            f"{s}: csv_rows={len(c)} pg_rows={len(p)} "
            f"csv_close_sum={round(c['Close'].sum(),2) if 'Close' in c else 0} "
            f"pg_close_sum={round(p['Close'].sum(),2) if 'Close' in p else 0}"
        )

    print("\n== Expiry comparison ==")
    for s in args.symbols:
        for t in ("weekly", "monthly"):
            c = _csv_expiry(s, t)
            p = repo.get_expiry_data(s, t)
            print(f"{s}-{t}: csv_rows={len(c)} pg_rows={len(p)}")


if __name__ == "__main__":
    main()
