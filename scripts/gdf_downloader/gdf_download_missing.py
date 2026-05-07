"""
GlobalDataFeed REST API — Missing Options Data Downloader
=========================================================
Downloads 1-minute NIFTY options data for specific missing trading days.
Writes to OUTPUT_DIR only — never touches original data.

SETUP:
  1. Fill in GDF_ENDPOINT, GDF_PORT, GDF_ACCESSKEY below
  2. Run:  python3 gdf_download_missing.py

GDF API format:
  http://{GDF_ENDPOINT}:{GDF_PORT}/GetHistory/?accesskey={GDF_ACCESSKEY}&...
"""

import os
import csv
import time
import requests
from datetime import datetime, date, timedelta

# ─────────────────────────────────────────────
# FILL THESE IN (from your GDF subscription email)
# ─────────────────────────────────────────────
GDF_ENDPOINT  = "YOUR_ENDPOINT_HERE"   # e.g. "datafeed.globaldatafeeds.in"
GDF_PORT      = "YOUR_PORT_HERE"       # e.g. "8085"
GDF_ACCESSKEY = "YOUR_API_KEY_HERE"    # e.g. "abc123xyz"

# Output directory — new folder, no existing data touched
OUTPUT_DIR = "/home/user/Algo_Test_Software/scripts/gdf_downloader/downloaded_data"

# ─────────────────────────────────────────────
# MISSING DATES: expiry → [trading days needed]
# ─────────────────────────────────────────────
MISSING = {
    "09JAN25": ["2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"],
    "16JAN25": ["2025-01-10"],
    "23JAN25": ["2025-01-17"],
    "30JAN25": ["2025-01-24"],
    "07NOV24": ["2024-11-01"],
    "25SEP25": ["2025-09-24", "2025-09-25"],
    "24DEC25": ["2025-12-24"],
    "26MAR26": ["2026-03-25"],
}

# NIFTY strike step and approximate ATM range to download
# We download ATM ± 50 strikes = ±2500 points either side of expected ATM
STRIKE_STEP   = 50
STRIKES_EACH_SIDE = 50   # 50 × 50 = 2500 pts each side → 101 strikes total

# Approximate ATM per expiry date (update if wrong — engine uses actual ATM from data)
APPROX_ATM = {
    "09JAN25": 23750,
    "16JAN25": 23200,
    "23JAN25": 23200,
    "30JAN25": 23300,
    "07NOV24": 24000,
    "25SEP25": 25500,
    "24DEC25": 24000,
    "26MAR26": 23500,
}

# ─────────────────────────────────────────────
BASE_URL = f"http://{GDF_ENDPOINT}:{GDF_PORT}"


def ts(dt: datetime) -> int:
    """Convert datetime to UNIX timestamp (IST = UTC+5:30)."""
    import calendar
    epoch = datetime(1970, 1, 1)
    # IST offset = 5.5 hours
    return int((dt - epoch).total_seconds()) - 19800  # subtract IST offset


