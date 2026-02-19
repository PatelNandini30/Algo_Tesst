"""
Backtest Data Diagnostics
Run this script directly: python diagnose_backtest.py
It will tell you EXACTLY what is wrong with your data.
"""

import os
import pandas as pd
from pathlib import Path

# ── EDIT THESE TO MATCH YOUR SETUP ────────────────────────────────────────────
PROJECT_ROOT    = r'E:\Algo_Test_Software'
TEST_DATE_RANGE = ('2025-10-01', '2026-01-10')   # the range that's failing
TEST_SYMBOL     = 'NIFTY'
TEST_INSTRUMENT = 'OPTIDX'   # try 'OPTSTK' if OPTIDX finds nothing
# ──────────────────────────────────────────────────────────────────────────────

CLEANED_CSV_DIR = os.path.join(PROJECT_ROOT, 'cleaned_csvs')
EXPIRY_DATA_DIR = os.path.join(PROJECT_ROOT, 'expiryData')
STRIKE_DATA_DIR = os.path.join(PROJECT_ROOT, 'strikeData')

SEP = "=" * 65

def section(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

# ── 1. CLEANED CSV FILE COVERAGE ──────────────────────────────────────────────
section("1. CLEANED CSV FILE COVERAGE")

all_files = sorted(Path(CLEANED_CSV_DIR).glob("*.csv"))
if not all_files:
    print("❌  NO CSV files found in cleaned_csvs directory!")
    print(f"    Looked in: {CLEANED_CSV_DIR}")
else:
    print(f"✅  Total bhavcopy files found: {len(all_files)}")
    print(f"    First file : {all_files[0].name}")
    print(f"    Last file  : {all_files[-1].name}")

    # Detect filename date format from first file
    sample_name = all_files[0].stem   # e.g. "2020-01-02" or "02-01-2020"
    print(f"\n    Sample filename (no ext): '{sample_name}'")

    # Check coverage for the failing date range
    from_dt = pd.to_datetime(TEST_DATE_RANGE[0])
    to_dt   = pd.to_datetime(TEST_DATE_RANGE[1])
    current = from_dt
    missing_files = []

    # Try to infer date format from first filename
    stem = all_files[0].stem
    for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%Y%m%d', '%d%m%Y']:
        try:
            pd.to_datetime(stem, format=fmt)
            detected_fmt = fmt
            break
        except:
            detected_fmt = None

    print(f"    Detected filename date format: {detected_fmt}")

    while current <= to_dt:
        if current.weekday() < 5:   # skip weekends
            if detected_fmt:
                fname = current.strftime(detected_fmt) + ".csv"
            else:
                fname = current.strftime('%Y-%m-%d') + ".csv"
            fpath = os.path.join(CLEANED_CSV_DIR, fname)
            if not os.path.exists(fpath):
                missing_files.append(current.strftime('%Y-%m-%d'))
        current += pd.Timedelta(days=1)

    if missing_files:
        print(f"\n⚠️   Missing bhavcopy files in {TEST_DATE_RANGE[0]} → {TEST_DATE_RANGE[1]}:")
        for m in missing_files[:30]:
            print(f"    • {m}")
        if len(missing_files) > 30:
            print(f"    ... and {len(missing_files)-30} more")
    else:
        print(f"\n✅  No missing bhavcopy files in test date range")

# ── 2. INSPECT A SAMPLE BHAVCOPY FILE ─────────────────────────────────────────
section("2. BHAVCOPY CONTENT CHECK (last available file)")

if all_files:
    sample_file = all_files[-1]
    print(f"    Reading: {sample_file.name}")
    try:
        df = pd.read_csv(sample_file)
        print(f"    Columns  : {df.columns.tolist()}")
        print(f"    Row count: {len(df)}")

        if 'Symbol' in df.columns:
            print(f"\n    Unique Symbols (first 15): {df['Symbol'].unique()[:15].tolist()}")

        if 'Instrument' in df.columns:
            print(f"    Unique Instruments       : {df['Instrument'].unique().tolist()}")

        # Check for NIFTY specifically
        nifty_rows = df[df['Symbol'] == TEST_SYMBOL] if 'Symbol' in df.columns else pd.DataFrame()
        print(f"\n    Rows where Symbol == '{TEST_SYMBOL}': {len(nifty_rows)}")

        if not nifty_rows.empty and 'Instrument' in nifty_rows.columns:
            print(f"    NIFTY Instruments found : {nifty_rows['Instrument'].unique().tolist()}")
        if not nifty_rows.empty and 'OptionType' in nifty_rows.columns:
            print(f"    NIFTY OptionTypes found : {nifty_rows['OptionType'].unique().tolist()}")
        if not nifty_rows.empty and 'ExpiryDate' in nifty_rows.columns:
            print(f"    NIFTY Expiry dates      : {sorted(nifty_rows['ExpiryDate'].unique())[:10]}")
        if not nifty_rows.empty and 'StrikePrice' in nifty_rows.columns:
            strikes = sorted(nifty_rows['StrikePrice'].unique())
            print(f"    NIFTY Strike range      : {strikes[0]} → {strikes[-1]} ({len(strikes)} strikes)")

    except Exception as e:
        print(f"❌  Failed to read file: {e}")

# ── 3. CHECK A SPECIFIC FAILING DATE ──────────────────────────────────────────
section(f"3. SPOT-CHECK: 2025-12-09 bhavcopy (one of your warning dates)")

# Try all common filename formats for 2025-12-09
test_date = pd.Timestamp('2025-12-09')
test_formats = ['%Y-%m-%d', '%d-%m-%Y', '%Y%m%d', '%d%m%Y', '%d-%b-%Y']
found_path = None

for fmt in test_formats:
    fname = test_date.strftime(fmt) + ".csv"
    fpath = os.path.join(CLEANED_CSV_DIR, fname)
    if os.path.exists(fpath):
        found_path = fpath
        print(f"✅  Found as: {fname}")
        break
    else:
        print(f"    Not found: {fname}")

if found_path:
    df = pd.read_csv(found_path)
    nifty_ce = df[
        (df.get('Symbol', pd.Series()) == TEST_SYMBOL) &
        (df.get('Instrument', pd.Series()) == TEST_INSTRUMENT) &
        (df.get('OptionType', pd.Series()) == 'CE')
    ] if all(c in df.columns for c in ['Symbol','Instrument','OptionType']) else pd.DataFrame()

    print(f"    NIFTY OPTIDX CE rows: {len(nifty_ce)}")
    if not nifty_ce.empty:
        print(f"    Strike range: {nifty_ce['StrikePrice'].min()} → {nifty_ce['StrikePrice'].max()}")
        # Check specifically for strike 25800 (one of the warning strikes)
        near_25800 = nifty_ce[abs(nifty_ce['StrikePrice'] - 25800) <= 100]
        print(f"    Rows near strike 25800 (±100): {len(near_25800)}")
else:
    print(f"❌  File for 2025-12-09 not found in any format!")

# ── 4. EXPIRY DATA CHECK ───────────────────────────────────────────────────────
section("4. EXPIRY DATA COVERAGE")

for fname in [f"{TEST_SYMBOL}.csv", f"{TEST_SYMBOL}_Monthly.csv"]:
    fpath = os.path.join(EXPIRY_DATA_DIR, fname)
    if os.path.exists(fpath):
        df = pd.read_csv(fpath)
        # Parse dates
        if 'Current Expiry' in df.columns:
            for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%d-%b-%Y']:
                try:
                    df['Current Expiry'] = pd.to_datetime(df['Current Expiry'], format=fmt)
                    break
                except: continue
            print(f"✅  {fname}")
            print(f"    Rows: {len(df)}")
            print(f"    First expiry: {df['Current Expiry'].min()}")
            print(f"    Last expiry : {df['Current Expiry'].max()}")
            gap = df['Current Expiry'].max()
            if gap < pd.Timestamp('2025-10-01'):
                print(f"⚠️   EXPIRY DATA ENDS BEFORE OCT 2025 — this will cause missing trades!")
    else:
        print(f"❌  Not found: {fpath}")

# ── 5. STRIKE DATA CHECK ───────────────────────────────────────────────────────
section("5. STRIKE DATA COVERAGE")

for fname in [
    f"{TEST_SYMBOL}_strike_data.csv",
    f"{TEST_SYMBOL.lower()}_strike_data.csv",
    f"{TEST_SYMBOL.capitalize()}_strike_data.csv",
]:
    fpath = os.path.join(STRIKE_DATA_DIR, fname)
    if os.path.exists(fpath):
        df = pd.read_csv(fpath)
        for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%d-%b-%Y']:
            try:
                df['Date'] = pd.to_datetime(df['Date'], format=fmt)
                break
            except: continue
        print(f"✅  {fname}")
        print(f"    Rows      : {len(df)}")
        print(f"    Date range: {df['Date'].min()} → {df['Date'].max()}")
        print(f"    Tickers   : {df['Ticker'].unique().tolist() if 'Ticker' in df.columns else 'No Ticker col'}")
        if df['Date'].max() < pd.Timestamp('2025-10-01'):
            print(f"⚠️   STRIKE DATA ENDS BEFORE OCT 2025 — this is likely causing your warnings!")
        break
else:
    print(f"❌  No strike data file found for {TEST_SYMBOL}")

# ── 6. OPTION PRICE LOOKUP SIMULATION ─────────────────────────────────────────
section("6. OPTION LOOKUP SIMULATION (mimics get_option_price)")

print("    Simulating lookup for: NIFTY OPTIDX CE strike=25800 expiry=2025-12-16")

# Find the Dec 9 file
for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%Y%m%d']:
    fname = pd.Timestamp('2025-12-09').strftime(fmt) + ".csv"
    fpath = os.path.join(CLEANED_CSV_DIR, fname)
    if os.path.exists(fpath):
        df = pd.read_csv(fpath)
        df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'], dayfirst=True, errors='coerce')
        target_expiry = pd.Timestamp('2025-12-16')
        target_strike = 25800

        mask = (
            (df['Symbol'] == TEST_SYMBOL) &
            (df['Instrument'] == TEST_INSTRUMENT) &
            (df['OptionType'] == 'CE') &
            (abs(df['StrikePrice'] - target_strike) <= 0.5)
        )
        exact = df[mask & (df['ExpiryDate'] == target_expiry)]
        print(f"    Exact match rows  : {len(exact)}")

        # Show what expiries ARE available for NIFTY on this day
        nifty_rows = df[(df['Symbol'] == TEST_SYMBOL) & (df['Instrument'] == TEST_INSTRUMENT)]
        if not nifty_rows.empty:
            available_expiries = sorted(nifty_rows['ExpiryDate'].dropna().unique())
            print(f"    Available expiries: {[str(e)[:10] for e in available_expiries[:8]]}")
            available_strikes  = sorted(nifty_rows[nifty_rows['OptionType']=='CE']['StrikePrice'].unique())
            print(f"    CE strikes avail  : {available_strikes[:10]} ... {available_strikes[-5:] if len(available_strikes)>5 else ''}")
        else:
            print(f"⚠️   NO NIFTY {TEST_INSTRUMENT} rows found in this file at all!")
            print(f"    Instruments in file: {df['Instrument'].unique().tolist() if 'Instrument' in df.columns else 'N/A'}")
        break

# ── SUMMARY ───────────────────────────────────────────────────────────────────
section("DIAGNOSIS COMPLETE")
print("""
  Next steps based on results above:
  ─────────────────────────────────────────────────────────
  If Section 1 shows missing files  → Download missing bhavcopy CSVs
  If Section 2 shows no NIFTY rows  → Wrong instrument type (OPTIDX vs OPTSTK)
  If Section 3 shows file not found → Filename format mismatch in load_bhavcopy()
  If Section 4 expiry ends early    → Update NIFTY.csv with recent expiry dates  
  If Section 5 strike data ends early → Update nifty_strike_data.csv
  If Section 6 shows 0 exact matches → ExpiryDate format mismatch in bhavcopy CSV
""")