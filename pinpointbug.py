"""
Pinpoint Bug Diagnostic - FIXED VERSION
Run: python pinpoint_bug_v2.py
"""

import os
import pandas as pd
import glob

PROJECT_ROOT    = r'E:\Algo_Test_Software'
CLEANED_CSV_DIR = os.path.join(PROJECT_ROOT, 'cleaned_csvs')
STRIKE_DATA_DIR = os.path.join(PROJECT_ROOT, 'strikeData')

SEP = "=" * 65

call_log = []

def get_option_price_debug(bhavcopy_df, symbol, instrument, option_type, expiry, strike):
    expiry_ts = pd.Timestamp(expiry)
    mask = (
        (bhavcopy_df['Symbol']     == symbol)     &
        (bhavcopy_df['Instrument'] == instrument) &
        (bhavcopy_df['OptionType'] == option_type)&
        (abs(bhavcopy_df['StrikePrice'] - strike) <= 0.5)
    )
    exact = bhavcopy_df[mask & (bhavcopy_df['ExpiryDate'] == expiry_ts)]
    result = "HIT" if not exact.empty else "MISS"
    entry = {
        "symbol": symbol, "instrument": instrument,
        "option_type": option_type, "expiry_passed": str(expiry_ts),
        "strike": strike, "result": result,
    }
    if exact.empty:
        sym_rows  = bhavcopy_df[bhavcopy_df['Symbol'] == symbol]
        inst_rows = sym_rows[sym_rows['Instrument'] == instrument]
        ot_rows   = inst_rows[inst_rows['OptionType'] == option_type]
        if sym_rows.empty:
            reason = f"Symbol '{symbol}' not found. Available: {bhavcopy_df['Symbol'].unique()[:5].tolist()}"
        elif inst_rows.empty:
            reason = f"Instrument '{instrument}' not found. Available: {sym_rows['Instrument'].unique().tolist()}"
        elif ot_rows.empty:
            reason = f"OptionType '{option_type}' not found. Available: {inst_rows['OptionType'].unique().tolist()}"
        else:
            close_strikes = sorted(ot_rows['StrikePrice'].unique())
            nearest = min(close_strikes, key=lambda x: abs(x - strike))
            avail_expiries = [str(e)[:10] for e in sorted(ot_rows['ExpiryDate'].unique())[:6]]
            reason = f"Strike {strike} nearest={nearest} OR expiry mismatch. Available expiries: {avail_expiries}"
        entry["miss_reason"] = reason
    call_log.append(entry)
    if not exact.empty:
        row = exact.iloc[0]
        return float(row['Close']), float(row['TurnOver']) if pd.notna(row['TurnOver']) else None
    tol = bhavcopy_df[mask & (
        (bhavcopy_df['ExpiryDate'] == expiry_ts + pd.Timedelta(days=1)) |
        (bhavcopy_df['ExpiryDate'] == expiry_ts - pd.Timedelta(days=1))
    )]
    if not tol.empty:
        call_log[-1]["result"] = "HIT_TOLERANCE"
        row = tol.iloc[0]
        return float(row['Close']), float(row['TurnOver']) if pd.notna(row['TurnOver']) else None
    return None, None


# ── LOAD BHAVCOPY FOR DEC 9 ────────────────────────────────────────────────────
print(SEP)
print("  LOADING 2025-12-09 BHAVCOPY")
print(SEP)
file_path = os.path.join(CLEANED_CSV_DIR, '2025-12-09.csv')
df = pd.read_csv(file_path)
df['Date']       = pd.to_datetime(df['Date'])
df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])
print(f"Loaded {len(df)} rows")

# ── LOAD STRIKE DATA WITH FIXED DATE PARSING ──────────────────────────────────
print(f"\n{SEP}")
print("  LOADING STRIKE DATA (fixed date parsing)")
print(SEP)
sk_path = os.path.join(STRIKE_DATA_DIR, 'Nifty_strike_data.csv')
sk = pd.read_csv(sk_path)
# Fix: use dayfirst=True to handle DD-MM-YYYY format
sk['Date'] = pd.to_datetime(sk['Date'], dayfirst=True)
print(f"Strike data loaded: {len(sk)} rows")
print(f"Date range: {sk['Date'].min().date()} → {sk['Date'].max().date()}")

# ── GET SPOT PRICE FOR DEC 9 ───────────────────────────────────────────────────
spot_row = sk[sk['Date'] == pd.Timestamp('2025-12-09')]
if spot_row.empty:
    # Try nearby dates
    nearby = sk[(sk['Date'] >= pd.Timestamp('2025-12-08')) &
                (sk['Date'] <= pd.Timestamp('2025-12-11'))]
    print(f"No exact spot for 2025-12-09. Nearby dates:\n{nearby[['Date','Close']]}")
    spot = nearby.iloc[-1]['Close'] if not nearby.empty else 25800
