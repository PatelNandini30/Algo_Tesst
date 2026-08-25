#!/usr/bin/env bash
# daily_update.sh — run at 6 AM via cron to pull new data and rebuild caches.
#
# What it does:
#   1. rsync new cleaned_csvs from SMB share (incremental, skips existing files)
#   2. Import new option CSVs into option_data table (file tracker skips already-done files)
#   3. Import OHLC xlsx (spot_data + index_ohlc) from SMB
#   4. Import delta/IV xlsx (option_data.delta/iv_pct) from SMB
#   5. Rebuild Arrow feathers for all index symbols (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY)
#   6. Rebuild NIFTYMIDCAP100 feather for the overlay
#   7. Restart workers so the trading calendar cache reloads
#
# NOTE: FINNIFTY is NOT in the OHLC xlsx — its spot_data comes from NSE archives only.
#       If FINNIFTY spot data is stale, update it manually from NSE ind_close_all CSVs.

set -euo pipefail

# gio (GVFS/SMB) needs the desktop session's D-Bus + runtime dir. cron has
# neither, so gio silently fails with "Operation not supported" and fetches
# 0 files (the DB then never updates). Point it at user 1000's live session
# bus so cron can reach the already-running GVFS daemon.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}"

REPO="/home/aff34/Downloads/Algo_Test_Software"
SMB_CLEANED_BASE="smb://192.168.4.50/share/Jitin/NSE_Options_Data/cleaned_csvs"
SMB_OHLC="smb://192.168.4.50/share/Yash Darji/DATA OF OHLC - Copy.xlsx"
SMB_DELTA="smb://192.168.4.50/share/Nandini Patel/Algo test/eod_delta_full_history.xlsx"
CONTAINER="algotest-backend"

# gio cat works without a GVFS fuse mount — works from cron (given the env above)
_gio_copy() {
    gio cat "$1" > "$2" 2>/dev/null && [ -s "$2" ]
}

cd "$REPO"

echo "========================================"
echo "daily_update.sh started at $(date)"
echo "========================================"

# 1. Sync cleaned CSVs — fetch any missing date files via gio cat (date-by-date)
#    Skips files already present locally. Goes back 90 days to catch any gaps.
echo "[1/7] Syncing cleaned_csvs from SMB (gio cat, last 90 days)..."
python3 -c "
import subprocess, datetime, pathlib, sys
SMB = '$SMB_CLEANED_BASE/'
LOCAL = pathlib.Path('$REPO/cleaned_csvs')
end   = datetime.date.today() - datetime.timedelta(days=1)
start = end - datetime.timedelta(days=90)
d = start; fetched = 0
while d <= end:
    fname = f'{d}.csv'
    dest  = LOCAL / fname
    if not dest.exists():
        r = subprocess.run(['gio', 'cat', SMB + fname], capture_output=True)
        if r.returncode == 0 and r.stdout:
            dest.write_bytes(r.stdout)
            fetched += 1
            print(f'  fetched {fname}', flush=True)
    d += datetime.timedelta(days=1)
print(f'  {fetched} new files fetched')
"
echo "      done."

# 2. Import new option CSVs into option_data
echo "[2/7] Importing option_data from cleaned_csvs..."
docker compose exec -T backend python migrate_data.py --option-data
echo "      done."

# 3. Update OHLC xlsx on SMB with latest NSE archive data, then import into DB
echo "[3/7] Updating OHLC xlsx from NSE archives and importing..."
python3 "$REPO/scripts/update_ohlc_xlsx.py" || echo "      WARNING: xlsx update failed (continuing)"
if _gio_copy "$SMB_OHLC" /tmp/ohlc_daily.xlsx; then
    docker cp /tmp/ohlc_daily.xlsx "$CONTAINER:/tmp/ohlc.xlsx"
    docker compose exec -T backend python scripts/import_ohlc_xlsx.py /tmp/ohlc.xlsx
    echo "      done."
else
    echo "      WARNING: OHLC xlsx not reachable — skipping spot data import"
fi

# 3b. Pull spot closes from NSE archives to fill any gap after the xlsx
echo "[3b/7] Pulling spot closes from NSE archives..."
python3 -c "
import urllib.request, datetime, io, csv, json, subprocess, tempfile, pathlib, sys
MAP = {'Nifty 50':'NIFTY','Nifty Bank':'BANKNIFTY','Nifty Financial Services':'FINNIFTY','Nifty Midcap Select':'MIDCPNIFTY'}
rows = []
end   = datetime.date.today() - datetime.timedelta(days=1)
start = end - datetime.timedelta(days=14)
d = start
while d <= end:
    url = f'https://archives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv'
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode()
        for row in csv.DictReader(io.StringIO(text)):
            sym = MAP.get(row.get('Index Name','').strip())
            if sym:
                close = row.get('Closing Index Value','').replace(',','').strip()
                if close: rows.append((str(d), sym, float(close)))
    except Exception:
        pass
    d += datetime.timedelta(days=1)