def day_range_ts(date_str: str):
    """Return (from_ts, to_ts) for 09:00 to 15:35 on a given date."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    from_dt = d.replace(hour=9,  minute=0,  second=0)
    to_dt   = d.replace(hour=15, minute=35, second=0)
    return ts(from_dt), ts(to_dt)


def get_strikes_from_api(expiry_code: str, approx_atm: int) -> list:
    """
    Try to get strike list from GDF GetStrikePrices.
    Falls back to generated list if API unavailable.
    """
    url = f"{BASE_URL}/GetStrikePrices/"
    params = {
        "accesskey": GDF_ACCESSKEY,
        "Exchange":  "NFO",
        "Symbol":    "NIFTY",
        "Expiry":    expiry_code,   # e.g. "09JAN25"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if isinstance(data, list) and data:
            return sorted(set(int(s) for s in data if s))
    except Exception:
        pass

    # Fallback: generate ±50 strikes around approx ATM
    atm_rounded = round(approx_atm / STRIKE_STEP) * STRIKE_STEP
    return list(range(
        atm_rounded - STRIKES_EACH_SIDE * STRIKE_STEP,
        atm_rounded + STRIKES_EACH_SIDE * STRIKE_STEP + STRIKE_STEP,
        STRIKE_STEP
    ))


def fetch_minute_data(instrument: str, from_ts: int, to_ts: int) -> list:
    """
    Call GDF GetHistory for 1-minute OHLCV data.
    Returns list of row dicts.
    """
    url = f"{BASE_URL}/GetHistory/"
    params = {
        "accesskey":            GDF_ACCESSKEY,
        "Exchange":             "NFO",
        "InstrumentIdentifier": instrument,
        "Periodicity":          "MINUTE",
        "Period":               1,
        "From":                 from_ts,
        "To":                   to_ts,
        "isShortIdentifier":    "False",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "d" in data:
            return data["d"]
        if isinstance(data, list):
            return data
    except requests.exceptions.RequestException as e:
        print(f"    HTTP error for {instrument}: {e}")
    except Exception as e:
        print(f"    Parse error for {instrument}: {e}")
    return []


def parse_expiry_date(expiry_code: str) -> str:
    """Convert '09JAN25' → '2025-01-09'"""
    return datetime.strptime(expiry_code, "%d%b%y").strftime("%Y-%m-%d")


def gdf_ts_to_datetime(ts_val) -> tuple:
    """Convert GDF LastTradeTime (UNIX ts) to (date_str, time_str)."""
    dt = datetime.utcfromtimestamp(int(ts_val)) + timedelta(hours=5, minutes=30)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")


def write_csv(rows: list, filepath: str, instrument: str, expiry_date: str):
    """Write downloaded rows to CSV in GDF ticker-wise format."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Ticker", "Date", "Time", "Expiry Date",
            "Open", "High", "Low", "Close",
            "Volume", "Open Interest", "Padding Flag"
        ])
        for row in rows:
            date_str, time_str = gdf_ts_to_datetime(row.get("LastTradeTime", 0))
            writer.writerow([
                f"{instrument}.NFO",
                date_str,
                time_str,
                expiry_date,
                row.get("Open",  0),
                row.get("High",  0),
                row.get("Low",   0),
                row.get("Close", 0),
                row.get("TradedQty",    0),
                row.get("OpenInterest", 0),
                0,   # Padding Flag = 0 (real data)
            ])


def main():
    if "YOUR_" in GDF_ENDPOINT or "YOUR_" in GDF_ACCESSKEY:
        print("ERROR: Please fill in GDF_ENDPOINT, GDF_PORT, GDF_ACCESSKEY at the top of this script.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total_files = 0
    total_rows  = 0

    for expiry_code, trading_days in sorted(MISSING.items()):
        expiry_date  = parse_expiry_date(expiry_code)
        approx_atm   = APPROX_ATM.get(expiry_code, 23500)
        strikes      = get_strikes_from_api(expiry_code, approx_atm)
        expiry_dir   = os.path.join(OUTPUT_DIR, expiry_code)

        print(f"\n{'='*60}")
        print(f"Expiry: {expiry_code} ({expiry_date})  ATM≈{approx_atm}  Strikes: {len(strikes)}")
        print(f"Trading days: {trading_days}")

        for trading_day in trading_days:
            from_ts, to_ts = day_range_ts(trading_day)
            print(f"\n  📅 {trading_day}")

            for strike in strikes:
                for opt_type in ["CE", "PE"]:
                    instrument = f"NIFTY{expiry_code}{strike}{opt_type}"
                    filename   = f"{instrument}.csv"
                    filepath   = os.path.join(expiry_dir, filename)

                    # Skip if already downloaded
                    if os.path.exists(filepath):
                        rows_existing = sum(1 for _ in open(filepath)) - 1
                        if rows_existing > 0:
                            continue

                    rows = fetch_minute_data(instrument, from_ts, to_ts)

                    if rows:
                        write_csv(rows, filepath, instrument, expiry_date)
                        total_files += 1
                        total_rows  += len(rows)
                        print(f"    ✅ {instrument}: {len(rows)} rows")
                    else:
                        pass  # option not traded — normal for deep OTM

                    time.sleep(0.05)  # 50ms between calls — stay within rate limit

    print(f"\n{'='*60}")
    print(f"DONE. Files written: {total_files}  Total rows: {total_rows}")
    print(f"Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