else:
    spot = float(spot_row.iloc[0]['Close'])
print(f"\nNIFTY Spot on 2025-12-09: {spot}")

# ── ATM CALCULATION TESTS ──────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  ATM STRIKE CALCULATION TEST")
print(SEP)

atm_50  = round(spot / 50)  * 50
atm_100 = round(spot / 100) * 100

print(f"Spot: {spot}")
print(f"ATM (rounded to 50)  : {atm_50}")
print(f"ATM (rounded to 100) : {atm_100}")

for strike_test in [atm_50, atm_100, spot]:
    price, _ = get_option_price_debug(df, 'NIFTY', 'OPTIDX', 'CE',
                                       pd.Timestamp('2025-12-16'), round(strike_test))
    status = "✅ FOUND" if price else "❌ MISS"
    print(f"  Strike {round(strike_test)}: {status}  price={price}")

# ── SCAN ALL STRATEGY FILES FOR get_option_price CALLS ───────────────────────
print(f"\n{SEP}")
print("  SCANNING ALL STRATEGY SCRIPTS")
print(SEP)

py_files = glob.glob(os.path.join(PROJECT_ROOT, '*.py'))
py_files = [f for f in py_files if 'diagnose' not in f.lower()
            and 'pinpoint' not in f.lower()
            and 'utils' not in f.lower()]

print(f"Found {len(py_files)} strategy scripts:")
for f in py_files:
    print(f"  {os.path.basename(f)}")

found_calls = False
for sf in py_files:
    try:
        with open(sf, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        for i, line in enumerate(lines, 1):
            if 'get_option_price' in line and not line.strip().startswith('#'):
                found_calls = True
                print(f"\n>>> [{os.path.basename(sf)}  line {i}]")
                start = max(0, i - 4)
                end   = min(len(lines), i + 3)
                for j in range(start, end):
                    marker = "==> " if j == i - 1 else "    "
                    print(f"  {marker}{j+1}: {lines[j].rstrip()}")
    except Exception as e:
        print(f"  Could not read {sf}: {e}")

if not found_calls:
    print("\n⚠️  No get_option_price calls found in root .py files!")
    print("   Searching subdirectories...")
    sub_files = glob.glob(os.path.join(PROJECT_ROOT, '**', '*.py'), recursive=True)
    sub_files = [f for f in sub_files
                 if 'diagnose' not in f.lower() and 'pinpoint' not in f.lower()]
    for sf in sub_files:
        try:
            with open(sf, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                if 'get_option_price' in line and not line.strip().startswith('#'):
                    found_calls = True
                    rel = os.path.relpath(sf, PROJECT_ROOT)
                    print(f"\n>>> [{rel}  line {i}]")
                    start = max(0, i - 4)
                    end   = min(len(lines), i + 3)
                    for j in range(start, end):
                        marker = "==> " if j == i - 1 else "    "
                        print(f"  {marker}{j+1}: {lines[j].rstrip()}")
        except:
            pass

if not found_calls:
    print("\n❌  get_option_price is never called anywhere!")
    print("   Your strategy script may be using a different function name.")
    print("   Searching for option price lookups by other names...")
    keywords = ['option_price', 'call_price', 'put_price', 'get_price',
                'bhavcopy', 'cleaned_csv', 'OPTIDX']
    for sf in sub_files:
        try:
            with open(sf, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines   = content.splitlines()
            for kw in keywords:
                if kw in content:
                    print(f"\n  File '{os.path.relpath(sf, PROJECT_ROOT)}' contains '{kw}'")
                    for i, line in enumerate(lines, 1):
                        if kw in line and not line.strip().startswith('#'):
                            print(f"    line {i}: {line.rstrip()}")
                    break
        except:
            pass

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  SUMMARY")
print(SEP)
hits   = [c for c in call_log if 'HIT' in c['result']]
misses = [c for c in call_log if c['result'] == 'MISS']
print(f"Test calls made : {len(call_log)}")
print(f"Hits            : {len(hits)}")
print(f"Misses          : {len(misses)}")
for m in misses:
    print(f"\n  MISS: {m['symbol']} {m['instrument']} {m['option_type']} "
          f"strike={m['strike']} expiry={m['expiry_passed'][:10]}")
    print(f"  Reason: {m.get('miss_reason','?')}")

print(f"""
{SEP}
  WHAT TO SHARE NEXT
{SEP}
  Please share the output above AND paste the content of
  whichever strategy .py file appears in the scan above
  (the one that calls get_option_price or runs the backtest).
  That will show us the EXACT bug in 30 seconds.
""")