if not rows: sys.exit(0)
tmp = '/tmp/nse_spot_update.json'
json.dump(rows, open(tmp,'w'))
subprocess.run(['docker','cp', tmp, '$CONTAINER:/tmp/nse_spot_update.json'])
subprocess.run(['docker','compose','exec','-T','backend','python','-c',
    \"import json,sys; sys.path.insert(0,'/app'); from migrate_data import _conn; rows=json.load(open('/tmp/nse_spot_update.json')); conn=_conn(); cur=conn.cursor(); cur.executemany('INSERT INTO spot_data (date,symbol,close) VALUES (%s,%s,%s) ON CONFLICT (date,symbol) DO UPDATE SET close=EXCLUDED.close', rows); conn.commit(); print(f'NSE spot: {len(rows)} rows upserted')\"
], cwd='$REPO')
" || echo "      WARNING: NSE archive fetch failed"
echo "      done."

# 4. Import delta/IV xlsx into option_data
echo "[4/7] Importing delta/IV xlsx..."
if _gio_copy "$SMB_DELTA" /tmp/delta_daily.xlsx; then
    docker cp /tmp/delta_daily.xlsx "$CONTAINER:/tmp/delta.xlsx"
    docker compose exec -T backend python scripts/import_delta_xlsx.py /tmp/delta.xlsx
    echo "      done."
else
    echo "      WARNING: delta xlsx not reachable — skipping delta import"
fi

# 5. Rebuild feathers for regular index symbols.
# Delete existing feather pair first so bulk_load_options loads fresh from DB.
echo "[5/7] Rebuilding index feathers..."
ARROW_ROOT="/data/cache/arrow"
for SYM in NIFTY BANKNIFTY FINNIFTY MIDCPNIFTY; do
    echo "      rebuilding $SYM..."
    docker compose exec -T backend sh -c "
        rm -f '$ARROW_ROOT/arrow-v2:bulk:${SYM}:full/options.feather' \
              '$ARROW_ROOT/arrow-v2:bulk:${SYM}:full/spot.feather'
        python rebuild_feather.py $SYM 2000-01-01 2030-12-31 --no-bump
    " || echo "      WARNING: rebuild failed for $SYM (continuing)"
done
echo "      done."

# 6. Rebuild NIFTYMIDCAP100 feather for overlay (index_ohlc table path)
echo "[6/7] Rebuilding NIFTYMIDCAP100 overlay feather..."
docker compose exec -T backend python -c "
import sys
sys.path.insert(0, '/app')
from services.index_ohlc_store import build_index_ohlc_feather
path = build_index_ohlc_feather('NIFTYMIDCAP100', force=True)
print('NIFTYMIDCAP100 feather:', path)
" || echo "      WARNING: NIFTYMIDCAP100 feather rebuild failed (continuing)"
echo "      done."

# 7. Single data_version bump — invalidates backtest result cache once for all rebuilds
echo "[7/7] Bumping data_version and restarting workers..."
docker compose exec -T backend python -c "
from services.backtest_cache import bump_data_version
v = bump_data_version()
print(f'data_version -> {v}')
" || echo "      WARNING: data_version bump failed"

# Restart workers to reload the trading calendar cache — but ONLY if idle.
# A blind restart kills any in-flight backtest/optim and leaves its Redis meta
# frozen at status=running (shows forever as "queued/running" in the UI). The
# data_version bump above already invalidates caches; a busy worker reloads the
# calendar on its next idle restart. Skip-if-busy honours that.
_restart_if_idle() {
    local svc="$1"
    local active
    active=$(docker compose exec -T "$svc" celery -A worker.celery inspect active 2>/dev/null \
             | grep -cE "run_(algotest_job|optimize_job|backtest_task)" || true)
    if [ "${active:-0}" -gt 0 ]; then
        echo "      SKIP $svc restart — $active job(s) running (will reload calendar when idle)"
    else
        docker compose restart "$svc" && echo "      restarted $svc"
    fi
}
_restart_if_idle worker-backtests
_restart_if_idle worker-backtests-fast
_restart_if_idle worker-optimize
echo "      done."

echo ""
echo "daily_update.sh completed at $(date)"
